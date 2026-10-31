"""MCP server bridging the ``zhihu`` CLI to AI agents.

The implementation lives in :mod:`click2mcp`: it walks the Click tree, synthesizes
the typed tool schemas, encodes argv, and provides the ``--groups`` launcher and
the ``zhihu_help`` / ``zhihu_run`` escape hatches. This module only configures it
for zhihu and names the commands that can never work over MCP — the full-screen
TUIs under ``chat`` and ``interact``, and the endless streams such as ``chat bot``
and ``tools loop``; see :data:`zhihu_cli.mcp.generate.EXCLUDED_PATHS`.

Commands run as subprocesses, which keeps the heavy imports (``curl_cffi``,
``lxml``) and any crash outside this process. Nothing here bounds how long a
command may run: how long to wait is the MCP client's decision, and this server
never kills a call of its own accord. The one case it must refuse rather than run
is a command that can never finish at all — a full-screen terminal UI would hang
until the client abandoned the call — so ``zhihu_run`` rejects an excluded path
*before* spawning anything.

Run over stdio, then register it with an MCP client::

    claude mcp add zhihu -- zhihu-mcp

The full tool list costs a large amount of context, so ``--groups`` narrows the
typed tools to the command subtrees named::

    zhihu-mcp --groups browse,search,tools.creator

The two escape hatches are left unfiltered on purpose — they exist to reach what
the typed tools do not cover, and each costs a single tool's worth of schema.
"""

from __future__ import annotations

import sys

# ── instructions and escape-hatch descriptions ─────────────────────────────

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

_HELP_DESCRIPTION = """\
Show the zhihu CLI command tree or a command's options.

Call with no argument to list the top-level command groups, then drill in.

:param path: space-separated command path, e.g. ``"search"`` or
    ``"search question"``. Empty for the top level.
:returns: the CLI's help text.
:raises ToolError: if the command exits non-zero. Only ``ToolError`` carries
    its message to the client: the SDK turns any other exception into a generic
    ``UnexpectedToolError`` and discards the text.
"""

_RUN_DESCRIPTION = """\
Run a zhihu CLI command and return its output.

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

# ── server ─────────────────────────────────────────────────────────────────

try:
    from click2mcp import ClickMCP

    from zhihu_cli.mcp.generate import EXCLUDED_PATHS, _load_cli
except ImportError as _exc:  # only reachable without the optional ``[mcp]`` extra
    _MCP_IMPORT_ERROR: ImportError | None = _exc
    _bridge = None
    mcp = None
else:
    _MCP_IMPORT_ERROR = None

    _bridge = ClickMCP(
        app="zhihu",
        cli=_load_cli,
        module="zhihu_cli.main",
        instructions=_INSTRUCTIONS,
        exclude=EXCLUDED_PATHS,
        # zhihu's only non-eager root option is --qps; exposing it on all 191
        # tools would bloat every schema. zhihu_run can still pass it.
        inherit_group_options=False,
        escape_hatches=True,
        help_description=_HELP_DESCRIPTION,
        run_description=_RUN_DESCRIPTION,
    )
    mcp = _bridge.server


def main() -> None:
    """Serve MCP over stdio, optionally restricted to selected command groups."""
    if _MCP_IMPORT_ERROR is not None:
        sys.exit("zhihu-mcp needs the optional MCP dependency.\nInstall it with: pip install 'zhihu-toolkit[mcp]'")
    _bridge.run()


if __name__ == "__main__":
    main()
