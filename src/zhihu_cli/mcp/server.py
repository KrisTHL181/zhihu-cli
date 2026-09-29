"""MCP server bridging the ``zhihu`` CLI to AI agents.

Rather than reimplementing each command group as a typed MCP tool, this server
exposes the CLI itself through two tools:

* :func:`zhihu_help` — walks the command tree via ``--help`` so the model can
  discover commands and options at runtime.
* :func:`zhihu_run` — executes any command and returns its output.

Commands run as subprocesses, which keeps the heavy imports (``curl_cffi``,
``lxml``) and any crash outside this process. Interactive commands are not
listed by hand: every command is run under a timeout, and one that opens a
full-screen TUI or streams indefinitely is killed and reported back to the
model as such — see :func:`_looks_like_tui`.

Run over stdio, then register it with an MCP client::

    claude mcp add zhihu -- zhihu-mcp
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from mcp.server import MCPServer

# ── tuning ─────────────────────────────────────────────────────────────────

#: Default ceiling for a single command, in seconds.
DEFAULT_TIMEOUT = 120

#: Longer ceilings for command paths that legitimately take minutes (batch
#: scraping, media downloads, analytics over a whole library). Matched by prefix.
SLOW_TIMEOUTS: dict[tuple[str, ...], int] = {
    ("scrape",): 900,
    ("download",): 900,
    ("tools",): 600,
}

#: Terminal control sequences a TUI emits when it takes over the screen. Textual
#: enters the alternate screen buffer on mount, which is what these select. Their
#: presence in the captured stderr identifies a full-screen TUI without needing a
#: hand-maintained list of which commands are interactive.
_TUI_MARKERS = ("\x1b[?1049h", "\x1b[?47h")

_INSTRUCTIONS = """\
Wraps the `zhihu` CLI (Zhihu scraping, publishing, and analysis).

Workflow:
1. Call `zhihu_help` with no arguments to see the command groups.
2. Call `zhihu_help` again with a command path (e.g. "search question") to see
   that command's options.
3. Call `zhihu_run` with the assembled command line.

Always append `--json` when the subcommand supports it — nearly every read
command does, and it yields parseable output instead of styled text:
`zhihu_run('search question "rust 异步" --json')`. Commands that do not support
`--json` (publish, config, convert) return plain text.

Every command runs under a timeout (120s, longer for scrape/download/tools). If
one is killed for exceeding it, read the error: when it reports a full-screen
terminal UI the command can never work over MCP, so do not retry it — ask the
user to run it in their own terminal. When the error is ambiguous, the command
may just be slow, and a larger `timeout_seconds` is worth trying once.
"""

mcp = MCPServer(
    name="zhihu",
    instructions=_INSTRUCTIONS,
)


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


def _invoke(tokens: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
    """Run the CLI with ``tokens`` and capture its output.

    :param tokens: CLI arguments, already split.
    :param timeout: seconds to wait before killing the process.
    :returns: the completed process.
    :raises subprocess.TimeoutExpired: if the command outlives ``timeout``.
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
        timeout=timeout,
        env=env,
    )


def _decode(raw: str | bytes | None) -> str:
    """Normalize possibly-binary captured output to text.

    :param raw: stdout/stderr captured by :func:`_invoke`, or ``None``.
    :returns: the decoded text, empty when ``raw`` is ``None``.
    """
    if isinstance(raw, bytes):
        return raw.decode("utf-8", "replace")
    return raw or ""


def _looks_like_tui(stderr: str) -> bool:
    """Report whether a killed command had taken over the terminal.

    Textual renders its full UI even when stdout is a pipe, so a TUI cannot be
    detected up front — but its escape sequences land in the captured stderr,
    which distinguishes it from a command that was merely slow.

    :param stderr: stderr captured before the command was killed.
    :returns: ``True`` if the command opened a full-screen terminal UI.
    """
    return any(marker in stderr for marker in _TUI_MARKERS)


def _killed_reason(tokens: list[str], exc: subprocess.TimeoutExpired, timeout: int) -> str:
    """Explain why a command was killed for exceeding its timeout.

    A full-screen TUI is a dead end that retrying cannot fix, so it is called
    out explicitly; anything else is reported as ambiguous, since a command that
    is merely slow may well succeed with a larger budget.

    :param tokens: CLI arguments, already split.
    :param exc: the exception raised by :func:`_invoke`.
    :param timeout: the timeout that expired, in seconds.
    :returns: a message fragment to append to the error.
    """
    if _looks_like_tui(_decode(exc.stderr)):
        return (
            ". This command opened a full-screen terminal UI, which can never complete "
            "when driven over MCP — do not retry it, ask the user to run it in their own "
            "terminal instead."
        )
    return (
        f". It may be an interactive command waiting for input, or one that streams "
        f"indefinitely, or simply one that needs more than {timeout}s. Check its help "
        f"with zhihu_help first; if it looks interactive, ask the user to run it in "
        f"their own terminal rather than retrying with a longer timeout."
    )


def _timeout_for(tokens: list[str]) -> int:
    """Pick a timeout for the given command.

    :param tokens: CLI arguments, already split.
    :returns: timeout in seconds.
    """
    path = tuple(t for t in tokens if not t.startswith("-"))
    for prefix, seconds in SLOW_TIMEOUTS.items():
        if path[: len(prefix)] == prefix:
            return seconds
    return DEFAULT_TIMEOUT


# ── tools ──────────────────────────────────────────────────────────────────


@mcp.tool()
def zhihu_help(path: str = "") -> str:
    """Show the zhihu CLI command tree or a command's options.

    Call with no argument to list the top-level command groups, then drill in.

    :param path: space-separated command path, e.g. ``"search"`` or
        ``"search question"``. Empty for the top level.
    :returns: the CLI's help text.
    """
    tokens = shlex.split(path)
    command = f"zhihu {' '.join(tokens)} --help".strip()
    try:
        proc = _invoke([*tokens, "--help"], DEFAULT_TIMEOUT)
    except subprocess.TimeoutExpired as exc:
        raise ValueError(
            f"`{command}` timed out after {DEFAULT_TIMEOUT}s" + _killed_reason(tokens, exc, DEFAULT_TIMEOUT)
        ) from None
    if proc.returncode != 0:
        raise ValueError(
            f"`zhihu {' '.join(tokens)} --help` exited with code {proc.returncode}.\n"
            f"{proc.stderr.strip() or '(no stderr)'}"
        )
    return proc.stdout


@mcp.tool()
def zhihu_run(args: str, timeout_seconds: int | None = None) -> str:
    """Run a zhihu CLI command and return its output.

    Always add ``--json`` when the subcommand supports it — most read commands
    do, and the output is then directly parseable. Example:
    ``zhihu_run('search question "rust 异步" --json')``.

    Commands that print styled text instead of JSON (publish, config, convert)
    are still usable; their output is returned as-is.

    :param args: the command line to run, without the leading ``zhihu``. Quote
        arguments that contain spaces.
    :param timeout_seconds: override the default timeout (120s, longer for
        scrape/download/tools). Only raise it when a command legitimately needs
        more time.
    :returns: the command's stdout.
    :raises ValueError: if the command times out or exits non-zero.
    """
    tokens = shlex.split(args)
    if not tokens:
        raise ValueError("No command given. Call zhihu_help() to discover commands.")

    timeout = timeout_seconds or _timeout_for(tokens)

    try:
        proc = _invoke(tokens, timeout)
    except subprocess.TimeoutExpired as exc:
        raise ValueError(
            f"`zhihu {' '.join(tokens)}` was killed after {timeout}s{_killed_reason(tokens, exc, timeout)}"
        ) from None

    if proc.returncode != 0:
        raise ValueError(
            f"`zhihu {' '.join(tokens)}` exited with code {proc.returncode}.\n{proc.stderr.strip() or '(no stderr)'}"
        )

    # Successful commands often warn on stderr; only surface it when stdout is
    # empty, so that JSON output stays clean and parseable.
    if not proc.stdout.strip() and proc.stderr.strip():
        return proc.stderr.strip()

    return proc.stdout


def main() -> None:
    """Serve MCP over stdio."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
