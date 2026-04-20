from .requests import get_page_entities, session
from ..utils.html2markdown import converter
from . import fmt_time

NEXT_URL_API = "https://www.zhihu.com/api/v4/questions/{question_id}/answers?include=data%5B%2A%5D.content%2Cfavlists_count%2Cvoteup_count%2Ccomment_count%2Cauthor.name&limit=5&offset=0&sort_by=default&platform=desktop"

def parse_question_metadata(item: dict) -> dict:
    author = item.get('author', {})

    return {
        "id": item["id"],
        "title": item.get("title", ""),
        "url": item.get("url", ""),
        "created_time": fmt_time(item.get("created", 0)),
        "updated_time": fmt_time(item.get("updatedTime", 0)),
        "answer_count": item.get("answerCount", 0),
        "comment_count": item.get("commentCount", 0),
        "visit_count": item.get("visitCount", 0),
        "follower_count": item.get("followerCount", 0),
        "author": {
            "name": author.get('name', '匿名用户'),
            "headline": author.get('headline', ''),
        },
    }

def scrape_question_data(question_url: str) -> tuple[dict, str]:
    entities = get_page_entities(question_url)
    item = entities.get("questions", {})
    if not item:
        raise ValueError(f"No {item} data found in entities")

    item_data = next(iter(item.values()))
    return parse_question_metadata(item_data), converter.convert(item_data.get("detail"))

def scrape_answers(question_data):
    next_url = NEXT_URL_API.replace("{question_id}", question_data["id"])

    is_end = False
    answer_num = 1

    while not is_end and next_url:
        resp = session.get(next_url)
        resp.raise_for_status()

        res_json = resp.json()
        answers = res_json.get("data", [])

        for ans in answers:
            author = ans.get("author", {}).get("name", "匿名用户")
            vote = ans.get("voteup_count", 0)
            content = converter.convert(ans.get("content", ""))

            yield answer_num, author, vote, content
            answer_num += 1

        paging = res_json.get("paging", {})
        is_end = paging.get("is_end", True)
        next_url = paging.get("next")
        if next_url:
            next_url = next_url.replace("http://", "https://")

def upvote_answer(answer_id) -> dict:
    resp = session.post(f"https://www.zhihu.com/api/v4/answers/{answer_id}/voters", json={"type": "up"})
    return resp.json()

def neutral_answer(answer_id) -> dict:
    resp = session.post(f"https://www.zhihu.com/api/v4/answers/{answer_id}/voters", json={"type": "neutral"})
    return resp.json()

def downvote_answer(answer_id) -> dict:
    resp = session.post(f"https://www.zhihu.com/api/v4/answers/{answer_id}/voters", json={"type": "down"})
    return resp.json()

def collect_answer(answer_id) -> dict:
    resp = session.post(f"https://www.zhihu.com/api/v4/collections/contents/answer/{answer_id}")
    return resp.json()

def thank_answer(answer_id) -> dict:
    resp = session.post(f"https://www.zhihu.com/api/v4/answers/{answer_id}/thankers")
    return resp.json()

def unthank_answer(answer_id) -> dict:
    resp = session.delete(f"https://www.zhihu.com/api/v4/answers/{answer_id}/thankers")
    return resp.json()
