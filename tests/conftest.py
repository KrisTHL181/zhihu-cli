"""Global pytest setup: redirect the whole zhihu-cli data tree.

The redirect must happen **before the first** ``import zhihu_cli``.  Several
modules capture :func:`zhihu_cli.paths.zhihu_home` at import time — the
``CacheManager`` singleton, the daemon's state/transport constants, and the LLM
config — so setting ``ZHIHU_CLI_HOME`` inside a fixture would already be too
late.  This conftest therefore installs the variable at the very top, before any
project import, so a test run can never read or write the real
``~/.zhihu-cli`` tree.

Nothing here imports ``zhihu_cli``; nested conftest files and the tests do that
later, by which point the environment is already redirected.
"""

from __future__ import annotations

import os
from pathlib import Path

_TESTS_DIR = Path(__file__).resolve().parent

#: Where test runs keep their state.  ``ZHIHU_TEST_HOME`` overrides it so a
#: developer can point a run at a persistent sandbox.
HOME_DIR = Path(os.environ.get("ZHIHU_TEST_HOME") or _TESTS_DIR / ".state").expanduser().resolve()

# Unconditional on purpose: an exported ``ZHIHU_CLI_HOME`` must never aim a test
# run at the user's real data directory.
os.environ["ZHIHU_CLI_HOME"] = str(HOME_DIR)
