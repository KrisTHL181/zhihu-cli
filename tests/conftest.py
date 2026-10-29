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
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from _pytest.config.argparsing import Parser

_TESTS_DIR = Path(__file__).resolve().parent

#: Where test runs keep their state.  ``ZHIHU_TEST_HOME`` overrides it so a
#: developer can point a run at a persistent sandbox.
HOME_DIR = Path(os.environ.get("ZHIHU_TEST_HOME") or _TESTS_DIR / ".state").expanduser().resolve()

# Unconditional on purpose: an exported ``ZHIHU_CLI_HOME`` must never aim a test
# run at the user's real data directory.
os.environ["ZHIHU_CLI_HOME"] = str(HOME_DIR)


def pytest_addoption(parser: Parser) -> None:
    """Register the live-suite switches at the root.

    pytest only loads the ``conftest.py`` files on the path given to the
    initial arguments, so an option registered in ``tests/live/conftest.py``
    is unknown to a bare ``pytest --live``.  Registering here makes
    ``pytest --live`` and ``pytest tests/live --live`` equivalent; the hooks
    that consume the options stay in ``tests/live`` where the tests live.

    :param parser: pytest's option parser.
    """
    parser.addoption(
        "--live",
        action="store_true",
        default=False,
        help="run tests that hit the real Zhihu API",
    )
    parser.addoption(
        "--live-write",
        action="store_true",
        default=False,
        help="run live tests that modify a real Zhihu account (implies --live)",
    )
    parser.addoption(
        "--live-budget",
        action="store",
        type=int,
        default=80,
        help="maximum number of HTTP requests one live session may send (0 = unlimited)",
    )
