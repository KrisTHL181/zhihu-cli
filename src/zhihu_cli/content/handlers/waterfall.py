import time
import warnings
from collections.abc import Callable, Iterable
from typing import Any

from zhihu_cli.content.handlers.requests import session


def stream_handler(
    initial_url: str,
    parser: Callable[[dict[str, Any]], Iterable[Any]],
    extract_next: Callable[[dict[str, Any]], str | None] | None = None,
    delay: float | None = None,
) -> Iterable[Any]:
    """Paginate a Zhihu API endpoint, yielding parsed items one by one.

    Args:
        initial_url: The first page URL (includes ``offset=0``).
        parser: Called on each page's JSON body; must yield zero or more
            parsed items per page.
        extract_next: Optional custom pagination resolver.  When omitted,
            ``paging.next`` / ``paging.is_end`` is used.
        delay: Seconds to sleep between pages.  ``None`` (the default)
            auto-detects: 0.0 when authenticated, 1.0 otherwise.
    """
    current_url = initial_url
    if delay is None:
        from zhihu_cli.content.handlers.following import get_my_url_token

        delay = 0.0 if get_my_url_token() is not None else 1.0

    api_totals = 0
    yielded_count = 0

    while current_url:
        resp = session.get(current_url)
        resp.raise_for_status()
        data = resp.json()

        paging = data.get("paging", {})
        # Capture totals from the first page that reports a non-zero value.
        if api_totals == 0:
            api_totals = paging.get("totals", 0) or 0

        for item in parser(data):
            yielded_count += 1
            yield item

        if extract_next:
            current_url = extract_next(data)
        else:
            if paging.get("is_end", True):
                current_url = None
            else:
                current_url = paging.get("next")

        if current_url:
            current_url = current_url.replace("http://", "https://")

        time.sleep(delay)

    # ── natural end of stream — check completeness ──────────────────────────
    if api_totals > 0 and yielded_count < api_totals:
        missing = api_totals - yielded_count
        warnings.warn(
            f"API reported {api_totals} total items but only {yielded_count} were returned (missing {missing}).",
            stacklevel=2,
        )
