"""Draft command group for zhihu-cli."""

import click

from zhihu_cli.commands._helpers import _read_content
from zhihu_cli.content.handlers.draft import draft_to_markdown, list_drafts, upload_draft
from zhihu_cli.content.utils.markdown2html import markdown2html
from zhihu_cli.output import (
    blank,
    echo,
    error,
    f_bold,
    f_dim,
    f_num,
    f_path,
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

    @draft.command("list")
    @click.argument("url")
    @click.option("--limit", "-l", default=10, type=int, help="Max drafts to list (default: 10)")
    @click.option("--json", "-j", "as_json", is_flag=True, default=False, help="Output as JSON")
    def draft_list(url: str, limit: int, as_json: bool) -> None:
        """List draft history for a Zhihu URL.

        Shows all saved drafts for the given question/answer/article,
        newest first.  Use the index shown to view a specific draft with
        ``zhihu draft view <url> -n <index>``.
        """
        from zhihu_cli.content.handlers import get_type_and_id

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
            from zhihu_cli.content.handlers import fmt_time

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
