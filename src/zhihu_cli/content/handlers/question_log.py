from typing import Any

from bs4 import BeautifulSoup

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

    soup = BeautifulSoup(resp.text, "html.parser")
    entries = []

    for item in soup.find_all("div", class_="zm-item"):
        log_id = item.get("id", "").replace("logitem-", "")

        action_div = item.find("div")
        user = None
        user_url = None
        action = ""

        if action_div:
            user_link = action_div.find("a")
            if user_link:
                user = user_link.get_text(strip=True)
                user_url = user_link.get("href", "")
            # Action text is the span after the user link, or the whole text
            action_span = action_div.find("span")
            if action_span:
                action = action_span.get_text(strip=True)
            else:
                raw_text = action_div.get_text(" ", strip=True)
                if user:
                    action = raw_text.replace(user, "").strip()

        detail = None
        detail_div = item.find("div", class_="zg-item-log-detail")
        if detail_div:
            detail = detail_div.get_text(strip=True) or None

        time_str = ""
        meta_div = item.find("div", class_="zm-item-meta")
        if meta_div:
            time_tag = meta_div.find("time")
            if time_tag:
                time_str = time_tag.get("datetime", time_tag.get_text(strip=True))

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
