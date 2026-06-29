"""Draft command group for zhihu-cli."""

import click

from zhihu_cli.commands._helpers import _read_content
from zhihu_cli.content.handlers import fmt_time, get_type_and_id
from zhihu_cli.content.handlers.draft import (
    draft_to_markdown,
    list_answer_drafts,
    list_article_drafts,
    list_drafts,
    list_pin_drafts,
    upload_draft,
)
from zhihu_cli.content.utils.markdown2html import markdown2html
from zhihu_cli.output import (
    blank,
    echo,
    error,
    f_bold,
    f_dim,
    f_meta,
    f_num,
    f_path,
    f_url,
    heading,
    item_index,
    print_json,
    success,
)


def register_draft(main_group):
    """Register the draft command group onto *main_group*."""

    @main_group.group()
    def draft() -> None:
        """View and manage Zhihu drafts."""

    @draft.command("view")
    @click.argument("url")
    @click.option("--number", "-n", default=0, type=int, help="Draft index: 0 = latest (default), 1 = previous, …")
    @click.option("--output", "-o", default=None, help="Save Markdown to file instead of printing")
    def draft_view(url: str, number: int, output: str | None) -> None:
        """View a draft of a Zhihu question/answer/article as Markdown.

        Provide a Zhihu URL (e.g. https://www.zhihu.com/question/123456)
        to fetch and convert your unpublished draft to Markdown.

        Use --number/-n to step back through draft history:
        0 = latest (default), 1 = previous, 2 = the one before that, …
        """
        try:
            metadata, markdown = draft_to_markdown(url, draft_index=number)
        except ValueError as e:
            error(f"{e}")
            raise SystemExit(1)

        if output:
            with open(output, "w", encoding="utf-8") as f:
                f.write(markdown)
            success(f"Draft saved to {f_path(output)}")
        else:
            echo(markdown)

    @draft.command("history")
    @click.argument("url")
    @click.option("--limit", "-l", default=10, type=int, help="Max drafts to list (default: 10)")
    @click.option("--json", "-j", "as_json", is_flag=True, default=False, help="Output as JSON")
    def draft_history(url: str, limit: int, as_json: bool) -> None:
        """List draft history for a Zhihu URL.

        Shows all saved drafts for the given question/answer/article,
        newest first.  Use the index shown to view a specific draft with
        ``zhihu draft view <url> -n <index>``.
        """
        object_type, object_id = get_type_and_id(url)
        if not object_type or not object_id:
            error(f"Cannot parse Zhihu URL: {url}")
            raise SystemExit(1)

        type_map = {"questions": "question", "answers": "answer", "articles": "article"}
        api_type = type_map.get(object_type)
        if not api_type:
            error(f"Drafts not supported for type: {object_type}")
            raise SystemExit(1)

        if object_type == "answers" and "/" in object_id:
            object_id = object_id.split("/")[1]

        drafts = list_drafts(api_type, object_id, limit=limit)
        if not drafts:
            echo(f_dim("No drafts found."))
            return

        if as_json:
            print_json(drafts)
            return

        for i, d in enumerate(drafts):
            echo(
                f"  {item_index(i)}  {f_bold(d.get('id', '?'))}"
                f"  {f_dim(fmt_time(d.get('updated_at', 0)))}"
                f"  {f_num(str(d.get('text_length', 0)))} chars"
            )
            excerpt = d.get("excerpt", "")
            if excerpt:
                echo(f"      {f_dim(excerpt[:120])}")
            blank()

        echo(f"  {f_dim('Use --number/-n <index> with `zhihu draft view` to view a specific draft.')}")

    @draft.group("list")
    def draft_list() -> None:
        """List your Zhihu drafts by type."""

    @draft_list.command("answer")
    @click.option("--limit", "-l", default=20, type=int, help="Max drafts to show (default: 20)")
    @click.option("--json", "-j", "as_json", is_flag=True, default=False, help="Output as JSON")
    def draft_list_answer(limit: int, as_json: bool) -> None:
        """List all answer drafts."""
        drafts = list_answer_drafts(limit=limit)
        if not drafts:
            echo(f_dim("No answer drafts found."))
            return

        if as_json:
            print_json(_format_list_json(drafts, "answer"))
            return

        heading("Answer Drafts")
        for i, d in enumerate(drafts):
            title = d.get("question", {}).get("title", "") or "(no title)"
            qid = d.get("question", {}).get("id", "?")
            ctime = fmt_time(d.get("created_time", 0))
            utime = fmt_time(d.get("updated_time", 0))
            words = d.get("content_words", 0)
            excerpt = d.get("excerpt", "")

            echo(f"  {item_index(i)}  {f_bold(title[:100])}")
            echo(f"      {f_dim('question:')} {f_url(str(qid))}  {f_dim('words:')} {f_num(words)}")
            echo(f"      {f_dim('created:')} {f_meta(ctime)}  {f_dim('updated:')} {f_meta(utime)}")
            if excerpt:
                echo(f"      {f_dim(excerpt[:120])}")
            blank()
        echo(f"  {f_dim(f'── {len(drafts)} answer drafts')}")

    @draft_list.command("article")
    @click.option("--limit", "-l", default=20, type=int, help="Max drafts to show (default: 20)")
    @click.option("--json", "-j", "as_json", is_flag=True, default=False, help="Output as JSON")
    def draft_list_article(limit: int, as_json: bool) -> None:
        """List all article drafts."""
        drafts = list_article_drafts(limit=limit)
        if not drafts:
            echo(f_dim("No article drafts found."))
            return

        if as_json:
            print_json(_format_list_json(drafts, "article"))
            return

        heading("Article Drafts")
        for i, d in enumerate(drafts):
            title = d.get("title", "") or "(no title)"
            art_id = d.get("id", "?")
            ctime = fmt_time(d.get("created", 0))
            utime = fmt_time(d.get("updated", 0))
            words = d.get("content_words", 0)
            summary = d.get("summary", "")

            echo(f"  {item_index(i)}  {f_bold(title[:100])}")
            echo(f"      {f_dim('id:')} {f_url(str(art_id))}  {f_dim('words:')} {f_num(words)}")
            echo(f"      {f_dim('created:')} {f_meta(ctime)}  {f_dim('updated:')} {f_meta(utime)}")
            if summary:
                echo(f"      {f_dim(summary[:120])}")
            blank()
        echo(f"  {f_dim(f'── {len(drafts)} article drafts')}")

    @draft_list.command("pin")
    @click.option("--limit", "-l", default=20, type=int, help="Max drafts to show (default: 20)")
    @click.option("--json", "-j", "as_json", is_flag=True, default=False, help="Output as JSON")
    def draft_list_pin(limit: int, as_json: bool) -> None:
        """List all pin drafts."""
        drafts = list_pin_drafts(limit=limit)
        if not drafts:
            echo(f_dim("No pin drafts found."))
            return

        if as_json:
            print_json(_format_list_json(drafts, "pin"))
            return

        heading("Pin Drafts")
        for i, d in enumerate(drafts):
            content_id = d.get("content_id", "?")
            utime = fmt_time(d.get("updated_at", 0))

            # Extract plain text from result.content
            result = d.get("result", {})
            content_list = result.get("content", [])
            text = ""
            if content_list:
                text = content_list[0].get("own_text", "") or content_list[0].get("content", "")

            echo(f"  {item_index(i)}  {f_bold(truncate_html(text, 100))}")
            echo(f"      {f_dim('id:')} {f_url(str(content_id))}  {f_dim('updated:')} {f_meta(utime)}")
            blank()
        echo(f"  {f_dim(f'── {len(drafts)} pin drafts')}")

    def _format_list_json(drafts: list[dict], draft_type: str) -> list[dict]:
        """Normalize draft list data for JSON output."""
        if draft_type == "answer":
            return [
                {
                    "id": d.get("question", {}).get("id"),
                    "title": d.get("question", {}).get("title", ""),
                    "content_words": d.get("content_words", 0),
                    "excerpt": d.get("excerpt", ""),
                    "created_time": d.get("created_time"),
                    "updated_time": d.get("updated_time"),
                    "raw": d,
                }
                for d in drafts
            ]
        elif draft_type == "article":
            return [
                {
                    "id": d.get("id"),
                    "title": d.get("title", ""),
                    "content_words": d.get("content_words", 0),
                    "summary": d.get("summary", ""),
                    "url_token": d.get("url_token", ""),
                    "created": d.get("created"),
                    "updated": d.get("updated"),
                    "raw": d,
                }
                for d in drafts
            ]
        else:
            return [
                {
                    "content_id": d.get("content_id"),
                    "updated_at": d.get("updated_at"),
                    "raw": d,
                }
                for d in drafts
            ]

    def truncate_html(html: str, max_len: int) -> str:
        """Strip HTML tags and truncate to *max_len* chars."""
        import re

        text = re.sub(r"<[^>]+>", "", html).strip()
        if len(text) > max_len:
            return text[: max_len - 1] + "…"
        return text

    @draft.command("upload")
    @click.argument("type", type=click.Choice(["article", "answer"]))
    @click.argument("id")
    @click.option("--file", "-f", "file", default=None, help="Markdown file to upload (use '-' for stdin)")
    def draft_upload(type: str, id: str, file: str | None) -> None:
        """Upload a draft to Zhihu.

        TYPE is the content type: 'article' or 'answer'.

        ID is the article_id (for article drafts) or question_id (for answer drafts).

        Reads Markdown from --file/-f, or from stdin if not provided.
        The Markdown is converted to HTML before uploading.
        """
        content = _read_content(file)
        if not content.strip():
            error("Empty content.")
            raise SystemExit(1)

        try:
            html = markdown2html(content, scene=type)  # pyright: ignore[reportArgumentType]
        except Exception as e:
            error(f"Markdown to HTML conversion failed: {e}")
            raise SystemExit(1)

        try:
            resp = upload_draft(type, id, html)
            print_json(resp)
            success("Draft uploaded successfully.")
        except ValueError as e:
            error(f"{e}")
            raise SystemExit(1)
        except Exception as e:
            error(f"Draft upload failed: {e}")
            raise SystemExit(1)
