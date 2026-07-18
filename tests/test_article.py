from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from zhihu_cli.content.handlers import article
from zhihu_cli.content.handlers import requests as request_helpers


class ArticleHandlerTests(unittest.TestCase):
    def test_extract_article_id(self) -> None:
        cases = {
            "2047966979009651330": "2047966979009651330",
            "https://zhuanlan.zhihu.com/p/2047966979009651330": "2047966979009651330",
            "https://zhuanlan.zhihu.com/p/2047966979009651330?utm_source=test": "2047966979009651330",
            "https://www.zhihu.com/api/v4/articles/2047966979009651330": "2047966979009651330",
        }
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(article.extract_article_id(value), expected)

    def test_fetch_article_item_prefers_api(self) -> None:
        api_item = {"id": "123", "title": "API title", "content": "<p>API body</p>"}
        with (
            patch.object(article, "fetch_json", return_value=api_item) as fetch_json,
            patch.object(article, "fetch_page_html") as fetch_page_html,
        ):
            self.assertIs(article.fetch_article_item("https://zhuanlan.zhihu.com/p/123"), api_item)

        fetch_json.assert_called_once_with("https://www.zhihu.com/api/v4/articles/123")
        fetch_page_html.assert_not_called()

    def test_fetch_article_item_falls_back_to_ssr_page(self) -> None:
        page_item = {"id": "123", "title": "Page title", "content": "<p>Page body</p>"}
        with (
            patch.object(article, "fetch_json", side_effect=RuntimeError("API unavailable")),
            patch.object(article, "fetch_page_html", return_value="<html></html>"),
            patch.object(article, "get_page_state", return_value={"articles": {"123": page_item}}),
        ):
            self.assertIs(article.fetch_article_item("https://zhuanlan.zhihu.com/p/123"), page_item)

    def test_fetch_article_item_reports_both_failures(self) -> None:
        with (
            patch.object(article, "fetch_json", side_effect=RuntimeError("API unavailable")),
            patch.object(article, "fetch_page_html", side_effect=RuntimeError("zh-zse-ck challenge")),
        ):
            with self.assertRaisesRegex(RuntimeError, "API unavailable.*zh-zse-ck challenge"):
                article.fetch_article_item("https://zhuanlan.zhihu.com/p/123")

    def test_scrape_article_supports_api_snake_case_counts(self) -> None:
        api_item = {
            "id": "123",
            "title": "API title",
            "content": "<p>API body</p>",
            "author": {"name": "Author"},
            "voteup_count": 7,
            "comment_count": 5,
            "favlists_count": 3,
        }
        with (
            patch.object(article, "fetch_article_item", return_value=api_item),
            patch.object(article.converter, "convert", return_value="API body"),
        ):
            metadata, markdown = article.scrape_article("https://zhuanlan.zhihu.com/p/123")

        self.assertEqual(metadata["stats"], {"voteup_count": 7, "comment_count": 5, "favlists_count": 3})
        self.assertEqual(metadata["url"], "https://zhuanlan.zhihu.com/p/123")
        self.assertEqual(markdown, "API body")


class RequestHelperTests(unittest.TestCase):
    def test_fetch_page_html_reports_zse_challenge(self) -> None:
        response = Mock(status_code=403, text='<meta id="zh-zse-ck">')
        session = Mock()
        session.get.return_value = response

        with patch.object(request_helpers, "_get_session", return_value=session):
            with self.assertRaisesRegex(RuntimeError, "zh-zse-ck risk-control challenge"):
                request_helpers.fetch_page_html("https://zhuanlan.zhihu.com/p/123")

        response.raise_for_status.assert_not_called()

    def test_fetch_page_html_checks_status(self) -> None:
        response = Mock(status_code=500, text="server error")
        response.raise_for_status.side_effect = RuntimeError("HTTP 500")
        session = Mock()
        session.get.return_value = response

        with patch.object(request_helpers, "_get_session", return_value=session):
            with self.assertRaisesRegex(RuntimeError, "HTTP 500"):
                request_helpers.fetch_page_html("https://www.zhihu.com/test")


if __name__ == "__main__":
    unittest.main()
