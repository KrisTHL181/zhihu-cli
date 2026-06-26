"""Interact command group — vote, thank, follow, block, comment, collect, report."""

import json

import click

from zhihu_cli.commands._helpers import _parse_item_url, _resolve_answer_id
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
from zhihu_cli.content.handlers.comments import comment_item, delete_comment
from zhihu_cli.content.handlers.people import block, follow, get_my_url_token, unblock, unfollow
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
from zhihu_cli.content.handlers.report import fetch_report_reasons, flatten_reasons, submit_report
from zhihu_cli.output import (
    echo,
    error,
    f_bold,
    f_dim,
    f_green,
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
    success,
    summary,
)


def register_interact(main_group) -> None:
    """Register the interact command group on *main_group*."""

    # ── main group ─────────────────────────────────────────────────────────

    @main_group.group()
    def interact() -> None:
        """Social interactions — vote, thank, follow, block, comment, collect."""

    # ── helpers ─────────────────────────────────────────────────────────────

    def _parse_item_url_safe(url_or_id: str) -> tuple[str | None, str | None]:
        """Try to parse as URL, fall back to treating as raw ID."""
        result = get_type_and_id(url_or_id)
        if result != (None, None):
            return result
        return (None, url_or_id)

    def _extract_url_token(token_or_url: str) -> str:
        """Extract a Zhihu url_token from a full profile URL or return as-is."""
        import re

        m = re.search(r"zhihu\.com/people/([^/?]+)", token_or_url)
        if m:
            return m.group(1)
        return token_or_url.rstrip("/").split("/")[-1]

    def _resolve_following_token(url_token: str | None) -> str:
        """Resolve the url_token: use provided value or auto-detect from /api/v4/me."""
        if url_token:
            return _extract_url_token(url_token)
        token = get_my_url_token()
        if not token:
            raise click.UsageError(
                "Cannot detect your url_token. Please authenticate first (zhihu auth login) "
                "or provide --url-token explicitly."
            )
        return token

    def _display_following_items(items: list[dict], totals: int | None = None) -> None:
        """Display a list of following items in terminal mode."""
        for i, item in enumerate(items, 1):
            ttype = item.get("type", "?")

            if ttype == "user":
                name = item.get("name", "")
                headline = item.get("headline", "")
                is_followed = item.get("is_followed", False)
                is_following = item.get("is_following", False)
                mutual = f" {f_green('[互关]')}" if (is_followed and is_following) else ""
                f_cnt = item.get("follower_count", 0)
                a_cnt = item.get("answer_count", 0)
                art_cnt = item.get("articles_count", 0)
                stats = f"{f_label('followers:')} {f_num(f_cnt)}  {f_label('answers:')} {f_num(a_cnt)}  {f_label('articles:')} {f_num(art_cnt)}"
                echo(f"  {item_index(i)} {f_bold(name)}{mutual}")
                if headline:
                    echo(f"    {f_dim(headline[:120])}")
                echo(f"    {f_dim(stats)}")
                echo(f"    {f_url(item.get('url', ''))}")

            elif ttype == "topic":
                name = item.get("name", "")
                intro = item.get("introduction", "") or item.get("excerpt", "")
                f_cnt = item.get("followers_count", 0)
                q_cnt = item.get("questions_count", 0)
                stats = f"{f_label('followers:')} {f_num(f_cnt)}  {f_label('questions:')} {f_num(q_cnt)}"
                echo(f"  {item_index(i)} {f_bold(name)} {f_tag('topic')}")
                if intro:
                    echo(f"    {f_dim(intro[:120])}")
                echo(f"    {f_dim(stats)}")
                echo(f"    {f_url(item.get('url', ''))}")

            elif ttype == "question":
                title = item.get("title", "") or item.get("excerpt", "") or "(no title)"
                a_cnt = item.get("answer_count", 0)
                f_cnt = item.get("follower_count", 0)
                ctime = item.get("created_time", "")
                stats = f"{f_label('answers:')} {f_num(a_cnt)}  {f_label('followers:')} {f_num(f_cnt)}  {f_label('created:')} {f_meta(ctime)}"
                echo(f"  {item_index(i)} {f_bold(title[:120])}")
                echo(f"    {f_dim(stats)}")
                echo(f"    {f_url(item.get('url', ''))}")

            elif ttype == "column":
                title = item.get("title", "") or "(no title)"
                desc = item.get("description", "") or item.get("excerpt", "")
                creator = item.get("creator", "")
                f_cnt = item.get("followers_count", 0)
                art_cnt = item.get("articles_count", 0)
                stats = f"{f_label('followers:')} {f_num(f_cnt)}  {f_label('articles:')} {f_num(art_cnt)}"
                echo(f"  {item_index(i)} {f_bold(title)} {f_tag('column')}")
                if creator:
                    echo(f"    {f_name(creator)}")
                if desc:
                    echo(f"    {f_dim(desc[:120])}")
                echo(f"    {f_dim(stats)}")
                echo(f"    {f_url(item.get('url', ''))}")

            elif ttype == "collection":
                title = item.get("title", "") or "(no title)"
                desc = item.get("description", "")
                creator_name = item.get("creator_name", "")
                a_cnt = item.get("answer_count", 0)
                f_cnt = item.get("follower_count", 0)
                stats = f"{f_label('items:')} {f_num(a_cnt)}  {f_label('followers:')} {f_num(f_cnt)}"
                echo(f"  {item_index(i)} {f_bold(title)} {f_tag('collection')}")
                if creator_name:
                    echo(f"    {f_label('by')} {f_name(creator_name)}")
                if desc:
                    echo(f"    {f_dim(desc[:120])}")
                echo(f"    {f_dim(stats)}")
                echo(f"    {f_url(item.get('url', ''))}")

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
    @click.option("--limit", type=int, default=20, help="Items per page")
    @click.option("--max", "-n", "max_items", type=int, default=None, help="Max total items")
    @click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
    @click.option("--output", "-o", type=str, default="", help="Save to JSON file")
    def collect_view(
        collection_id_or_url: str, limit: int, max_items: int | None, output_json: bool, output: str
    ) -> None:
        """View contents of a collection by ID or URL.

        \b
        Examples:
          zhihu interact collect view 921015490
          zhihu interact collect view https://www.zhihu.com/collection/921015490
          zhihu interact collect view 921015490 --max 10 --json
        """
        # Resolve ID from URL or use as-is
        item_type, item_id = get_type_and_id(collection_id_or_url)
        if item_type == "collections":
            collection_id = item_id
        else:
            collection_id = collection_id_or_url.strip()

        meta = get_collection_meta(collection_id)
        items = list_collection_contents(collection_id, limit=limit, max_items=max_items)

        if output_json:
            result = {"meta": meta, "items": items}
            print_json(result)
            if output:
                with open(output, "w", encoding="utf-8") as f:
                    json.dump(result, f, ensure_ascii=False, indent=2)
                success(f"Saved {len(items)} items to {output}")
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

        if output:
            result = {"meta": meta, "items": items}
            with open(output, "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
            success(f"Saved {len(items)} items to {output}")

    @interact_collect.command("list")
    @click.option("--url-token", "-u", type=str, default=None, help="Your Zhihu url_token (auto-detected if omitted)")
    @click.option("--limit", type=int, default=20, help="Items per page")
    @click.option("--max", "-n", "max_items", type=int, default=None, help="Max total items")
    @click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
    @click.option("--output", "-o", type=str, default="", help="Save to JSON file")
    def collect_list(url_token: str | None, limit: int, max_items: int | None, output_json: bool, output: str) -> None:
        """List your collections."""
        token = _resolve_following_token(url_token)
        info(f"Fetching collections for {token}...")

        items = list_collections(token, limit=limit, max_items=max_items)

        if output_json:
            print_json(items)
            if output:
                with open(output, "w", encoding="utf-8") as f:
                    json.dump(items, f, ensure_ascii=False, indent=2)
                success(f"Saved {len(items)} items to {output}")
            return

        if not items:
            info("No collections found.")
            return

        _display_following_items(items)

        if output:
            with open(output, "w", encoding="utf-8") as f:
                json.dump(items, f, ensure_ascii=False, indent=2)
            success(f"Saved {len(items)} items to {output}")

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
    @click.argument("url")
    @click.option("--reason", "-r", "reason_id", required=True, help="Reason ID (from 'zhihu interact report reasons')")
    @click.option("--custom-reason", "-c", default="", help="Custom explanation text")
    @click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
    def report_submit(url: str, reason_id: str, custom_reason: str, output_json: bool) -> None:
        """Submit a report for content at URL.

        \b
        Examples:
          zhihu interact report reasons answer
          zhihu interact report submit https://www.zhihu.com/question/123/answer/456 -r 1040-irrelevant-answer
          zhihu interact report submit https://www.zhihu.com/question/123/answer/456 -r 1040-irrelevant-answer -c "广告垃圾"
        """
        item_type, item_id = _parse_item_url(url)
        if item_type == "answers":
            item_id = _resolve_answer_id(item_id)
        # Map URL type to API object_type
        type_map = {
            "articles": "article",
            "questions": "question",
            "answers": "answer",
            "pins": "pin",
        }
        object_type = type_map.get(item_type, item_type)

        resp = submit_report(
            resource_id=item_id,
            object_type=object_type,
            reason_id=reason_id,
            custom_reason=custom_reason,
            url=url,
        )

        if output_json:
            print_json(resp)
        else:
            if resp.get("is_reported") or (isinstance(resp, dict) and resp.get("success", True)):
                success(f"Report submitted for {f_tag(item_type)} {item_id}")
                echo(f"  {f_label('Reason:')} {reason_id}")
                if custom_reason:
                    echo(f"  {f_label('Detail:')} {custom_reason}")
            else:
                error(f"Report failed: {json.dumps(resp, ensure_ascii=False)}")
