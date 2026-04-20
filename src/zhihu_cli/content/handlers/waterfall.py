from typing import Iterable, Callable, Dict, Any, Optional
from .requests import session
import time

def stream_handler(
    initial_url: str,
    parser: Callable[[Dict[str, Any]], Iterable[Any]],
    extract_next: Optional[Callable[[Dict[str, Any]], Optional[str]]] = None,
    delay: float = 1.0
) -> Iterable:
    current_url = initial_url
    while current_url:
        resp = session.get(current_url)
        resp.raise_for_status()
        data = resp.json()

        yield from parser(data)

        if extract_next:
            current_url = extract_next(data)
        else:
            paging = data.get("paging", {})
            if paging.get("is_end", True):
                current_url = None
            else:
                current_url = paging.get("next")

        if current_url:
            current_url = current_url.replace("http://", "https://")

        time.sleep(delay)
