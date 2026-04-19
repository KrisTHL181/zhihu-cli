from .requests import get_page_entities
from ..html2markdown import converter
from . import fmt_time

def parse_article_metadata(item: dict) -> dict:
    article_id = item.get('id', '')
    title = item.get('title', '无标题')
    excerpt = item.get('excerpt', '')
    content_preview = excerpt or (item.get('content', '')[:200] if item.get('content') else '')
    
    # 统计数据
    voteup_count = item.get('voteup_count', 0)
    comment_count = item.get('comment_count', 0)
    
    # 时间戳
    created = item.get('created', 0)
    updated = item.get('updated', 0)
    
    # 作者信息
    author = item.get('author', {})
    author_name = author.get('name', '未知用户')

    # 文章链接
    url = item.get('url', '')
    if not url and article_id:
        url = f"https://zhuanlan.zhihu.com/p/{article_id}"

    
    return {
        "id": article_id,
        "title": title,
        "excerpt": content_preview,
        "url": url,
        "created_time": fmt_time(created),
        "updated_time": fmt_time(updated),
        "stats": {
            "voteup_count": voteup_count,
            "comment_count": comment_count
        },
        "author": {
            "name": author_name,
            "headline": author.get('headline', ''),
        },
        "comment_permission": item.get('comment_permission', '')
    }


def scrape_article(article_url: str) -> tuple[dict, str]:
    entities = get_page_entities(article_url)
    item = entities.get("articles", {})
    if not item:
        raise ValueError(f"No {item} data found in entities")
    item_data = next(iter(item.values()))
    return parse_article_metadata(item_data), converter.convert(item_data["content"])
