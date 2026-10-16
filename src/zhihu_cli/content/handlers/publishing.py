from __future__ import annotations

import json
from typing import Any

from zhihu_cli.content.handlers.requests import fetch_json, session
from zhihu_cli.content.utils import generate_trace_context
from zhihu_cli.content.utils.html2markdown import calculate_text_length
from zhihu_cli.content.utils.markdown2html import markdown2html

PUBLISH_API: str = "https://www.zhihu.com/api/v4/content/publish"
CONTENT_DRAFTS_API: str = "https://www.zhihu.com/api/v4/content/drafts"
TOPIC_API: str = "https://www.zhihu.com/api/v4/topics/{topic_id}"


def _create_content_draft(action: str) -> str:
    """Create an empty content draft and return its ``content_id``.

    New articles and pins must be published against a draft, and the draft
    has to exist before the publish call carries the body.

    :param action: Content type the draft is for, e.g. ``"article"`` or ``"pin"``.
    :returns: The draft's ``content_id``, as a string.
    :raises ValueError: If the response carries no ``content_id``.
    """
    resp = session.post(CONTENT_DRAFTS_API, json={"action": action})
    resp.raise_for_status()
    content_id = resp.json().get("data", {}).get("content_id", "")
    if not content_id:
        raise ValueError(f"Content draft created but no content_id returned (action={action!r})")
    return str(content_id)


def build_image_html(image_infos: list[dict[str, Any]]) -> str:
    """Build the ``<img>`` tags Zhihu expects for already-uploaded images.

    :param image_infos: Image dicts as returned by
        :func:`~zhihu_cli.content.handlers.upload_image.upload_image`.
    :returns: Concatenated ``<img>`` tags, ready to append to a content body.
    """
    return "".join(
        f'<img src="{info["src"]}" data-caption="" data-size="normal"'
        f' data-rawwidth="{info.get("width", 0)}" data-rawheight="{info.get("height", 0)}"'
        f' data-watermark="{info.get("watermark", "watermark")}"'
        f' data-original-src="{info.get("original_src", info["src"])}"'
        f' data-watermark-src="{info.get("watermark_src", "")}"'
        f' data-private-watermark-src=""/>'
        for info in image_infos
    )


def extract_published_id(resp: dict[str, Any]) -> str:
    """Pull the created content's ID out of a publish response.

    ``content/publish`` sometimes nests the real payload inside
    ``data.result`` as a JSON *string* rather than an object, so that is
    unwrapped before looking for ``id``.

    :param resp: Raw response dict from a publish call.
    :returns: The content ID, or an empty string when the response has none.
    """
    payload: Any = resp
    data = resp.get("data") if isinstance(resp, dict) else None
    if isinstance(data, dict):
        result = data.get("result")
        if isinstance(result, dict):
            payload = result
        elif isinstance(result, str) and result:
            try:
                payload = json.loads(result)
            except ValueError:
                payload = resp

    if isinstance(payload, dict):
        item_id = payload.get("id")
        if item_id:
            return str(item_id)
    if isinstance(data, dict) and data.get("id"):
        return str(data["id"])
    return ""


def publish_answer(
    question_id: str, content: str, *, html: str | None = None, text_length: int | None = None
) -> dict[str, Any]:
    """Publish a new answer to *question_id*.

    :param question_id: Zhihu question ID.
    :param content: Markdown content.
    :param html: Pre-converted HTML (avoids re-conversion).
    :param text_length: Server-provided word count from draft data.
        When omitted, computed client-side via :func:`calculate_text_length`.
    """
    trace_id = ",".join([str(x) for x in generate_trace_context()])
    if html is None:
        html = markdown2html(content, scene="answer")
    if text_length is None:
        text_length = calculate_text_length(html)

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
                "hybrid": {"html": html, "textLength": text_length},
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


def modify_answer(
    answer_id: str, content: str, *, html: str | None = None, text_length: int | None = None
) -> dict[str, Any]:
    """Modify an existing *answer_id*.

    :param answer_id: Zhihu answer ID.
    :param content: Markdown content.
    :param html: Pre-converted HTML (avoids re-conversion).
    :param text_length: Server-provided word count from draft data.
        When omitted, computed client-side via :func:`calculate_text_length`.
    """
    trace_id = ",".join([str(x) for x in generate_trace_context()])
    if html is None:
        html = markdown2html(content, scene="answer")
    if text_length is None:
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

    # Use server-provided word count from draft data when available;
    # falls back to client-side calculate_text_length inside each publish fn.
    content_words = draft_data.get("content_words") or draft_detail.get("content_words")
    text_length = int(content_words) if content_words is not None else None

    markdown_content = converter.convert(html_content)

    if object_type == "questions":
        return publish_answer(object_id, markdown_content, html=html_content, text_length=text_length)
    elif object_type == "answers":
        answer_id = object_id.split("/")[1] if "/" in object_id else object_id
        return modify_answer(answer_id, markdown_content, html=html_content, text_length=text_length)
    elif object_type == "articles":
        title = draft_data.get("title") or draft_detail.get("title", "")
        if not title:
            raise ValueError("Draft has no title")
        return modify_article(object_id, title, markdown_content, html=html_content, text_length=text_length)
    else:
        raise ValueError(f"Unsupported type for draft publishing: {object_type}")


def publish_article(
    title: str, content: str, *, html: str | None = None, text_length: int | None = None
) -> dict[str, Any]:
    """Publish a new article.

    :param title: Article title.
    :param content: Markdown content.
    :param html: Pre-converted HTML (avoids re-conversion).
    :param text_length: Server-provided word count from draft data.
        When omitted, computed client-side via :func:`calculate_text_length`.
    """
    trace_id = ",".join([str(x) for x in generate_trace_context()])
    if html is None:
        html = markdown2html(content, scene="article")
    if text_length is None:
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
                "draft": {"disabled": 1, "id": _create_content_draft("article")},
                "hybrid": {"html": html, "textLength": text_length},
                "title": {"title": title},
            },
        },
    )
    return resp.json()


def modify_article(
    article_id: str, title: str, content: str, *, html: str | None = None, text_length: int | None = None
) -> dict[str, Any]:
    """Modify an existing article.

    :param article_id: Zhihu article ID.
    :param title: Article title.
    :param content: Markdown content.
    :param html: Pre-converted HTML (avoids re-conversion).
    :param text_length: Server-provided word count from draft data.
        When omitted, computed client-side via :func:`calculate_text_length`.
    """
    trace_id = ",".join([str(x) for x in generate_trace_context()])
    if html is None:
        html = markdown2html(content, scene="article")
    if text_length is None:
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


def publish_pin(
    title: str,
    content: str,
    *,
    image_infos: list[dict[str, Any]] | None = None,
    html: str | None = None,
    text_length: int | None = None,
) -> dict[str, Any]:
    """Publish a new pin (想法).

    :param title: Pin title. Zhihu stores it alongside the body but does not
        render it on the pin page; the publish API still requires it.
    :param content: Markdown body. May be empty for an image-only pin.
    :param image_infos: Image dicts from
        :func:`~zhihu_cli.content.handlers.upload_image.upload_image`. Each is
        appended to the body and registered as pin media.
    :param html: Pre-converted HTML body (avoids re-conversion).
    :param text_length: Character count of the visible text. When omitted,
        computed client-side via :func:`calculate_text_length`.
    """
    trace_id = ",".join([str(x) for x in generate_trace_context()])
    if html is None:
        html = markdown2html(content, scene="answer") if content.strip() else ""
    if image_infos:
        html += build_image_html(image_infos)
    if text_length is None:
        text_length = calculate_text_length(html)

    data: dict[str, Any] = {
        "publish": {"traceId": trace_id},
        "commentsPermission": {"comment_permission": "all"},
        "extra_info": {"view_permission": "all", "publisher": "pc"},
        "draft": {"disabled": 1, "id": _create_content_draft("pin")},
        "title": {"title": title},
        "hybrid": {"html": html, "textLength": text_length},
    }
    if image_infos:
        data["media"] = {
            "medias": [
                {
                    "image": {
                        "width": info.get("width", 0),
                        "height": info.get("height", 0),
                        "url": info["src"],
                        "originalUrl": info.get("original_src", info["src"]),
                        "watermark": info.get("watermark", "watermark"),
                        "watermarkUrl": info.get("watermark_src", ""),
                    }
                }
                for info in image_infos
            ]
        }

    resp = session.post(PUBLISH_API, json={"action": "pin", "data": data})
    return resp.json()


def resolve_topic(topic_id: str) -> dict[str, str]:
    """Resolve a topic id into the topic object the publish API expects.

    ``--topic`` only carries an id, while ``content/publish`` wants the
    topic's *name* next to its token, so the name is looked up here.  A
    topic that cannot be resolved raises rather than being dropped: a typo
    in ``--topic`` must not silently publish an untagged question.

    :param topic_id: Zhihu topic id or ``url_token``, e.g. ``"19551706"``.
    :returns: ``{"topic_token", "topic_name", "topic_id"}``, ready to go
        into :func:`_build_question_payload`.
    :raises ValueError: If the topic cannot be fetched or carries no name.
    """
    try:
        topic = fetch_json(TOPIC_API.format(topic_id=topic_id))
        name = topic.get("name", "") if isinstance(topic, dict) else ""
    except Exception as e:
        # Broad on purpose: a 404, a risk-control block and a connection
        # error all mean the same thing here — this topic is unknown.
        raise ValueError(f"Unknown topic id: {topic_id!r}") from e

    if not name:
        raise ValueError(f"Unknown topic id: {topic_id!r}")
    return {"topic_token": topic_id, "topic_name": str(name), "topic_id": ""}


def _build_question_payload(
    title: str, html: str, topics: list[dict[str, str]], *, text_length: int | None = None
) -> dict[str, Any]:
    """Build the ``content/publish`` body for a new question.

    Kept separate from :func:`create_question` so the payload — the part
    that has to be exactly right — can be asserted without any network.
    Two details are load-bearing and easy to "fix" back into breakage:

    * ``extra_info`` must **not** carry a ``publisher`` key.  The server
      branches on that field's value, and ``"pc"`` sends it down a path
      that never parses the title, answering with a misleading
      "问题标题长度不符合要求" for every title.
    * ``topic.topics`` must hold topic *objects*, not bare token strings.

    :param title: Question title.
    :param html: Body HTML for ``hybrid``.  When empty the ``hybrid`` key
        is omitted entirely — that is the shape the mobile app sends for a
        question with no detail.
    :param topics: Topic objects from :func:`resolve_topic`.
    :param text_length: Pre-computed word count.  When omitted it is
        measured from *html*; pass it when the body had media appended
        after the count was taken.
    :returns: The full request body, ``{"action": "question", "data": ...}``.
    """
    data: dict[str, Any] = {
        "title": {"title": title},
        "topic": {"topics": topics},
        "extra_info": {
            "extra_tag": "fakeurl://publisher_no_animation",
            "publish_type": "question",
            "editState": "true",
        },
        "questionConfig": {"type": "0"},
        "draft": {"disabled": 1},
    }
    if html:
        data["hybrid"] = {
            "html": html,
            "textLength": calculate_text_length(html) if text_length is None else text_length,
        }
    return {"action": "question", "data": data}


def create_question(
    title: str,
    detail: str = "",
    *,
    topic_ids: list[str] | None = None,
    image_infos: list[dict[str, Any]] | None = None,
    html: str | None = None,
) -> dict[str, Any]:
    """Create a new question (提问).

    Both the plain and the with-images cases go through
    ``content/publish`` with ``action="question"``; ``/api/v4/questions``
    is not a usable creation route, and any payload carrying
    ``extra_info.publisher`` makes the server skip parsing the title (see
    :func:`_build_question_payload`).  *topic_ids* are resolved to topic
    objects via :func:`resolve_topic` before publishing, so an unknown
    topic aborts the request instead of posting an untagged question.

    Business failures — including the daily question cap — come back as
    HTTP 400 with a JSON body and are returned as-is, so the caller can
    print the server's own message.

    :param title: Question title.
    :param detail: Markdown question detail (补充说明). Optional.
    :param topic_ids: Topic ids or ``url_token``s to tag the question with.
    :param image_infos: Image dicts from
        :func:`~zhihu_cli.content.handlers.upload_image.upload_image`.
    :param html: Pre-converted HTML detail (avoids re-conversion).
    :returns: API response dict.
    :raises ValueError: If any topic id cannot be resolved.
    """
    if html is None:
        html = markdown2html(detail, scene="answer") if detail.strip() else ""

    # The word count has always covered the detail text only, so it is
    # measured before the image tags are appended to the body.
    text_length = calculate_text_length(html)
    if image_infos:
        html += build_image_html(image_infos)

    # dict.fromkeys dedupes while preserving order, so a repeated --topic
    # costs one lookup rather than one request per occurrence.
    topics = [resolve_topic(topic_id) for topic_id in dict.fromkeys(topic_ids or [])]

    resp = session.post(PUBLISH_API, json=_build_question_payload(title, html, topics, text_length=text_length))
    return resp.json()
