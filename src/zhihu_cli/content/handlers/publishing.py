import json
from typing import Any

from zhihu_cli.content.handlers.requests import session
from zhihu_cli.content.utils import generate_trace_context
from zhihu_cli.content.utils.html2markdown import calculate_text_length
from zhihu_cli.content.utils.markdown2html import markdown2html

PUBLISH_API: str = "https://www.zhihu.com/api/v4/content/publish"


def publish_answer(question_id: str, content: str, *, html: str | None = None) -> dict[str, Any]:
    trace_id = ",".join([str(x) for x in generate_trace_context()])
    if html is None:
        html = markdown2html(content, scene="answer")

    resp = session.post(
        PUBLISH_API,
        json={
            "action": "answer",
            "data": {
                "publish": {"traceId": trace_id},
                "hybridInfo": {},
                "draft": {"isPublished": False, "disabled": 1},
                "extra_info": {
                    "question_id": question_id,
                    "publisher": "pc",
                    "include": "is_contain_ai_content,is_visible,paid_info,paid_info_content,has_column,admin_closed_comment,reward_info,annotation_action,annotation_detail,collapse_reason,is_normal,is_sticky,collapsed_by,suggest_edit,comment_count,thanks_count,favlists_count,can_comment,content,editable_content,voteup_count,reshipment_settings,comment_permission,created_time,updated_time,review_info,relevant_info,question,excerpt,attachment,content_source,is_labeled,endorsements,reaction_instruction,reaction,ip_info,relationship.is_authorized,voting,is_thanked,is_author,is_nothelp,is_favorited;author.vip_info,kvip_info,badge[*].topics;settings.table_of_content.enabled",
                    "pc_business_params": '{"reshipment_settings":"allowed","comment_permission":"all","columns":null,"reward_setting":{"can_reward":false,"tagline":""},"disclaimer_status":"close","disclaimer_type":"none","commercial_report_info":{"is_report":false},"commercial_zhitask_bind_info":null,"is_report":false,"push_activity":true,"table_of_contents_enabled":false,"thank_inviter_status":"close","thank_inviter":""}',
                },
                "hybrid": {"html": html, "textLength": calculate_text_length(html)},
                "reprint": {"reshipment_settings": "allowed"},
                "commentsPermission": {"comment_permission": "all"},
                "appreciate": {"can_reward": False, "tagline": ""},
                "publishSwitch": {"draft_type": "normal"},
                "creationStatement": {"disclaimer_status": "close", "disclaimer_type": "none"},
                "commercialReportInfo": {"isReport": 0},
                "toFollower": {},
                "contentsTables": {"table_of_contents_enabled": False},
                "thanksInvitation": {"thank_inviter_status": "close", "thank_inviter": ""},
            },
        },
    )
    return resp.json()


def modify_answer(answer_id: str, content: str, *, html: str | None = None) -> dict[str, Any]:
    trace_id = ",".join([str(x) for x in generate_trace_context()])
    if html is None:
        html = markdown2html(content, scene="answer")
    text_length = calculate_text_length(html)

    payload = {
        "action": "answer",
        "data": {
            "publish": {"traceId": trace_id},
            "draft": {"contentId": answer_id, "isPublished": True, "disabled": 1},
            "hybridInfo": {},
            "extra_info": {
                "publisher": "pc",
                "include": "is_contain_ai_content,is_visible,paid_info,paid_info_content,has_column,admin_closed_comment,reward_info,annotation_action,annotation_detail,collapse_reason,is_normal,is_sticky,collapsed_by,suggest_edit,comment_count,thanks_count,favlists_count,can_comment,content,editable_content,voteup_count,reshipment_settings,comment_permission,created_time,updated_time,review_info,relevant_info,question,excerpt,attachment,content_source,is_labeled,endorsements,reaction_instruction,reaction,ip_info,relationship.is_authorized,voting,is_thanked,is_author,is_nothelp,is_favorited;author.vip_info,kvip_info,badge[*].topics;settings.table_of_content.enabled",
                "pc_business_params": json.dumps(
                    {
                        "is_paid_column": False,
                        "reward_setting": {"can_reward": False, "tagline": ""},
                        "disclaimer_status": "close",
                        "disclaimer_type": "none",
                        "push_activity": True,
                        "table_of_contents_enabled": False,
                    }
                ),
            },
            "hybrid": {"html": html, "textLength": text_length},
            "publishSwitch": {"draft_type": "normal"},
        },
    }

    resp = session.post(PUBLISH_API, json=payload)
    return resp.json()


def publish_draft(url: str) -> dict[str, Any]:
    """Publish the latest draft for a Zhihu content URL.

    Fetches the most recent draft associated with *url* and publishes it.
    For question URLs the draft answer is published as a new answer; for
    answer and article URLs the draft is published as a modification of
    the existing content.

    :param url: Zhihu URL (question, answer, or article) whose draft
        should be published.
    :returns: API response dict.
    :raises ValueError: If the URL cannot be parsed, no drafts are found,
        or the draft has no content.
    """
    from zhihu_cli.content.handlers import get_type_and_id
    from zhihu_cli.content.handlers.draft import get_draft, list_drafts
    from zhihu_cli.content.utils.html2markdown import converter

    object_type, object_id = get_type_and_id(url)
    if not object_type or not object_id:
        raise ValueError(f"Cannot parse Zhihu URL: {url}")

    type_map = {"questions": "question", "answers": "answer", "articles": "article"}
    api_type = type_map.get(object_type)
    if not api_type:
        raise ValueError(f"Drafts not supported for type: {object_type}")

    draft_object_id = object_id
    if object_type == "answers" and "/" in object_id:
        draft_object_id = object_id.split("/")[1]

    drafts = list_drafts(api_type, draft_object_id)
    if not drafts:
        raise ValueError(f"No drafts found for: {url}")

    latest = drafts[0]
    draft_detail = get_draft(latest["id"], latest.get("version_type", "current"))

    draft_data = draft_detail.get("draft", {})
    html_content = draft_data.get("content", "")
    if not html_content:
        raise ValueError("Draft has no content")

    markdown_content = converter.convert(html_content)

    if object_type == "questions":
        return publish_answer(object_id, markdown_content, html=html_content)
    elif object_type == "answers":
        answer_id = object_id.split("/")[1] if "/" in object_id else object_id
        return modify_answer(answer_id, markdown_content, html=html_content)
    elif object_type == "articles":
        title = draft_data.get("title") or draft_detail.get("title", "")
        if not title:
            raise ValueError("Draft has no title")
        return modify_article(object_id, title, markdown_content, html=html_content)
    else:
        raise ValueError(f"Unsupported type for draft publishing: {object_type}")


def publish_article(title: str, content: str, *, html: str | None = None) -> dict[str, Any]:
    trace_id = ",".join([str(x) for x in generate_trace_context()])
    if html is None:
        html = markdown2html(content, scene="article")
    text_length = calculate_text_length(html)

    pc_business_params = json.dumps(
        {
            "disclaimer_type": "none",
            "disclaimer_status": "close",
            "table_of_contents_enabled": False,
            "content": content,
            "title": title,
            "commercial_report_info": {"commercial_types": []},
            "commercial_zhitask_bind_info": None,
            "canReward": False,
        },
        ensure_ascii=False,
    )

    resp = session.post(
        PUBLISH_API,
        json={
            "action": "article",
            "data": {
                "publish": {"traceId": trace_id},
                "extra_info": {
                    "publisher": "pc",
                    "pc_business_params": pc_business_params,
                },
                "hybrid": {"html": html, "textLength": text_length},
                "title": {"title": title},
            },
        },
    )
    return resp.json()


def modify_article(article_id: str, title: str, content: str, *, html: str | None = None) -> dict[str, Any]:
    trace_id = ",".join([str(x) for x in generate_trace_context()])
    if html is None:
        html = markdown2html(content, scene="article")
    text_length = calculate_text_length(html)

    pc_business_params = json.dumps(
        {
            "disclaimer_type": "none",
            "disclaimer_status": "close",
            "table_of_contents_enabled": False,
            "content": content,
            "title": title,
            "commercial_report_info": {"commercial_types": []},
            "commercial_zhitask_bind_info": None,
            "canReward": False,
        },
        ensure_ascii=False,
    )

    resp = session.post(
        PUBLISH_API,
        json={
            "action": "article",
            "data": {
                "publish": {"traceId": trace_id},
                "extra_info": {
                    "publisher": "pc",
                    "pc_business_params": pc_business_params,
                },
                "draft": {"disabled": 1, "id": article_id, "isPublished": True},
                "hybrid": {"html": html, "textLength": text_length},
                "title": {"title": title},
            },
        },
    )
    return resp.json()
