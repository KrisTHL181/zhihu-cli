"""Shared smoothing helper — reads config and applies MA or EMA."""

from __future__ import annotations

import pandas as pd

from zhihu_cli.content.handlers.cache_manager import cache_manager


def compute_smoothed(series: pd.Series, window: int = 7) -> pd.Series:
    """Apply MA or EMA smoothing based on the ``smoothing`` config key.

    Returns a Series with the smoothed values:
    - ``"ma"``  → ``series.rolling(window, center=True).mean()``
    - ``"ema"`` → ``series.ewm(span=window, adjust=False).mean()``

    Defaults to EMA when no config value is set.
    """
    method = cache_manager.get_smoothing()
    if method == "ma":
        return series.rolling(window=window, center=True).mean()
    return series.ewm(span=window, adjust=False).mean()


def smoothing_label(window: int | None = None) -> str:
    """Return a human-readable label for the current smoothing method.

    ``window``, when provided, is appended inside parentheses — e.g.
    ``"MA (7-day)"`` or ``"EMA (21)"``.
    """
    method = cache_manager.get_smoothing().upper()
    if window is not None:
        return f"{method} ({window}-day)"
    return method


def smoothing_suffix() -> str:
    """Return ``"ma"`` or ``"ema"`` — useful for dynamic column naming."""
    return cache_manager.get_smoothing()
