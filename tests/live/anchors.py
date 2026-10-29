"""Environment-driven anchors for the live suite.

Live tests need real Zhihu URLs and usernames, but committing them would publish
third-party content identifiers in the repository history — exactly what
``chore(privacy)`` removed.  Every anchor therefore comes from an environment
variable, read **at call time** rather than import time so a session can set
them late, and its absence skips the dependent test with the variable name to
set.
"""

from __future__ import annotations

import os

import pytest

__all__ = ["answer_url", "article_url", "question_url", "user_url"]


def _require(var: str) -> str:
    """Return the value of *var*, skipping the test when it is unset or blank.

    :param var: Environment variable naming the anchor.
    :returns: The stripped value.
    """
    value = os.environ.get(var, "").strip()
    if not value:
        pytest.skip(f"set {var} to run this test")
    return value


def article_url() -> str:
    """Return the article URL anchor from ``ZHIHU_TEST_ARTICLE_URL``.

    :returns: A Zhihu article URL.
    """
    return _require("ZHIHU_TEST_ARTICLE_URL")


def question_url() -> str:
    """Return the question URL anchor from ``ZHIHU_TEST_QUESTION_URL``.

    :returns: A Zhihu question URL.
    """
    return _require("ZHIHU_TEST_QUESTION_URL")


def answer_url() -> str:
    """Return the answer URL anchor from ``ZHIHU_TEST_ANSWER_URL``.

    :returns: A Zhihu answer URL.
    """
    return _require("ZHIHU_TEST_ANSWER_URL")


def user_url() -> str:
    """Return the member anchor from ``ZHIHU_TEST_USER``.

    The value may be a ``zhihu.com/people/...`` URL or a bare ``url_token``.

    :returns: A member URL or token.
    """
    return _require("ZHIHU_TEST_USER")
