import time
from typing import Dict, Generator
from ..utils.html2markdown import converter
from .requests import session

def fetch_child_comments(parent_comment: Dict) -> Generator[Dict, None, None]:
    """递归获取子评论，生成每条子评论"""
    child_offset = parent_comment.get("child_comment_next_offset")
    if not child_offset:
        return
    child_api_url = f"https://www.zhihu.com/api/v4/comment_v5/comment/{parent_comment['id']}/child_comment"
    child_next_url = f"{child_api_url}?limit=20&offset={child_offset}"
    
    while child_next_url:
        resp = session.get(child_next_url)
        if resp.status_code != 200:
            break
        child_json = resp.json()
        for child in child_json.get("data", []):
            yield {
                "author": child.get("author", {}).get("name", "匿名用户"),
                "like_count": child.get("like_count", 0),
                "dislike_count": child.get("dislike_count", 0),
                "content": converter.convert(child.get("content", "")),
                "id": child.get("id")
            }
        paging = child_json.get("paging", {})
        child_next_url = paging.get("next") if not paging.get("is_end") else None
        if child_next_url:
            time.sleep(0.5)

def fetch_root_comments(
    item_type: str,
    item_id: str,
) -> Generator[Dict, None, None]:
    """获取根评论（含子评论）生成器"""
    next_url = f"https://www.zhihu.com/api/v4/comment_v5/{item_type}/{item_id}/root_comment?order_by=score&limit=20&offset="
    while next_url:
        resp = session.get(next_url)
        if resp.status_code != 200:
            break
        res_json = resp.json()
        for comment in res_json.get("data", []):
            root = {
                "author": comment.get("author", {}).get("name", "匿名用户"),
                "like_count": comment.get("like_count", 0),
                "dislike_count": comment.get("dislike_count", 0),
                "content": converter.convert(comment.get("content", "")),
                "id": comment.get("id"),
                "child_comments": []
            }
            # 已有的直接子评论
            for child in comment.get("child_comments", []):
                root["child_comments"].append({
                    "author": child.get("author", {}).get("name", "匿名用户"),
                    "like_count": child.get("like_count", 0),
                    "dislike_count": child.get("dislike_count", 0),
                    "content": converter.convert(child.get("content", "")),
                })
            # 递归获取更多子评论
            for child in fetch_child_comments(comment):
                root["child_comments"].append(child)
            yield root
        
        paging = res_json.get("paging", {})
        if paging.get("is_end"):
            break
        next_url = paging.get("next")
        if next_url:
            next_url = next_url.replace("http://", "https://")
        time.sleep(1)

def print_comments(item_type: str, item_id: str) -> None:
    """打印所有评论（带格式）"""
    comment_id = 1
    for comment in fetch_root_comments(item_type, item_id):
        print(f"\n[{comment_id}] 作者: {comment['author']} | 赞: {comment['like_count']} | 踩: {comment['dislike_count']}")
        print("-" * 20)
        print(comment['content'])
        if comment['child_comments']:
            print("\n  ↳ 子评论:")
            for child in comment['child_comments']:
                print(f"    - 作者: {child['author']} | 赞: {child['like_count']} | 踩: {child['dislike_count']}")
                print(f"      {child['content']}\n")
        print("-" * 20)
        comment_id += 1
