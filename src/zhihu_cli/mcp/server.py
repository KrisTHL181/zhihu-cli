"""MCP server bridging the ``zhihu`` CLI to AI agents.

Every leaf command of the CLI is exposed as its own typed MCP tool, generated from
the Click command tree at startup by :mod:`zhihu_cli.mcp.generate`. The model sees
real parameter names, types, defaults, and enums rather than having to spell a
command line from memory. The commands that can never work over MCP — the
full-screen TUIs under ``chat`` and ``interact``, and the endless streams such as
``chat bot`` and ``tools loop`` — are excluded by name; see
:data:`zhihu_cli.mcp.generate.EXCLUDED_PATHS`.

Two hand-written tools remain as an escape hatch:

* :func:`zhihu_help` — walks the command tree via ``--help`` so the model can
  discover details the generated schema does not carry.
* :func:`zhihu_run` — executes an arbitrary command line, for anything the typed
  tools do not cover.

Commands run as subprocesses, which keeps the heavy imports (``curl_cffi``,
``lxml``) and any crash outside this process. Nothing here bounds how long a
command may run: how long to wait is the MCP client's decision, and this server
never kills a call of its own accord. The one case it must refuse rather than run
is a command that can never finish at all — a full-screen terminal UI would hang
until the client abandoned the call — so :func:`zhihu_run` rejects an excluded
path *before* spawning anything; see :func:`_excluded_path`.

Run over stdio, then register it with an MCP client::

    claude mcp add zhihu -- zhihu-mcp

The full tool list costs a large amount of context, so ``--groups`` narrows the
typed tools to the command subtrees named::

    zhihu-mcp --groups browse,search,tools.creator

The two escape hatches are left unfiltered on purpose — they exist to reach what
the typed tools do not cover, and each costs a single tool's worth of schema.
"""

from __future__ import annotations

import argparse
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from zhihu_cli.mcp.generate import EXCLUDED_PATHS, generate_tools

# ── instructions ───────────────────────────────────────────────────────────

_INSTRUCTIONS = """\
Wraps the `zhihu` CLI (Zhihu scraping, publishing, and analysis) as one typed tool
per command: `zhihu_search_question`, `zhihu_browse_following_users`,
`zhihu_publish_article`, and so on. A tool's name is its command path with `_` for
every space and hyphen, so `zhihu browse following users` is
`zhihu_browse_following_users`.

Prefer the typed tools. Each one names its own arguments, types, and allowed
values, and its description ends with the exact command line it maps to. Most read
commands take a `json` argument — set it to `true` for parseable output instead of
styled text.

Two escape hatches remain:
- `zhihu_help(path)` prints the CLI's own `--help` for any command path, for
  detail the typed schema does not carry.
- `zhihu_run(args)` runs an arbitrary command line, for anything the typed tools do
  not cover.

How long a command may run is the client's decision, not this server's: a call
returns when the command finishes. Some commands can never finish over MCP — the
full-screen TUIs (`chat interactive`, `interact comment interactive`) and the endless
streams (`chat bot`, `listen messages`, `listen notifications`, `tools loop`). Those
are refused before they run, so do not retry them; ask the user to run one in their
own terminal instead.
"""


# ── launcher resolution ────────────────────────────────────────────────────


def _launcher() -> list[str]:
    """Return the argv prefix that invokes the ``zhihu`` CLI.

    Prefers the console script next to the running interpreter (guaranteeing the
    same environment and a correct ``prog_name`` in ``--help`` output), then
    ``$PATH``, then falls back to ``python -m``.

    :returns: argv prefix to prepend to the CLI arguments.
    """
    sibling = Path(sys.executable).parent / "zhihu"
    if sibling.is_file() and os.access(sibling, os.X_OK):
        return [str(sibling)]
    found = shutil.which("zhihu")
    if found:
        return [found]
    return [sys.executable, "-m", "zhihu_cli.main"]


# ── invocation helpers ─────────────────────────────────────────────────────


def _invoke(tokens: list[str]) -> subprocess.CompletedProcess[str]:
    """Run the CLI with ``tokens`` and capture its output.

    Nothing here bounds how long the command may run: it returns when it exits, and
    deciding when to give up on it is the client's call, not this server's.

    :param tokens: CLI arguments, already split.
    :returns: the completed process.
    """
    env = {
        **os.environ,
        "NO_COLOR": "1",
        "TERM": "dumb",
        "PYTHONIOENCODING": "utf-8",
    }
    return subprocess.run(
        [*_launcher(), *tokens],
        capture_output=True,
        text=True,
        env=env,
        # This process's own stdin is the JSON-RPC pipe when serving over stdio, so
        # the child is given /dev/null rather than a handle onto the protocol stream.
        stdin=subprocess.DEVNULL,
    )


def _excluded_path(tokens: list[str]) -> tuple[str, ...] | None:
    """Find the excluded command path ``tokens`` starts with, if any.

    Nothing kills a subprocess, so a command that opens a full-screen terminal UI —
    or streams until interrupted — would hang until the client abandoned the call.
    Refusing it up front is the only cheap answer, and it costs no subprocess.

    The match is a plain comparison of the leading tokens against each excluded
    path, so an option that trails the path (``chat interactive --foo``) still
    matches, while one that precedes it (``--profile x chat interactive``) does not.
    That is the intended trade: these commands are reached by their bare path, and
    the generated tools cannot hit this at all, since those paths are never
    generated.

    :param tokens: CLI arguments, already split.
    :returns: the excluded path that was asked for, or ``None``.
    """
    for path in EXCLUDED_PATHS:
        if tuple(tokens[: len(path)]) == path:
            return path
    return None


# ── hand-written escape hatches ────────────────────────────────────────────


def zhihu_help(path: str = "") -> str:
    """Show the zhihu CLI command tree or a command's options.

    Call with no argument to list the top-level command groups, then drill in.

    :param path: space-separated command path, e.g. ``"search"`` or
        ``"search question"``. Empty for the top level.
    :returns: the CLI's help text.
    :raises ToolError: if the command exits non-zero. Only ``ToolError`` carries
        its message to the client: the SDK turns any other exception into a generic
        ``UnexpectedToolError`` and discards the text.
    """
    tokens = shlex.split(path)
    command = f"zhihu {' '.join(tokens)} --help".strip()
    proc = _invoke([*tokens, "--help"])
    if proc.returncode != 0:
        raise ToolError(f"`{command}` exited with code {proc.returncode}.\n{proc.stderr.strip() or '(no stderr)'}")
    return proc.stdout


def zhihu_run(args: str) -> str:
    """Run a zhihu CLI command and return its output.

    Always add ``--json`` when the subcommand supports it — most read commands
    do, and the output is then directly parseable. Example:
    ``zhihu_run('search question "rust 异步" --json')``.

    Commands that print styled text instead of JSON (publish, config, convert)
    are still usable; their output is returned as-is.

    :param args: the command line to run, without the leading ``zhihu``. Quote
        arguments that contain spaces.
    :returns: the command's stdout.
    :raises ToolError: if no command is given, if the command is one that can never
        finish over MCP (a full-screen terminal UI or an endless stream), or if it
        exits non-zero. Only ``ToolError`` carries its message to the client, where
        any other exception is reported as a bare ``UnexpectedToolError``.
    """
    tokens = shlex.split(args)
    if not tokens:
        raise ToolError("No command given. Call zhihu_help() to discover commands.")

    blocked = _excluded_path(tokens)
    if blocked is not None:
        raise ToolError(
            f"`zhihu {' '.join(blocked)}` opens a full-screen terminal UI (or streams until "
            f"interrupted) and can never run over MCP: nothing here can display it or stop it, "
            f"so the call would hang until it was abandoned. Nothing was run — ask the user to "
            f"run it in their own terminal instead."
        )

    proc = _invoke(tokens)
    if proc.returncode != 0:
        raise ToolError(
            f"`zhihu {' '.join(tokens)}` exited with code {proc.returncode}.\n{proc.stderr.strip() or '(no stderr)'}"
        )

    # Successful commands often warn on stderr; only surface it when stdout is
    # empty, so that JSON output stays clean and parseable.
    if not proc.stdout.strip() and proc.stderr.strip():
        return proc.stderr.strip()

    return proc.stdout


# ── server ─────────────────────────────────────────────────────────────────


def _group_prefixes(value: str) -> tuple[tuple[str, ...], ...]:
    """Parse the ``--groups`` argument into command path prefixes.

    Each comma-separated entry names a command path, written with dots
    (``tools.creator``) or spaces (``tools creator``). The path is a prefix, so
    ``tools`` selects the whole ``tools`` subtree; an empty entry is refused
    instead of silently selecting everything.

    :param value: the raw argument value.
    :returns: the path prefixes, in the order given.
    :raises argparse.ArgumentTypeError: if an entry is empty.
    """
    prefixes: list[tuple[str, ...]] = []
    for entry in value.split(","):
        tokens = tuple(entry.replace(".", " ").split())
        if not tokens:
            raise argparse.ArgumentTypeError(f"empty command path in {value!r}")
        prefixes.append(tokens)
    return tuple(prefixes)


def _build_server(include: tuple[tuple[str, ...], ...] | None = None) -> MCPServer:
    """Build an MCP server exposing the selected command subtrees.

    One typed tool per leaf command is generated from the Click tree here rather
    than registered afterwards: ``MCPServer.add_tool`` derives the JSON Schema
    from a function signature, so the tools have to be built before the
    constructor runs. Generation is best-effort — a CLI that cannot be imported
    costs the typed tools, not the server.

    :param include: command path prefixes to expose, or ``None`` for every
        command. The hand-written escape hatches are registered either way.
    :returns: the configured server.
    """
    server = MCPServer(
        name="zhihu",
        instructions=_INSTRUCTIONS,
        tools=generate_tools(invoke=_invoke, include=include),
    )
    server.add_tool(zhihu_help)
    server.add_tool(zhihu_run)
    return server


#: The unfiltered server, also what a library consumer importing ``mcp`` gets.
mcp = _build_server()


def main() -> None:
    """Serve MCP over stdio, optionally restricted to selected command groups."""
    parser = argparse.ArgumentParser(
        prog="zhihu-mcp",
        description="Expose the zhihu CLI to AI agents over MCP (stdio).",
    )
    parser.add_argument(
        "--groups",
        metavar="PATHS",
        type=_group_prefixes,
        help=(
            "comma-separated command paths to expose as typed tools, e.g. "
            "'browse,search,tools.creator'; each path selects its whole subtree. "
            "Omit to expose every command. zhihu_help and zhihu_run stay unfiltered."
        ),
    )
    args = parser.parse_args()
    server = mcp if args.groups is None else _build_server(args.groups)
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
