"""Process-wide request rate limiter (stdlib only).

Why a global limiter
--------------------
Zhihu's risk control reacts to request *rate*, not just to individual request
shapes, so the fix has to sit at the lowest chokepoint every request passes
through rather than at each command.  The two such chokepoints are
:meth:`zhihu_cli.content.handlers._session_core.ZhihuSession.request` (direct
sessions, the daemon server's shared session, and streaming fallbacks) and
:meth:`zhihu_cli.daemon.proxy.DaemonProxySession.request` (the client side of
the daemon hop).  Both call :data:`limiter`; there is deliberately no second
limiter.

Why reservation instead of "sleep then send"
--------------------------------------------
The classic implementation — take a lock, sleep ``1/qps``, release, send — both
serialises the sends and *sums* the delays when two limiters are stacked (a
client limiter and the server's own limiter at the same qps would halve
throughput).  Reserving an absolute monotonic *slot* under the lock and then
sleeping outside it gives two useful properties:

* threads overlap their network I/O; only the *start times* are spaced, so
  throughput is actually ``qps`` instead of ``qps / latency``;
* stacked limiters at the same qps compute the same slot sequence, so their
  composition is ``max`` (the later of the two waits), not a sum.  Slots are
  absolute, and neither limiter hands out a slot earlier than the one the other
  already consumed.

Defaults and configuration sources
----------------------------------
``qps == 0`` means unlimited and must be a zero-behaviour-change no-op: the
hot path checks a precomputed interval and returns without touching the lock
or the clock.  The value comes from, in order, an explicit ``--qps`` /
``ZHIHU_QPS`` (per invocation, client only), the persistent
``rate_limit.qps`` in ``config.json`` (also pushed to a running daemon so it
becomes an account-wide cap), or 0.

This module is imported by :mod:`zhihu_cli.daemon.proxy`, which is kept free of
``curl_cffi``/``lxml``, so it must stay stdlib-only.  The heavier
:mod:`cache_manager` import is therefore deferred into :func:`resolve_qps`.
"""

from __future__ import annotations

import math
import os
import threading
import time

#: Marks that the caller supplied a per-invocation qps (CLI option or env var).
#: Once set, :func:`configure_from_config` must not overwrite it when a session
#: is built later in the same process: an explicit ``--qps`` outranks the
#: persisted config for that invocation.
_explicit_lock = threading.Lock()
_explicit_qps = False


def _coerce_qps(value: object) -> float:
    """Coerce *value* to a finite, non-negative float, else ``0.0``.

    Non-numeric strings, ``None``, NaN, and infinities all mean "do not
    limit": treating garbage as 0 keeps a typo in an environment variable from
    either crashing a request path or accidentally pinning the process to an
    unusable rate.

    :param value: Anything a CLI option, env var, or config key might hold.
    :returns: A finite ``>= 0`` float; ``0.0`` for anything invalid.
    """
    try:
        qps = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(qps) or qps <= 0:
        return 0.0
    return qps


class RateLimiter:
    """Thread-safe, process-wide request pacer based on absolute time slots.

    All state is one lock and three scalars.  The lock is a leaf lock: it is
    held only for the arithmetic that claims a slot, never across ``sleep`` or
    any I/O.  That matters because every request in the process contends on it;
    a thread that slept while holding it would stall the whole process — and,
    on the daemon path, would prevent the captcha resolver from using the
    socket (see the note in :meth:`DaemonProxySession.request`).
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()  # leaf lock: no I/O, no logging while held
        self._qps = 0.0
        self._interval = 0.0
        self._next = 0.0  # monotonic timestamp of the next unreserved slot

    @property
    def qps(self) -> float:
        """The configured rate in requests/second (``0.0`` = unlimited)."""
        return self._qps

    @property
    def enabled(self) -> bool:
        """Whether a request would actually wait."""
        return self._interval > 0

    def configure(self, qps: float) -> None:
        """Set the rate, clamping invalid or negative values to unlimited.

        Resetting ``_next`` is intentional: switching rates starts a fresh
        timeline rather than carrying slots reserved under the old rate, which
        could otherwise impose a long stale wait after e.g. 0.1 qps → 20 qps.

        :param qps: Requests per second; ``<= 0``, NaN, or inf disables
            limiting.
        """
        qps = _coerce_qps(qps)
        interval = 1.0 / qps if qps > 0 else 0.0
        with self._lock:
            self._qps = qps
            self._interval = interval
            self._next = 0.0

    def acquire(self) -> None:
        """Block until this caller may start its request.

        The fast path reads :attr:`_interval` without the lock: when limiting
        is disabled it is a single float comparison, so the default qps=0
        configuration adds no measurable cost to the request path.  When
        enabled, claiming a slot and sleeping are deliberately split so the
        lock never covers the wait.

        A slightly stale read of ``_interval`` after a concurrent
        :meth:`configure` is harmless — the worst case is one extra request
        paced by the old interval — and re-reading under the lock would make
        the common path pay for a correctness the semantics do not need.
        """
        interval = self._interval
        if interval <= 0:
            return

        with self._lock:
            now = time.monotonic()
            slot = max(now, self._next)
            self._next = slot + interval

        delay = slot - time.monotonic()
        if delay > 0:
            time.sleep(delay)


#: The one limiter every request path shares.  Import it — never construct a
#: second one, or the two timelines would double the effective rate.
limiter = RateLimiter()


def resolve_qps(cli: float | None = None) -> float:
    """Return the effective qps from CLI, env, then the persistent config.

    Precedence is deliberate:

    1. *cli* — the ``--qps`` option, per invocation;
    2. ``ZHIHU_QPS`` — the same intent expressed in the environment;
    3. ``cache_manager.get_qps()`` — the persistent ``rate_limit.qps``;
    4. ``0`` — unlimited.

    ``cache_manager`` is imported lazily so this module stays cheap enough for
    :mod:`zhihu_cli.daemon.proxy` (and for ``--help``) to import.  An invalid
    env value falls through to the config rather than forcing 0, which keeps a
    stale/garbled variable from silently disabling a persisted cap; an explicit
    *cli* value that is invalid resolves to 0, because "the caller said
    something about rate" should not be overridden by a config they did not
    mention.

    :param cli: The ``--qps`` value, or ``None`` when not supplied.
    :returns: A finite non-negative float.
    """
    if cli is not None:
        return _coerce_qps(cli)

    env = os.environ.get("ZHIHU_QPS")
    if env is not None and env.strip():
        try:
            qps = float(env)
        except (TypeError, ValueError):
            pass
        else:
            if math.isfinite(qps) and qps > 0:
                return qps
            # A finite non-positive value is an explicit "off".
            if math.isfinite(qps):
                return 0.0

    try:
        from zhihu_cli.content.handlers.cache_manager import cache_manager

        return _coerce_qps(cache_manager.get_qps())
    except Exception:
        # A broken/unreadable config must never break request setup.
        return 0.0


def set_explicit_qps(qps: float) -> None:
    """Configure the limiter from a per-invocation value and pin it.

    Called by ``main`` for ``--qps``/``ZHIHU_QPS``.  The pin matters because
    :func:`configure_from_config` runs again when a session is built; without
    it, the persisted config would overwrite the user's explicit choice for
    this run.

    :param qps: Requests per second; invalid values disable limiting.
    """
    global _explicit_qps
    with _explicit_lock:
        _explicit_qps = True
    limiter.configure(qps)


def configure_from_config(cli: float | None = None) -> None:
    """Configure the limiter from the persisted config unless already pinned.

    A no-op when :func:`set_explicit_qps` has run, so building a session can
    call this unconditionally.  The check and the :func:`resolve_qps` read are
    not held under one lock on purpose: ``resolve_qps`` may touch the
    filesystem, and a concurrent explicit call racing with this one only
    decides whether *this* configuration happens — the explicit value always
    wins by the time the caller next sends a request.

    :param cli: Optional ``--qps`` fallback when the explicit setter was not
        used (e.g. a library caller supplying the value directly).
    """
    with _explicit_lock:
        if _explicit_qps:
            return
    limiter.configure(resolve_qps(cli))
