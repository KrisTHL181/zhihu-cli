"""Shared LLM configuration resolution for every LLM-backed command.

This module centralizes what used to be copy-pasted in five places: reading
the cached ``llm_config.json``, layering CLI args and ``LLM_*`` environment
variables on top of it, and constructing an OpenAI-compatible client.

It deliberately depends only on the standard library, :mod:`click`, and
:mod:`zhihu_cli.output`.  Neither ``curl_cffi``, ``lxml`` nor ``openai`` is
imported at module load time — ``openai`` is imported lazily inside
:func:`build_client`, so importing this module stays cheap.

The on-disk format is unchanged: a JSON object with the string keys
``api_base``, ``api_key``, ``model``, ``vision`` and ``max_image_side``.  Two
of those are not naturally strings — ``vision`` is stored as ``"true"`` or
``"false"`` and ``max_image_side`` as a decimal such as ``"8000"`` — because
:func:`load` keeps only non-empty strings.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, TypeVar

import click

from zhihu_cli.output import error

#: Endpoint used when neither the cache nor the environment supplies one.
DEFAULT_API_BASE = "https://api.openai.com/v1"

#: Model used when neither the cache nor the environment supplies one.
DEFAULT_MODEL = "gpt-4o-mini"

#: Longest side, in pixels, an image may keep once re-encoded for an LLM.
#:
#: DeepSeek answers 400 ``unsupported image`` for anything over 8192 px on a
#: side — the limit is on the side, not on the pixel count or the byte size,
#: so a 480x8555 screenshot of a long comment thread is refused while a
#: 2400x2400 square (twice the pixels) is accepted.  Comment pictures are
#: routinely tall screenshots, so the re-encode scales the long side down to
#: this.  The default sits just under the observed 8192 to leave a little
#: room; it is overridable through ``LLM_MAX_IMAGE_SIDE`` and the cache.
DEFAULT_MAX_IMAGE_SIDE = 8000

#: Current cache location for the LLM configuration.
CONFIG_PATH = Path.home() / ".zhihu-cli" / "llm_config.json"

#: Pre-refactor cache location, read as a fallback and migrated on write.
LEGACY_CONFIG_PATH = Path.home() / ".zhihu-cli" / "crank" / "llm_config.json"

#: Message shown when no API key could be resolved from any source.
MISSING_API_KEY_MESSAGE = (
    "LLM API key not configured. Set it via:\n"
    "  zhihu config llm set --api-base <URL> --api-key <KEY> --model <NAME>\n"
    "Or set the LLM_API_KEY environment variable."
)

#: Values (case-insensitive) that mark the ``vision`` flag as enabled.
_TRUTHY = {"true", "on", "1", "yes"}

_F = TypeVar("_F", bound=Callable[..., Any])


@dataclass(frozen=True)
class LLMConfig:
    """An immutable, fully resolved LLM configuration.

    :param api_base: OpenAI-compatible API base URL.
    :param api_key: API key; empty means "not configured".
    :param model: Model name to use.
    :param vision: Whether the model is declared to accept image input.
    :param max_image_side: Longest side, in pixels, an image may keep once it
        has been re-encoded for the model.
    """

    api_base: str = DEFAULT_API_BASE
    api_key: str = ""
    model: str = DEFAULT_MODEL
    vision: bool = False
    max_image_side: int = DEFAULT_MAX_IMAGE_SIDE

    def with_overrides(
        self,
        *,
        api_base: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        vision: bool | None = None,
        max_image_side: int | None = None,
    ) -> LLMConfig:
        """Return a copy with each non-``None`` argument replacing its field.

        :param api_base: Replacement API base, if any.
        :param api_key: Replacement API key, if any.
        :param model: Replacement model name, if any.
        :param vision: Replacement vision flag, if any.
        :param max_image_side: Replacement per-side image limit, if any.
        :returns: A new :class:`LLMConfig` instance.
        """
        overrides: dict[str, Any] = {}
        if api_base is not None:
            overrides["api_base"] = api_base
        if api_key is not None:
            overrides["api_key"] = api_key
        if model is not None:
            overrides["model"] = model
        if vision is not None:
            overrides["vision"] = vision
        if max_image_side is not None:
            overrides["max_image_side"] = max_image_side
        return replace(self, **overrides)


def load() -> dict[str, str]:
    """Read the cached LLM configuration.

    Reads :data:`CONFIG_PATH`, falling back to the read-only legacy location
    :data:`LEGACY_CONFIG_PATH` when the current file does not exist.  A read
    never writes to disk.

    :returns: The cached string values, or an empty dict when nothing usable
        is cached.
    """
    path = CONFIG_PATH if CONFIG_PATH.exists() else LEGACY_CONFIG_PATH
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in data.items() if isinstance(v, str) and v}


def save(
    api_base: str,
    api_key: str,
    model: str,
    *,
    vision: bool | None = None,
    max_image_side: int | None = None,
) -> None:
    """Persist the LLM configuration atomically.

    Writes to a temporary file in the same directory and then replaces
    :data:`CONFIG_PATH`, so a crash can never leave a truncated cache.  The
    legacy file is deleted best-effort afterwards.

    :param api_base: API base URL to store.
    :param api_key: API key to store.
    :param model: Model name to store.
    :param vision: Whether the model accepts image input; ``None`` keeps the
        currently cached value.
    :param max_image_side: Per-side image limit to store; ``None`` keeps the
        currently cached value.
    """
    if vision is None:
        vision = supports_vision()
    if max_image_side is None:
        max_image_side = image_side_limit()
    data = {
        "api_base": api_base,
        "api_key": api_key,
        "model": model,
        "vision": "true" if vision else "false",
        "max_image_side": str(max_image_side),
    }
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(delete=False, dir=CONFIG_PATH.parent, mode="w", encoding="utf-8") as tmp:
        json.dump(data, tmp, ensure_ascii=False, indent=2)
        tmp_name = tmp.name
    os.replace(tmp_name, CONFIG_PATH)
    try:
        LEGACY_CONFIG_PATH.unlink(missing_ok=True)
    except OSError:
        pass  # best-effort migration; the new cache is already durable


def supports_vision(cfg: dict[str, str] | None = None) -> bool:
    """Report whether the cached config declares multimodal image support.

    The flag is stored as the string ``"true"``/``"false"`` rather than a JSON
    boolean, because :func:`load` keeps only non-empty strings.

    :param cfg: Pre-loaded config; loaded from disk when omitted.
    :returns: True when the configured model is declared to accept image input.
    """
    cfg = load() if cfg is None else cfg
    return str(cfg.get("vision", "")).strip().lower() in _TRUTHY


def _parse_image_side(raw: object) -> int | None:
    """Parse a stored or configured per-side image limit.

    :param raw: Candidate value, typically a cache string or an environment
        variable; anything that is not a positive integer is rejected.
    :returns: The positive integer it denotes, or ``None`` when it is absent,
        blank, unparsable, or not positive.
    """
    if raw is None:
        return None
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def image_side_limit(cfg: dict[str, str] | None = None) -> int:
    """Report the longest side an image may keep once re-encoded for an LLM.

    The limit is stored as a decimal string rather than a JSON number, because
    :func:`load` keeps only non-empty strings.  A missing, blank, unparsable or
    non-positive value falls back to :data:`DEFAULT_MAX_IMAGE_SIDE`; this
    function never raises.

    :param cfg: Pre-loaded config; loaded from disk when omitted.
    :returns: The configured per-side pixel limit, or the built-in default.
    """
    cfg = load() if cfg is None else cfg
    value = _parse_image_side(cfg.get("max_image_side"))
    return value if value is not None else DEFAULT_MAX_IMAGE_SIDE


def load_config() -> LLMConfig:
    """Build a configuration from the cache alone, without consulting the env.

    :returns: The cached configuration with defaults filled in for any missing
        field.
    """
    cached = load()
    return LLMConfig(
        api_base=cached.get("api_base") or DEFAULT_API_BASE,
        api_key=cached.get("api_key", ""),
        model=cached.get("model") or DEFAULT_MODEL,
        vision=supports_vision(cached),
        max_image_side=image_side_limit(cached),
    )


def resolve(
    *,
    api_base: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
    max_image_side: int | None = None,
) -> LLMConfig | None:
    """Resolve an LLM configuration without printing anything.

    Precedence for each field is explicit argument, then the matching ``LLM_*``
    environment variable, then the cached value, then the built-in default.
    ``vision`` comes from ``LLM_VISION`` when set, otherwise the cached value.
    ``max_image_side`` comes from ``LLM_MAX_IMAGE_SIDE`` when set to a positive
    integer, otherwise the cached value; a blank, unparsable or non-positive
    variable is ignored rather than rejected.

    :param api_base: Explicit API base override.
    :param api_key: Explicit API key override.
    :param model: Explicit model override.
    :param max_image_side: Explicit per-side image limit override.
    :returns: The resolved config, or ``None`` when no API key is available.
    """
    cached = load()
    resolved_base = api_base or os.environ.get("LLM_API_BASE") or cached.get("api_base") or DEFAULT_API_BASE
    resolved_key = api_key or os.environ.get("LLM_API_KEY") or cached.get("api_key", "")
    resolved_model = model or os.environ.get("LLM_MODEL") or cached.get("model") or DEFAULT_MODEL

    vision_env = os.environ.get("LLM_VISION")
    if vision_env is None:
        vision = supports_vision(cached)
    else:
        vision = vision_env.strip().lower() in _TRUTHY

    side_env = _parse_image_side(os.environ.get("LLM_MAX_IMAGE_SIDE"))
    if max_image_side is not None:
        resolved_side = max_image_side
    elif side_env is not None:
        resolved_side = side_env
    else:
        resolved_side = image_side_limit(cached)

    if not resolved_key:
        return None
    return LLMConfig(
        api_base=resolved_base,
        api_key=resolved_key,
        model=resolved_model,
        vision=vision,
        max_image_side=resolved_side,
    )


def require(
    *,
    api_base: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
    max_image_side: int | None = None,
) -> LLMConfig | None:
    """Resolve a configuration, reporting a missing API key to the user.

    :param api_base: Explicit API base override.
    :param api_key: Explicit API key override.
    :param model: Explicit model override.
    :param max_image_side: Explicit per-side image limit override.
    :returns: The resolved config, or ``None`` after printing an error when no
        API key is available.
    """
    cfg = resolve(api_base=api_base, api_key=api_key, model=model, max_image_side=max_image_side)
    if cfg is None:
        error(MISSING_API_KEY_MESSAGE)
        return None
    return cfg


def build_client(cfg: LLMConfig, *, purpose: str) -> Any:
    """Construct an OpenAI client for *cfg*, importing ``openai`` on demand.

    :param cfg: The resolved configuration to connect with.
    :param purpose: Human-readable description of the calling feature, used in
        the error message when ``openai`` is not installed.
    :returns: An ``openai.OpenAI`` instance, or ``None`` when the package is
        missing.
    """
    try:
        from openai import OpenAI  # type: ignore[import-untyped]
    except ImportError:
        error(f"The 'openai' package is required for {purpose}. Install with: pip install -e \".[llm]\"")
        return None
    return OpenAI(base_url=cfg.api_base, api_key=cfg.api_key)


def clear() -> bool:
    """Delete both the current and legacy LLM config files.

    :returns: True when at least one of the files existed.
    """
    existed = CONFIG_PATH.exists() or LEGACY_CONFIG_PATH.exists()
    CONFIG_PATH.unlink(missing_ok=True)
    LEGACY_CONFIG_PATH.unlink(missing_ok=True)
    return existed


def options(*, use_env: bool = True, vision: bool = False, image_side: bool = False) -> Callable[[_F], _F]:
    """Build a click decorator adding the shared ``--api-base``/``--api-key``/``--model`` options.

    :param use_env: When True, the options read ``LLM_API_BASE``,
        ``LLM_API_KEY`` and ``LLM_MODEL`` as fallbacks.  ``config llm set``
        passes False so it never bakes ambient env vars into the cache.
    :param vision: When True, also add a ``--vision/--no-vision`` switch.
    :param image_side: When True, also add a ``--max-image-side`` integer
        option.
    :returns: A decorator applying the options to a click command function.
    """
    env_suffix = " or the environment variable" if use_env else ""
    opts = [
        click.option(
            "--api-base",
            "api_base",
            default=None,
            envvar="LLM_API_BASE" if use_env else None,
            help=f"LLM API base URL (saved to cache; default: `zhihu config llm`{env_suffix}).",
        ),
        click.option(
            "--api-key",
            "api_key",
            default=None,
            envvar="LLM_API_KEY" if use_env else None,
            help=f"LLM API key (saved to cache; default: `zhihu config llm`{env_suffix}).",
        ),
        click.option(
            "--model",
            "model",
            default=None,
            envvar="LLM_MODEL" if use_env else None,
            help=f"LLM model name (saved to cache; default: `zhihu config llm`{env_suffix}).",
        ),
    ]
    if vision:
        opts.append(
            click.option(
                "--vision/--no-vision",
                default=None,
                help="Declare whether the model accepts image input (saved to cache).",
            )
        )
    if image_side:
        opts.append(
            click.option(
                "--max-image-side",
                "max_image_side",
                type=click.IntRange(min=1),
                default=None,
                help=(
                    "Longest side, in pixels, an image may keep after re-encoding for the model "
                    f"(saved to cache; default: `zhihu config llm`{env_suffix})."
                ),
            )
        )

    def decorator(fn: _F) -> _F:
        for opt in reversed(opts):
            fn = opt(fn)
        return fn

    return decorator


# ── back-compat adapters ─────────────────────────────────────────────────────
# ``extensions/crank/archiver.py`` used to export these three names; keep the
# dict-shaped signatures so existing importers keep working unchanged.


def load_llm_config() -> dict[str, str]:
    """Back-compat alias for :func:`load`.

    :returns: The cached string values, or an empty dict.
    """
    return load()


def save_llm_config(
    api_base: str,
    api_key: str,
    model: str,
    *,
    vision: bool | None = None,
    max_image_side: int | None = None,
) -> None:
    """Back-compat alias for :func:`save`.

    :param api_base: API base URL to store.
    :param api_key: API key to store.
    :param model: Model name to store.
    :param vision: Whether the model accepts image input; ``None`` keeps the
        currently cached value.
    :param max_image_side: Per-side image limit to store; ``None`` keeps the
        currently cached value.
    """
    save(api_base, api_key, model, vision=vision, max_image_side=max_image_side)


def llm_supports_vision(cfg: dict[str, str] | None = None) -> bool:
    """Back-compat alias for :func:`supports_vision`.

    :param cfg: Pre-loaded config; loaded from disk when omitted.
    :returns: True when the configured model is declared to accept image input.
    """
    return supports_vision(cfg)
