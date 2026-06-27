"""Scrape command group — batch scrape user content lists to JSON files."""

import json
from pathlib import Path

import click

from zhihu_cli.commands._helpers import _resolve_url_token
from zhihu_cli.content.handlers import get_data_dir
from zhihu_cli.content.handlers.people import (
    fetch_member_activities,
    fetch_member_answers,
    fetch_member_articles,
)
from zhihu_cli.output import f_name, f_num, f_path, info, print_json, set_json_mode, success


def register_scrape(main_group: click.Group) -> None:
    """Register the scrape command group onto the main CLI group."""

    @main_group.group()
    def scrape() -> None:
        """Batch scrape user content lists to JSON files."""

    @scrape.command("creations")
    @click.option(
        "--output",
        "-o",
        default=str(get_data_dir() / "exports" / "all_assets_list.json"),
        help="Output JSON file",
    )
    @click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
    def scrape_creations(output: str, output_json: bool) -> None:
        """Fetch all user creation IDs (answers, articles, pins) -> JSON."""
        set_json_mode(output_json)
        from zhihu_cli.creator_tools.parse_content_datas import generate_assets_file

        generate_assets_file(Path(output))
        if output_json:
            with open(output, encoding="utf-8") as f:
                data = json.load(f)
            print_json(data)
            return

    @scrape.command("activities")
    @click.argument("url_token", required=False, default=None)
    @click.option(
        "--output",
        "-o",
        default=str(get_data_dir() / "exports" / "zhihu_user_activities.json"),
        help="Output JSON file",
    )
    @click.option("--limit", "-n", type=int, default=20, help="Items per page (default: 20)")
    @click.option("--max", "-m", "max_items", type=int, default=None, help="Max total items (default: fetch all)")
    @click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
    def scrape_activities(
        url_token: str | None,
        output: str,
        limit: int,
        max_items: int | None,
        output_json: bool,
    ) -> None:
        """Fetch a user's activity feed -> JSON.

        URL_TOKEN can be a Zhihu url_token (e.g. "zhangsan") or a full profile URL.
        Defaults to the authenticated user.
        """
        set_json_mode(output_json)
        token = _resolve_url_token(url_token)
        info(f"Fetching activities for {f_name(token)}...")
        items = fetch_member_activities(token, limit=limit, max_items=max_items)

        with open(output, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=2)
        success(f"Saved {f_num(len(items))} activities to {f_path(output)}")
        if output_json:
            print_json(items)

    @scrape.command("answers")
    @click.argument("url_token", required=False, default=None)
    @click.option(
        "--output",
        "-o",
        default=str(get_data_dir() / "exports" / "zhihu_answers.json"),
        help="Output JSON file",
    )
    @click.option("--limit", "-n", type=int, default=20, help="Items per page (default: 20)")
    @click.option("--max", "-m", "max_items", type=int, default=None, help="Max total items (default: fetch all)")
    @click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
    def scrape_answers_list(
        url_token: str | None,
        output: str,
        limit: int,
        max_items: int | None,
        output_json: bool,
    ) -> None:
        """Fetch a user's answer list -> JSON.

        URL_TOKEN can be a Zhihu url_token (e.g. "zhangsan") or a full profile URL.
        Defaults to the authenticated user.
        """
        set_json_mode(output_json)
        token = _resolve_url_token(url_token)
        info(f"Fetching answers for {f_name(token)}...")
        items = fetch_member_answers(token, limit=limit, max_items=max_items)

        with open(output, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=2)
        success(f"Saved {f_num(len(items))} answers to {f_path(output)}")
        if output_json:
            print_json(items)

    @scrape.command("articles")
    @click.argument("url_token", required=False, default=None)
    @click.option(
        "--output",
        "-o",
        default=str(get_data_dir() / "exports" / "zhihu_articles.json"),
        help="Output JSON file",
    )
    @click.option("--limit", "-n", type=int, default=20, help="Items per page (default: 20)")
    @click.option("--max", "-m", "max_items", type=int, default=None, help="Max total items (default: fetch all)")
    @click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
    def scrape_articles_list(
        url_token: str | None,
        output: str,
        limit: int,
        max_items: int | None,
        output_json: bool,
    ) -> None:
        """Fetch a user's article list -> JSON.

        URL_TOKEN can be a Zhihu url_token (e.g. "zhangsan") or a full profile URL.
        Defaults to the authenticated user.
        """
        set_json_mode(output_json)
        token = _resolve_url_token(url_token)
        info(f"Fetching articles for {f_name(token)}...")
        items = fetch_member_articles(token, limit=limit, max_items=max_items)

        with open(output, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=2)
        success(f"Saved {f_num(len(items))} articles to {f_path(output)}")
        if output_json:
            print_json(items)
