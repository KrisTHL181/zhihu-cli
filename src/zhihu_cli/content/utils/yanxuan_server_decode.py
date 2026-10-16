"""Decode the Yanxuan font-substitution cipher with Zhihu's own endpoint.

:mod:`zhihu_cli.content.utils.yanxuan_font` undoes the cipher locally by
rendering each scrambled glyph and matching it against a checked-in reference
table.  This module is the table-free alternative: it hands the still-obfuscated
text to ``POST https://api.zhihu.com/remix-web/paid_columns/manuscript/decode``
and reads the decoded text back.

The request body is JSON ``{"font_type": ..., "content": ...}`` encrypted with
:class:`~zhihu_cli.content.utils.zse.ZSECipher` — the same cipher that signs
``x-zse-96`` — except that the header seed byte is :data:`ZSE_SEED` (``91``)
rather than the cipher's default ``12``.  Two constraints shape the code:

* The reply is capped at :data:`CHUNK_SIZE` (200) characters regardless of how
  much is sent, so long bodies must be split.
* The request **must not** carry ``x-zse-96``; with it the endpoint answers
  HTTP 400, without it HTTP 200.  There is no signing bypass inside
  :class:`~zhihu_cli.content.handlers._session_core.ZhihuSession`, so a raw
  session is built here instead.

Paragraphs are rejoined with **no separator** and chunked at
:data:`CHUNK_SIZE`, then restored by slicing the decoded text at the original
cumulative paragraph lengths.  The substitution is length-preserving, so this
needs no assumption about how the server treats whitespace — a newline-joined
assembly would additionally require the server to preserve newlines, which has
never been observed.

The server does not return the same codepoints as the local path: on a real
section it answers with the CJK radical-block look-alikes (``⽩`` U+2F69, ``⻓``
U+2ED3, ``⼩`` U+2F29, …) where the local path yields the unified ideograph.  Both
paths therefore fold the decoded string through
:func:`~zhihu_cli.content.utils.yanxuan_font.normalize_lookalikes` — the local one
inside ``decode_manuscript_report``, this one at the end of
:func:`decode_texts` — so ``--server-decode`` is byte-identical to the default
path.  Folding does not reintroduce the staleness risk that motivated the
table-free design: the look-alike map is stable Unicode data (an NFKC pass over
the compatibility blocks plus a fixed radical list), not the glyph raster
matching that goes stale whenever Zhihu changes the typeface.

This module must stay importable without ``curl_cffi`` or ``lxml``: the heavy
import happens inside :func:`_build_session`, so :func:`decode_texts` is the
only name the handler needs and the CLI startup path never pays for it.
"""

from __future__ import annotations

import json
import os
from typing import Any

from zhihu_cli.content.utils.wait import wait
from zhihu_cli.content.utils.zse import ZSECipher

#: Endpoint that performs the decode server-side.
DECODE_ENDPOINT = "https://api.zhihu.com/remix-web/paid_columns/manuscript/decode"

#: Maximum number of characters the endpoint will decode in one reply.
CHUNK_SIZE = 200

#: Header seed byte this endpoint expects, as opposed to the cipher default.
ZSE_SEED = 91

#: App-flavoured ``x-zse-83`` value the endpoint accepts.
ZSE83 = "3_3.0"

#: TLS impersonation profile, i.e. what ``get_browser(APP_UA)`` returns.
#: Hardcoded rather than derived so that ``_session_core`` — which pulls in
#: ``curl_cffi`` *and* ``lxml`` — stays off this path.
IMPERSONATE = "chrome_android"

#: App User-Agent the endpoint expects.  This will rot; see :data:`UA_ENV`.
APP_UA = (
    "ZhihuHybrid com.zhihu.android/Futureve/11.9.0 Mozilla/5.0 (Linux; Android 14; "
    "M2102K1G Build/UKQ1.240624.001; wv) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Version/4.0 Chrome/153.0.8010.36 Mobile Safari/537.36"
)

#: Environment variable overriding :data:`APP_UA`, read at call time.
UA_ENV = "ZHIHU_YANXUAN_DECODE_UA"


class ServerDecodeError(RuntimeError):
    """Raised when the server-side decode cannot be trusted.

    Raised for a missing ``font_type``, a transport or HTTP failure, an
    unparseable reply, and for any violation of the length invariants that make
    the paragraph reassembly safe.  The caller is expected to catch it and fall
    back to the local glyph table.
    """


def _build_session() -> Any:
    """Build the unsigned session this endpoint requires.

    Deliberately a raw ``curl_cffi`` session rather than a
    :class:`ZhihuSession`: every Zhihu host is signed automatically, and a
    present ``x-zse-96`` makes this endpoint answer HTTP 400.  The heavy import
    is function-local (the ``daemon/proxy.py`` convention) so that importing
    this module stays cheap.

    This is the promotion point if a second unsigned endpoint ever appears.

    :returns: A session carrying the active profile's Cookie, the app
        User-Agent and ``x-zse-83``, but no signature headers.
    """
    from curl_cffi import requests as curl_requests

    from zhihu_cli.content.handlers.cache_manager import cache_manager

    headers = cache_manager.load_headers()
    session = curl_requests.Session(impersonate=IMPERSONATE)
    session.headers.update(
        {
            "User-Agent": os.environ.get(UA_ENV) or APP_UA,
            "x-zse-83": ZSE83,
            "Cookie": headers.get("Cookie", ""),
        }
    )
    return session


def _chunk(joined: str) -> list[str]:
    """Split *joined* into :data:`CHUNK_SIZE`-sized chunks.

    :param joined: The concatenated paragraphs.
    :returns: The chunks, in order; empty when *joined* is empty.
    """
    return [joined[i : i + CHUNK_SIZE] for i in range(0, len(joined), CHUNK_SIZE)]


def _decode_chunks(chunks: list[str], font_type: str, timeout: float) -> list[str]:
    """Decode every chunk through the endpoint, in order.

    Sequential on purpose: risk-control behaviour under parallel unsigned POSTs
    is unproven, and a mid-run 403 is indistinguishable from a hard failure.

    :param chunks: The obfuscated chunks to decode.
    :param font_type: The response's ``font.type``.
    :param timeout: Per-request timeout in seconds.
    :returns: One decoded string per input chunk.
    :raises ServerDecodeError: On any transport, HTTP or parse failure, or if
        the endpoint changes the length of a chunk.
    """
    if not chunks:
        return []

    session = _build_session()
    cipher = ZSECipher()
    decoded: list[str] = []

    for chunk in chunks:
        payload = json.dumps({"font_type": font_type, "content": chunk}, ensure_ascii=False)
        body = cipher.encrypt(payload, seed=ZSE_SEED)

        wait(0.3)
        try:
            resp = session.post(
                DECODE_ENDPOINT,
                data=body,
                headers={
                    "Content-Type": "text/plain;charset=UTF-8",
                    # Required: without an app-shaped ``x-requested-with`` the
                    # endpoint answers HTTP 403 regardless of the ciphertext.
                    "x-requested-with": "Fetch",
                    "origin": "https://www.zhihu.com",
                    "referer": "https://www.zhihu.com/",
                    "accept": "*/*",
                },
                timeout=timeout,
            )
        except Exception as exc:
            raise ServerDecodeError(f"request failed: {exc}") from exc

        if resp.status_code != 200:
            raise ServerDecodeError(f"endpoint returned HTTP {resp.status_code}")

        try:
            out = resp.json().get("content", "")
        except Exception as exc:
            raise ServerDecodeError(f"unparseable reply: {exc}") from exc

        if len(out) != len(chunk):
            raise ServerDecodeError(f"length drift {len(chunk)} -> {len(out)}")

        decoded.append(out)

    return decoded


def decode_texts(paragraphs: list[str], font_type: str, *, timeout: float = 20.0) -> list[str]:
    """Decode *paragraphs* with Zhihu's own decode endpoint.

    All-or-nothing: every failure propagates as :class:`ServerDecodeError` so
    the caller can fall back to the local glyph table without ever seeing a
    partially decoded body.  The paragraphs are joined with no separator and
    restored by slicing at the original cumulative lengths; see the module
    docstring for why.

    :param paragraphs: Body paragraphs, still scrambled by the font cipher.
    :param font_type: Value of the response's ``font.type``.
    :param timeout: Per-request timeout in seconds.
    :returns: The paragraphs with substituted characters resolved, one per
        input paragraph, folded through
        :func:`~zhihu_cli.content.utils.yanxuan_font.normalize_lookalikes` so
        that they match the local path byte for byte.
    :raises ServerDecodeError: If *font_type* is empty, or if any request,
        reply or length invariant fails.
    """
    if not font_type:
        raise ServerDecodeError("response carries no font.type to send")

    joined = "".join(paragraphs)
    if not joined:
        return list(paragraphs)

    chunks = _chunk(joined)
    decoded = _decode_chunks(chunks, font_type, timeout)
    whole = "".join(decoded)

    if len(whole) != len(joined):
        raise ServerDecodeError("total length changed")

    # Fold the look-alikes exactly as the local path does, so that both paths
    # agree byte for byte.  Imported here rather than at module level so that
    # this module stays importable without the glyph-table stack.
    from zhihu_cli.content.utils.yanxuan_font import normalize_lookalikes

    folded = normalize_lookalikes(whole)
    if len(folded) != len(whole):
        raise ServerDecodeError("look-alike folding changed the length")

    # Slice the decoded text back into paragraphs at the original boundaries.
    out: list[str] = []
    cursor = 0
    for paragraph in paragraphs:
        end = cursor + len(paragraph)
        out.append(folded[cursor:end])
        cursor = end
    return out
