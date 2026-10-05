"""zhihu-cli content package.

This package provides Zhihu scraping, Markdown conversion, and download helpers.

The re-exports below resolve lazily: :mod:`zhihu_cli.content.download_contents`
imports ``requests`` and :mod:`~zhihu_cli.content.utils.html2markdown` imports
``lxml``, so binding them here would make every ``import zhihu_cli.content.*``
pay for both. Import the defining submodule directly when you only need one.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from zhihu_cli.content.download_contents import (
        ContentDownloader,
        extract_config_from_curl,
        extract_metadata_from_html,
        sanitize_filename,
    )
    from zhihu_cli.content.handlers.cache_manager import cache_manager
    from zhihu_cli.content.utils.html2markdown import PageToMarkdown

__all__ = [
    "cache_manager",
    "ContentDownloader",
    "extract_config_from_curl",
    "extract_metadata_from_html",
    "sanitize_filename",
    "PageToMarkdown",
]

#: Public re-export → the submodule that actually defines it.
_LAZY_EXPORTS = {
    "ContentDownloader": "zhihu_cli.content.download_contents",
    "extract_config_from_curl": "zhihu_cli.content.download_contents",
    "extract_metadata_from_html": "zhihu_cli.content.download_contents",
    "sanitize_filename": "zhihu_cli.content.download_contents",
    "cache_manager": "zhihu_cli.content.handlers.cache_manager",
    "PageToMarkdown": "zhihu_cli.content.utils.html2markdown",
}


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
