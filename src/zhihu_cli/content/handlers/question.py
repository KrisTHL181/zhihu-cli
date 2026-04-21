from zhihu_cli.content.handlers import fmt_time
from zhihu_cli.content.handlers.requests import get_page_entities, session
from zhihu_cli.content.handlers.waterfall import stream_handler
from zhihu_cli.content.utils.html2markdown import converter

NEXT_URL_API = "https://www.zhihu.com/api/v4/questions/{question_id}/answers?include=data%5B%2A%5D.content%2Cfavlists_count%2Cvoteup_count%2Ccomment_count%2Cauthor.name&limit=5&offset=0&sort_by=default&platform=desktop"


def parse_question_metadata(item: dict) -> dict:
    author = item.get("author", {})

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
            "name": author.get("name", "匿名用户"),
            "headline": author.get("headline", ""),
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
    url = NEXT_URL_API.replace("{question_id}", question_data["id"])

    def parse_ans(data):
        for ans in data.get("data", []):
            yield {
                "author": ans.get("author", {}).get("name", "匿名用户"),
                "vote": ans.get("voteup_count", 0),
                "content": converter.convert(ans.get("content", "")),
            }

    return stream_handler(url, parse_ans)


def upvote_answer(answer_id) -> dict:
    resp = session.post(f"https://www.zhihu.com/api/v4/answers/{answer_id}/voters", json={"type": "up"})
    return resp.json()


def neutral_answer(answer_id) -> dict:
    resp = session.post(f"https://www.zhihu.com/api/v4/answers/{answer_id}/voters", json={"type": "neutral"})
    return resp.json()


def downvote_answer(answer_id) -> dict:
    resp = session.post(f"https://www.zhihu.com/api/v4/answers/{answer_id}/voters", json={"type": "down"})
    return resp.json()


def thank_answer(answer_id) -> dict:
    resp = session.post(f"https://www.zhihu.com/api/v4/answers/{answer_id}/thankers")
    return resp.json()


def unthank_answer(answer_id) -> dict:
    resp = session.delete(f"https://www.zhihu.com/api/v4/answers/{answer_id}/thankers")
    return resp.json()


def upvote_question(question_id) -> dict:
    resp = session.post(f"https://www.zhihu.com/api/v4/questions/{question_id}/voters/up")
    return resp.json()


def unvote_question(question_id) -> dict:
    resp = session.delete(f"https://www.zhihu.com/api/v4/questions/{question_id}/voters")
    return resp.json()


def downvote_quesetion(question_id) -> dict:  # 未公开接口！
    resp = session.post(f"https://www.zhihu.com/api/v4/questions/{question_id}/voters/down")
    return resp.json()


def follow_question(question_id) -> dict:
    resp = session.post(f"https://www.zhihu.com/api/v4/questions/{question_id}/followers")
    return resp.json()


def unfollow_question(question_id) -> dict:
    resp = session.delete(f"https://www.zhihu.com/api/v4/questions/{question_id}/followers")
    return resp.json()
