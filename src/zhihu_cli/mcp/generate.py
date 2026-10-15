"""Generate one typed MCP tool per ``zhihu`` CLI leaf command.

``server.py`` exposes the CLI through two hand-written tools — ``zhihu_help`` and
``zhihu_run`` — which ask a model to spell a command line correctly with no
schema telling it what that command accepts. This module walks the Click command
tree at startup and synthesizes one typed tool per leaf command instead, so the
model sees the real parameter names, types, defaults, and enums. The two shell
tools stay as the escape hatch for anything the typed tools do not cover, though
``zhihu_run`` refuses the excluded paths rather than hang on them.

The schema has to be derived from a *function signature* — ``MCPServer.add_tool``
goes through :meth:`Tool.from_function`, which builds it with pydantic's
``func_metadata``, and there is no seam for a hand-written ``inputSchema``. So for
each leaf command a function is built whose ``__signature__`` is that command's own
parameters (its arguments, then its options), and the resulting ``Tool`` objects are
handed to the ``MCPServer`` constructor. Each generated body closes over its own
frozen :class:`ToolSpec`, which is what keeps every tool from resolving to the last
command walked.

Nothing here imports :mod:`zhihu_cli.mcp.server`: the subprocess plumbing arrives
by injection as ``invoke``.
"""

from __future__ import annotations

import inspect
import logging
import subprocess
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal  # Literal is a runtime value here, not an annotation

import click
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.mcpserver.tools import Tool

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

logger = logging.getLogger(__name__)

#: Command paths that can never work over MCP: they open a full-screen terminal UI
#: or stream until interrupted. Every other leaf command is exposed, so this set is
#: the only thing that decides which commands are absent from the tool list.
#: ``server.py`` imports it as well, to reject these paths in ``zhihu_run`` before spawning
#: anything: with nothing to kill a subprocess that never finishes, refusing them
#: up front is the only thing that keeps such a call from hanging forever.
#:
#: ``chat bot`` and ``tools loop`` return only on ``Ctrl+C`` / ``--once``; they are
#: listed for the same reason as the TUIs. Excluding ``tools loop`` costs nothing —
#: the command it would re-run on a schedule is itself an exposed tool.
EXCLUDED_PATHS: frozenset[tuple[str, ...]] = frozenset(
    {
        ("chat", "bot"),
        ("chat", "interactive"),
        ("interact", "comment", "interactive"),
        ("listen", "messages"),
        ("listen", "notifications"),
        ("tools", "loop"),
    }
)

#: Click type name -> Python type object. Keyed by the string ``type.name``
#: rather than ``isinstance``: click 8.2+ does not export ``IntParamType``,
#: ``BoolParamType``, or ``FloatParamType``. ``file`` and ``path`` both become
#: ``str`` — the CLI reads the file itself, in its own process.
_TYPE_EXPRESSIONS: dict[str, Any] = {
    "text": str,
    "boolean": bool,
    "integer": int,
    "integer range": int,
    "float": float,
    "path": str,
    "file": str,
}

#: Used for a click type this module does not know. A string accepts anything,
#: where guessing a narrower type would reject a legitimate call.
_FALLBACK_TYPE: Any = str

#: Cap on the CLI's own help text copied into a *required* parameter's schema.
#: Optional parameters carry no help: the 109 required ones are the ones a model
#: has to get right, and help on the other 365 would only lengthen every schema.
_HELP_LIMIT = 100

#: Cap on the one-line summary at the head of a tool's description.
_DESCRIPTION_LIMIT = 120

#: click's "no default was given" sentinel. Click 8.4 defines it as
#: ``click.core.UNSET`` and does not re-export it at the package root, so it is
#: reached through ``click.core``. Kept as a module constant so :func:`_is_unset` can
#: compare by identity — see that function for why a name check is not good enough.
_UNSET: Any = click.core.UNSET


@dataclass(frozen=True)
class ParamSpec:
    """One CLI parameter, and how it is exposed as a tool argument.

    :param kwarg: the name the model uses in the tool call.
    :param click_name: the name click stores the parsed value under.
    :param is_argument: whether this is a positional argument rather than an option.
    :param annotation: the Python type for the generated signature — a real object
        such as ``str`` or ``list[str] | None``, not source text.
    :param default: the value shown in the schema; ``None`` when the parameter is
        required. This is the *exposed* default, so click's "no default given"
        sentinel is normalized to ``None``.
    :param required: whether the model must supply the parameter.
    :param is_flag: whether this is a boolean flag, spelled without a value.
    :param multiple: whether the option may be repeated.
    :param variadic: whether the argument swallows every remaining token.
    :param option: the long spelling (``"--json"``), for options.
    :param negative: the reversal of a dual flag (``"--no-markdown"``), if any.
    :param help: the CLI's own help text, kept for the schema of required params.
    """

    kwarg: str
    click_name: str
    is_argument: bool
    annotation: Any
    default: Any = None
    required: bool = False
    is_flag: bool = False
    multiple: bool = False
    variadic: bool = False
    option: str | None = None
    negative: str | None = None
    help: str = ""


@dataclass(frozen=True)
class ToolSpec:
    """Everything one generated tool needs, frozen so a generated body can close over it.

    :param path: the command path as it is spelled on the command line, e.g.
        ``("config", "app-version", "set")`` — hyphens intact, since this is what
        gets run.
    :param name: the exposed tool name, e.g. ``"zhihu_config_app_version_set"``.
        Normalized to an identifier, for the same reason a Python name cannot
        contain a hyphen: it is also the generated function's name.
    :param description: the tool description: click's one-line summary, then the
        exact CLI spelling so a model falling back to ``zhihu_run`` gets it right.
    :param params: the parameters, in click's declaration order.
    """

    path: tuple[str, ...]
    name: str
    description: str
    params: tuple[ParamSpec, ...]


def generate_tools(
    *,
    invoke: Callable[[list[str]], subprocess.CompletedProcess[str]],
    exclude: frozenset[tuple[str, ...]] = EXCLUDED_PATHS,
) -> list[Tool]:
    """Build one typed MCP tool for every leaf command of the ``zhihu`` CLI.

    :param invoke: runs the CLI and returns the completed process; ``server._invoke``.
    :param exclude: command paths to leave out — TUI and streaming commands, which
        can never complete when driven over MCP.
    :returns: the tools, in command-tree order. Empty if the CLI could not be
        imported, so the server still starts with its two shell tools. A leaf that
        cannot be exposed costs exactly that one tool: ``server.py`` calls this at
        import time, so letting a single bad command raise here would take the whole
        server down instead of one command.
    """
    cli = _load_cli()
    if cli is None:
        return []

    ctx = click.Context(cli)
    tools: list[Tool] = []
    claimed: dict[str, tuple[str, ...]] = {}
    for path, command in _iter_leaves(cli, ctx, exclude=exclude):
        try:
            spec = _spec_for(path, command)
            _claim_name(spec, claimed)
            tool = _build_tool(spec, invoke)
        except Exception as exc:
            logger.warning(
                "zhihu-mcp: skipping `zhihu %s`, it cannot be exposed as a tool: %s",
                " ".join(path),
                exc,
                exc_info=True,
            )
            continue
        if tool is not None:
            tools.append(tool)
    return tools


def _load_cli() -> click.Group | None:
    """Import the CLI entry point, or give up and serve only the shell tools.

    ``zhihu_cli.main`` registers every built-in command group and then runs
    ``discover_extensions()`` at *import* time. That catches ``ImportError`` and
    nothing else, so an extension that raises anything else takes the import down
    with it. Losing the typed tools is survivable; failing to start the server is
    not.

    :returns: the root command group, or ``None`` if it could not be imported.
    """
    try:
        from zhihu_cli.main import main
    except Exception:
        logger.warning(
            "zhihu-mcp: could not import the zhihu CLI; serving only zhihu_help/zhihu_run",
            exc_info=True,
        )
        return None
    return main


def _iter_leaves(
    command: click.Command,
    ctx: click.Context,
    path: tuple[str, ...] = (),
    exclude: frozenset[tuple[str, ...]] = EXCLUDED_PATHS,
) -> Iterator[tuple[tuple[str, ...], click.Command]]:
    """Walk a Click command tree, yielding every leaf command with its path.

    :param command: the command to walk; a group is descended into, anything else
        is a leaf and yielded.
    :param ctx: context used to resolve sub-commands through ``get_command``.
    :param path: the path of ``command`` within the tree.
    :param exclude: command paths to skip, subtrees included.
    :returns: an iterator of ``(path, command)`` pairs.
    """
    if path and path in exclude:
        return
    if isinstance(command, click.Group):
        for name in command.list_commands(ctx):
            child = command.get_command(ctx, name)
            if child is None:
                continue
            yield from _iter_leaves(child, ctx, (*path, name), exclude)
    else:
        yield path, command


def _spec_for(path: tuple[str, ...], command: click.Command) -> ToolSpec:
    """Describe one leaf command as a :class:`ToolSpec`.

    :param path: the command's path in the tree.
    :param command: the leaf command itself.
    :returns: the frozen spec.
    :raises RuntimeError: if two parameters of the command would need the same
        argument name, or one cannot be named as a Python identifier.
    """
    params: list[ParamSpec] = []
    taken: set[str] = set()
    for param in command.params:
        if getattr(param, "hidden", False):
            continue
        kwarg = _preferred_kwarg(param)
        if kwarg in taken:
            # The long spelling is normally unique; fall back to click's own name
            # rather than dropping the parameter.
            kwarg = param.name
        if not kwarg.isidentifier() or kwarg.startswith("_") or kwarg in taken:
            raise RuntimeError(
                f"cannot expose {' '.join(path)}: parameters {sorted(taken)} leave no usable name for {param.name!r}"
            )
        taken.add(kwarg)
        params.append(_param_spec(param, kwarg))

    return ToolSpec(
        path=path,
        name=_tool_name(path),
        description=_description_for(path, command),
        params=tuple(params),
    )


def _preferred_kwarg(param: click.Parameter) -> str:
    """Pick the name a parameter is exposed under.

    The long option spelling wins over click's own parameter name, so a model sees
    ``json`` for ``--json`` rather than the internal ``output_json``. Arguments
    keep their click name.

    :param param: the click parameter.
    :returns: the preferred argument name, already hyphen-free.
    """
    if isinstance(param, click.Option):
        longs = [opt for opt in param.opts if opt.startswith("--")]
        if longs:
            return max(longs, key=len).lstrip("-").replace("-", "_")
    return param.name


def _param_spec(param: click.Parameter, kwarg: str) -> ParamSpec:
    """Describe one click parameter, including how to spell it back as argv.

    :param param: the click parameter.
    :param kwarg: the name it is exposed under.
    :returns: the frozen spec.
    """
    is_argument = isinstance(param, click.Argument)
    variadic = is_argument and param.nargs == -1
    multiple = bool(getattr(param, "multiple", False))

    annotation = _annotation_for(param)
    if multiple or variadic:
        annotation = list[annotation]

    raw_default = param.default
    if param.required:
        default: Any = None
    elif _is_unset(raw_default) or raw_default is None:
        default = None
        annotation = annotation | None
    elif multiple:
        default = list(raw_default)
    else:
        default = raw_default

    option: str | None = None
    negative: str | None = None
    if not is_argument:
        longs = [opt for opt in param.opts if opt.startswith("--")]
        # Record the longest `--` spelling; `_build_argv` emits it.
        option = max(longs, key=len) if longs else (param.opts[0] if param.opts else f"--{param.name}")
        secondary = tuple(getattr(param, "secondary_opts", ()) or ())
        negative = secondary[0] if secondary else None

    return ParamSpec(
        kwarg=kwarg,
        click_name=param.name,
        is_argument=is_argument,
        annotation=annotation,
        default=default,
        required=bool(param.required),
        is_flag=bool(getattr(param, "is_flag", False)),
        multiple=multiple,
        variadic=variadic,
        option=option,
        negative=negative,
        # Arguments have no help text in click; only options do.
        help=(getattr(param, "help", None) or "").strip(),
    )


def _annotation_for(param: click.Parameter) -> Any:
    """Resolve the Python type for a click parameter.

    A ``click.Choice`` becomes a ``Literal``, which is what makes pydantic publish
    an ``enum`` instead of a bare string.

    :param param: the click parameter.
    :returns: the type object, ready to sit in a signature.
    """
    if isinstance(param.type, click.Choice):
        return Literal[tuple(str(choice) for choice in param.type.choices)]
    return _TYPE_EXPRESSIONS.get(getattr(param.type, "name", ""), _FALLBACK_TYPE)


def _is_unset(value: Any) -> bool:
    """Report whether a value is click's "no default was given" sentinel.

    ``multiple=True`` parameters and required arguments carry it rather than a real
    default. It is identified by *identity* against the sentinel click itself uses,
    not by the class name ``Sentinel``: a class-name check would also match a
    legitimate default that happens to be an enum or class named ``Sentinel``, and
    rewrite it to ``None`` — changing the exposed default and dropping the parameter
    from argv. ``click.core.UNSET`` is private to click, which is still strictly
    safer than guessing from the name.

    :param value: the value to test.
    :returns: ``True`` for the sentinel.
    """
    return value is _UNSET


def _tool_name(path: tuple[str, ...]) -> str:
    """Derive the exposed tool name for a command path.

    :param path: the command path, hyphens intact.
    :returns: e.g. ``"zhihu_config_app_version_set"`` — a valid Python identifier.
    """
    return "_".join(("zhihu", *path)).replace("-", "_")


def _description_for(path: tuple[str, ...], command: click.Command) -> str:
    """Build a tool description: what the command does, then how to spell it.

    :param path: the command path.
    :param command: the leaf command.
    :returns: the description text.
    """
    summary = command.get_short_help_str(limit=_DESCRIPTION_LIMIT) or " ".join(path)
    return f"{summary}\n\nCLI: zhihu {' '.join(path)} [ARGS] [OPTIONS]"


def _claim_name(spec: ToolSpec, claimed: dict[str, tuple[str, ...]]) -> None:
    """Reserve a tool name for a command, refusing one another command already holds.

    Two different paths can normalize to the same name — ``--foo-bar`` colliding with
    ``--foo_bar``, or ``a-b`` with ``a_b`` — and the second would silently shadow the
    first if both were exposed. Refusing the name raises, and the caller skips just
    that one command.

    :param spec: the command being exposed.
    :param claimed: tool name -> the path that already owns it, mutated in place.
    :raises RuntimeError: if the name is already taken.
    """
    clash = claimed.get(spec.name)
    if clash is not None:
        raise RuntimeError(f"tool name {spec.name!r} is already used by `zhihu {' '.join(clash)}`")
    claimed[spec.name] = spec.path


def _build_tool(
    spec: ToolSpec,
    invoke: Callable[[list[str]], subprocess.CompletedProcess[str]],
) -> Tool | None:
    """Turn one spec into a :class:`Tool`, or skip it if it cannot be built.

    One unusable command must not cost every other tool: the server is constructed
    at import time, so a raise here would leave ``zhihu-mcp`` unable to start at all.

    :param spec: the command to expose.
    :param invoke: runs the CLI.
    :returns: the tool, or ``None`` if building it failed.
    """
    try:
        fn = _build_function(spec, _make_runner(spec, invoke))
        tool = Tool.from_function(fn, name=spec.name, description=spec.description)
    except Exception:
        logger.warning(
            "zhihu-mcp: skipping `zhihu %s` (%s), could not build a tool for it",
            " ".join(spec.path),
            spec.name,
            exc_info=True,
        )
        return None

    _strip_titles(tool.parameters)
    _describe_required(tool, spec)
    return tool


def _build_function(spec: ToolSpec, runner: Callable[..., str]) -> Callable[..., str]:
    """Build the function whose signature becomes the tool's JSON Schema.

    Required parameters come first: Python does not allow a defaulted parameter
    before a bare one, and click is free to declare them in any order. Everything is
    passed by keyword, so the reordering is invisible to the body. Both
    ``__signature__`` and ``__annotations__`` are set: pydantic reads
    ``inspect.signature``, while the SDK's ``Context`` / ``Resolve(...)`` scan goes
    through ``typing.get_type_hints`` and sees only ``__annotations__``. Both hold
    real type objects, so neither reader evaluates source text.

    :param spec: the command to expose.
    :param runner: the callable the generated body closes over.
    :returns: the generated function.
    """
    ordered = sorted(spec.params, key=lambda param: not param.required)
    parameters = [
        inspect.Parameter(
            param.kwarg,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            annotation=param.annotation,
            default=inspect.Parameter.empty if param.required else param.default,
        )
        for param in ordered
    ]

    def body(**values: Any) -> str:
        return runner(**values)

    body.__name__ = spec.name
    body.__doc__ = spec.description
    body.__signature__ = inspect.Signature(parameters, return_annotation=str)
    body.__annotations__ = {param.kwarg: param.annotation for param in ordered} | {"return": str}
    return body


def _make_runner(
    spec: ToolSpec,
    invoke: Callable[[list[str]], subprocess.CompletedProcess[str]],
) -> Callable[..., str]:
    """Bind a spec to the subprocess plumbing, producing the tool's body.

    :param spec: the command to expose.
    :param invoke: runs the CLI.
    :returns: a callable taking the tool's arguments by keyword and returning the
        command's output.
    """

    def run(**values: Any) -> str:
        argv = _build_argv(spec, values)
        command = "zhihu " + " ".join(argv)
        proc = invoke(argv)
        if proc.returncode != 0:
            raise ToolError(f"`{command}` exited with code {proc.returncode}.\n{proc.stderr.strip() or '(no stderr)'}")
        # A successful command often warns on stderr; only surface it when stdout is
        # empty, so JSON output stays parseable.
        if not proc.stdout.strip() and proc.stderr.strip():
            return proc.stderr.strip()
        return proc.stdout

    return run


def _build_argv(spec: ToolSpec, values: dict[str, Any]) -> list[str]:
    """Spell a tool call back as CLI arguments.

    Options come first, then an end-of-options ``--``, then the positionals in
    declaration order. The separator is what lets a model pass a positional whose
    value starts with ``-`` — a negative number, or text like ``--json`` — which a
    human can type but the model has no other way to express. An option is left out
    when the model passed ``None`` or reproduced the default click would have applied
    anyway. A valued option is one token — ``--long=value``, never ``--long value`` —
    so a value that is empty, contains ``=``, or starts with ``-`` arrives intact.

    :param spec: the command being called.
    :param values: the arguments the model supplied, keyed by exposed name.
    :returns: argv, starting at the command path.
    """
    argv = list(spec.path)

    for param in spec.params:
        if param.is_argument:
            continue
        value = values.get(param.kwarg)
        if value is None or value == param.default:
            continue
        if param.is_flag:
            flag = param.option if value else param.negative
            if flag is not None:
                argv.append(flag)
        elif param.multiple:
            argv.extend(f"{param.option}={item}" for item in value)
        else:
            argv.append(f"{param.option}={value}")

    positionals: list[str] = []
    for param in spec.params:
        if not param.is_argument:
            continue
        value = values.get(param.kwarg)
        if param.variadic:
            positionals.extend(str(item) for item in value or ())
        elif value is not None:
            positionals.append(str(value))

    if positionals:
        argv.append("--")
        argv.extend(positionals)

    return argv


def _strip_titles(schema: Any) -> None:
    """Drop pydantic's ``title`` keys from a JSON Schema, in place.

    Every property carries a redundant ``title`` restating its name, which is pure
    noise multiplied across the whole tool list. Parameter *names* are never touched:
    a command really can have a ``title`` parameter, and that lives as a key of
    ``properties`` rather than as a schema keyword.

    :param schema: a schema node — a dict, a list of them, or a scalar to ignore.
    """
    if isinstance(schema, list):
        for item in schema:
            _strip_titles(item)
        return
    if not isinstance(schema, dict):
        return
    schema.pop("title", None)
    for key, value in schema.items():
        if key in ("properties", "$defs", "definitions") and isinstance(value, dict):
            for sub_schema in value.values():
                _strip_titles(sub_schema)
        else:
            _strip_titles(value)


def _describe_required(tool: Tool, spec: ToolSpec) -> None:
    """Copy the CLI's help text into the schema of each required parameter.

    :param tool: the built tool, mutated in place. Argument validation reads
        ``fn_metadata.arg_model``, never ``parameters``, so this cannot affect it.
    :param spec: the command the tool wraps.
    """
    properties = tool.parameters.get("properties")
    if not isinstance(properties, dict):
        return
    for param in spec.params:
        if not param.required or not param.help:
            continue
        schema = properties.get(param.kwarg)
        if isinstance(schema, dict):
            schema["description"] = _shorten(param.help, _HELP_LIMIT)


def _shorten(text: str, limit: int) -> str:
    """Collapse whitespace and truncate text to a budget.

    :param text: the text to shorten.
    :param limit: the maximum length of the result.
    :returns: the shortened text.
    """
    collapsed = " ".join(text.split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 1].rstrip() + "…"
