"""Centralized styled output for zhihu-cli.

All terminal presentation should route through these helpers for a
consistent, colorful experience across every command.

Colour conventions
------------------
Cyan bold   — titles, headings, section labels
Green bold  — success confirmations, usernames
Red bold    — errors (goes to stderr)
Yellow      — warnings, item indices, short tags
Magenta     — numeric values, counts, statistics
Blue dim    — URLs, links
Dim         — metadata, timestamps, secondary info
Bold        — emphasis on key actionable items
"""

import json
from typing import Any

import click

__all__ = [
    "echo",
    "info",
    "success",
    "error",
    "warning",
    "blank",
    "title_text",
    "name_text",
    "url_text",
    "meta_text",
    "num_text",
    "tag_text",
    "label_text",
    "path_text",
    "dim_text",
    "bold_text",
    "item_index",
    "kv",
    "stat",
    "divider",
    "section",
    "heading",
    "file_saved",
    "empty_msg",
    "summary",
    "arrow",
    "print_json",
    "print_table",
    "print_panel",
    "print_markdown",
    "f_title",
    "f_name",
    "f_url",
    "f_num",
    "f_meta",
    "f_tag",
    "f_bold",
    "f_dim",
    "f_path",
    "f_label",
    "f_green",
    "f_cyan",
    "set_json_mode",
    "in_json_mode",
]
from rich import box
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table

# ── console singleton ───────────────────────────────────────────────────────

_console = Console()
_console_err = Console(stderr=True)

_json_mode: bool = False


def set_json_mode(enabled: bool) -> None:
    """Enable/disable JSON output mode.

    When enabled, all display functions route to stderr so stdout
    contains only valid JSON.
    """
    global _json_mode
    _json_mode = enabled


def in_json_mode() -> bool:
    """Return whether JSON output mode is active."""
    return _json_mode


# ── Simple styled echoes ────────────────────────────────────────────────────


def echo(msg: Any = "") -> None:
    """Plain message (accepts any type, converted to string)."""
    click.echo(str(msg), err=_json_mode)


def info(msg: Any = "") -> None:
    """Information / status message (dim)."""
    click.secho(str(msg), dim=True, err=_json_mode)


def success(msg: Any = "") -> None:
    """Success confirmation."""
    click.secho(f"✓ {str(msg)}", fg="green", bold=True, err=_json_mode)


def error(msg: Any = "") -> None:
    """Error message on stderr."""
    click.secho(f"✗ {str(msg)}", fg="red", bold=True, err=True)


def warning(msg: Any = "") -> None:
    """Warning message."""
    click.secho(f"⚠ {str(msg)}", fg="yellow", err=_json_mode)


def blank() -> None:
    """Print a blank line."""
    click.echo(err=_json_mode)


# ── Style-only helpers (return styled string) ───────────────────────────────


def title_text(text: str) -> str:
    """Style a primary title."""
    return click.style(text, fg="cyan", bold=True)


def name_text(text: str) -> str:
    """Style a person's name (green)."""
    return click.style(text, fg="green", bold=True)


def url_text(text: str) -> str:
    """Style a URL (blue, dim)."""
    return click.style(text, fg="blue", dim=True)


def meta_text(text: str) -> str:
    """Style metadata / secondary info (dim)."""
    return click.style(str(text), dim=True)


def num_text(n: Any) -> str:
    """Style a numeric value (magenta)."""
    return click.style(str(n), fg="magenta")


def tag_text(text: str) -> str:
    """Style a short type tag e.g. [article]."""
    return click.style(f"[{text}]", fg="yellow")


def label_text(text: str) -> str:
    """Style a field label / key (bold)."""
    return click.style(text, bold=True)


def path_text(text: str) -> str:
    """Style a file path (cyan)."""
    return click.style(text, fg="cyan")


def dim_text(text: str) -> str:
    """Dimmed text."""
    return click.style(str(text), dim=True)


def bold_text(text: str) -> str:
    """Bold text."""
    return click.style(str(text), bold=True)


# ── Compound output helpers ─────────────────────────────────────────────────


def item_index(i: int, total: int | None = None) -> str:
    """Format an index e.g. [1] or [3/10]."""
    if total:
        return click.style(f"[{i}/{total}]", fg="yellow")
    return click.style(f"[{i}]", fg="yellow")


def kv(key: str, value: Any, indent: int = 2) -> None:
    """Print ``key: value`` on one line."""
    click.echo(f"{' ' * indent}{label_text(key)} {value}", err=_json_mode)


def stat(label: str, value: Any, indent: int = 2) -> None:
    """Print a statistic with a dimmed label."""
    click.echo(f"{' ' * indent}{dim_text(label + ':')} {value}", err=_json_mode)


def divider(char: str = "─", length: int | None = None) -> None:
    """Print a horizontal rule."""
    n = length if length else min(_console.width, 80)
    click.secho(char * n, dim=True, err=_json_mode)


def section(title: str) -> None:
    """Print a section heading preceded by a blank line."""
    blank()
    click.secho(title, fg="cyan", bold=True, err=_json_mode)


def heading(title: str) -> None:
    """Print a heading with underline."""
    click.secho(f"── {title} ──", fg="cyan", bold=True, err=_json_mode)
    click.secho("─" * min(len(title) + 6, 80), dim=True, err=_json_mode)


def file_saved(path: str) -> None:
    """Print a file-saved confirmation."""
    click.secho(f"  → {path}", fg="green", err=_json_mode)


def empty_msg(msg: str = "Nothing found.") -> None:
    """Print an empty / no-results message."""
    click.secho(f"(empty) {msg}", dim=True, err=_json_mode)


def summary(text: str) -> None:
    """Print a bold summary line."""
    click.secho(text, bold=True, err=_json_mode)


def arrow(text: str) -> str:
    """Return a green-arrow + text string (inline)."""
    return click.style(f"  → {text}", fg="green")


# ── JSON output ─────────────────────────────────────────────────────────────


def print_json(data: Any) -> None:
    """Pretty-print data as JSON."""
    click.echo(json.dumps(data, ensure_ascii=False, indent=2))


# ── Rich structured helpers ─────────────────────────────────────────────────


def print_table(
    title: str | None,
    columns: list[str],
    rows: list[list[Any]],
    *,
    box_style: box.Box = box.ROUNDED,
    **kwargs: Any,
) -> None:
    """Render a styled table."""
    table = Table(title=title, box=box_style, **kwargs)
    for col in columns:
        table.add_column(col, style="cyan", header_style="bold cyan")
    for row in rows:
        table.add_row(*[str(c) for c in row])
    (_console_err if _json_mode else _console).print(table)


def print_panel(content: Any, title: str = "", **kwargs: Any) -> None:
    """Render content in a bordered panel."""
    panel = Panel(content, title=title, border_style="cyan", **kwargs)
    (_console_err if _json_mode else _console).print(panel)


def print_markdown(content: str) -> None:
    """Render a Markdown string via Rich."""
    (_console_err if _json_mode else _console).print(Markdown(content))


# ── inline format helpers (for use in f-strings) ────────────────────────────


def f_title(s: str) -> str:
    return click.style(s, fg="cyan", bold=True)


def f_name(s: str) -> str:
    return click.style(s, fg="green", bold=True)


def f_url(s: str) -> str:
    return click.style(s, fg="blue", dim=True)


def f_num(n: Any) -> str:
    return click.style(str(n), fg="magenta")


def f_meta(s: str) -> str:
    return click.style(str(s), dim=True)


def f_tag(s: str) -> str:
    return click.style(f"[{s}]", fg="yellow")


def f_bold(s: str) -> str:
    return click.style(str(s), bold=True)


def f_dim(s: str) -> str:
    return click.style(str(s), dim=True)


def f_path(s: str) -> str:
    return click.style(s, fg="cyan")


def f_label(s: str) -> str:
    return click.style(s, bold=True)


def f_green(s: str) -> str:
    return click.style(s, fg="green")


def f_cyan(s: str) -> str:
    return click.style(s, fg="cyan")
