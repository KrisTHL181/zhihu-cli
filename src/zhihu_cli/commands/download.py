"""Download Zhihu content as Markdown files."""

import json
import os
from pathlib import Path

import click

from zhihu_cli.commands._helpers import _extract_url_token, _save_markdown
from zhihu_cli.content.download_contents import (
    ContentDownloader,
    download_media_files,
    sanitize_filename,
    save_article,
    save_pin,
)
from zhihu_cli.content.handlers import get_data_dir
from zhihu_cli.content.handlers.article import scrape_article
from zhihu_cli.content.handlers.people import (
    fetch_member_answers,
    fetch_member_articles,
    fetch_member_pins,
    fetch_member_profile,
)
from zhihu_cli.content.handlers.pin import scrape_pin
from zhihu_cli.content.handlers.question import (
    scrape_answer_page,
    scrape_answers,
    scrape_question_data,
)
from zhihu_cli.content.handlers.requests import session
from zhihu_cli.content.handlers.zvideo import get_best_video_url, scrape_zvideo
from zhihu_cli.content.utils.wait import wait
from zhihu_cli.output import (
    echo,
    error,
    f_dim,
    f_label,
    f_meta,
    f_name,
    f_num,
    f_path,
    f_title,
    file_saved,
    info,
    item_index,
    print_json,
    section,
    set_json_mode,
    success,
    warning,
)


@click.group()
def download() -> None:
    """Download Zhihu content as Markdown files."""


@download.command("article")
@click.argument("url")
@click.option(
    "--output-dir",
    "-o",
    default=str(get_data_dir() / "downloads" / "articles"),
    help="Output directory",
)
@click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
@click.option("--with-media", is_flag=True, default=False, help="Download images/videos alongside Markdown")
def download_article(url: str, output_dir: str, output_json: bool, with_media: bool) -> None:
    """Download a single Zhihu article as Markdown."""
    metadata, markdown = scrape_article(url)
    if with_media:
        markdown, n = download_media_files(markdown, output_dir)
        if n:
            metadata = {**metadata, "media_files": n}
    filepath = _save_markdown(metadata, markdown, output_dir)
    if output_json:
        print_json({"metadata": metadata, "filepath": filepath})
        return
    echo(f"  {f_title(str(metadata.get('title', 'untitled')))}")
    file_saved(filepath)


@download.command("question")
@click.argument("url")
@click.option(
    "--output-dir",
    "-o",
    default=str(get_data_dir() / "downloads" / "questions"),
    help="Output directory",
)
@click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
@click.option("--with-media", is_flag=True, default=False, help="Download images/videos alongside Markdown")
def download_question(url: str, output_dir: str, output_json: bool, with_media: bool) -> None:
    """Download a Zhihu question and all its answers as Markdown."""
    q_meta, q_detail_md = scrape_question_data(url)
    os.makedirs(output_dir, exist_ok=True)

    title = sanitize_filename(q_meta.get("title", "untitled"))

    # Question detail
    if with_media:
        q_detail_md, _ = download_media_files(q_detail_md, output_dir)
    filepath = os.path.join(output_dir, f"{title}_question.md")[:200]
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(f"# {q_meta['title']}\n\n{q_detail_md}\n")

    ans_dir = os.path.join(output_dir, f"{title}_answers")
    os.makedirs(ans_dir, exist_ok=True)
    count = 0
    for ans in scrape_answers(q_meta):
        count += 1
        content = ans["content"]
        if with_media:
            content, _ = download_media_files(content, ans_dir)
        afile = os.path.join(ans_dir, f"{count:04d}_{sanitize_filename(ans['author'])}.md")[:200]
        with open(afile, "w", encoding="utf-8") as f:
            f.write(f"# Answer by {ans['author']} (+{ans['vote']})\n\n{content}\n")

    if output_json:
        print_json({"metadata": q_meta, "filepath": filepath, "answers_count": count, "answers_dir": ans_dir})
        return

    echo(f"  {f_title('Question:')} {q_meta['title']}")
    file_saved(filepath)
    echo(f"  {f_num(count)} answers saved to {f_path(ans_dir)}")


@download.command("pin")
@click.argument("url")
@click.option(
    "--output-dir",
    "-o",
    default=str(get_data_dir() / "downloads" / "pins"),
    help="Output directory",
)
@click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
@click.option("--with-media", is_flag=True, default=False, help="Download images/videos alongside Markdown")
def download_pin(url: str, output_dir: str, output_json: bool, with_media: bool) -> None:
    """Download a single Zhihu pin as Markdown."""
    metadata, markdown = scrape_pin(url)
    if with_media:
        markdown, n = download_media_files(markdown, output_dir)
        if n:
            metadata = {**metadata, "media_files": n}
    filepath = _save_markdown(metadata, markdown, output_dir)
    if output_json:
        print_json({"metadata": metadata, "filepath": filepath})
        return
    echo(f"  {f_title('Pin')} by {f_name(str(metadata.get('author', 'unknown')))}")
    file_saved(filepath)


@download.command("video")
@click.argument("url")
@click.option(
    "--output-dir",
    "-o",
    default=str(get_data_dir() / "downloads" / "videos"),
    help="Output directory",
)
@click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
@click.option("--no-download-video", is_flag=True, default=False, help="Skip downloading the video file")
def download_video(url: str, output_dir: str, output_json: bool, no_download_video: bool) -> None:
    """Download a Zhihu zvideo and its metadata."""
    set_json_mode(output_json)
    metadata, markdown = scrape_zvideo(url)
    filepath = _save_markdown(metadata, markdown, output_dir)

    video_path: str | None = None
    if not no_download_video:
        video_url = get_best_video_url(metadata)
        if video_url:
            os.makedirs(output_dir, exist_ok=True)
            title = sanitize_filename(metadata.get("title", "video"))
            ext = ".mp4"
            video_path = os.path.join(output_dir, f"{title}{ext}")[:200]
            info(f"Downloading video ({metadata.get('quality_tiers', [{}])[0].get('tier', 'best')} quality)...")
            try:
                resp = session.get(video_url, timeout=300, stream=True)
                resp.raise_for_status()
                with open(video_path, "wb") as f:
                    for chunk in resp.iter_content(chunk_size=8192):
                        f.write(chunk)
                success(f"Video saved to {f_path(video_path)}")
            except Exception as e:
                warning(f"Video download failed: {e}")
                video_path = None

    if output_json:
        result: dict = {"metadata": metadata, "filepath": filepath}
        if video_path:
            result["video_path"] = video_path
        print_json(result)
        return

    echo(f"  {f_title(str(metadata.get('title', 'untitled')))}")
    file_saved(filepath)
    if video_path:
        file_saved(video_path)


@download.command("batch-answers")
@click.option(
    "--input",
    "-i",
    "input_file",
    default=str(get_data_dir() / "exports" / "all_assets_list.json"),
    help="Assets JSON file",
)
@click.option(
    "--output-dir",
    "-o",
    default=str(get_data_dir() / "downloads" / "answers"),
    help="Output directory",
)
@click.option("--delay", "-d", type=float, default=1.0, help="Delay between requests (seconds)")
@click.option("--no-cache-headers", is_flag=True, help="Force re-paste of cURL")
@click.option("--with-media", is_flag=True, default=False, help="Download images/videos alongside Markdown")
@click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
def download_batch_answers(
    input_file: str, output_dir: str, delay: float, no_cache_headers: bool, with_media: bool, output_json: bool
) -> None:
    """Batch download all answers listed in an assets JSON file."""
    set_json_mode(output_json)
    if not os.path.exists(input_file):
        error(f"file not found: {input_file}")
        raise SystemExit(1)

    with open(input_file, encoding="utf-8") as f:
        assets = json.load(f)

    urls = [f"https://www.zhihu.com/answer/{a['id']}" for a in assets if a.get("type") == "answer"]
    if not urls:
        info("No answers found in the assets file.")
        return

    echo(f"  {f_label('Found')} {f_num(len(urls))} {f_dim('answers.')}")
    dl = ContentDownloader(output_dir=output_dir, with_media=with_media)
    if not dl.load_headers_from_curl(quick_mode=not no_cache_headers):
        raise SystemExit(1)
    results = dl.download_answers(urls, delay=delay)
    if output_json:
        print_json(results)
        return


@download.command("batch-articles")
@click.option(
    "--input",
    "-i",
    "input_file",
    default=str(get_data_dir() / "exports" / "all_assets_list.json"),
    help="Assets JSON file",
)
@click.option(
    "--output-dir",
    "-o",
    default=str(get_data_dir() / "downloads" / "articles"),
    help="Output directory",
)
@click.option("--delay", "-d", type=float, default=1.0, help="Delay between requests (seconds)")
@click.option("--no-cache-headers", is_flag=True, help="Force re-paste of cURL")
@click.option("--with-media", is_flag=True, default=False, help="Download images/videos alongside Markdown")
@click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
def download_batch_articles(
    input_file: str, output_dir: str, delay: float, no_cache_headers: bool, with_media: bool, output_json: bool
) -> None:
    """Batch download all articles listed in an assets JSON file."""
    set_json_mode(output_json)
    if not os.path.exists(input_file):
        error(f"file not found: {input_file}")
        raise SystemExit(1)

    with open(input_file, encoding="utf-8") as f:
        assets = json.load(f)

    urls = [f"https://zhuanlan.zhihu.com/p/{a['id']}" for a in assets if a.get("type") == "article"]
    if not urls:
        info("No articles found in the assets file.")
        return

    echo(f"  {f_label('Found')} {f_num(len(urls))} {f_dim('articles.')}")
    dl = ContentDownloader(output_dir=output_dir, with_media=with_media)
    if not dl.load_headers_from_curl(quick_mode=not no_cache_headers):
        raise SystemExit(1)
    results = dl.download_articles(urls, delay=delay)
    if output_json:
        print_json(results)
        return


@download.command("user")
@click.argument("user")
@click.option(
    "--output-dir",
    "-o",
    default=None,
    help="Base output directory (default: ~/.zhihu-cli/downloads/<username>)",
)
@click.option("--delay", "-d", type=float, default=1.0, help="Delay between requests in seconds")
@click.option("--max-items", "-n", type=int, default=None, help="Max items per content type")
@click.option(
    "--type",
    "content_types",
    default="all",
    type=click.Choice(["answers", "articles", "pins", "all"]),
    help="Content types to download (default: all)",
)
@click.option("--with-media", is_flag=True, default=False, help="Download images/videos alongside Markdown")
@click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
def download_user(
    user: str,
    output_dir: str | None,
    delay: float,
    max_items: int | None,
    content_types: str,
    with_media: bool,
    output_json: bool,
) -> None:
    """Download all answers, articles, and pins from a Zhihu user."""
    set_json_mode(output_json)
    url_token = _extract_url_token(user)

    profile = fetch_member_profile(url_token)
    user_name = profile["name"] if profile else url_token
    echo(f"  {f_label('User:')} {f_name(user_name)} (url_token: {f_meta(url_token)})")

    if output_dir is None:
        base_dir = get_data_dir() / "downloads" / sanitize_filename(user_name)
    else:
        base_dir = Path(output_dir)

    downloaded: dict[str, int] = {"answers": 0, "articles": 0, "pins": 0}

    if content_types in ("answers", "all"):
        answers_dir = str(base_dir / "answers")
        info(f"\nFetching answers list for {user_name}...")
        answer_items = fetch_member_answers(url_token, max_items=max_items)
        echo(f"  {f_label('Found')} {f_num(len(answer_items))} {f_dim('answers. Downloading full content...')}")

        for i, item in enumerate(answer_items, 1):
            try:
                meta, md = scrape_answer_page(item["url"])
                save_meta = {
                    "title": meta.get("title", "untitled"),
                    "author": meta.get("author", "unknown"),
                    "created": meta.get("created", "unknown"),
                }
                filepath = save_article(item["url"], save_meta, md, answers_dir, with_media=with_media)
                echo(
                    f"  {item_index(i, len(answer_items))} {save_meta['title'][:50]} -> {f_path(os.path.basename(filepath))}"
                )
                downloaded["answers"] += 1
            except Exception as e:
                error(f"  {item_index(i, len(answer_items))} Error: {e}")
            wait(delay)

    if content_types in ("articles", "all"):
        articles_dir = str(base_dir / "articles")
        info(f"\nFetching articles list for {user_name}...")
        article_items = fetch_member_articles(url_token, max_items=max_items)
        echo(f"  {f_label('Found')} {f_num(len(article_items))} {f_dim('articles. Downloading full content...')}")

        for i, item in enumerate(article_items, 1):
            try:
                meta, md = scrape_article(item["url"])
                author = meta.get("author", {})
                author_name = author.get("name", "unknown") if isinstance(author, dict) else str(author)
                save_meta = {
                    "title": meta.get("title", "untitled"),
                    "author": author_name,
                    "created": (meta.get("created_time", "unknown") or "unknown")[:10],
                }
                filepath = save_article(item["url"], save_meta, md, articles_dir, with_media=with_media)
                echo(
                    f"  {item_index(i, len(article_items))} {save_meta['title'][:50]} -> {f_path(os.path.basename(filepath))}"
                )
                downloaded["articles"] += 1
            except Exception as e:
                error(f"  {item_index(i, len(article_items))} Error: {e}")
            wait(delay)

    if content_types in ("pins", "all"):
        pins_dir = str(base_dir / "pins")
        info(f"\nFetching pins list for {user_name}...")
        pin_items = fetch_member_pins(url_token, max_items=max_items)
        echo(f"  {f_label('Found')} {f_num(len(pin_items))} {f_dim('pins. Downloading full content...')}")

        for i, item in enumerate(pin_items, 1):
            try:
                meta, md = scrape_pin(item["url"])
                author = meta.get("author", {})
                author_name = author.get("name", "unknown") if isinstance(author, dict) else str(author)
                save_meta = {
                    "author": author_name,
                    "created": (meta.get("created_time", "unknown") or "unknown")[:10],
                    "pin_id": str(meta.get("id", "")),
                }
                filepath = save_pin(item["url"], save_meta, md, pins_dir, with_media=with_media)
                preview = (meta.get("excerpt", "") or "")[:30]
                echo(f"  {item_index(i, len(pin_items))} {preview} -> {f_path(os.path.basename(filepath))}")
                downloaded["pins"] += 1
            except Exception as e:
                error(f"  {item_index(i, len(pin_items))} Error: {e}")
            wait(delay)

    if output_json:
        print_json(
            {"downloaded": downloaded, "base_dir": str(base_dir), "user_name": user_name, "url_token": url_token}
        )
        return

    section(f"Done! Downloaded from {f_name(user_name)}:")
    echo(f"  {f_label('Answers:')}  {f_num(downloaded['answers'])}")
    echo(f"  {f_label('Articles:')} {f_num(downloaded['articles'])}")
    echo(f"  {f_label('Pins:')}     {f_num(downloaded['pins'])}")
    echo(f"  {f_label('Output:')}   {f_path(str(base_dir))}")


def register_download(main_group) -> None:
    """Register the download command group on the main CLI group."""
    main_group.add_command(download)
