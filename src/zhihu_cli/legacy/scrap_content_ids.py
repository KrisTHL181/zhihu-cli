import json
import re
import sys
import time
from pathlib import Path

from zhihu_cli.content.handlers.cache_manager import cache_manager
from zhihu_cli.content.handlers.requests import session


def load_headers(quick_mode: bool = False) -> dict[str, str] | None:
    """Load headers from file or via cURL paste"""
    if quick_mode:
        headers = cache_manager.load_headers()
        if headers:
            print("[Success] Loaded cached headers")
            return headers

    print("\n--- Please paste cURL from any Zhihu Article Page ---")
    print("Tip: Press Ctrl+D (Unix) or Ctrl+Z+Enter (Win) to finish\n")

    curl_input = sys.stdin.read()
    if not curl_input.strip():
        return None

    base_url, headers = extract_config_from_curl(curl_input)
    if not headers:
        return None

    # Clean up headers
    headers.pop("Accept-Encoding", None)
    cache_manager.save_headers(headers)
    print("[Success] Headers configured and cached.")
    return headers


def extract_config_from_curl(curl_text: str) -> tuple[str, dict[str, str]]:
    """从 cURL 中提取基础信息"""
    url_match = re.search(r"curl\s+'([^']+)'", curl_text)
    full_url = url_match.group(1) if url_match else ""
    base_url = full_url.split("?")[0]

    headers = {}
    header_matches = re.findall(r"-H\s+'([^']+)'", curl_text)
    for h in header_matches:
        if ":" in h:
            k, v = h.split(":", 1)
            headers[k.strip()] = v.strip()
    return base_url, headers


def fetch_all_creation_assets() -> list[dict[str, str]] | None:
    print("--- 请粘贴【所有内容列表 / creations/v2/all】的 cURL 命令 ---")
    headers = load_headers(quick_mode=True)
    if not headers:
        return

    base_url = "https://www.zhihu.com/api/v4/creations/all"

    # 存储结构：[{'id': 'xxx', 'type': 'answer'}, ...]
    all_assets = []
    offset = 0
    limit = 20  # 资产列表通常支持 20
    is_end = False

    print("\n[量化盘点] 开始全品类资产扫描 (Answers, Pins, Articles)...")

    while not is_end:
        params = {"start": 0, "end": 0, "limit": limit, "offset": offset, "need_co_creation": 1, "sort_type": "created"}

        try:
            resp = session.get(base_url, headers=headers, params=params, timeout=15)

            if resp.status_code == 200:
                res_json = resp.json()
                data_list = res_json.get("data", [])
                paging = res_json.get("paging", {})

                # 提取并分类
                for item in data_list:
                    asset_type = item.get("type")
                    asset_id = item.get("data", {}).get("id")

                    if asset_id and asset_type in ["answer", "pin", "article"]:
                        all_assets.append(
                            {
                                "id": asset_id,
                                "type": asset_type,
                                "title": item.get("data", {}).get("title", f"Pin_{asset_id}"),
                            }
                        )

                # 更新翻页状态
                is_end = paging.get("is_end", True)
                totals = paging.get("totals", 0)
                offset += limit

                print(f"  [进度] 扫描中: {min(offset, totals)}/{totals} | 已录入: {len(all_assets)}")

                if offset >= totals:
                    break
            else:
                print(f"  [错误] 状态码: {resp.status_code}，签名可能失效")
                break

        except Exception as e:
            print(f"  [异常] {e}")
            break

        time.sleep(1.2)

    # --- 统计与保存 ---
    if all_assets:
        # 按类型统计展示
        stats = {}
        for a in all_assets:
            stats[a["type"]] = stats.get(a["type"], 0) + 1

        output_file = str(Path.home() / ".zhihu-cli" / "exports" / "all_assets_list.json")
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(all_assets, f, indent=4, ensure_ascii=False)

        print("\n" + "=" * 40)
        print("✅ 全品类资产盘点完成！")
        for t, count in stats.items():
            print(f"📊 {t.capitalize()}: {count} 个")
        print(f"📁 完整资产清单保存至: {output_file}")
        print("=" * 40)
        return all_assets
    else:
        print("\n[!] 资产列表为空，请检查 cURL。")
        return []


if __name__ == "__main__":
    fetch_all_creation_assets()
