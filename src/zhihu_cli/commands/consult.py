"""Consult command group — manage paid-consultation (付费咨询) answers."""

from __future__ import annotations

import click

from zhihu_cli.content.handlers.consult import (
    fetch_consult_answers,
    fetch_conversation_detail,
    parse_conversation_id,
)
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


def _print_conversation_detail(item: dict) -> None:
    """Print a single consultation conversation in full detail."""
    # ── header ─────────────────────────────────────────────────────────
    service = item.get("service_title", "")
    tags: list[str] = [service] if service else []
    if item.get("is_anonymous"):
        tags.append("anonymous")
    if item.get("is_public"):
        tags.append("public")

    questioner = item["questioner"]
    responder = item["responder"]
    questioner_label = f"{questioner['name']} (anonymous)" if item.get("is_anonymous") else questioner["name"]

    tag_str = " ".join(f_tag(t) for t in tags) + " " if tags else ""
    status = item["status"]
    price_yuan = f"{item['price'] / 100:.2f}"
    heading(f"Consultation {tag_str}{f_dim(f'[{status}]')}")

    # Metadata block
    echo(f"  {f_label('From:')}    {f_name(questioner_label)}")
    echo(f"  {f_label('To:')}      {f_name(responder['name'])}")
    echo(f"  {f_label('Price:')}   ¥{f_num(price_yuan)}")
    if item.get("audience_price"):
        audience_yuan = f"{item['audience_price'] / 100:.2f}"
        echo(f"  {f_label('Audience:')} ¥{f_num(audience_yuan)} (旁听)")
    if item.get("actual_income_title"):
        echo(f"  {f_label('Income:')}  {f_dim(item['actual_income_title'])}")
    echo(f"  {f_label('Expires:')} {f_meta(item['expires_at'])}")
    if item.get("first_answer_at"):
        echo(f"  {f_label('Answered:')} {f_meta(item['first_answer_at'])}")
    echo(f"  {f_url(item.get('url', ''))}")
    blank()

    # ── messages ───────────────────────────────────────────────────────
    messages = item.get("messages", [])
    if not messages:
        info("No messages.")
        return

    for i, msg in enumerate(messages):
        msg_type = msg.get("type", "")
        if msg_type == "question":
            if msg.get("is_first_question"):
                label = "Question"
            else:
                label = "Follow-up"
            echo(f"  {f_bold(f'[{label}]')} {f_meta(msg.get('created_at', ''))}")
        elif msg_type == "answer":
            echo(f"  {f_bold('[Answer]')} {f_meta(msg.get('created_at', ''))}")
        else:
            echo(f"  {f_bold(f'[{msg_type}]')} {f_meta(msg.get('created_at', ''))}")

        text = msg.get("text", "")
        if text:
            # Indent multi-line text
            for line in text.split("\n"):
                echo(f"  {f_dim('│')} {line}")
        # Images
        for img in msg.get("images", []):
            echo(f"  {f_dim('│')} {f_url(img)}")

        if i < len(messages) - 1:
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

    # ── show ────────────────────────────────────────────────────────────

    @consult.command("show")
    @click.argument("url_or_id")
    @json_opt
    def consult_show(url_or_id: str, output_json: bool) -> None:
        """Show full detail of a consultation conversation.

        URL_OR_ID can be a full conversation URL
        (``https://www.zhihu.com/consult/conversation/...``) or a
        bare numeric conversation ID.
        """
        set_json_mode(output_json)
        info("Fetching conversation detail...")
        try:
            conversation_id = parse_conversation_id(url_or_id)
        except ValueError as e:
            raise click.BadParameter(str(e), param_hint="URL_OR_ID") from e

        detail = fetch_conversation_detail(conversation_id)
        if output_json:
            print_json(detail)
            return
        _print_conversation_detail(detail)

    return consult
