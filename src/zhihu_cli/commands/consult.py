"""Consult command group — manage paid-consultation (付费咨询) answers."""

from __future__ import annotations

import click

from zhihu_cli.content.handlers.consult import fetch_consult_answers
from zhihu_cli.output import (
    blank,
    echo,
    f_bold,
    f_dim,
    f_label,
    f_meta,
    f_name,
    f_num,
    f_tag,
    f_url,
    heading,
    info,
    print_json,
    set_json_mode,
)


def _print_consult(item: dict) -> None:
    """Print a single consultation item."""
    title = item.get("title", "(no question)")
    questioner = item.get("questioner_name", "")
    service = item.get("service_title", "")
    price = item.get("price", 0)
    audience = item.get("audience_price", 0)
    created = item.get("created_time", "")
    expires = item.get("expires_at", "")
    is_public = item.get("is_public", False)
    is_anonymous = item.get("is_anonymous", False)

    # Build status tags
    tags: list[str] = []
    if service:
        tags.append(service)
    if is_public:
        tags.append("public")
    if is_anonymous:
        tags.append("anonymous")

    # Build stats line
    parts: list[str] = [f_meta(created)]
    if questioner:
        parts.append(f"{f_label('from')} {f_name(questioner)}")
    if price:
        parts.append(f"{f_label('¥')}{f_num(f'{price / 100:.2f}')}")
    if audience:
        parts.append(f"{f_dim('(旁听')} {f_num(f'{audience / 100:.2f}')}{f_dim(')')}")
    if expires:
        parts.append(f"{f_label('expires:')} {f_meta(expires)}")

    tag_str = " ".join(f_tag(t) for t in tags) + " " if tags else ""
    echo(f"  {tag_str}{f_bold(title[:120])}")
    echo(f"  {f_dim('  '.join(parts))}")
    echo(f"  {f_url(item.get('url', ''))}")
    blank()


def register_consult(main_group: click.Group) -> click.Group:
    """Register the ``consult`` command group on the main Click group."""

    @main_group.group()
    def consult() -> None:
        """Manage paid-consultation answers (付费咨询)."""

    # ── shared options ────────────────────────────────────────────────────

    limit_opt = click.option("--limit", "-l", type=int, default=20, help="Page size (default: 20)")
    max_opt = click.option(
        "--max", "-n", "max_items", type=int, default=None, help="Max total items (default: unlimited)"
    )
    json_opt = click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")

    # ── unanswered ────────────────────────────────────────────────────────

    @consult.command("unanswered")
    @limit_opt
    @max_opt
    @json_opt
    def consult_unanswered(limit: int, max_items: int | None, output_json: bool) -> None:
        """List unanswered paid-consultation questions."""
        set_json_mode(output_json)
        info("Fetching unanswered consultations...")
        items = fetch_consult_answers("unanswered", limit=limit, max_items=max_items)
        if output_json:
            print_json(items)
            return
        if not items:
            info("No unanswered consultations.")
            return
        heading(f"Unanswered ({len(items)})")
        for item in items:
            _print_consult(item)
        echo(f"  {f_dim(f'── {len(items)} total')}")

    # ── closed ────────────────────────────────────────────────────────────

    @consult.command("closed")
    @limit_opt
    @max_opt
    @json_opt
    def consult_closed(limit: int, max_items: int | None, output_json: bool) -> None:
        """List closed / resolved paid-consultation questions."""
        set_json_mode(output_json)
        info("Fetching closed consultations...")
        items = fetch_consult_answers("closed", limit=limit, max_items=max_items, sub_status="all")
        if output_json:
            print_json(items)
            return
        if not items:
            info("No closed consultations.")
            return
        heading(f"Closed ({len(items)})")
        for item in items:
            _print_consult(item)
        echo(f"  {f_dim(f'── {len(items)} total')}")

    # ── other ─────────────────────────────────────────────────────────────

    @consult.command("other")
    @limit_opt
    @max_opt
    @json_opt
    def consult_other(limit: int, max_items: int | None, output_json: bool) -> None:
        """List other paid-consultation questions (non-standard status)."""
        set_json_mode(output_json)
        info("Fetching other consultations...")
        items = fetch_consult_answers("other", limit=limit, max_items=max_items)
        if output_json:
            print_json(items)
            return
        if not items:
            info("No 'other' consultations.")
            return
        heading(f"Other ({len(items)})")
        for item in items:
            _print_consult(item)
        echo(f"  {f_dim(f'── {len(items)} total')}")

    return consult
