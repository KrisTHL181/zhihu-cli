"""Zhihu Daily (知乎日报) command group.

Provides access to the daily paper's issues and stories: the latest issue, the
issue published before a given date, and a single story rendered or saved as
Markdown.  Anonymous access works; setting ``ZHIHU_DAILY_TOKEN`` in the
environment adds a bearer token when one is available.
"""

from __future__ import annotations

from datetime import datetime

import click

from zhihu_cli.content.handlers import get_data_dir
from zhihu_cli.content.handlers.daily import fetch_before, fetch_latest, scrape_story
from zhihu_cli.output import (
    blank,
    echo,
    f_dim,
    f_title,
    f_url,
    file_saved,
    heading,
    info,
    item_index,
    print_json,
    section,
    set_json_mode,
)


def _format_issue_date(raw: object) -> str:
    """Format the envelope's ``YYYYMMDD`` date as ``YYYY-MM-DD``.

    :param raw: The issue date from the API envelope.
    :returns: A hyphenated date, the value unchanged when it does not parse, or
        ``"unknown"`` when absent.
    """
    text = str(raw or "").strip()
    try:
        return datetime.strptime(text, "%Y%m%d").date().isoformat()
    except ValueError:
        return text or "unknown"


def _render_stories(stories: list[dict]) -> None:
    """Print a list of stories as numbered, styled entries.

    :param stories: Story entries from an issue envelope.
    """
    total = len(stories)
    for i, story in enumerate(stories, 1):
        echo(f"  {item_index(i, total)} {f_title(story.get('title') or 'untitled')}")
        hint = story.get("hint")
        if hint:
            echo(f"    {f_dim(hint)}")
        line = f"    {f_dim('#' + str(story.get('id', '')))}"
        url = story.get("url")
        if url:
            line += f"  {f_url(url)}"
        echo(line)
        blank()


def _render_issue(envelope: dict, *, show_top: bool) -> None:
    """Print one issue, with top stories first when the payload has them.

    Top stories already present in the regular list are skipped, so no story ID
    appears twice.

    :param envelope: A ``stories/latest`` or ``stories/before`` envelope.
    :param show_top: Render the ``top_stories`` section.  The ``before``
        endpoint does not return top stories, so its command passes ``False``.
    """
    heading(f"知乎日报 · {_format_issue_date(envelope.get('date'))}")

    stories = envelope.get("stories") or []
    top_stories: list[dict] = []
    if show_top:
        story_ids = {story.get("id") for story in stories}
        top_stories = [story for story in (envelope.get("top_stories") or []) if story.get("id") not in story_ids]

    if not top_stories and not stories:
        info("No stories in this issue.")
        return
    if top_stories:
        section("头条")
        _render_stories(top_stories)
    if stories:
        section("今日" if show_top else "内容")
        _render_stories(stories)


def _print_reading(header: str, markdown: str, reading_mode: bool) -> None:
    """Print a story, paging through it when Rich is available.

    :param header: Markdown header block with the story's title and metadata.
    :param markdown: The story body as Markdown.
    :param reading_mode: Page the output through Rich.  Falls back to plain
        printing when Rich is not installed.
    """
    if reading_mode:
        try:
            from rich.console import Console
            from rich.markdown import Markdown
        except ImportError:
            reading_mode = False

    if reading_mode:
        console = Console()
        with console.pager(styles=True, links=True):
            console.print(Markdown(header))
            if markdown:
                console.print(Markdown(markdown))
    else:
        echo(header)
        blank()
        echo(markdown)


def register_daily(main_group: click.Group) -> click.Group:
    """Register the ``daily`` command group onto *main_group*.

    :param main_group: The root Click group.
    :returns: The same group, for chaining.
    """

    @main_group.group()
    def daily() -> None:
        """Read 知乎日报 — the latest issue, past issues, and single stories."""

    @daily.command("latest")
    @click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
    def daily_latest(output_json: bool) -> None:
        """Show the latest Zhihu Daily issue."""
        set_json_mode(output_json)
        try:
            envelope = fetch_latest()
        except ValueError as exc:
            raise click.ClickException(str(exc)) from None
        if output_json:
            print_json(envelope)
            return
        _render_issue(envelope, show_top=True)

    @daily.command("before")
    @click.argument("date")
    @click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
    def daily_before(date: str, output_json: bool) -> None:
        """Show the issue published before DATE (the API returns the previous day's issue)."""
        set_json_mode(output_json)
        try:
            envelope = fetch_before(date)
        except ValueError as exc:
            raise click.ClickException(str(exc)) from None
        if output_json:
            print_json(envelope)
            return
        _render_issue(envelope, show_top=False)

    @daily.command("story")
    @click.argument("story_id", type=int)
    @click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
    @click.option("--save", is_flag=True, default=False, help="Save the story as Markdown")
    @click.option(
        "--output-dir",
        "-o",
        type=click.Path(file_okay=False, path_type=str),
        default=str(get_data_dir() / "downloads" / "daily"),
        help="Directory for saved Markdown",
    )
    @click.option("--no-images", is_flag=True, default=False, help="Do not download images")
    @click.option("--reading-mode/--no-reading-mode", default=True, help="Use Rich pager for reading")
    def daily_story(
        story_id: int,
        output_json: bool,
        save: bool,
        output_dir: str,
        no_images: bool,
        reading_mode: bool,
    ) -> None:
        """Show or save one Zhihu Daily story as Markdown."""
        set_json_mode(output_json)
        try:
            metadata, markdown = scrape_story(story_id)
        except ValueError as exc:
            raise click.ClickException(str(exc)) from None

        if save:
            from zhihu_cli.content.download_contents import save_article

            path = save_article(metadata["url"], metadata, markdown, output_dir, with_media=not no_images)
            file_saved(path)

        if output_json:
            print_json({"metadata": metadata, "content_md": markdown})
            return

        title = metadata.get("title") or "untitled"
        author = metadata.get("author") or "unknown"
        url = metadata.get("url")
        credit = f"> {author}" + (f" · [原文]({url})" if url else "")
        _print_reading(f"# {title}\n\n{credit}", markdown, reading_mode)

    return main_group
