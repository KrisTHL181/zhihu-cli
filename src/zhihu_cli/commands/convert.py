"""Convert command group for zhihu-cli."""

import json
import os

import click

from zhihu_cli.content.handlers import get_data_dir
from zhihu_cli.content.handlers.draft import draft_to_markdown
from zhihu_cli.content.universal_converter import convert_items, load_json
from zhihu_cli.output import (
    echo,
    error,
    f_dim,
    f_num,
    f_path,
    success,
)


def register_convert(main_group):
    """Register the convert command group onto *main_group*."""

    @main_group.group()
    def convert() -> None:
        """Convert between JSON export formats."""

    @convert.command("universal")
    @click.argument("inputs", nargs=-1, required=True)
    @click.option(
        "--output", "-o", default=str(get_data_dir() / "exports" / "all_assets_list.json"), help="Output file"
    )
    @click.option("--type", "-t", "forced_type", default=None, help="Force a specific type")
    def convert_universal(inputs: tuple[str, ...], output: str, forced_type: str | None) -> None:
        """Normalize multiple JSON export files into a unified assets list."""
        all_items: list[dict] = []
        for fpath in inputs:
            all_items.extend(load_json(fpath))

        if not all_items:
            error("No valid items found.")
            raise SystemExit(1)

        converted = convert_items(all_items, forced_type)
        with open(output, "w", encoding="utf-8") as f:
            json.dump(converted, f, ensure_ascii=False, indent=2)
        success(f"Converted {f_num(len(converted))} items {f_dim('→')} {f_path(output)}")

    @convert.command("user-act")
    @click.argument("input_file", default=str(get_data_dir() / "exports" / "zhihu_user_activities.json"))
    @click.argument("output_file", default=str(get_data_dir() / "exports" / "all_assets_list.json"))
    def convert_user_act(input_file: str, output_file: str) -> None:
        """Convert zhihu_user_activities.json to all_assets_list.json format."""
        if not os.path.exists(input_file):
            error(f"file not found: {input_file}")
            raise SystemExit(1)

        converted = convert_items(load_json(input_file))

        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(converted, f, ensure_ascii=False, indent=2)
        success(f"Converted {f_num(len(converted))} items {f_dim('→')} {f_path(output_file)}")

    @convert.command("draft")
    @click.argument("url")
    @click.option("--output", "-o", default=None, help="Save Markdown to file instead of printing")
    def convert_draft(url: str, output: str | None) -> None:
        """Convert the latest draft of a Zhihu question/answer to Markdown.

        Provide a Zhihu question URL (e.g. https://www.zhihu.com/question/123456)
        to fetch and convert your unpublished draft to Markdown.
        """
        try:
            metadata, markdown = draft_to_markdown(url)
        except ValueError as e:
            error(f"{e}")
            raise SystemExit(1)

        if output:
            with open(output, "w", encoding="utf-8") as f:
                f.write(markdown)
            success(f"Draft saved to {f_path(output)}")
        else:
            echo(markdown)
