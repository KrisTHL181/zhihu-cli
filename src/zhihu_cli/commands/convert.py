"""Convert command group for zhihu-cli."""

from __future__ import annotations

import json
import os
from pathlib import Path

import click

from zhihu_cli.content.handlers import get_data_dir
from zhihu_cli.content.universal_converter import convert_items, load_json
from zhihu_cli.content.utils.html2markdown import PageToMarkdown
from zhihu_cli.output import (
    error,
    f_dim,
    f_num,
    f_path,
    success,
    warning,
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

    @convert.command("markdown")
    @click.argument("path", type=click.Path(exists=True))
    @click.option(
        "--override",
        is_flag=True,
        default=False,
        help="Delete original HTML file(s) after conversion.",
    )
    def convert_markdown(path: str, override: bool) -> None:
        """Convert raw HTML files to Markdown.

        PATH may be a single .html file or a directory. When PATH is a
        directory, all .html / .htm files under it are converted recursively.

        By default the generated .md file sits alongside the original HTML.
        Use --override to delete the original HTML after conversion.
        """
        input_path = Path(path)
        converter = PageToMarkdown()

        # ── collect files ─────────────────────────────────────────────────
        if input_path.is_file():
            html_files = [input_path]
        else:
            html_files = sorted(
                p for p in input_path.rglob("*") if p.is_file() and p.suffix.lower() in (".html", ".htm")
            )

        if not html_files:
            error(f"No HTML files found in {f_path(path)}")
            raise SystemExit(1)

        # ── convert each file ─────────────────────────────────────────────
        converted = 0
        skipped = 0
        for html_file in html_files:
            if html_file.suffix.lower() not in (".html", ".htm"):
                skipped += 1
                warning(f"Skipping non-HTML file: {f_path(str(html_file))}")
                continue

            md_path = html_file.with_suffix(".md")

            # Read HTML
            try:
                html_content = html_file.read_text(encoding="utf-8")
            except Exception as exc:
                warning(f"Cannot read {f_path(str(html_file))}: {exc}")
                skipped += 1
                continue

            if not html_content.strip():
                warning(f"Skipping empty file: {f_path(str(html_file))}")
                skipped += 1
                continue

            # Convert
            markdown = converter.convert(html_content)

            # Write Markdown
            md_path.write_text(markdown, encoding="utf-8")
            converted += 1

            # Delete original if --override
            if override:
                html_file.unlink()
                success(f"{f_path(str(html_file))} {f_dim('→')} {f_path(str(md_path))}  (deleted)")
            else:
                success(f"{f_path(str(html_file))} {f_dim('→')} {f_path(str(md_path))}")

        # ── summary ──────────────────────────────────────────────────────
        msg = f"Converted {f_num(converted)} file(s)"
        if skipped:
            msg += f", {f_num(skipped)} skipped"
        if override:
            msg += f" {f_dim('(--override)')}"
        success(msg)
