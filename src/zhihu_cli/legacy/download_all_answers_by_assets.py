#!/usr/bin/env python3
"""
批量下载知乎用户的所有回答
基于 scrap-content-list.py 生成的 JSON 文件
"""

import argparse
import json
import os
import sys
from pathlib import Path

# 导入已有的下载模块
from zhihu_cli.content.download_contents import ContentDownloader


def load_answers_from_json(json_path: str) -> list[str]:
    """
    从 all_assets_list.json 中提取所有回答的 URL
    """
    if not os.path.exists(json_path):
        print(f"[Error] File not found: {json_path}")
        return []

    try:
        with open(json_path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print(f"[Error] Failed to parse JSON: {e}")
        return []

    if not isinstance(data, list):
        print("[Error] JSON format error: expected a list of items")
        return []

    urls = []
    for item in data:
        item_type = item.get("type", "")
        if item_type == "answer":
            # 获取 ID 并构建网页 URL
            answer_id = item.get("id")
            if answer_id:
                url = f"https://www.zhihu.com/answer/{answer_id}"
                urls.append(url)

    print(f"[Info] Found {len(urls)} answers in {json_path}")
    return urls


def main() -> None:
    parser = argparse.ArgumentParser(description="批量下载知乎用户的所有回答（基于动态列表 JSON）")
    parser.add_argument(
        "--input",
        "-i",
        type=str,
        default=str(Path.home() / ".zhihu-cli" / "exports" / "all_assets_list.json"),
        help="all_assets_list.json file path",
    )
    parser.add_argument(
        "--output-dir",
        "-o",
        type=str,
        default=str(Path.home() / ".zhihu-cli" / "downloads" / "answers"),
        help="Output directory",
    )
    parser.add_argument("--delay", "-d", type=float, default=1.0, help="请求间隔秒数 (默认: 1.0)")
    parser.add_argument("--no-cache-headers", action="store_true", help="不使用缓存的 headers，强制重新粘贴 cURL")

    args = parser.parse_args()

    # 1. 从 JSON 中提取回答 URL
    urls = load_answers_from_json(args.input)
    if not urls:
        print("[Error] No answers found, exiting.")
        sys.exit(1)

    # 2. 创建下载器
    downloader = ContentDownloader(output_dir=args.output_dir)

    # 3. 加载请求头（使用 quick mode 自动加载缓存，除非强制重新获取）
    quick_mode = not args.no_cache_headers
    if not downloader.load_headers_from_curl(quick_mode=quick_mode):
        print("[Error] Failed to load headers, exiting.")
        sys.exit(1)

    # 4. 批量下载
    print(f"\nStarting download of {len(urls)} answers...")
    downloader.download_answers(urls, delay=args.delay)

    print("\nAll done.")


if __name__ == "__main__":
    main()
