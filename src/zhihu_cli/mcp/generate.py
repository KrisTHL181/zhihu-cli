"""Zhihu-specific configuration for the generic :mod:`click2mcp` generator.

The walker, signature synthesis, and argv encoding live in
:mod:`click2mcp.generator`. This module only names the zhihu CLI, lists the
commands that can never finish over MCP, and keeps the pre-extraction
``generate_tools`` entry point working.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from click2mcp.generator import generate_tools as _generate_tools

if TYPE_CHECKING:
    import subprocess
    from collections.abc import Callable

    import click
    from click2mcp.generator import Tool

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


def generate_tools(
    *,
    invoke: Callable[[list[str]], subprocess.CompletedProcess[str]],
    exclude: frozenset[tuple[str, ...]] = EXCLUDED_PATHS,
    include: tuple[tuple[str, ...], ...] | None = None,
    inherit_group_options: bool = False,
) -> list[Tool]:
    """Build one typed MCP tool per leaf command of the ``zhihu`` CLI.

    New code should use :func:`click2mcp.generator.generate_tools` directly; this
    wrapper only fills in the zhihu-specific arguments.

    :param invoke: runs the CLI and returns the completed process.
    :param exclude: command paths to leave out — TUI and streaming commands, which
        can never complete when driven over MCP.
    :param include: command path prefixes to expose; ``None`` for every leaf.
    :param inherit_group_options: whether ancestor group options are exposed on
        each leaf tool. Off here, so ``--qps`` does not bloat every schema.
    :returns: the generated tools.
    """
    return _generate_tools(
        cli=_load_cli(),
        invoke=invoke,
        app="zhihu",
        exclude=exclude,
        include=include,
        inherit_group_options=inherit_group_options,
    )
