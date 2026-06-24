"""Scrape command group — batch scrape user content lists to JSON files."""

import json
import re
import sys
from pathlib import Path

import click

from zhihu_cli.content.handlers import get_data_dir
from zhihu_cli.content.handlers.cache_manager import cache_manager
from zhihu_cli.content.handlers.waterfall import stream_handler
from zhihu_cli.output import (
    echo,
    error,
    f_num,
    f_path,
    info,
    print_json,
    success,
)


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
        from zhihu_cli.creator_tools.parse_content_datas import generate_assets_file

        generate_assets_file(Path(output))
        if output_json:
            with open(output, encoding="utf-8") as f:
                data = json.load(f)
            print_json(data)
            return

    def _generic_list_scrape(api_description: str, output_file: str, output_json: bool = False) -> None:
        """Generic stdin-based list scraper. User pastes the API's cURL command."""

        if not cache_manager.load_headers():
            error("No cached headers. Run 'zhihu auth paste' first.")
            raise SystemExit(1)

        echo(f"Paste the cURL command for the {api_description} API (Ctrl+D to finish):")
        try:
            curl_text = sys.stdin.read()
        except EOFError:
            curl_text = ""

        if not curl_text.strip():
            error("No input.")
            raise SystemExit(1)

        url_match = re.search(r"curl\s+'([^']+)'", curl_text)
        if not url_match:
            error("Could not parse URL from cURL.")
            raise SystemExit(1)

        initial_url = url_match.group(1).replace("http://", "https://")

        def parse_items(data: dict) -> list[dict]:
            return data.get("data", [])

        all_items: list[dict] = []
        for item in stream_handler(initial_url, parse_items):
            all_items.append(item)
            if len(all_items) % 20 == 0:
                info(f"Collected {len(all_items)} items...")

        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(all_items, f, ensure_ascii=False, indent=2)
        success(f"Saved {f_num(len(all_items))} items to {f_path(output_file)}")
        if output_json:
            print_json(all_items)

    @scrape.command("activities")
    @click.option(
        "--output",
        "-o",
        default=str(get_data_dir() / "exports" / "zhihu_user_activities.json"),
        help="Output JSON file",
    )
    @click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
    def scrape_activities(output: str, output_json: bool) -> None:
        """Fetch user activity feed -> JSON. Requires pasting the activities API cURL."""
        _generic_list_scrape("activities", output, output_json=output_json)

    @scrape.command("answers")
    @click.option(
        "--output",
        "-o",
        default=str(get_data_dir() / "exports" / "zhihu_answers.json"),
        help="Output JSON file",
    )
    @click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
    def scrape_answers_list(output: str, output_json: bool) -> None:
        """Fetch user's answer list -> JSON. Requires pasting the answers API cURL."""
        _generic_list_scrape("answers list", output, output_json=output_json)

    @scrape.command("articles")
    @click.option(
        "--output",
        "-o",
        default=str(get_data_dir() / "exports" / "zhihu_articles.json"),
        help="Output JSON file",
    )
    @click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
    def scrape_articles_list(output: str, output_json: bool) -> None:
        """Fetch user's article list -> JSON. Requires pasting the articles API cURL."""
        _generic_list_scrape("articles list", output, output_json=output_json)
