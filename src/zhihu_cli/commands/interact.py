"""Interact command group — vote, thank, follow, block, comment, collect, report, delete."""

from __future__ import annotations

import sys

import click

from zhihu_cli.commands._helpers import (
    _display_following_items,
    _parse_item_url,
    _read_content,
    _resolve_answer_id,
    _resolve_url_token,
    _save_json_output,
)
from zhihu_cli.content.handlers import get_type_and_id
from zhihu_cli.content.handlers.collection import (
    add_to_collection,
    collect,
    create_collection,
    delete_collection,
    delete_to_collection,
    get_collection_meta,
    list_collection_contents,
    list_collections,
)
from zhihu_cli.content.handlers.comments import (
    cancel_dislike_comment,
    cancel_like_comment,
    comment_item,
    delete_comment,
    dislike_comment,
    like_comment,
)
from zhihu_cli.content.handlers.delete import delete_content
from zhihu_cli.content.handlers.people import block, follow, unblock, unfollow
from zhihu_cli.content.handlers.question import (
    downvote_answer,
    downvote_question,
    follow_question,
    neutral_answer,
    thank_answer,
    unfollow_question,
    unthank_answer,
    upvote_answer,
    upvote_question,
)
from zhihu_cli.content.handlers.report import (
    fetch_report_reasons,
    flatten_reasons,
    submit_reports_batch,
)
from zhihu_cli.content.handlers.waterfall import PAGE_SIZE
from zhihu_cli.output import (
    echo,
    error,
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
    item_index,
    print_json,
    set_json_mode,
    success,
    summary,
    warning,
)

# ── report input parsing ───────────────────────────────────────────────────

# URL type (from get_type_and_id) → the object_type the reports API expects.
_REPORT_TYPE_MAP = {
    "articles": "article",
    "questions": "question",
    "answers": "answer",
    "pins": "pin",
}


def _report_target_from_url(url: str) -> dict[str, str]:
    """Resolve a Zhihu URL into the fields ``submit_report`` takes.

    :param url: A Zhihu content URL — answer, article, question, or pin.
    :raises click.BadParameter: If the URL is not a reportable Zhihu URL.
    """
    item_type, item_id = _parse_item_url(url)
    if item_type == "answers":
        item_id = _resolve_answer_id(item_id)
    return {
        "object_type": _REPORT_TYPE_MAP.get(item_type, item_type),
        "resource_id": item_id,
        "url": url,
    }


def _parse_report_lines(text: str) -> list[tuple[int, str, str | None, str | None]]:
    """Parse batch input into ``(line_number, url, reason_id, custom_reason)``.

    Blank lines and lines starting with ``#`` are skipped. A line holds a URL,
    then optionally a per-item reason ID, then optionally free-text custom
    reason (which may contain spaces).

    :param text: The contents of the input file, or ``-`` for stdin.
    :returns: One tuple per non-empty line, in file order.
    """
    entries: list[tuple[int, str, str | None, str | None]] = []
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(maxsplit=2)
        reason_id = parts[1] if len(parts) > 1 else None
        custom_reason = parts[2].strip() if len(parts) > 2 else None
        entries.append((lineno, parts[0], reason_id, custom_reason))
    return entries


def register_interact(main_group) -> None:
    """Register the interact command group on *main_group*."""

    # ── main group ─────────────────────────────────────────────────────────

    @main_group.group()
    def interact() -> None:
        """Social interactions — vote, thank, follow, block, comment, collect, report, delete."""

    # ── helpers ─────────────────────────────────────────────────────────────

    def _parse_item_url_safe(url_or_id: str) -> tuple[str | None, str | None]:
        """Try to parse as URL, fall back to treating as raw ID."""
        result = get_type_and_id(url_or_id)
        if result != (None, None):
            return result
        return (None, url_or_id)

    # ── vote ────────────────────────────────────────────────────────────────

    @interact.group("vote")
    def interact_vote() -> None:
        """Vote on answers and questions."""

    @interact_vote.command("up")
    @click.argument("url_or_id")
    def vote_up(url_or_id: str) -> None:
        """Upvote an answer or question."""
        item_type, item_id = _parse_item_url_safe(url_or_id)
        if item_type in ("answers", "answer"):
            echo(upvote_answer(_resolve_answer_id(item_id)))
        elif item_type in ("questions", "question"):
            echo(upvote_question(item_id))
        else:
            upvote_answer(url_or_id)  # treat as raw ID

    @interact_vote.command("neutral")
    @click.argument("url_or_id")
    def vote_neutral(url_or_id: str) -> None:
        """Remove vote from an answer."""
        item_type, item_id = _parse_item_url_safe(url_or_id)
        echo(neutral_answer(_resolve_answer_id(item_id) if item_type else url_or_id))

    @interact_vote.command("down")
    @click.argument("url_or_id")
    def vote_down(url_or_id: str) -> None:
        """Downvote an answer or question."""
        item_type, item_id = _parse_item_url_safe(url_or_id)
        if item_type in ("answers", "answer"):
            echo(downvote_answer(_resolve_answer_id(item_id)))
        elif item_type in ("questions", "question"):
            echo(downvote_question(item_id))
        else:
            downvote_answer(url_or_id)

    # ── thank ───────────────────────────────────────────────────────────────

    @interact.group("thank")
    def interact_thank() -> None:
        """Thank or unthank answers."""

    @interact_thank.command("add")
    @click.argument("answer_id")
    def thank_add(answer_id: str) -> None:
        """Thank an answer."""
        echo(thank_answer(answer_id))

    @interact_thank.command("remove")
    @click.argument("answer_id")
    def thank_remove(answer_id: str) -> None:
        """Remove thanks from an answer."""
        echo(unthank_answer(answer_id))

    # ── follow ──────────────────────────────────────────────────────────────

    @interact.group("follow")
    def interact_follow() -> None:
        """Follow users and questions."""

    @interact_follow.command("user")
    @click.argument("user_id")
    def follow_user(user_id: str) -> None:
        """Follow a user by URL token or ID."""
        echo(follow(user_id))

    @interact_follow.command("question")
    @click.argument("question_id")
    def follow_question_cmd(question_id: str) -> None:
        """Follow a question."""
        echo(follow_question(question_id))

    # ── unfollow ────────────────────────────────────────────────────────────

    @interact.group("unfollow")
    def interact_unfollow() -> None:
        """Unfollow users and questions."""

    @interact_unfollow.command("user")
    @click.argument("user_id")
    def unfollow_user(user_id: str) -> None:
        """Unfollow a user."""
        echo(unfollow(user_id))

    @interact_unfollow.command("question")
    @click.argument("question_id")
    def unfollow_question_cmd(question_id: str) -> None:
        """Unfollow a question."""
        echo(unfollow_question(question_id))

    # ── block ───────────────────────────────────────────────────────────────

    @interact.group("block")
    def interact_block() -> None:
        """Block or unblock users."""

    @interact_block.command("add")
    @click.argument("user_id")
    def block_user(user_id: str) -> None:
        """Block a user."""
        block(user_id)
        success(f"Blocked {user_id}")

    @interact_block.command("remove")
    @click.argument("user_id")
    def block_remove(user_id: str) -> None:
        """Unblock a user."""
        unblock(user_id)
        success(f"Unblocked {user_id}")

    # ── comment ─────────────────────────────────────────────────────────────

    @interact.group("comment")
    def interact_comment() -> None:
        """Post or delete comments."""

    @interact_comment.command("post")
    @click.argument("url")
    @click.argument("content")
    def comment_post(url: str, content: str) -> None:
        """Post a comment on an item. Use URL to identify the item."""
        item_type, item_id = _parse_item_url(url)
        if item_type == "answers":
            item_id = _resolve_answer_id(item_id)
        resp = comment_item(item_type, item_id, content)
        echo(resp)

    @interact_comment.command("reply")
    @click.argument("url")
    @click.argument("comment_id")
    @click.argument("content")
    def comment_reply(url: str, comment_id: str, content: str) -> None:
        """Reply to an existing comment on an item.

        URL identifies the item (answer/article/pin/question) the comment
        belongs to.  COMMENT_ID is the target comment to reply to.

        \b
        Examples:
          zhihu interact comment reply https://www.zhihu.com/question/123/answer/456 11515861130 "同意！"
          zhihu interact comment reply https://zhuanlan.zhihu.com/p/123456 11515861130 "好文"
        """
        item_type, item_id = _parse_item_url(url)
        if item_type == "answers":
            item_id = _resolve_answer_id(item_id)
        resp = comment_item(item_type, item_id, content, reply_comment_id=comment_id)
        echo(resp)

    @interact_comment.command("delete")
    @click.argument("comment_id")
    def comment_delete(comment_id: str) -> None:
        """Delete a comment by ID."""
        delete_comment(comment_id)
        success(f"Deleted comment {comment_id}")

    @interact_comment.command("like")
    @click.argument("comment_id")
    def comment_like(comment_id: str) -> None:
        """Like a comment."""
        like_comment(comment_id)
        success(f"Liked comment {comment_id}")

    @interact_comment.command("unlike")
    @click.argument("comment_id")
    def comment_unlike(comment_id: str) -> None:
        """Cancel a previous like on a comment."""
        cancel_like_comment(comment_id)
        success(f"Canceled like on comment {comment_id}")

    @interact_comment.command("dislike")
    @click.argument("comment_id")
    def comment_dislike(comment_id: str) -> None:
        """Dislike a comment."""
        dislike_comment(comment_id)
        success(f"Disliked comment {comment_id}")

    @interact_comment.command("undislike")
    @click.argument("comment_id")
    def comment_undislike(comment_id: str) -> None:
        """Cancel a previous dislike on a comment."""
        cancel_dislike_comment(comment_id)
        success(f"Canceled dislike on comment {comment_id}")

    @interact_comment.command("interactive")
    @click.argument("url")
    def comment_interactive(url: str) -> None:
        """Browse and reply to comments in an interactive terminal UI.

        Fetches the full comment tree for the given URL, then opens a
        keyboard-navigable interface:

        \b
          ↑/↓ or j/k  — Navigate comments (including children)
          PgUp/PgDn   — Jump to previous/next top-level comment
          Enter       — Open reply panel for the selected comment
          Esc         — Cancel reply / close reply panel
          q           — Quit

        \b
        Example:
          zhihu interact comment interactive https://www.zhihu.com/question/123/answer/456
        """
        item_type, item_id = _parse_item_url(url)
        if item_type == "answers":
            item_id = _resolve_answer_id(item_id)
        from zhihu_cli.commands.interact_comment_interactive import run_interactive_comments

        run_interactive_comments(item_type, item_id)

    # ── collect ─────────────────────────────────────────────────────────────

    @interact.group("collect")
    def interact_collect() -> None:
        """Manage collections."""

    @interact_collect.command("add")
    @click.argument("url")
    @click.option("--collection", "-c", "collection_id", help="Target collection ID")
    def collect_add(url: str, collection_id: str | None) -> None:
        """Add an item to the default or specified collection."""
        item_type, item_id = _parse_item_url(url)
        if collection_id:
            echo(add_to_collection(item_type, item_id, collection_id))
        else:
            echo(collect(item_type, item_id))

    @interact_collect.command("remove")
    @click.argument("url")
    @click.option("--collection", "-c", "collection_id", required=True, help="Target collection ID")
    def collect_remove(url: str, collection_id: str) -> None:
        """Remove an item from a collection."""
        item_type, item_id = _parse_item_url(url)
        echo(delete_to_collection(item_type, item_id, collection_id))

    @interact_collect.command("create")
    @click.argument("title")
    @click.option("--description", "-d", default="", help="Collection description")
    @click.option("--public/--private", default=True, help="Visibility")
    def collect_create(title: str, description: str, public: bool) -> None:
        """Create a new collection."""
        echo(create_collection(title, description, public))

    @interact_collect.command("delete")
    @click.argument("collection_id")
    def collect_delete(collection_id: str) -> None:
        """Delete a collection."""
        echo(delete_collection(collection_id))

    @interact_collect.command("view")
    @click.argument("collection_id_or_url")
    @click.option("--max", "-n", "max_items", type=int, default=None, help="Max total items (default: all)")
    @click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
    @click.option("--output", "-o", type=str, default="", help="Save to JSON file")
    def collect_view(collection_id_or_url: str, max_items: int | None, output_json: bool, output: str) -> None:
        """View contents of a collection by ID or URL.

        \b
        Examples:
          zhihu interact collect view 921015490
          zhihu interact collect view https://www.zhihu.com/collection/921015490
          zhihu interact collect view 921015490 --max 10 --json
        """
        set_json_mode(output_json)
        # Resolve ID from URL or use as-is
        item_type, item_id = get_type_and_id(collection_id_or_url)
        if item_type == "collections":
            collection_id = item_id
        else:
            collection_id = collection_id_or_url.strip()

        meta = get_collection_meta(collection_id)
        items = list_collection_contents(collection_id, limit=PAGE_SIZE, max_items=max_items)

        if output_json:
            result = {"meta": meta, "items": items}
            print_json(result)
            _save_json_output(result, output, "items")
            return

        # ── Terminal display ──────────────────────────────────────────────

        title = meta.get("title", "") or "(untitled)"
        creator = meta.get("creator", {})
        creator_name = creator.get("name", "") or "unknown"
        description = meta.get("description", "")
        item_count = meta.get("item_count", 0)
        follower_count = meta.get("follower_count", 0)

        heading(f"Collection: {title}")
        echo(f"  {f_label('by')} {f_name(creator_name)}")
        if description:
            echo(f"  {f_dim(description)}")
        echo(f"  {f_label('items:')} {f_num(item_count)}  {f_label('followers:')} {f_num(follower_count)}")
        echo()

        if not items:
            info("No items in this collection.")
            return

        for i, item in enumerate(items, 1):
            ttype = item.get("type", "?")
            item_title = item.get("title", "") or item.get("excerpt", "")[:80] or "(no title)"
            author_name = item.get("author_name", "")
            url = item.get("url", "")
            upvotes = item.get("voteup_count", 0)
            comments = item.get("comment_count", 0)
            except_str = item.get("excerpt", "")
            collect_time = item.get("collect_time", "")

            echo(f"  {item_index(i, len(items))} {f_tag(ttype)} {f_bold(item_title[:100])}")
            meta_line = f"{f_label('by')} {f_name(author_name)}" if author_name else ""
            meta_line += f"  {f_label('↑')} {f_num(upvotes)}  {f_label('💬')} {f_num(comments)}"
            if collect_time:
                meta_line += f"  {f_meta(collect_time)}"
            echo(f"    {meta_line}")
            if except_str and except_str != item_title:
                echo(f"    {f_dim(except_str[:150])}")
            echo(f"    {f_url(url)}")

        echo()
        summary(f"{len(items)} items")

        _save_json_output({"meta": meta, "items": items}, output, "items")

    @interact_collect.command("list")
    @click.option("--url-token", "-u", type=str, default=None, help="Your Zhihu url_token (auto-detected if omitted)")
    @click.option("--max", "-n", "max_items", type=int, default=None, help="Max total items (default: all)")
    @click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
    @click.option("--output", "-o", type=str, default="", help="Save to JSON file")
    def collect_list(url_token: str | None, max_items: int | None, output_json: bool, output: str) -> None:
        """List your collections."""
        set_json_mode(output_json)
        token = _resolve_url_token(url_token)
        info(f"Fetching collections for {token}...")

        items = list_collections(token, limit=PAGE_SIZE, max_items=max_items)

        if output_json:
            print_json(items)
            _save_json_output(items, output, "collections")
            return

        if not items:
            info("No collections found.")
            return

        _display_following_items(items)

        _save_json_output(items, output, "collections")

    # ── report ──────────────────────────────────────────────────────────────

    @interact.group("report")
    def interact_report() -> None:
        """Report (举报) content — list reasons or submit a report."""

    @interact_report.command("reasons")
    @click.argument("object_type", default="answer")
    @click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
    def report_reasons(object_type: str, output_json: bool) -> None:
        """List available report reasons for an object type.

        OBJECT_TYPE: answer, question, article, comment, or pin (default: answer).
        """
        data = fetch_report_reasons(object_type)

        if output_json:
            print_json(data)
            return

        reasons = flatten_reasons(data)
        if not reasons:
            info(f"No report reasons found for type '{object_type}'.")
            return

        heading(f"Report reasons for '{object_type}'")
        current_category = None
        for r in reasons:
            if r["category"] and r["category"] != current_category:
                current_category = r["category"]
                echo(f"  {f_tag(current_category)}")
            label = f"    {r['id']} — {r['text']}" if r["category"] else f"  {r['id']} — {r['text']}"
            echo(label)

    @interact_report.command("submit")
    @click.argument("urls", nargs=-1)
    @click.option(
        "--reason",
        "-r",
        "reason_id",
        default=None,
        help="Default reason ID (from 'zhihu interact report reasons') for items that don't carry their own",
    )
    @click.option("--custom-reason", "-c", default="", help="Default custom explanation text")
    @click.option("--file", "-f", "file", default=None, help="Read targets from a file, one per line ('-' for stdin)")
    @click.option("--delay", default=2.0, show_default=True, help="Seconds to wait between submissions")
    @click.option("-y", "--yes", "skip_confirm", is_flag=True, default=False, help="Skip the confirmation prompt")
    @click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
    def report_submit(
        urls: tuple[str, ...],
        reason_id: str | None,
        custom_reason: str,
        file: str | None,
        delay: float,
        skip_confirm: bool,
        output_json: bool,
    ) -> None:
        """Report one or more Zhihu items (举报).

        URLs can be passed as arguments and/or read from --file, one per line
        (blank lines and '#' comments are ignored). A line may carry its own
        reason ID and custom text, which take precedence over -r/-c:

        \b
            <url> [reason_id] [custom reason text]

        Reports go out one at a time with --delay between them, and one failed
        item does not stop the rest. A pipe into '-f -' cannot be confirmed, so
        the prompt is skipped in that case — pass -y to suppress the warning.

        \b
        Examples:
          zhihu interact report reasons answer
          zhihu interact report submit https://www.zhihu.com/question/123/answer/456 -r 1040-irrelevant-answer
          zhihu interact report submit URL1 URL2 -r 1040-irrelevant-answer -c "广告垃圾" -y
          zhihu interact report submit -f urls.txt
          cat urls.txt | zhihu interact report submit -f -
        """
        set_json_mode(output_json)

        raw_entries: list[tuple[int | None, str, str | None, str | None]] = []
        if file:
            raw_entries.extend(_parse_report_lines(_read_content(file)))
        raw_entries.extend((None, url, None, None) for url in urls)

        targets: list[dict[str, str]] = []
        problems: list[str] = []
        for lineno, url, line_reason, line_custom in raw_entries:
            where = f"line {lineno}" if lineno is not None else "argument"
            reason = line_reason or reason_id
            if not reason:
                problems.append(f"{where}: {url} — no reason given (use --reason, or '<url> <reason_id>')")
                continue
            try:
                target = _report_target_from_url(url)
            except click.BadParameter as e:
                problems.append(f"{where}: {e.message}")
                continue
            target["reason_id"] = reason
            target["custom_reason"] = custom_reason if line_custom is None else line_custom
            targets.append(target)

        if problems:
            for problem in problems:
                error(problem)
            raise SystemExit(1)
        if not targets:
            error("No report targets given. Pass URLs as arguments or use --file.")
            raise SystemExit(1)

        # ``-f -`` has already consumed stdin, so a pipe leaves nothing for the
        # prompt to read; only a terminal-typed list can still be confirmed.
        if not skip_confirm and file == "-" and not sys.stdin.isatty():
            warning("Reading targets from stdin — skipping the confirmation prompt (pass -y to silence this).")
            skip_confirm = True

        if not skip_confirm:
            heading(f"Report {len(targets)} item(s)")
            for index, target in enumerate(targets, start=1):
                echo(
                    f"  {item_index(index, len(targets))} "
                    f"{f_tag(target['object_type'])} {target['resource_id']}  "
                    f"{f_label(target['reason_id'])}"
                )
            click.confirm("Submit these reports?", abort=True)

        def _progress(index: int, result: dict) -> None:
            label = f"{f_tag(result['object_type'])} {result['resource_id']}"
            position = f"[{index + 1}/{len(targets)}]"
            if result["success"]:
                success(f"{position} Reported {label}")
            elif result.get("skipped"):
                warning(f"{position} Skipped {label} — aborted after a 403")
            else:
                error(f"{position} Failed {label}: {result.get('error', 'unknown error')}")

        results = submit_reports_batch(targets, delay=delay, on_result=_progress)
        reported = sum(1 for r in results if r["success"])
        failed = len(results) - reported

        if output_json:
            print_json(results)
        else:
            summary(f"{reported} reported, {failed} failed")

        if failed:
            raise SystemExit(1)

    # ── delete ──────────────────────────────────────────────────────────────

    @interact.group("delete")
    def interact_delete() -> None:
        """Delete your own content (删除自己发布的内容)."""

    def _delete_and_report(kind: str, item_id: str, skip_confirm: bool, output_json: bool) -> None:
        """Confirm, delete, and report the outcome — shared by the subcommands.

        :param kind: Content type, matching a handler endpoint key.
        :param item_id: Numeric Zhihu ID to delete.
        :param skip_confirm: When false, prompt before deleting.
        :param output_json: Emit the raw response as JSON instead of prose.
        """
        if not skip_confirm:
            click.confirm(f"Delete {kind} {item_id}? This cannot be undone.", abort=True)

        try:
            resp = delete_content(kind, item_id)
        except (ValueError, RuntimeError) as e:
            error(f"Failed to delete {kind} {item_id}: {e}")
            raise SystemExit(1)

        if output_json:
            print_json({"type": kind, "id": item_id, "deleted": True, "response": resp})
            return

        success(f"Deleted {kind} {item_id}")

    @interact_delete.command("answer")
    @click.argument("answer_id")
    @click.option("-y", "--yes", "skip_confirm", is_flag=True, default=False, help="Skip the confirmation prompt")
    @click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
    def delete_answer(answer_id: str, skip_confirm: bool, output_json: bool) -> None:
        """Delete one of your own answers (删除自己的回答).

        ANSWER_ID accepts either a bare answer ID or the composite
        question_id/answer_id form used elsewhere in this CLI.

        \b
        Example:
          zhihu interact delete answer 123456789 -y
        """
        _delete_and_report("answer", _resolve_answer_id(answer_id), skip_confirm, output_json)

    @interact_delete.command("question")
    @click.argument("question_id")
    @click.option("-y", "--yes", "skip_confirm", is_flag=True, default=False, help="Skip the confirmation prompt")
    @click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
    def delete_question(question_id: str, skip_confirm: bool, output_json: bool) -> None:
        """Delete one of your own questions (删除自己发布的提问).

        \b
        Example:
          zhihu interact delete question 12345678 -y
        """
        _delete_and_report("question", question_id, skip_confirm, output_json)

    @interact_delete.command("article")
    @click.argument("article_id")
    @click.option("-y", "--yes", "skip_confirm", is_flag=True, default=False, help="Skip the confirmation prompt")
    @click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
    def delete_article(article_id: str, skip_confirm: bool, output_json: bool) -> None:
        """Delete one of your own articles (删除自己发布的文章).

        \b
        Example:
          zhihu interact delete article 123456 -y
        """
        _delete_and_report("article", article_id, skip_confirm, output_json)

    @interact_delete.command("pin")
    @click.argument("pin_id")
    @click.option("-y", "--yes", "skip_confirm", is_flag=True, default=False, help="Skip the confirmation prompt")
    @click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
    def delete_pin(pin_id: str, skip_confirm: bool, output_json: bool) -> None:
        """Delete one of your own pins (删除自己发布的想法).

        \b
        Example:
          zhihu interact delete pin 1234567890 -y
        """
        _delete_and_report("pin", pin_id, skip_confirm, output_json)
