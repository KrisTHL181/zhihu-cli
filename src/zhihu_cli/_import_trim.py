"""Skip third-party imports that zhihu-cli never uses.

Imported first by :mod:`zhihu_cli` so the trim is in place before any
heavy dependency is loaded.

``curl_cffi/requests/models.py`` imports ``markdownify`` and ``readability``
under ``contextlib.suppress(ImportError)`` — they are only needed by
:meth:`curl_cffi.requests.Response.markdown`, which zhihu-cli never calls
(it converts HTML with :mod:`zhihu_cli.content.utils.html2markdown`).
``markdownify`` alone drags in beautifulsoup4 → soupsieve → chardet and
accounts for roughly half of curl_cffi's ~400 ms import.

Mapping a name to ``None`` in :data:`sys.modules` makes importing it raise
:class:`ImportError`, which curl_cffi's ``suppress`` swallows.
``setdefault`` leaves an already-imported module untouched, so the only
code affected is curl_cffi's unused optional path — a caller that
genuinely wants ``markdownify`` gets a clear ``ImportError`` rather than a
silently broken ``Response.markdown()``.
"""

from __future__ import annotations

import sys

_UNUSED_OPTIONAL_DEPS = ("markdownify", "readability")

for _name in _UNUSED_OPTIONAL_DEPS:
    sys.modules.setdefault(_name, None)

del _name
