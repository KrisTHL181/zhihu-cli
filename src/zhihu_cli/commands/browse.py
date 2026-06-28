"""Browse Zhihu content in the terminal."""

import json

import click

from zhihu_cli.commands._helpers import (
    _display_following_items,
    _parse_item_url,
    _resolve_answer_id,
    _resolve_url_token,
    _save_json_output,
    _sort_items,
)
from zhihu_cli.content.handlers import fmt_time
from zhihu_cli.content.handlers.article import scrape_article
from zhihu_cli.content.handlers.comments import print_comments
from zhihu_cli.content.handlers.feed import fetch_feed, fetch_feed_with_markdown
from zhihu_cli.content.handlers.following import (
    fetch_followees,
    fetch_followers,
    fetch_following_collections,
    fetch_following_columns,
    fetch_following_questions,
    fetch_following_topics,
)
from zhihu_cli.content.handlers.hot import fetch_hot_list
from zhihu_cli.content.handlers.question import scrape_answer_page, scrape_answers, scrape_question_data
from zhihu_cli.content.handlers.question_log import fetch_question_log
from zhihu_cli.content.handlers.segment_comments import fetch_segment_comments
from zhihu_cli.content.handlers.upvoter import fetch_upvoters
from zhihu_cli.content.handlers.yanxuan import extract_url_token, fetch_yanxuan_segments, segments_to_text
from zhihu_cli.output import (
    blank,
    echo,
    error,
    f_bold,
    f_cyan,
    f_dim,
    f_green,
    f_label,
    f_meta,
    f_name,
    f_num,
    f_tag,
    f_url,
    info,
    item_index,
    print_json,
    set_json_mode,
    success,
)


def register_browse(main_group):
    """Register the browse command group and all its sub-commands."""

    # ── browse ──────────────────────────────────────────────────────────────

    @main_group.group()
    def browse() -> None:
        """Browse Zhihu content in the terminal."""

    @browse.command("question")
    @click.argument("url")
    @click.option("--reading-mode/--no-reading-mode", default=True, help="Use Rich pager for reading")
    @click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
    @click.option(
        "--sort",
        "sort_by",
        type=click.Choice(["default", "time", "upvotes", "favorites", "comments"]),
        default="default",
        help="Sort answers by criteria",
    )
    def browse_question(url: str, reading_mode: bool, output_json: bool, sort_by: str) -> None:
        """Browse a Zhihu question and all its answers."""
        q_meta, q_detail_md = scrape_question_data(url)

        answers = list(scrape_answers(q_meta))
        if sort_by != "default":
            _SORT_MAP = {"time": "created_time", "upvotes": "vote", "favorites": "favorite", "comments": "comment"}
            answers = _sort_items(answers, _SORT_MAP[sort_by])

        if output_json:
            print_json({"question": q_meta, "detail_md": q_detail_md, "answers": answers})
            return

        if reading_mode:
            try:
                from rich.console import Console
                from rich.markdown import Markdown
            except ImportError:
                reading_mode = False

        question_md = f"# {q_meta['title']}\n\n{q_detail_md}"
        if reading_mode:
            console = Console()
            with console.pager(styles=True, links=True):
                console.print(Markdown(question_md))
                for i, ans in enumerate(answers, 1):
                    console.print(
                        f"\n--- Answer #{i} (ID: {ans['id']}) by {ans['author']} (+{ans['vote']} votes, {ans['comment']} comments, {ans['favorite']} favorites) ---"
                    )
                    console.print(Markdown(ans["content"]))
        else:
            echo(question_md)
            for i, ans in enumerate(answers, 1):
                ans_id = ans["id"]
                author = ans["author"]
                vote = ans["vote"]
                comment = ans["comment"]
                favorite = ans["favorite"]
                echo(
                    f"\n{f_bold('--- Answer')} #{i} "
                    f"({f_label('ID:')} {ans_id}) "
                    f"{f_bold('by')} {f_name(author)} "
                    f"({f_green('+' + str(vote))} {f_meta('votes')}, "
                    f"{f_num(comment)} {f_meta('comments')}, "
                    f"{f_num(favorite)} {f_meta('favorites')}) ---"
                )
                echo(ans["content"])

    @browse.command("answer")
    @click.argument("url")
    @click.option("--reading-mode/--no-reading-mode", default=True, help="Use Rich pager for reading")
    @click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
    def browse_answer(url: str, reading_mode: bool, output_json: bool) -> None:
        """View a single Zhihu answer in the terminal."""
        metadata, markdown = scrape_answer_page(url)

        if output_json:
            print_json({"metadata": metadata, "content_md": markdown})
            return

        if reading_mode:
            try:
                from rich.console import Console
                from rich.markdown import Markdown
            except ImportError:
                reading_mode = False

        title = metadata.get("title", "untitled")
        author = metadata.get("author", "unknown")
        created = metadata.get("created", "unknown")
        upvotes = metadata.get("vote", 0)
        comments = metadata.get("comment", 0)
        favorites = metadata.get("favorite", 0)
        header = f"# {title}\n\n**Author:** {author} | **Date:** {created} | **Upvotes:** {upvotes} | **Comments:** {comments} | **Favorites:** {favorites}"

        if reading_mode:
            console = Console()
            with console.pager(styles=True, links=True):
                console.print(Markdown(header))
                console.print(Markdown(markdown))
        else:
            echo(header)
            blank()
            echo(markdown)

    @browse.command("article")
    @click.argument("url")
    @click.option("--reading-mode/--no-reading-mode", default=True, help="Use Rich pager for reading")
    @click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
    def browse_article(url: str, reading_mode: bool, output_json: bool) -> None:
        """Read a Zhihu article in the terminal."""
        metadata, markdown = scrape_article(url)

        if output_json:
            print_json({"metadata": metadata, "content_md": markdown})
            return

        if reading_mode:
            try:
                from rich.console import Console
                from rich.markdown import Markdown
            except ImportError:
                reading_mode = False

        title = metadata.get("title", "untitled")
        author = metadata.get("author", {}).get("name", "unknown")
        stats = metadata.get("stats", {})
        upvotes = stats.get("voteup_count", 0)
        comments = stats.get("comment_count", 0)
        favorites = stats.get("favlists_count", 0)
        header = f"# {title}\n\n**Author:** {author} | **Upvotes:** {upvotes} | **Comments:** {comments} | **Favorites:** {favorites}"

        if reading_mode:
            console = Console()
            with console.pager(styles=True, links=True):
                console.print(Markdown(header))
                console.print(Markdown(markdown))
        else:
            echo(header)
            blank()
            echo(markdown)

    @browse.command("log")
    @click.argument("url")
    @click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
    @click.option(
        "--sort", "sort_by", type=click.Choice(["default", "time"]), default="default", help="Sort log entries by time"
    )
    def browse_log(url: str, output_json: bool, sort_by: str) -> None:
        """View the edit history (log) of a Zhihu question."""
        _, question_id = _parse_item_url(url)
        if not question_id:
            raise click.BadParameter(f"Cannot parse question ID from URL: {url}")

        entries = fetch_question_log(question_id)
        if sort_by != "default":
            _SORT_MAP = {"time": "time"}
            entries = _sort_items(entries, _SORT_MAP[sort_by])

        if output_json:
            print_json(entries)
            return

        if not entries:
            info("No edit history found.")
            return

        for entry in entries:
            user = entry["user"] or "unknown"
            action = entry["action"]
            time_str = entry["time"]
            detail = entry["detail"]

            echo(f"  {f_meta(f'[{time_str}]')} {f_name(user)} {action}")
            if detail:
                echo(f"    {f_dim(detail[:200])}")
            blank()

    @browse.command("comments")
    @click.argument("url")
    @click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
    @click.option(
        "--sort",
        "sort_by",
        type=click.Choice(["default", "time", "likes", "dislikes"]),
        default="default",
        help="Sort comments by criteria",
    )
    def browse_comments(url: str, output_json: bool, sort_by: str) -> None:
        """Print the comment tree for any Zhihu item."""
        item_type, item_id = _parse_item_url(url)
        if item_type == "answers":
            item_id = _resolve_answer_id(item_id)

        from zhihu_cli.content.handlers.comments import fetch_root_comments

        comments = list(fetch_root_comments(item_type, item_id))
        if sort_by != "default":
            _SORT_MAP = {"time": "created_time", "likes": "like_count", "dislikes": "dislike_count"}
            comments = _sort_items(comments, _SORT_MAP[sort_by])

        if output_json:
            print_json(comments)
            return

        print_comments(comments=comments)

    @browse.command("annotation")
    @click.argument("url")
    @click.option("--limit", type=int, default=20, help="Comments per page (default: 20)")
    @click.option("--max", "-n", "max_items", type=int, default=None, help="Max total comments to fetch")
    @click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
    @click.option(
        "--sort",
        "sort_by",
        type=click.Choice(["default", "time", "likes"]),
        default="default",
        help="Sort segment comments by criteria",
    )
    def browse_annotation(url: str, limit: int, max_items: int | None, output_json: bool, sort_by: str) -> None:
        """Show segment comments (句子评论) for an answer.

        Segment comments are anchored to specific text spans within the
        answer, shown together with the highlighted passage they annotate.

        URL must point to a Zhihu answer.
        """
        set_json_mode(output_json)

        _, item_id = _parse_item_url(url)
        answer_id = _resolve_answer_id(item_id)

        info(f"Fetching segment comments for answer {answer_id} ...")
        comments = list(fetch_segment_comments(answer_id))

        if max_items is not None and len(comments) > max_items:
            comments = comments[:max_items]

        if sort_by != "default":
            _SORT_MAP = {"time": "created_time", "likes": "like_count"}
            comments = _sort_items(comments, _SORT_MAP[sort_by])

        if output_json:
            print_json(comments)
            return

        if not comments:
            info("No segment comments found.")
            return

        echo(f"  {f_bold(f'Segment Comments ({len(comments)})')}")
        blank()

        for i, cmt in enumerate(comments, 1):
            author = cmt["author"]["name"]
            seg_text = cmt.get("segment_text", "")
            seg_removed = cmt.get("segment_is_removed", False)
            seg_reaction = cmt.get("segment_reaction", {})
            seg_likes = seg_reaction.get("like_count", 0)

            # ── Segment highlight ──────────────────────────────────────
            if seg_text:
                seg_marker = f" {f_tag('[已删除]')}" if seg_removed else ""
                echo(f"  {item_index(i)} {f_cyan('「')}{f_dim(seg_text)}{f_cyan('」')}{seg_marker}")
                if seg_likes:
                    echo(f"    {f_meta(f'segment likes: {seg_likes}')}")
            else:
                echo(f"  {item_index(i)} {f_dim('(segment text unavailable)')}")

            # ── Comment line ───────────────────────────────────────────
            echo(
                f"    {f_name(author)} "
                f"{f_meta('| ' + fmt_time(cmt.get('created_time')))} "
                f"{f_meta('|')} {f_green('+' + str(cmt.get('like_count', 0)))}"
            )
            echo(f"    {cmt.get('content', '')}")

            # ── Reply count hint ───────────────────────────────────────
            child_count = cmt.get("child_comment_count", 0)
            if child_count:
                echo(f"    {f_dim(f'↳ {child_count} replies')}")

            # ── Comment tags (IP location etc.) ────────────────────────
            tags = cmt.get("comment_tag", [])
            ip_tag = next((t for t in tags if t.get("type") == "ip_info"), None)
            if ip_tag:
                echo(f"    {f_dim(ip_tag.get('text', ''))}")

            blank()

        echo(f"  {f_dim(f'── {len(comments)} segment comments')}")

    @browse.command("feed")
    @click.option("--type", "-t", "feed_type", type=click.Choice(["recommend", "follow"]), default="recommend")
    @click.option("--limit", type=int, default=20, help="Items per page")
    @click.option("--max", "-n", "max_items", type=int, default=20, help="Max total items (default: 20)")
    @click.option("--markdown/--no-markdown", default=False, help="Convert HTML to Markdown")
    @click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
    @click.option("--output", "-o", type=str, default="", help="Save to JSON file")
    @click.option(
        "--sort",
        "sort_by",
        type=click.Choice(["default", "time", "upvotes", "comments"]),
        default="default",
        help="Sort feed items by criteria",
    )
    def browse_feed(
        feed_type: str, limit: int, max_items: int | None, markdown: bool, output_json: bool, output: str, sort_by: str
    ) -> None:
        """Stream Zhihu recommend or follow feed."""
        set_json_mode(output_json)
        fetch_fn = fetch_feed_with_markdown if markdown else fetch_feed
        items = fetch_fn(feed_type, limit, max_items)
        if sort_by != "default":
            _SORT_MAP = {"time": "created_time", "upvotes": "voteup_count", "comments": "comment_count"}
            items = _sort_items(items, _SORT_MAP[sort_by])

        if output_json:
            print_json(items)
            if output:
                with open(output, "w", encoding="utf-8") as f:
                    json.dump(items, f, ensure_ascii=False, indent=2)
                success(f"Saved {len(items)} items to {output}")
            return

        for item in items:
            ttype = item.get("target_type", "?")
            title = item.get("title", "") or item.get("excerpt", "") or "(no title)"
            author = item.get("author", {}).get("name", "unknown")
            url = item.get("url", "")
            excerpt = item.get("excerpt", "")

            echo(f"  {f_tag(ttype)} {f_bold(title[:120])}")
            if excerpt:
                echo(f"    {f_dim(f'preview: {excerpt[:200]}')}")
            echo(f"    {f_label('author=')}{f_name(author)}  {f_label('votes=')}{f_num(item.get('voteup_count', 0))}")
            if url:
                echo(f"    {f_label('link:')} {f_url(url)}")
            blank()

        if output:
            with open(output, "w", encoding="utf-8") as f:
                json.dump(items, f, ensure_ascii=False, indent=2)
            success(f"Saved {len(items)} items to {output}")

    @browse.command("hot")
    @click.option("--limit", "-n", type=int, default=30, help="Number of hot items to show (default: 30)")
    @click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
    @click.option("--output", "-o", type=str, default="", help="Save to JSON file")
    @click.option(
        "--sort",
        "sort_by",
        type=click.Choice(["default", "heat", "comments", "answers"]),
        default="default",
        help="Sort hot list by criteria",
    )
    def browse_hot(limit: int, output_json: bool, output: str, sort_by: str) -> None:
        """View the Zhihu real-time hot list."""
        set_json_mode(output_json)
        items = fetch_hot_list(limit=50 if sort_by != "default" else limit)
        if sort_by != "default":
            _SORT_MAP = {"heat": "heat", "comments": "comment_count", "answers": "answer_count"}
            items = _sort_items(items, _SORT_MAP[sort_by])

        if limit and len(items) > limit:
            items = items[:limit]

        if output_json:
            print_json(items)
            if output:
                with open(output, "w", encoding="utf-8") as f:
                    json.dump(items, f, ensure_ascii=False, indent=2)
                success(f"Saved {len(items)} items to {output}")
            return

        for i, item in enumerate(items, 1):
            title = item["title"] or "(no title)"
            heat = item["heat"]
            ttype = item["target_type"]
            url = item["url"]
            card_label = item["card_label"]
            answer_count = item["answer_count"]
            follower_count = item["follower_count"]

            label_str = f" {f_tag(card_label)}" if card_label else ""
            echo(f"  {item_index(i)} {f_num(heat)}{label_str}  {f_tag(ttype)}")
            echo(f"    {f_bold(title)}")
            excerpt = item["excerpt"]
            if excerpt:
                echo(f"    {f_dim(f'preview: {excerpt[:200]}')}")
            author = item["author"]
            if author and author != "anonymous":
                echo(f"    {f_label('author:')} {f_name(author)}")
            if answer_count or follower_count:
                parts = []
                if answer_count:
                    parts.append(f"{f_num(answer_count)} {f_dim('answers')}")
                if follower_count:
                    parts.append(f"{f_num(follower_count)} {f_dim('followers')}")
                echo(f"    {'  '.join(parts)}")
            if url:
                echo(f"    {f_url(url)}")
            blank()

        if output:
            with open(output, "w", encoding="utf-8") as f:
                json.dump(items, f, ensure_ascii=False, indent=2)
            success(f"Saved {len(items)} items to {output}")

        if not items:
            info("No hot items found. Try logging in first: zhihu auth login")

    @browse.command("notifications")
    @click.option("--limit", type=int, default=20, help="Items per page")
    @click.option("--max", "-n", "max_items", type=int, default=20, help="Max total items (default: 20)")
    @click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
    @click.option("--output", "-o", type=str, default="", help="Save to JSON file")
    @click.option(
        "--sort",
        "sort_by",
        type=click.Choice(["default", "time"]),
        default="default",
        help="Sort notifications by time",
    )
    def browse_notifications(limit: int, max_items: int | None, output_json: bool, output: str, sort_by: str) -> None:
        """View your Zhihu notifications."""
        set_json_mode(output_json)
        from zhihu_cli.content.handlers.notifications import fetch_notifications

        items = fetch_notifications(limit=limit, max_items=max_items)
        if sort_by != "default":
            _SORT_MAP = {"time": "timestamp"}
            items = _sort_items(items, _SORT_MAP[sort_by])

        if output_json:
            print_json(items)
            if output:
                with open(output, "w", encoding="utf-8") as f:
                    json.dump(items, f, ensure_ascii=False, indent=2)
                success(f"Saved {len(items)} items to {output}")
            return

        for i, item in enumerate(items, 1):
            marker = " " if item["is_read"] else "*"
            verb = item["verb"]
            actor = item["actor_name"]
            target_text = item["target_text"]
            target_link = item["target_link"]
            rtype = item["resource_type"]
            time_str = item["time"]
            merge = item["merge_count"]

            merge_str = f" (+{f_num(merge - 1)})" if merge > 1 else ""

            echo(f"  {item_index(i)}{marker} {f_name(actor)} {verb}{merge_str}  ({f_tag(rtype)}: {target_text})")
            comment = item["comment_text"]
            if comment:
                echo(f"    {f_dim(f'> {comment}')}")
            if target_link:
                echo(f"    {f_url(target_link)}")
            echo(f"    {f_meta(time_str)}")
            blank()

        if output:
            with open(output, "w", encoding="utf-8") as f:
                json.dump(items, f, ensure_ascii=False, indent=2)
            success(f"Saved {len(items)} items to {output}")

        if not items:
            info("No notifications found. Try logging in first: zhihu auth login")

    @browse.command("history")
    @click.option("--limit", type=int, default=20, help="Items per page")
    @click.option("--max", "-n", "max_items", type=int, default=20, help="Max total items (default: 20)")
    @click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
    @click.option("--output", "-o", type=str, default="", help="Save to JSON file")
    @click.option(
        "--sort", "sort_by", type=click.Choice(["default", "time"]), default="default", help="Sort history by time"
    )
    def browse_history(limit: int, max_items: int | None, output_json: bool, output: str, sort_by: str) -> None:
        """View your Zhihu read history."""
        set_json_mode(output_json)
        from zhihu_cli.content.handlers.read_history import fetch_read_history

        items = fetch_read_history(limit=limit, max_items=max_items)
        if sort_by != "default":
            _SORT_MAP = {"time": "read_time"}
            items = _sort_items(items, _SORT_MAP[sort_by])

        if output_json:
            print_json(items)
            if output:
                with open(output, "w", encoding="utf-8") as f:
                    json.dump(items, f, ensure_ascii=False, indent=2)
                success(f"Saved {len(items)} items to {output}")
            return

        for i, item in enumerate(items, 1):
            ctype = item["content_type"]
            title = item["title"] or "(no title)"
            author = item["author_name"]
            summary_text = item["summary"]
            stats = item["stats_text"]
            url = item["url"]
            read_time = item["read_time"]

            echo(f"  {item_index(i)} {f_tag(ctype)} {f_bold(title[:120])}")
            if author:
                echo(f"    {f_label('author:')} {f_name(author)}")
            if summary_text:
                echo(f"    {f_dim(summary_text[:200])}")
            if stats:
                echo(f"    {f_meta(stats)}")
            if url:
                echo(f"    {f_url(url)}")
            echo(f"    {f_label('read:')} {f_meta(read_time)}")
            blank()

        if output:
            with open(output, "w", encoding="utf-8") as f:
                json.dump(items, f, ensure_ascii=False, indent=2)
            success(f"Saved {len(items)} items to {output}")

        if not items:
            info("No read history found. Try logging in first: zhihu auth login")

    @browse.command("yanxuan")
    @click.argument("url_or_id")
    @click.option("--offset", type=int, default=0, help="Starting segment offset (default: 0)")
    @click.option("--max-segments", "-n", type=int, default=None, help="Max segments to fetch")
    @click.option("--max-pages", type=int, default=None, help="Max API pages to fetch")
    @click.option("--reading-mode/--no-reading-mode", default=True, help="Use Rich pager for reading")
    @click.option("--json", "output_json", is_flag=True, default=False, help="Output segments as JSON")
    @click.option("--output", "-o", type=str, default="", help="Save to file")
    def browse_yanxuan(
        url_or_id: str,
        offset: int,
        max_segments: int | None,
        max_pages: int | None,
        reading_mode: bool,
        output_json: bool,
        output: str,
    ) -> None:
        """Read Zhihu Yanxuan (盐选) premium content in the terminal.

        URL_OR_ID can be a full answer URL, a composite question_id/answer_id,
        or a raw answer ID (url_token).
        """
        set_json_mode(output_json)
        url_token = extract_url_token(url_or_id)

        meta, segments = fetch_yanxuan_segments(
            url_token,
            offset=offset,
            max_segments=max_segments,
            max_pages=max_pages,
        )

        if output_json:
            print_json({"meta": meta, "segments": segments})
            if output:
                with open(output, "w", encoding="utf-8") as f:
                    json.dump({"meta": meta, "segments": segments}, f, ensure_ascii=False, indent=2)
                success(f"Saved {len(segments)} segments to {output}")
            return

        if not segments:
            info("No content found for this yanxuan item.")
            return

        # Build header from meta
        title = meta.get("title", "") or meta.get("story_name", "")
        brand = meta.get("brand", "")
        copyright_ = meta.get("copyright", "")

        header_parts = []
        if title:
            header_parts.append(f"# {title}")
        if brand:
            header_parts.append(f"**{brand}**")
        if copyright_:
            header_parts.append(f"*{copyright_}*")

        header = "\n\n".join(header_parts) if header_parts else ""
        body = segments_to_text(segments)
        full_text = f"{header}\n\n{body}" if header else body

        if reading_mode:
            try:
                from rich.console import Console
                from rich.markdown import Markdown
            except ImportError:
                reading_mode = False

        if reading_mode:
            console = Console()
            with console.pager(styles=True, links=True):
                console.print(Markdown(full_text))
        else:
            echo(full_text)

        if output:
            with open(output, "w", encoding="utf-8") as f:
                f.write(full_text)
            success(f"Saved {len(segments)} segments to {output}")

    @browse.command("upvoters")
    @click.argument("url")
    @click.option("--limit", "-n", type=int, default=20, help="Items per page (default: 20, max: 20)")
    @click.option("--max", "-m", "max_items", type=int, default=None, help="Max total items (default: fetch all)")
    @click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
    @click.option(
        "--sort",
        "sort_by",
        type=click.Choice(["default", "followers", "upvotes"]),
        default="default",
        help="Sort upvoters by criteria",
    )
    def browse_upvoters(url: str, limit: int, max_items: int | None, output_json: bool, sort_by: str) -> None:
        """List users who upvoted an answer or article.

        \b
        URL can be an answer or article URL.
        Examples:
          zhihu browse upvoters https://www.zhihu.com/question/123/answer/456
          zhihu browse upvoters https://zhuanlan.zhihu.com/p/123456
        """
        set_json_mode(output_json)
        item_type, item_id = _parse_item_url(url)

        if item_type not in ("answers", "articles"):
            error(f"Upvoters are only available for answers and articles. Got: {item_type}")
            raise SystemExit(1)

        if item_type == "answers":
            item_id = _resolve_answer_id(item_id)

        info(f"Fetching upvoters for {item_type} {item_id}...")
        items = fetch_upvoters(item_type, item_id, limit=limit, max_items=max_items)
        if sort_by != "default":
            _SORT_MAP = {"followers": "follower_count", "upvotes": "member_upvote_cnt"}
            items = _sort_items(items, _SORT_MAP[sort_by])

        if output_json:
            print_json(items)
            return

        if not items:
            info("No upvoters found.")
            return

        for i, u in enumerate(items, 1):
            name = u["name"] or u["url_token"]
            endorse = u.get("relationship_endorse", "")
            influence = u.get("zhihu_influence", "")
            headline = u.get("headline", "")

            echo(f"  {f_bold(f'{i}.')} {f_name(name)}")
            if headline:
                echo(f"     {f_dim(headline[:100])}")
            if influence:
                echo(f"     {f_meta(influence)}")
            if endorse:
                echo(f"     {f_dim(endorse)}")
            echo(f"     {f_url(u['url'])}")
            echo(
                f"     {f_label('followers:')} {f_num(u['follower_count'])}  "
                f"{f_label('upvotes given:')} {f_num(u['member_upvote_cnt'])}"
            )
            if i < len(items):
                blank()

        echo(f"  {f_dim(f'── {len(items)} upvoters')}")

    # ── browse following ────────────────────────────────────────────────────

    @browse.group("following")
    def browse_following() -> None:
        """View your followed users, topics, questions, columns, and collections."""

    def _following_command(
        fetch_fn,
        url_token: str | None,
        limit: int,
        max_items: int | None,
        output_json: bool,
        output: str,
        label: str,
        sort_by: str = "default",
        sort_map: dict | None = None,
    ) -> None:
        """Shared execution path for following sub-commands."""
        set_json_mode(output_json)
        token = _resolve_url_token(url_token)
        info(f"Fetching {label} for {token}...")
        items = fetch_fn(token, limit=limit, max_items=max_items)
        if sort_map and sort_by != "default":
            items = _sort_items(items, sort_map[sort_by])

        if output_json:
            print_json(items)
            _save_json_output(items, output, label)
            return

        if not items:
            info(f"No {label} found.")
            return

        _display_following_items(items)

        _save_json_output(items, output, label)

    @browse_following.command("users")
    @click.option("--url-token", "-u", type=str, default=None, help="Your Zhihu url_token (auto-detected if omitted)")
    @click.option("--limit", type=int, default=20, help="Items per page")
    @click.option("--max", "-n", "max_items", type=int, default=None, help="Max total items")
    @click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
    @click.option("--output", "-o", type=str, default="", help="Save to JSON file")
    @click.option(
        "--sort",
        "sort_by",
        type=click.Choice(["default", "followers", "answers", "articles"]),
        default="default",
        help="Sort users by criteria",
    )
    def following_users(
        url_token: str | None, limit: int, max_items: int | None, output_json: bool, output: str, sort_by: str
    ) -> None:
        """List users you follow."""
        _FOLLOWING_SORT = {"followers": "follower_count", "answers": "answer_count", "articles": "articles_count"}
        _following_command(
            fetch_followees,
            url_token,
            limit,
            max_items,
            output_json,
            output,
            "followed users",
            sort_by,
            _FOLLOWING_SORT,
        )

    @browse_following.command("followers")
    @click.option("--url-token", "-u", type=str, default=None, help="Your Zhihu url_token (auto-detected if omitted)")
    @click.option("--limit", type=int, default=20, help="Items per page")
    @click.option("--max", "-n", "max_items", type=int, default=None, help="Max total items")
    @click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
    @click.option("--output", "-o", type=str, default="", help="Save to JSON file")
    @click.option(
        "--sort",
        "sort_by",
        type=click.Choice(["default", "followers", "answers", "articles"]),
        default="default",
        help="Sort followers by criteria",
    )
    def following_followers(
        url_token: str | None, limit: int, max_items: int | None, output_json: bool, output: str, sort_by: str
    ) -> None:
        """List your followers (people who follow you)."""
        _FOLLOWING_SORT = {"followers": "follower_count", "answers": "answer_count", "articles": "articles_count"}
        _following_command(
            fetch_followers, url_token, limit, max_items, output_json, output, "followers", sort_by, _FOLLOWING_SORT
        )

    @browse_following.command("topics")
    @click.option("--url-token", "-u", type=str, default=None, help="Your Zhihu url_token (auto-detected if omitted)")
    @click.option("--limit", type=int, default=20, help="Items per page")
    @click.option("--max", "-n", "max_items", type=int, default=None, help="Max total items")
    @click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
    @click.option("--output", "-o", type=str, default="", help="Save to JSON file")
    @click.option(
        "--sort",
        "sort_by",
        type=click.Choice(["default", "followers", "questions"]),
        default="default",
        help="Sort topics by criteria",
    )
    def following_topics(
        url_token: str | None, limit: int, max_items: int | None, output_json: bool, output: str, sort_by: str
    ) -> None:
        """List topics you follow."""
        _FOLLOWING_SORT = {"followers": "followers_count", "questions": "questions_count"}
        _following_command(
            fetch_following_topics,
            url_token,
            limit,
            max_items,
            output_json,
            output,
            "followed topics",
            sort_by,
            _FOLLOWING_SORT,
        )

    @browse_following.command("questions")
    @click.option("--url-token", "-u", type=str, default=None, help="Your Zhihu url_token (auto-detected if omitted)")
    @click.option("--limit", type=int, default=20, help="Items per page")
    @click.option("--max", "-n", "max_items", type=int, default=None, help="Max total items")
    @click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
    @click.option("--output", "-o", type=str, default="", help="Save to JSON file")
    @click.option(
        "--sort",
        "sort_by",
        type=click.Choice(["default", "time", "answers", "followers", "comments"]),
        default="default",
        help="Sort questions by criteria",
    )
    def following_questions(
        url_token: str | None, limit: int, max_items: int | None, output_json: bool, output: str, sort_by: str
    ) -> None:
        """List questions you follow."""
        _FOLLOWING_SORT = {
            "time": "created_time",
            "answers": "answer_count",
            "followers": "follower_count",
            "comments": "comment_count",
        }
        _following_command(
            fetch_following_questions,
            url_token,
            limit,
            max_items,
            output_json,
            output,
            "followed questions",
            sort_by,
            _FOLLOWING_SORT,
        )

    @browse_following.command("columns")
    @click.option("--url-token", "-u", type=str, default=None, help="Your Zhihu url_token (auto-detected if omitted)")
    @click.option("--limit", type=int, default=20, help="Items per page")
    @click.option("--max", "-n", "max_items", type=int, default=None, help="Max total items")
    @click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
    @click.option("--output", "-o", type=str, default="", help="Save to JSON file")
    @click.option(
        "--sort",
        "sort_by",
        type=click.Choice(["default", "followers", "articles"]),
        default="default",
        help="Sort columns by criteria",
    )
    def following_columns(
        url_token: str | None, limit: int, max_items: int | None, output_json: bool, output: str, sort_by: str
    ) -> None:
        """List columns (zhuanlan) you follow."""
        _FOLLOWING_SORT = {"followers": "followers_count", "articles": "articles_count"}
        _following_command(
            fetch_following_columns,
            url_token,
            limit,
            max_items,
            output_json,
            output,
            "followed columns",
            sort_by,
            _FOLLOWING_SORT,
        )

    @browse_following.command("collections")
    @click.option("--url-token", "-u", type=str, default=None, help="Your Zhihu url_token (auto-detected if omitted)")
    @click.option("--limit", type=int, default=20, help="Items per page")
    @click.option("--max", "-n", "max_items", type=int, default=None, help="Max total items")
    @click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
    @click.option("--output", "-o", type=str, default="", help="Save to JSON file")
    @click.option(
        "--sort",
        "sort_by",
        type=click.Choice(["default", "time", "items", "followers", "comments"]),
        default="default",
        help="Sort collections by criteria",
    )
    def following_collections(
        url_token: str | None, limit: int, max_items: int | None, output_json: bool, output: str, sort_by: str
    ) -> None:
        """List collections (favorites) you follow."""
        _FOLLOWING_SORT = {
            "time": "created_time",
            "items": "answer_count",
            "followers": "follower_count",
            "comments": "comment_count",
        }
        _following_command(
            fetch_following_collections,
            url_token,
            limit,
            max_items,
            output_json,
            output,
            "followed collections",
            sort_by,
            _FOLLOWING_SORT,
        )
