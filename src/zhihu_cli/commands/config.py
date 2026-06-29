"""Config command group for zhihu-cli."""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import click

from zhihu_cli.content.handlers import get_user_agent, set_user_agent
from zhihu_cli.content.handlers.cache_manager import cache_manager
from zhihu_cli.output import echo, error, f_label, f_title, info, success

# ═══════════════════════════════════════════════════════════════════════
#  Config item abstraction — eliminates the set/show/clear boilerplate
# ═══════════════════════════════════════════════════════════════════════


@dataclass
class _Item:
    """Specification for a config sub-group with set/show/clear subcommands.

    Each field maps to one dimension of variance across the otherwise-identical
    config items.  Only *name*, *display*, *label*, *getter*, *setter*, and
    *default* are required — the rest have sensible defaults.
    """

    name: str  #: CLI group name, e.g. ``"user-agent"``
    display: str  #: Human-readable name used in success/info messages
    label: str  #: Label shown by ``show`` (colon appended automatically)
    getter: Callable[[], Any]  #: () -> current value
    setter: Callable[[Any], None]  #: (value) -> None
    default: Any  #: Value restored by ``clear``

    # ── optional customisation ──
    group_help: str | None = None  #: Group docstring (default: generic sentence)
    param_type: Any = str  #: click argument type for ``set``
    param_name: str = "value"  #: click argument name for ``set``
    needs_reload: bool = False  #: call ``reload_session()`` after set/clear
    fmt: Callable[[Any], str] | None = None  #: value formatter for display (e.g. ``str.upper``)
    show_on_newline: bool = False  #: put value on its own line in ``set`` / ``show``
    show_none_msg: str | None = None  #: shown by ``show`` when getter returns None
    clear_msg: str | None = None  #: override the entire ``clear`` success message

    def _format(self, value: Any) -> str:
        """Format *value* for display, using *fmt* if set."""
        if self.fmt is not None:
            return self.fmt(value)
        return str(value)


def _register_item(config_group: click.Group, item: _Item) -> None:
    """Create a config sub-group (set/show/clear) on *config_group* for *item*."""

    @config_group.group(item.name, help=item.group_help or f"Manage {item.display}.")
    def subgroup() -> None:
        pass

    # ── set ────────────────────────────────────────────────────────────

    @subgroup.command("set")
    @click.argument(item.param_name, type=item.param_type)
    def cmd_set(**kwargs: Any) -> None:
        """Set the value."""
        value = kwargs[item.param_name]
        item.setter(value)
        if item.needs_reload:
            from zhihu_cli.content.handlers.requests import reload_session

            reload_session()
        if item.show_on_newline:
            success(f"{item.display} set to:\n{item._format(value)}")
        else:
            success(f"{item.display} set to: {item._format(value)}")

    cmd_set.__doc__ = f"Set the {item.display.lower()}."

    # ── show ───────────────────────────────────────────────────────────

    @subgroup.command("show")
    def cmd_show() -> None:
        """Show the current value."""
        value = item.getter()
        if value is None and item.show_none_msg:
            info(item.show_none_msg)
        else:
            sep = "\n" if item.show_on_newline else " "
            echo(f"{f_label(item.label + ':')}{sep}{item._format(value)}")

    cmd_show.__doc__ = f"Show the current {item.display.lower()}."

    # ── clear ──────────────────────────────────────────────────────────

    @subgroup.command("clear")
    def cmd_clear() -> None:
        """Reset to default."""
        item.setter(item.default)
        if item.needs_reload:
            from zhihu_cli.content.handlers.requests import reload_session

            reload_session()
        if item.clear_msg:
            success(item.clear_msg)
        else:
            success(f"{item.display} reset to: {item._format(item.default)}")

    cmd_clear.__doc__ = f"Reset {item.display.lower()} to default."


# ═══════════════════════════════════════════════════════════════════════
#  Item declarations
# ═══════════════════════════════════════════════════════════════════════

_CONFIG_ITEMS: list[_Item] = [
    _Item(
        name="user-agent",
        display="User-Agent",
        label="Configured User-Agent",
        getter=get_user_agent,
        setter=set_user_agent,
        default=None,
        needs_reload=True,
        show_on_newline=True,
        show_none_msg="No custom User-Agent configured (using per-profile default).",
        clear_msg="Custom User-Agent cleared (now using per-profile default).",
        group_help="Manage the custom User-Agent override.",
    ),
    _Item(
        name="start-date",
        display="Default start date",
        label="Default start date",
        getter=cache_manager.get_start_date,
        setter=cache_manager.set_start_date,
        default="2026-01-16",
        param_name="date_str",
        group_help="Manage the default start date for data fetching.",
    ),
    _Item(
        name="smoothing",
        display="Chart smoothing method",
        label="Chart smoothing",
        getter=cache_manager.get_smoothing,
        setter=cache_manager.set_smoothing,
        default="ema",
        param_type=click.Choice(["ma", "ema"], case_sensitive=False),
        param_name="method",
        fmt=str.upper,
        group_help="""Manage the default smoothing method for charts (MA or EMA).

MA  = Simple Moving Average  — each day in the window counts equally.
EMA = Exponential Moving Average — recent days carry more weight.

Affects income trend lines, content-metrics MA lines, and follower-detail
MA lines.  Bollinger Bands and MACD always use their canonical definitions
(SMA for Bollinger, EMA for MACD) regardless of this setting.""",
    ),
    _Item(
        name="plot-dpi",
        display="Plot DPI",
        label="Plot DPI",
        getter=cache_manager.get_plot_dpi,
        setter=cache_manager.set_plot_dpi,
        default=300,
        param_type=int,
        param_name="dpi",
        group_help="Manage the DPI (resolution) for saved plot images.",
    ),
    _Item(
        name="app-za",
        display="x-app-za",
        label="x-app-za",
        getter=cache_manager.get_app_za,
        setter=cache_manager.set_app_za,
        default="OS=Android",
        needs_reload=True,
        group_help="""Manage the x-app-za header sent to mobile-api endpoints.

This header identifies the client device/platform to Zhihu's
mobile APIs (e.g. segment comments).  The default value
``OS=Android`` is the minimal non-leaking string — it contains
no device fingerprint.

If Zhihu starts rejecting requests you can provide a fuller
value, e.g.:

  zhihu config app-za set \\
    "OS=Android&Release=14&Model=Generic&VersionName=10.95.0&Product=com.zhihu.android".""",
    ),
    _Item(
        name="app-version",
        display="x-app-version",
        label="x-app-version",
        getter=cache_manager.get_app_version,
        setter=cache_manager.set_app_version,
        default="10.95.0",
        needs_reload=True,
        group_help="""Manage the x-app-version header sent to mobile-api endpoints.

This header identifies the Zhihu Android app version.  The
default is ``10.95.0``.  If Zhihu starts returning ``data:
null`` for mobile endpoints, bump this to a recent version
string.""",
    ),
]


# ═══════════════════════════════════════════════════════════════════════
#  Entry point
# ═══════════════════════════════════════════════════════════════════════


def register_config(main_group: click.Group) -> None:
    """Register the config command group onto *main_group*."""

    @main_group.group()
    def config() -> None:
        """Manage zhihu-cli configuration."""

    # ── register all simple config items ───────────────────────────────

    for item in _CONFIG_ITEMS:
        _register_item(config, item)

    # ── config llm (hand-rolled — multi-option set, dict-based show) ───

    @config.group("llm")
    def config_crank_llm() -> None:
        """Manage the cached LLM configuration for the crank extension."""

    @config_crank_llm.command("set")
    @click.option("--api-base", required=True, help="LLM API endpoint URL.")
    @click.option("--api-key", required=True, help="API key for authentication.")
    @click.option("--model", required=True, help="Model name to use.")
    def config_llm_set(api_base: str, api_key: str, model: str) -> None:
        """Cache LLM credentials for the crank archiver.

        \033[2mExample:\033[0m
          zhihu config llm set --api-base https://api.openai.com/v1 --api-key sk-xxx --model gpt-4
        """
        try:
            from zhihu_cli.extensions.crank.archiver import save_llm_config
        except ImportError:
            error("crank extension is not available (missing dependencies).")
            raise SystemExit(1)

        save_llm_config(api_base, api_key, model)
        success(f"LLM config saved:\n  {f_label('api_base:')} {api_base}\n  {f_label('model:')} {model}")

    @config_crank_llm.command("show")
    def config_llm_show() -> None:
        """Show the currently cached LLM configuration."""
        try:
            from zhihu_cli.extensions.crank.archiver import load_llm_config
        except ImportError:
            error("crank extension is not available (missing dependencies).")
            raise SystemExit(1)

        cfg = load_llm_config()
        if cfg:
            echo(f"{f_title('Cached LLM config:')}")
            for k, v in cfg.items():
                if k == "api_key" and v:
                    v = v[:8] + "..." if len(v) > 8 else v
                echo(f"  {f_label(k + ':')} {v}")
        else:
            info("No cached LLM config found.")

    @config_crank_llm.command("clear")
    def config_llm_clear() -> None:
        """Remove the cached LLM configuration."""
        try:
            from zhihu_cli.extensions.crank.archiver import LLM_CONFIG_PATH
        except ImportError:
            error("crank extension is not available (missing dependencies).")
            raise SystemExit(1)

        if os.path.exists(LLM_CONFIG_PATH):
            os.remove(LLM_CONFIG_PATH)
            success("Cached LLM config removed.")
        else:
            info("No cached LLM config to remove.")
