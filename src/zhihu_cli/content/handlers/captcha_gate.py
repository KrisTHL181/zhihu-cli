"""Cross-thread CAPTCHA coordination ("the gate").

A multi-threaded consumer (e.g. a scraper driving
:mod:`zhihu_cli.content.handlers` from a ``ThreadPoolExecutor``) can be hit by
a risk-control challenge on any number of threads at once.  Without
coordination every one of them calls
:func:`~zhihu_cli.content.handlers.captcha.handle_captcha` independently: N
threads on the same session would print N banners, open N browser tabs and
block on N ``input()`` prompts — a user has to answer the same challenge once
per worker, while the pool sits idle.

The gate turns that into "pause everything, prompt exactly once, resume":

* :func:`captcha_gate.acquire` is called at the top of every request.  While
  one thread is resolving, the others park here instead of hammering Zhihu
  with requests that would only come back 403.
* The first thread to *see* a captcha wins :meth:`CaptchaGate.try_begin` and
  becomes the resolver; it runs the interactive flow while the gate is active.
* When it finishes, :meth:`CaptchaGate.finish` clears the gate and wakes every
  parked thread.  A thread whose own request had already been 403'd re-sends
  that request once; a thread that had not sent yet simply proceeds.  A thread
  that never parked — its request was still in flight when the resolution
  completed — detects the stale 403 through :attr:`CaptchaGate.epoch` and
  re-sends once too, instead of prompting the user for the same challenge a
  second time.
* The gate is **re-entrant for the resolver thread**: ``handle_captcha``
  issues nested requests on the same session (the appeal API, then the
  unhuman URL to harvest the post-challenge cookies), and those must not
  deadlock behind the gate the same thread is holding.

Everything here is stdlib-only on purpose: the module is imported by both
:mod:`zhihu_cli.content.handlers._session_core` (heavy: curl_cffi + lxml) and
:mod:`zhihu_cli.daemon.proxy` (light, and must stay free of curl_cffi imports).

The gate is on by default.  Two environment variables tune it without any
config-file or CLI surface:

``ZHIHU_CAPTCHA_GATE=0``
    Escape hatch — disable coordination entirely.  ``acquire()`` and
    ``try_begin()`` then always return ``True``, i.e. the pre-gate behaviour
    where every thread prompts for itself.
``ZHIHU_CAPTCHA_TIMEOUT=<seconds>``
    How long a parked thread waits for the resolver before giving up and
    returning its own 403 (default :data:`DEFAULT_WAIT_TIMEOUT`).  Invalid or
    non-positive values fall back to the default.
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable
from typing import Any

DEFAULT_WAIT_TIMEOUT = 300.0
"""Seconds a parked thread waits for the resolver before giving up."""

_GATE_ENV = "ZHIHU_CAPTCHA_GATE"
_TIMEOUT_ENV = "ZHIHU_CAPTCHA_TIMEOUT"

_FALSEY = frozenset({"0", "false", "no", "off", "disable", "disabled"})


def _gate_disabled() -> bool:
    """Whether the escape hatch ``ZHIHU_CAPTCHA_GATE`` turns the gate off.

    :returns: ``True`` when the variable is set to a falsey value.
    """
    return os.environ.get(_GATE_ENV, "").strip().lower() in _FALSEY


def _default_timeout() -> float:
    """Resolve the wait timeout from ``ZHIHU_CAPTCHA_TIMEOUT``.

    Read on every call (rather than at import time) so tests and shells can
    change it without reloading the module.

    :returns: The configured timeout in seconds, or
        :data:`DEFAULT_WAIT_TIMEOUT` when unset, non-numeric, or non-positive.
    """
    raw = os.environ.get(_TIMEOUT_ENV, "").strip()
    if raw:
        try:
            value = float(raw)
        except ValueError:
            return DEFAULT_WAIT_TIMEOUT
        if value > 0:
            return value
    return DEFAULT_WAIT_TIMEOUT


class CaptchaGate:
    """A one-resolver-at-a-time gate shared by every session in the process.

    The gate is a single :class:`threading.Condition` plus the id of the
    thread currently resolving, plus :attr:`epoch` — a counter of completed
    resolutions.  The condition is only ever held for short, lock-free state
    transitions — never across I/O or the interactive prompt, which is why
    :meth:`acquire` can be called from the hot path of every request.

    :param timeout: Default wait timeout in seconds for :meth:`acquire`, or
        ``None`` to follow ``ZHIHU_CAPTCHA_TIMEOUT`` /
        :data:`DEFAULT_WAIT_TIMEOUT` at call time.
    """

    def __init__(self, timeout: float | None = None) -> None:
        self._cond = threading.Condition()
        self._resolver_tid: int | None = None
        self._epoch = 0
        self._timeout = timeout

    @property
    def active(self) -> bool:
        """Whether a resolution is currently in progress (diagnostics/tests)."""
        with self._cond:
            return self._resolver_tid is not None

    @property
    def epoch(self) -> int:
        """Count of resolutions that have completed since process start.

        A caller reads this *immediately before* sending a request and compares
        the value again when that request comes back as a captcha: if it moved,
        a resolution finished while this request was in flight, so the 403 is
        stale (it was produced with the pre-resolution cookies) and must be
        re-sent rather than raised — and, crucially, rather than prompting for
        a challenge the user has already answered.

        Reading it *before* the send (never at request entry) is what keeps a
        genuine new challenge promptable: a resolution that completed before
        the send already applies to the request, so its 403 is real.

        :returns: The number of completed resolutions.
        """
        with self._cond:
            return self._epoch

    def is_resolver(self) -> bool:
        """Whether the calling thread is the current resolver.

        :returns: ``True`` when this thread owns the gate.
        """
        with self._cond:
            return self._resolver_tid == threading.get_ident()

    def try_begin(self) -> bool:
        """Try to become THE resolver thread.

        Only the call that performs the ``None`` → *tid* transition gets
        ``True``; every other concurrent caller gets ``False`` and should
        park in :meth:`acquire` instead.  A re-entrant call from the resolver
        thread itself also gets ``False`` — callers are expected to check
        :meth:`is_resolver` first and skip the resolution entirely.

        :returns: ``True`` if the caller now owns the gate, else ``False``.
        """
        if _gate_disabled():
            return True
        with self._cond:
            if self._resolver_tid is not None:
                return False
            self._resolver_tid = threading.get_ident()
            return True

    def acquire(self, timeout: float | None = None) -> bool:
        """Wait until no other thread is resolving a captcha.

        Re-entrant: the resolver thread itself returns immediately, which is
        what lets ``handle_captcha``'s nested requests on the same session
        proceed instead of deadlocking.

        :param timeout: Per-call override in seconds.  ``None`` uses the
            instance timeout, else ``ZHIHU_CAPTCHA_TIMEOUT``, else
            :data:`DEFAULT_WAIT_TIMEOUT`.
        :returns: ``True`` if the gate is open (or became open), ``False`` if
            the wait timed out — the caller should then carry on and let the
            original 403 surface rather than hang forever.
        """
        if _gate_disabled():
            return True

        ident = threading.get_ident()
        with self._cond:
            if self._resolver_tid is None or self._resolver_tid == ident:
                return True

            if timeout is None:
                timeout = self._timeout if self._timeout is not None else _default_timeout()
            deadline = time.monotonic() + timeout

            while self._resolver_tid is not None and self._resolver_tid != ident:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._cond.wait(remaining)
            return True

    def finish(self) -> None:
        """Mark the resolution done and wake every parked thread.

        A no-op when called by a thread that does not own the gate (or when
        the gate is disabled — :meth:`try_begin` never claims ownership then,
        so the ownership check below fails and :attr:`epoch` stays put), so it
        is always safe to put in a ``finally``.
        """
        with self._cond:
            if self._resolver_tid != threading.get_ident():
                return
            self._resolver_tid = None
            # Only a genuine release counts as a completed resolution: this is
            # what tells an in-flight request that its 403 predates a
            # resolution (see ``epoch``).
            self._epoch += 1
            self._cond.notify_all()


captcha_gate = CaptchaGate()
"""Process-wide gate shared by direct sessions, the daemon proxy, and tests."""


# ── pluggable resolver ─────────────────────────────────────────────────────

#: The registered resolver, or ``None`` for the built-in interactive one.
_resolver: Callable[[Any, dict], str] | None = None
_resolver_lock = threading.Lock()


def set_captcha_resolver(resolver: Callable[[Any, dict], str] | None) -> None:
    """Install a custom captcha resolver for the whole process.

    Lets a library consumer own the interactive step — a GUI dialog, a
    desktop notification, a pre-recorded cookie — without monkey-patching
    library internals.  The resolver is called with the session that hit the
    challenge and the ``detect_captcha()`` error dict.

    :param resolver: A callable returning one of ``"resolved"``, ``"skipped"``
        or ``"cancelled"``, or ``None`` to restore the built-in
        :func:`~zhihu_cli.content.handlers.captcha.handle_captcha`.  It runs on
        the resolver thread while the gate is held (i.e. with every other
        thread parked), so it must not block on those other threads.
    """
    global _resolver
    with _resolver_lock:
        _resolver = resolver


def get_captcha_resolver() -> Callable[[Any, dict], str] | None:
    """Return the registered resolver, or ``None`` when the default is active."""
    with _resolver_lock:
        return _resolver


def resolve_captcha(session: Any, captcha_error: dict) -> str:
    """Resolve a captcha, through the registered resolver or the default flow.

    :param session: The session that hit the challenge.  The default resolver
        issues nested requests on it, so it must be reentrant-safe — the gate
        is, because the resolver thread owns it.
    :param captcha_error: Error dict as returned by ``detect_captcha()``.
    :returns: ``"resolved"``, ``"skipped"`` or ``"cancelled"``.
    """
    resolver = get_captcha_resolver()
    if resolver is not None:
        return resolver(session, captcha_error)

    # Imported lazily: captcha.py is light, but this keeps the import out of
    # the daemon-proxy startup path and avoids any import cycle.
    from zhihu_cli.content.handlers.captcha import handle_captcha

    return handle_captcha(session, captcha_error)
