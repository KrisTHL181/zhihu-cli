"""Tests for the input parser behind ``zhihu auth paste``."""

from __future__ import annotations

import pytest

from zhihu_cli.commands.auth import _parse_pasted_headers

COOKIE = "z_c0=abc; _xsrf=xyz"


def test_curl_with_colon_headers() -> None:
    """The original cURL form keeps working."""
    text = (
        "curl 'https://www.zhihu.com/api/v4/me' \\\n"
        f"  -H 'Cookie: {COOKIE}' \\\n"
        "  -H 'User-Agent: Mozilla/5.0' \\\n"
        "  -H 'accept-encoding: gzip'"
    )

    assert _parse_pasted_headers(text) == {"Cookie": COOKIE, "User-Agent": "Mozilla/5.0"}


def test_curl_with_equals_headers() -> None:
    """``-H 'Key=Value'`` splits like ``-H 'Key: Value'``."""
    text = f"curl 'https://www.zhihu.com' -H 'Cookie={COOKIE}' -H \"x-zse-96=2.0_abc=\""

    assert _parse_pasted_headers(text) == {"Cookie": COOKIE, "x-zse-96": "2.0_abc="}


def test_key_value_lines() -> None:
    """One ``Key=Value`` header per line."""
    text = f"Cookie={COOKIE}\nUser-Agent=Mozilla/5.0\nx-zse-96=2.0_abc="

    assert _parse_pasted_headers(text) == {
        "Cookie": COOKIE,
        "User-Agent": "Mozilla/5.0",
        "x-zse-96": "2.0_abc=",
    }


def test_key_colon_lines() -> None:
    """Colon-separated lines are accepted too."""
    assert _parse_pasted_headers(f"Cookie: {COOKIE}\nUser-Agent: Mozilla/5.0") == {
        "Cookie": COOKIE,
        "User-Agent": "Mozilla/5.0",
    }


def test_bare_cookie_string() -> None:
    """A lone cookie string becomes the ``Cookie`` header."""
    assert _parse_pasted_headers(COOKIE) == {"Cookie": COOKIE}


def test_bare_cookie_string_across_lines() -> None:
    """Cookie pairs may be separated by newlines."""
    assert _parse_pasted_headers("z_c0=abc;\n_xsrf=xyz") == {"Cookie": COOKIE}


def test_ampersand_separated_cookies() -> None:
    """Query-string cookies normalize to ``; `` separators."""
    assert _parse_pasted_headers("z_c0=abc&_xsrf=xyz") == {"Cookie": COOKIE}


def test_cookie_pairs_mixed_with_headers() -> None:
    """Cookie-shaped lines fold into ``Cookie`` next to real headers."""
    text = f"Cookie={COOKIE}\nUser-Agent=Mozilla/5.0\nd_c0=def"

    assert _parse_pasted_headers(text) == {"Cookie": f"{COOKIE}; d_c0=def", "User-Agent": "Mozilla/5.0"}


def test_lowercase_names_are_canonicalized() -> None:
    """The rest of the codebase reads ``Cookie`` / ``User-Agent`` case-sensitively."""
    assert _parse_pasted_headers("cookie=z_c0=abc\nuser-agent=Mozilla") == {
        "Cookie": "z_c0=abc",
        "User-Agent": "Mozilla",
    }


def test_ignored_lines() -> None:
    """cURL flags, URLs, and stale transport headers are skipped."""
    text = f"curl 'https://www.zhihu.com/api/v4/me?x=1' \\\n --compressed -X GET\nCookie={COOKIE}"
    text += "\naccept-encoding: br\nContent-Length: 12"

    assert _parse_pasted_headers(text) == {"Cookie": COOKIE}


@pytest.mark.parametrize("text", ["", "   \n", "hello world", "just some prose"])
def test_unparseable_input(text: str) -> None:
    """Nothing parseable yields an empty mapping."""
    assert _parse_pasted_headers(text) == {}
