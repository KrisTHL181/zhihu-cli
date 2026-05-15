"""crank extension — crank paper monitor, archiver, and AI review tools.

Commands
  zhihu crank check       Check watched authors for new papers (report only).
  zhihu crank fetch       Download new papers from watched authors.
  zhihu crank bootstrap   Scan serial papers dirs and generate authors_registry.json.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import click


def register_cli(main_group: click.Group) -> None:
    """Register the ``zhihu crank`` command group and its subcommands."""
    from zhihu_cli.extensions.crank.monitor import register_commands

    register_commands(main_group)
