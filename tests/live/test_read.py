"""Structural live tests for the read-only public API.

Live data changes by the minute, so these tests assert **structural invariants
only** — "the hot list is a non-empty list of titled items", "an article's
metadata ID matches the ID in its URL" — never exact titles, counts, or bodies.
A test that pins real content would fail on someone else's account, in another
region, or tomorrow.

Every anchor comes from an environment variable (see :mod:`tests.live.anchors`);
an absent anchor skips that test instead of failing.
"""

from __future__ import annotations

import pytest

from .anchors import article_url, question_url, user_url

pytestmark = pytest.mark.live


def test_hot_list(live_session: object) -> None:
    """The anonymous hot list is a non-empty list of titled, linked items.

    :param live_session: Opens the direct session scope for this test.
    """
    from zhihu_cli.api import fetch_hot_list

    items = fetch_hot_list(limit=5)

    assert isinstance(items, list)
    assert items, "the hot list came back empty"
    for item in items:
        assert isinstance(item, dict)
        assert item.get("title", "").strip(), f"hot item without a title: {item!r}"
        assert item.get("url") or item.get("target_id"), f"hot item without a url or id: {item!r}"


def test_article_roundtrip(live_session: object) -> None:
    """An article scrapes to metadata whose ID matches its URL, plus Markdown.

    :param live_session: Opens the direct session scope for this test.
    """
    from zhihu_cli.api import scrape_article
    from zhihu_cli.content.handlers import get_type_and_id

    url = article_url()
    item_type, expected_id = get_type_and_id(url)
    assert item_type == "articles", f"{url!r} is not an article URL"

    metadata, markdown = scrape_article(url)

    assert str(metadata.get("id")) == expected_id
    assert metadata.get("title", "").strip(), "article metadata has no title"
    assert markdown.strip(), "article converted to empty Markdown"


def test_question_data(live_session: object) -> None:
    """A question yields its metadata and at least one answer with a numeric ID.

    :param live_session: Opens the direct session scope for this test.
    """
    from zhihu_cli.api import scrape_answers, scrape_question_data
    from zhihu_cli.content.handlers import get_type_and_id

    url = question_url()
    item_type, expected_id = get_type_and_id(url)
    assert item_type == "questions", f"{url!r} is not a question URL"
    assert expected_id is not None and expected_id.isdigit()

    metadata, _detail = scrape_question_data(url)

    assert str(metadata.get("id")) == expected_id
    assert metadata.get("title", "").strip(), "question metadata has no title"
    assert isinstance(metadata.get("answer_count"), int)

    answers = list(scrape_answers(metadata, limit=1, max_items=1))
    assert answers, "the question anchor has no answers"
    for answer in answers:
        assert str(answer.get("id", "")).isdigit(), f"answer id is not numeric: {answer.get('id')!r}"


def test_member_profile(live_session: object) -> None:
    """A member anchor resolves to a profile with a name and a url_token.

    :param live_session: Opens the direct session scope for this test.
    """
    from zhihu_cli.api import fetch_member_profile
    from zhihu_cli.commands._helpers import _extract_url_token

    token = _extract_url_token(user_url())
    profile = fetch_member_profile(token)

    assert profile is not None, f"no profile returned for {token!r}"
    assert profile.get("name", "").strip(), "profile has no name"
    assert profile.get("url_token", "").strip(), "profile has no url_token"


def test_pagination_invariants(live_session: object) -> None:
    """A paginated answer stream has unique IDs and respects the reported total.

    :param live_session: Opens the direct session scope for this test.
    """
    from zhihu_cli.api import scrape_answers, scrape_question_data

    metadata, _detail = scrape_question_data(question_url())
    stream = scrape_answers(metadata, limit=5, max_items=10)

    items = list(stream)
    assert items, "the answer stream yielded nothing"

    ids = [str(item.get("id", "")) for item in items]
    assert len(ids) == len(set(ids)), f"duplicate answer ids across pages: {ids!r}"

    totals = getattr(stream, "totals", None)
    if totals is not None:
        assert totals >= len(items), f"API reported {totals} items but yielded {len(items)}"
