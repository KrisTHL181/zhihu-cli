from zhihu_cli.content.handlers import fmt_time
from zhihu_cli.content.handlers.requests import get_page_entities
from zhihu_cli.content.utils.html2markdown import converter


def parse_pin_metadata(item: dict) -> dict:
    pin_id = item.get("id", "")
    title = item.get("title", "") or f"想法 {pin_id}"
    excerpt = item.get("excerpt", "")
    content_preview = excerpt or (item.get("content", "")[:200] if item.get("content") else "")

    voteup_count = item.get("voteup_count", 0)
    comment_count = item.get("comment_count", 0)

    created = item.get("created", 0)
    updated = item.get("updated", 0)

    author = item.get("author", {})
    author_name = author.get("name", "未知用户")

    # 想法内容可能是列表格式
    content_raw = item.get("content", "")
    if isinstance(content_raw, list) and content_raw:
        content_raw = content_raw[0].get("content", "")

    url = item.get("url", "")
    if not url and pin_id:
        url = f"https://www.zhihu.com/pin/{pin_id}"

    return {
        "id": pin_id,
        "title": title,
        "excerpt": content_preview,
        "url": url,
        "created_time": fmt_time(created),
        "updated_time": fmt_time(updated),
        "stats": {"voteup_count": voteup_count, "comment_count": comment_count},
        "author": {
            "name": author_name,
            "headline": author.get("headline", ""),
        },
        "comment_permission": item.get("comment_permission", ""),
    }


def scrape_pin(pin_url: str) -> tuple[dict, str]:
    entities = get_page_entities(pin_url)
    item = entities.get("pins", {})
    if not item:
        raise ValueError("No pins data found in entities")

    item_data = next(iter(item.values()))

    content = item_data.get("content", "")  # 想法内容可能嵌套
    if isinstance(content, list) and content:
        content = content[0].get("content", "")
    return parse_pin_metadata(item_data), converter.convert(content)
