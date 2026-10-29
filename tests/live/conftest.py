"""Plumbing for the live integration suite: gating, budget, and fixtures.

Nothing here runs by default.  The ``live`` tests are collected but skipped
unless ``--live`` is passed at the command line, and anything marked
``live_write`` additionally needs ``--live-write``.  A plain ``pytest`` run
therefore makes zero network requests.

The data tree is relocated by ``tests/conftest.py`` before any project import.
The autouse :func:`_live_home_guard` below re-checks that redirect anyway: if
somehow ``ZHIHU_CLI_HOME`` points outside the test tree, every live test is
skipped with a loud message rather than touching real data or a real account.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from collections.abc import Iterator

    from _pytest.config import Config
    from _pytest.config.argparsing import Parser
    from _pytest.nodes import Item

_LIVE_DIR = Path(__file__).resolve().parent
_DEFAULT_TEST_HOME = _LIVE_DIR.parent / ".state"


def pytest_addoption(parser: Parser) -> None:
    """Register the live-test switches.

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


def pytest_collection_modifyitems(config: Config, items: list[Item]) -> None:
    """Skip live tests unless the matching flags were passed.

    :param config: pytest's configuration object.
    :param items: Every collected test item.
    """
    run_live = config.getoption("--live")
    run_write = config.getoption("--live-write")

    skip_live = pytest.mark.skip(reason="live test: pass --live to run it against the real Zhihu API")
    skip_write = pytest.mark.skip(reason="live write test: pass --live --live-write to run it against a real account")

    for item in items:
        if item.get_closest_marker("live") is not None and not run_live:
            item.add_marker(skip_live)
        elif item.get_closest_marker("live_write") is not None and not run_write:
            item.add_marker(skip_write)


@pytest.fixture(scope="session", autouse=True)
def _live_home_guard() -> None:
    """Refuse to run live tests unless the data tree is inside the test sandbox.

    Belt and braces on top of ``tests/conftest.py``: an externally exported
    ``ZHIHU_CLI_HOME`` or a broken redirect must never let a live test read the
    user's profiles or write into ``~/.zhihu-cli``.

    :raises pytest.skip.Exception: If :func:`zhihu_cli.paths.zhihu_home` points
        outside the test tree.
    """
    from zhihu_cli.paths import zhihu_home

    expected = Path(os.environ.get("ZHIHU_TEST_HOME") or _DEFAULT_TEST_HOME).expanduser().resolve()
    actual = zhihu_home().resolve()
    if not actual.is_relative_to(expected):
        pytest.skip(
            "REFUSING TO RUN LIVE TESTS: zhihu_home() is "
            f"{actual}, outside the isolated test tree {expected}. "
            "Unset ZHIHU_CLI_HOME or point ZHIHU_TEST_HOME at a sandbox directory."
        )


@pytest.fixture(scope="session")
def seeded_profile() -> str | None:
    """Copy an explicitly named profile into the test home, if one was named.

    ``ZHIHU_TEST_PROFILE`` names a profile under the *real* ``~/.zhihu-cli``;
    its JSON is copied into the sandbox so the live suite can authenticate
    without ever reading or writing the user's ``active_profile`` pointer.

    :returns: The profile name, or ``None`` when the variable is unset or the
        profile does not exist (anonymous tests still run).
    """
    from zhihu_cli.paths import zhihu_home

    name = os.environ.get("ZHIHU_TEST_PROFILE", "").strip()
    if not name:
        return None

    source = Path.home() / ".zhihu-cli" / "cache" / "profiles" / f"{name}.json"
    if not source.exists():
        return None

    state = zhihu_home()
    profiles = state / "cache" / "profiles"
    profiles.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, profiles / f"{name}.json")
    (state / "cache" / "active_profile").write_text(name, encoding="utf-8")
    return name


class _Budget:
    """Count the HTTP requests a live session sends and fail past the cap.

    The wrapper is installed through ``direct_sessions(configure=...)``, which
    runs it on each newly built session.  It sits in front of
    :meth:`ZhihuSession.request`, so every request the handlers make — including
    those issued while iterating a lazy stream — is counted.  Retries performed
    inside the session (captcha resolution) bypass it, which matches how a
    user would count "requests made by the test".

    :ivar limit: Maximum requests; ``0`` means unlimited.
    :ivar count: Requests sent so far.
    """

    def __init__(self, limit: int) -> None:
        """Create a counter with the given cap.

        :param limit: Maximum requests, or ``0``/negative for unlimited.
        """
        self.limit = limit
        self.count = 0

    def configure(self, session: Any) -> None:
        """Wrap ``session.request`` so each call is counted and capped.

        :param session: A freshly built direct session.
        """
        original = session.request

        def counted(method: str, url: str, **kwargs: Any) -> Any:
            self.count += 1
            if self.limit > 0 and self.count > self.limit:
                pytest.fail(f"live request budget of {self.limit} exceeded at request #{self.count}: {method} {url}")
            return original(method, url, **kwargs)

        session.request = counted


@pytest.fixture(scope="session")
def budget(request: pytest.FixtureRequest) -> _Budget:
    """Return the session's request budget.

    :param request: pytest's fixture request, used to read ``--live-budget``.
    :returns: The budget counter shared by the live session.
    """
    return _Budget(request.config.getoption("--live-budget"))


@pytest.fixture(scope="session")
def live_session(
    _live_home_guard: None,
    seeded_profile: str | None,
    budget: _Budget,
) -> Iterator[Any]:
    """Open one direct, non-daemon session for the whole live session.

    The block stays open for the session's lifetime: sessions are per thread and
    are closed when ``direct_sessions`` exits, so the tests must run inside it.

    :param _live_home_guard: Ensures the sandbox redirect is in place.
    :param seeded_profile: Profile copied into the sandbox, or ``None`` for
        anonymous access.
    :param budget: Requests counter installed on every new session.
    :returns: The ``DirectSessions`` handle.
    """
    from zhihu_cli.api import direct_sessions, rate_limit

    with rate_limit(0.5):
        with direct_sessions(
            profile=seeded_profile,
            captcha_handler="ignore",
            request_timeout=30.0,
            configure=(budget.configure,),
        ) as handle:
            yield handle


@pytest.fixture(scope="session")
def identity(live_session: Any) -> Any:
    """Return the logged-in member's identity, or skip.

    Calls ``/api/v4/me`` through :func:`zhihu_cli.api.get_my_url_token`.  That
    handler returns ``None`` rather than raising for an anonymous session, so a
    missing token is treated the same as a rejected login.

    :param live_session: Ensures the direct session scope is active.
    :returns: Whatever ``get_my_url_token()`` yields (normally the ``url_token``).
    :raises pytest.skip.Exception: With no usable login, or when risk control
        blocks the request.
    """
    from zhihu_cli.api import AuthError, RateLimitError, get_my_url_token

    try:
        token = get_my_url_token()
    except AuthError:
        pytest.skip("no usable login: authenticate a profile and set ZHIHU_TEST_PROFILE")
    except RateLimitError:
        pytest.skip("risk control: solve the captcha and rerun")

    if not token:
        pytest.skip("no usable login: /api/v4/me returned no url_token")
    return token
