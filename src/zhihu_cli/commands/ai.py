"""AI command group — Zhihu Direct Answer (知乎直答)."""

from __future__ import annotations

import click

from zhihu_cli.content.handlers import fmt_time
from zhihu_cli.output import (
    blank,
    echo,
    f_bold,
    f_cyan,
    f_dim,
    f_green,
    f_label,
    f_meta,
    f_name,
    f_tag,
    f_url,
    item_index,
    print_json,
)


def register_ai(main_group):
    """Register the ai command group onto *main_group*."""

    @main_group.group()
    def ai() -> None:
        """Zhihu Direct Answer (知乎直答) — Zhihu's built-in LLM service."""

    @ai.command("list")
    @click.option("--limit", "-n", type=int, default=None, help="Max items to show")
    @click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
    def ai_list(limit: int | None, output_json: bool) -> None:
        """List your Zhihu Direct Answer session history."""
        from zhihu_cli.content.handlers.ai_direct import fetch_session_history

        items = fetch_session_history()

        if limit is not None and len(items) > limit:
            items = items[:limit]

        if output_json:
            print_json(items)
            return

        if not items:
            echo(f"  {f_dim('No session history found.')}")
            return

        for i, item in enumerate(items, 1):
            title = item["title"] or "(no title)"
            summary = item.get("summary", "")
            send_time = fmt_time(item.get("send_time", 0))
            is_fav = item.get("is_favorite", False)
            fav_mark = f" {f_tag('★')}" if is_fav else ""

            echo(f"  {item_index(i)}{fav_mark} {f_bold(title)}")
            echo(f"    {f_label('id:')} {f_dim(item['id'])}")
            if summary:
                echo(f"    {f_dim(summary[:200])}")
            echo(f"    {f_label('time:')} {f_meta(send_time)}")
            if i < len(items):
                blank()

    @ai.command("view")
    @click.argument("session_id", type=str)
    @click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
    def ai_view(session_id: str, output_json: bool) -> None:
        """View a Zhihu Direct Answer conversation by session ID.

        SESSION_ID is the conversation ID from ``zhihu ai list``.
        """
        from zhihu_cli.content.handlers.ai_direct import fetch_session_detail

        turns = fetch_session_detail(session_id)

        if output_json:
            print_json(turns)
            return

        if not turns:
            echo(f"  {f_dim('No conversation found.')}")
            return

        for ti, turn in enumerate(turns):
            is_user = turn["type"] == "user"
            content = turn.get("content", "")
            send_time = fmt_time(turn.get("send_time", 0))

            if is_user:
                echo(f"  {f_cyan('You')}  {f_meta(send_time)}")
                echo(f"  {f_bold(content)}")
            else:
                # ── AI answer ────────────────────────────────────────────
                echo(f"  {f_green('知乎直答')}  {f_meta(send_time)}")

                # ── Thinking ─────────────────────────────────────────────
                thinking = turn.get("thinking", "")
                if thinking:
                    echo(f"  {f_dim('Thinking...')}")
                    echo(f"  {f_dim(thinking)}")
                    blank()

                if content:
                    echo(f"  {content}")
                else:
                    echo(f"  {f_dim('(no answer text)')}")

                # ── Sources ──────────────────────────────────────────────
                sources = turn.get("sources", [])
                if sources:
                    blank()
                    echo(f"    {f_label(f'Sources ({len(sources)})')}")
                    for si, src in enumerate(sources, 1):
                        src_title = src.get("title", "")
                        src_url = src.get("url", "")
                        src_author = src.get("author", "")
                        author_str = f" {f_dim('by')} {f_name(src_author)}" if src_author else ""
                        echo(f"    {item_index(si)} {f_bold(src_title)}{author_str}")
                        if src_url:
                            echo(f"       {f_url(src_url)}")

                # ── Follow-ups ───────────────────────────────────────────
                followups = turn.get("followups", [])
                if followups:
                    blank()
                    echo(f"    {f_label('Related questions')}")
                    for fq in followups:
                        echo(f"    {f_dim('→')} {f_dim(fq)}")

                # ── Timing ────────────────────────────────────────────────
                cost = turn.get("cost_time_ms")
                if cost is not None:
                    echo(f"    {f_dim(f'({cost}ms)')}")

            if ti < len(turns) - 1:
                blank()
                echo(f"  {f_dim('─' * 60)}")
                blank()

    _KNOWLEDGE_HELP = (
        "Knowledge bases to search, comma-separated.\n"
        "Presets: all, zhihu-only, paper-only, global-only, personal-only, none.\n"
        "Raw IDs: KBT_GLOBAL, KBT_ZHIHU, KBT_PAPER, KBT_PERSONAL_KNOWLEDGE_BASE."
    )

    @ai.command("chat")
    @click.argument("message", type=str)
    @click.option(
        "--model",
        "-m",
        type=click.Choice(["deepseek-r1"]),
        default="deepseek-r1",
        help="Model to use (default: deepseek-r1)",
    )
    @click.option(
        "--session",
        "-s",
        type=str,
        default="",
        help="Session ID to continue an existing conversation",
    )
    @click.option(
        "--mode",
        type=click.Choice(["fast", "deep-thinking"]),
        default="fast",
        help="Chat mode (default: fast)",
    )
    @click.option(
        "--knowledge",
        "-k",
        type=str,
        default="all",
        help=_KNOWLEDGE_HELP,
    )
    @click.option(
        "--no-stream",
        is_flag=True,
        default=False,
        help="Wait for the full answer instead of streaming",
    )
    @click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
    def ai_chat(
        message: str,
        model: str,
        session: str,
        mode: str,
        knowledge: str,
        no_stream: bool,
        output_json: bool,
    ) -> None:
        """Send a message to Zhihu Direct Answer and stream the response.

        MESSAGE is the question or prompt you want to ask.

        \b
        Examples:
          zhihu ai chat "什么是黎曼猜想？"
          zhihu ai chat -s <session_id> "再详细解释一下"
          zhihu ai chat --mode deep-thinking "证明根号2是无理数"
          zhihu ai chat -k paper-only "Rust异步编程的最佳实践？"
          zhihu ai chat -k KBT_ZHIHU,KBT_PAPER "最近有什么AI新闻？"
        """
        from zhihu_cli.content.handlers.ai_direct import (
            CHAT_MODELS,
            CHAT_MODES,
            chat_complete,
            chat_stream,
            resolve_knowledge_ids,
        )

        chat_model = CHAT_MODELS.get(model, model)
        chat_mode = CHAT_MODES.get(mode, mode)
        knowledge_ids = resolve_knowledge_ids(knowledge)

        if no_stream:
            result = chat_complete(
                message=message,
                session_id=session,
                chat_model=chat_model,
                chat_mode=chat_mode,
                knowledge_ids=knowledge_ids,
            )
            if output_json:
                print_json(result)
                return
            if result.get("thinking"):
                echo(f"  {f_dim('Thinking...')}")
                echo(f"  {f_dim(result['thinking'])}")
                blank()
            echo(f"  {f_green('知乎直答')}")
            echo(f"  {result['answer']}")
            if result.get("session_id"):
                echo(f"  {f_label('session:')} {f_dim(result['session_id'])}")
            return

        # ── Streaming mode ────────────────────────────────────────────────
        answer_parts: list[str] = []
        thinking_parts: list[str] = []
        session_id: str = session
        sources: list[dict] = []
        followups: list[dict] = []
        first_chunk = True
        thinking_started = False

        for event in chat_stream(
            message=message,
            session_id=session,
            chat_model=chat_model,
            chat_mode=chat_mode,
            knowledge_ids=knowledge_ids,
        ):
            etype = event["event"]

            if etype == "init":
                session_id = event["data"]["session_id"]

            elif etype == "think_chunk":
                chunk = event["data"]["thinking"]
                if chunk:
                    if not output_json:
                        if not thinking_started:
                            echo(f"  {f_dim('Thinking...')}")
                            thinking_started = True
                        click.echo(f_dim(chunk), nl=False)
                    thinking_parts.append(chunk)

            elif etype == "answer_chunk":
                chunk = event["data"]["summary"]
                if chunk:
                    if not output_json:
                        if thinking_started:
                            blank()
                            blank()
                            thinking_started = False
                        if first_chunk:
                            echo(f"  {f_green('知乎直答')}")
                            blank()
                            first_chunk = False
                        click.echo(chunk, nl=False)
                    answer_parts.append(chunk)

            elif etype == "cards":
                sources = event["data"]

            elif etype == "followups":
                followups = event["data"]

            elif etype == "end":
                pass  # stream finished, print summary below

        # ── Post-stream summary ───────────────────────────────────────────
        if not output_json:
            if not first_chunk:
                blank()
                blank()

        if output_json:
            print_json(
                {
                    "session_id": session_id,
                    "answer": "".join(answer_parts),
                    "thinking": "".join(thinking_parts),
                    "sources": sources,
                    "followups": followups,
                }
            )
            return

        # Sources
        if sources:
            echo(f"  {f_label(f'Sources ({len(sources)})')}")
            for si, src in enumerate(sources, 1):
                src_title = src.get("title", "")
                src_url = src.get("url", "")
                src_author = src.get("author", "")
                author_str = f" {f_dim('by')} {f_name(src_author)}" if src_author else ""
                echo(f"  {item_index(si)} {f_bold(src_title)}{author_str}")
                if src_url:
                    echo(f"     {f_url(src_url)}")
            blank()

        # Follow-up questions
        if followups:
            echo(f"  {f_label('Related questions')}")
            for fq in followups:
                fq_text = fq.get("intro_word", "") if isinstance(fq, dict) else str(fq)
                echo(f"  {f_dim('→')} {f_dim(fq_text)}")

        # Session ID for continued chat
        if session_id:
            echo(f"  {f_label('session:')} {f_dim(session_id)}")
