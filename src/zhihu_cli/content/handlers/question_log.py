from typing import Any

from lxml import html as lxml_html

from zhihu_cli.content.handlers.requests import session

LOG_URL = "https://www.zhihu.com/question/{question_id}/log"


def fetch_question_log(question_id: str) -> list[dict[str, Any]]:
    """Fetch and parse the question edit history page.

    Returns a list of log entries, each with:
      - log_id: internal log item ID
      - user: display name of the editor
      - user_url: relative profile URL (or None)
      - action: action description (e.g. "编辑了问题")
      - detail: change detail text (or None)
      - time: timestamp string
    """
    resp = session.get(LOG_URL.format(question_id=question_id))
    resp.raise_for_status()

    doc = lxml_html.fromstring(resp.text)
    entries = []

    for item in doc.cssselect("div.zm-item"):
        log_id = item.get("id", "").replace("logitem-", "")

        action_div = item.find("div")
        user = None
        user_url = None
        action = ""

        if action_div is not None:
            user_link = action_div.find("a")
            if user_link is not None:
                user = user_link.text_content().strip()
                user_url = user_link.get("href", "")
            # Action text is the span after the user link, or the whole text
            action_span = action_div.find("span")
            if action_span is not None:
                action = action_span.text_content().strip()
            else:
                raw_text = action_div.text_content().strip()
                if user:
                    action = raw_text.replace(user, "").strip()

        detail = None
        detail_divs = item.cssselect("div.zg-item-log-detail")
        if detail_divs:
            detail = detail_divs[0].text_content().strip() or None

        time_str = ""
        meta_divs = item.cssselect("div.zm-item-meta")
        if meta_divs:
            meta_div = meta_divs[0]
            time_tag = meta_div.find("time")
            if time_tag is not None:
                time_str = time_tag.get("datetime", time_tag.text_content().strip())

        entries.append(
            {
                "log_id": log_id,
                "user": user,
                "user_url": user_url or None,
                "action": action,
                "detail": detail,
                "time": time_str,
            }
        )

    return entries
