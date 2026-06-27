#!/usr/bin/env python3
"""
Generic converter: unify different Zhihu export JSONs into all_assets_list.json.
Supported inputs:
  - Raw API answer list (question.title -> answer type)
  - Raw API article list (id + title -> article type)
  - Activity/user-act format (items with 'type', 'id', 'title')
  - Any file with items containing 'id' and 'title' (use --type to force)
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def detect_format(items: list[dict[str, Any]]) -> str:
    """Heuristically detect the format of the first item."""
    if not items:
        return "unknown"
    first = items[0]
    if "question" in first and isinstance(first["question"], dict):
        return "answers"  # raw API answer format: title under question.title
    if "type" in first:
        return "typed"  # already has 'type' field (user-act format)
    if "title" in first and "id" in first:
        return "generic_title"  # raw API article format
    return "unknown"


def convert_items(items: list[dict[str, Any]], forced_type: str | None = None) -> list[dict[str, str]]:
    """Convert items to unified format: {id, type, title, url?}."""
    result = []
    fmt = detect_format(items) if not forced_type else "forced"

    for item in items:
        # Ensure id is string
        asset_id = str(item.get("id", ""))
        if not asset_id:
            continue

        # Determine type
        if forced_type:
            asset_type = forced_type
        elif fmt == "typed":
            asset_type = item.get("type", "unknown")
        elif fmt == "answers":
            asset_type = "answer"
        elif fmt == "generic_title":
            asset_type = "article"
        else:
            asset_type = "unknown"

        # Determine title
        if fmt == "answers":
            title = item.get("question", {}).get("title", "untitled answer")
        else:
            title = item.get("title", f"{asset_type}_{asset_id}")

        # Build unified asset
        asset = {"id": asset_id, "type": asset_type, "title": title}
        # Optionally add URL if present
        if "url" in item:
            asset["url"] = item["url"]
        result.append(asset)

    return result


def load_json(file_path: str) -> list[dict[str, Any]]:
    """Load JSON file, handling both list and object wrappers."""
    with open(file_path, encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and "data" in data:
        return data["data"]
    raise ValueError(f"Unsupported JSON structure in {file_path}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert one or more Zhihu export JSONs to unified all_assets_list.json"
    )
    parser.add_argument("inputs", nargs="+", help="Input JSON files (export outputs or raw API data)")
    parser.add_argument(
        "--output",
        "-o",
        default=str(Path.home() / ".zhihu-cli" / "exports" / "all_assets_list.json"),
        help="Output unified assets file",
    )
    parser.add_argument("--type", "-t", help="Force a specific type for all items (e.g., 'article', 'answer', 'pin')")
    args = parser.parse_args()

    all_assets = []
    for inp in args.inputs:
        if not Path(inp).exists():
            print(f"Warning: {inp} not found, skipping.", file=sys.stderr)
            continue
        try:
            items = load_json(inp)
            converted = convert_items(items, forced_type=args.type)
            print(f"Loaded {len(items)} items from {inp} -> {len(converted)} assets", file=sys.stderr)
            all_assets.extend(converted)
        except Exception as e:
            print(f"Error processing {inp}: {e}", file=sys.stderr)
            continue

    if not all_assets:
        print("No valid assets found. Exiting.", file=sys.stderr)
        sys.exit(1)

    # Remove duplicates (same id + type)
    seen = set()
    unique_assets = []
    for asset in all_assets:
        key = (asset["type"], asset["id"])
        if key not in seen:
            seen.add(key)
            unique_assets.append(asset)

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(unique_assets, f, ensure_ascii=False, indent=4)

    print(f"Successfully wrote {len(unique_assets)} unique assets to {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()
