"""Publish command group."""

import click

from zhihu_cli.commands._helpers import _read_content
from zhihu_cli.content.handlers.publishing import (
    modify_answer,
    modify_article,
    publish_answer,
    publish_article,
)
from zhihu_cli.content.handlers.upload_image import to_visible_url, upload_image
from zhihu_cli.output import echo, error, f_green, print_json


def register_publish(main_group: click.Group) -> None:
    """Register the publish command group onto *main_group*."""

    @main_group.group()
    def publish() -> None:
        """Publish or modify answers and articles."""

    @publish.command("answer")
    @click.argument("question_id")
    @click.option("--file", "-f", "file", default=None, help="Markdown file (use '-' for stdin)")
    def publish_answer_cmd(question_id: str, file: str | None) -> None:
        """Publish a new answer to a question. Reads Markdown from file or stdin."""
        content = _read_content(file)
        if not content.strip():
            error("Empty content.")
            raise SystemExit(1)
        resp = publish_answer(question_id, content)
        print_json(resp)

    @publish.command("modify-answer")
    @click.argument("answer_id")
    @click.option("--file", "-f", "file", default=None, help="Markdown file (use '-' for stdin)")
    def publish_modify_answer(answer_id: str, file: str | None) -> None:
        """Modify an existing answer."""
        content = _read_content(file)
        if not content.strip():
            error("Empty content.")
            raise SystemExit(1)
        resp = modify_answer(answer_id, content)
        print_json(resp)

    @publish.command("article")
    @click.argument("title")
    @click.option("--file", "-f", "file", default=None, help="Markdown file (use '-' for stdin)")
    def publish_article_cmd(title: str, file: str | None) -> None:
        """Publish a new article. Reads Markdown from file or stdin."""
        content = _read_content(file)
        if not content.strip():
            error("Empty content.")
            raise SystemExit(1)
        resp = publish_article(title, content)
        print_json(resp)

    @publish.command("modify-article")
    @click.argument("article_id")
    @click.argument("title")
    @click.option("--file", "-f", "file", default=None, help="Markdown file (use '-' for stdin)")
    def publish_modify_article(article_id: str, title: str, file: str | None) -> None:
        """Modify an existing article."""
        content = _read_content(file)
        if not content.strip():
            error("Empty content.")
            raise SystemExit(1)
        resp = modify_article(article_id, title, content)
        print_json(resp)

    @publish.command("upload-image")
    @click.argument("file_path")
    @click.option(
        "--source",
        "-s",
        default="article",
        help="Upload context: article (default), pin, answer, question",
    )
    def publish_upload_image(file_path: str, source: str) -> None:
        """Upload an image to Zhihu. Outputs the uploaded image URL."""
        try:
            img_info = upload_image(file_path, source=source)
            echo(img_info["src"])
            visible = to_visible_url(img_info.get("original_src", img_info["src"]))
            echo(f_green(f"Visible URL: {visible}"))
        except FileNotFoundError as e:
            error(f"{e}")
            raise SystemExit(1)
        except RuntimeError as e:
            error(f"{e}")
            raise SystemExit(1)
