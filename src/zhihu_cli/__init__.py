"""zhihu-cli: Zhihu scraping, automation, and analysis toolkit.

The convenience re-exports below are resolved lazily. Importing them eagerly
would drag :mod:`zhihu_cli.content` — and with it ``curl_cffi`` and ``lxml`` —
into every ``import zhihu_cli``, which costs roughly a second of startup on
every CLI invocation. Submodule imports are unaffected either way.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

from zhihu_cli import _import_trim  # noqa: F401  # must precede zhihu_cli.content

if TYPE_CHECKING:
    from zhihu_cli.content import (
        ContentDownloader,
        PageToMarkdown,
        cache_manager,
        extract_config_from_curl,
        extract_metadata_from_html,
        sanitize_filename,
    )

__all__ = [
    "ContentDownloader",
    "PageToMarkdown",
    "cache_manager",
    "extract_config_from_curl",
    "extract_metadata_from_html",
    "sanitize_filename",
]

#: Public re-export → the submodule that actually defines it.
_LAZY_EXPORTS = dict.fromkeys(__all__, "zhihu_cli.content")


def __getattr__(name: str) -> object:
    """Resolve a lazy re-export on first access (PEP 562)."""
    module_name = _LAZY_EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    value = getattr(importlib.import_module(module_name), name)
    globals()[name] = value  # cache so later lookups skip this path
    return value


def __dir__() -> list[str]:
    """Report the lazy re-exports alongside the module's real attributes."""
    return sorted({*globals(), *__all__})
