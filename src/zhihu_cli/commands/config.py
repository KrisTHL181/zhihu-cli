"""Config command group for zhihu-cli."""

import os

import click

from zhihu_cli.content.handlers import get_user_agent, set_user_agent
from zhihu_cli.content.handlers.cache_manager import cache_manager
from zhihu_cli.output import (
    echo,
    error,
    f_label,
    f_title,
    info,
    success,
)


def register_config(main_group):
    """Register the config command group onto *main_group*."""

    @main_group.group()
    def config() -> None:
        """Manage zhihu-cli configuration."""

    @config.group("user-agent")
    def config_user_agent() -> None:
        """Manage the custom User-Agent override."""

    @config_user_agent.command("set")
    @click.argument("user_agent")
    def config_ua_set(user_agent: str) -> None:
        """Set a custom User-Agent for all requests.

        \033[2mExample:\033[0m
          zhihu config user-agent set "Mozilla/5.0 ... Chrome/145.0.0.0 Safari/537.36"
        """
        set_user_agent(user_agent)
        from zhihu_cli.content.handlers.requests import reload_session

        reload_session()
        success(f"User-Agent set to:\n{user_agent}")

    @config_user_agent.command("show")
    def config_ua_show() -> None:
        """Show the currently configured User-Agent."""
        ua = get_user_agent()
        if ua:
            echo(f"{f_label('Configured User-Agent:')}\n{ua}")
        else:
            info("No custom User-Agent configured (using per-profile default).")

    @config_user_agent.command("clear")
    def config_ua_clear() -> None:
        """Remove the custom User-Agent override."""
        set_user_agent(None)
        from zhihu_cli.content.handlers.requests import reload_session

        reload_session()
        success("Custom User-Agent cleared (now using per-profile default).")

    # ── config start-date ─────────────────────────────────────────────────────

    @config.group("start-date")
    def config_start_date() -> None:
        """Manage the default start date for data fetching."""

    @config_start_date.command("set")
    @click.argument("date_str")
    def config_sd_set(date_str: str) -> None:
        """Set the default start date for creator-tools data fetching.

        DATE_STR must be in YYYY-MM-DD format.

        \033[2mExample:\033[0m
          zhihu config start-date set 2024-01-01
        """
        cache_manager.set_start_date(date_str)
        success(f"Default start date set to: {date_str}")

    @config_start_date.command("show")
    def config_sd_show() -> None:
        """Show the currently configured default start date."""
        date_str = cache_manager.get_start_date()
        echo(f"{f_label('Default start date:')} {date_str}")

    @config_start_date.command("clear")
    def config_sd_clear() -> None:
        """Reset the default start date to the built-in default (2026-01-16)."""
        cache_manager.set_start_date("2026-01-16")
        success("Default start date reset to: 2026-01-16")

    # ── config smoothing ──────────────────────────────────────────────────────

    @config.group("smoothing")
    def config_smoothing() -> None:
        """Manage the default smoothing method for charts (MA or EMA).

        MA  = Simple Moving Average  — each day in the window counts equally.
        EMA = Exponential Moving Average — recent days carry more weight.

        Affects income trend lines, content-metrics MA lines, and follower-detail
        MA lines.  Bollinger Bands and MACD always use their canonical definitions
        (SMA for Bollinger, EMA for MACD) regardless of this setting.
        """

    @config_smoothing.command("set")
    @click.argument("method", type=click.Choice(["ma", "ema"], case_sensitive=False))
    def config_smoothing_set(method: str) -> None:
        """Set the chart smoothing method.

        \033[2mExamples:\033[0m
          zhihu config smoothing set ema
          zhihu config smoothing set ma
        """
        cache_manager.set_smoothing(method.lower())
        success(f"Chart smoothing method set to: {method.upper()}")

    @config_smoothing.command("show")
    def config_smoothing_show() -> None:
        """Show the currently configured smoothing method."""
        method = cache_manager.get_smoothing()
        echo(f"{f_label('Chart smoothing:')} {method.upper()}")

    @config_smoothing.command("clear")
    def config_smoothing_clear() -> None:
        """Reset smoothing to the default (EMA)."""
        cache_manager.set_smoothing("ema")
        success("Chart smoothing method reset to: EMA")

    # ── config plot-dpi ───────────────────────────────────────────────────────

    @config.group("plot-dpi")
    def config_plot_dpi() -> None:
        """Manage the DPI (resolution) for saved plot images.

        Higher DPI = sharper images but larger file sizes.
        Default is 300.  Valid range: 72–1200.
        """

    @config_plot_dpi.command("set")
    @click.argument("dpi", type=int)
    def config_plot_dpi_set(dpi: int) -> None:
        """Set the DPI for saved plots.

        \033[2mExamples:\033[0m
          zhihu config plot-dpi set 150
          zhihu config plot-dpi set 600
        """
        cache_manager.set_plot_dpi(dpi)
        success(f"Plot DPI set to: {dpi}")

    @config_plot_dpi.command("show")
    def config_plot_dpi_show() -> None:
        """Show the currently configured plot DPI."""
        dpi = cache_manager.get_plot_dpi()
        echo(f"{f_label('Plot DPI:')} {dpi}")

    @config_plot_dpi.command("clear")
    def config_plot_dpi_clear() -> None:
        """Reset plot DPI to the default (300)."""
        cache_manager.set_plot_dpi(300)
        success("Plot DPI reset to: 300")

    # ── config crank-llm ──────────────────────────────────────────────────────

    @config.group("crank-llm")
    def config_crank_llm() -> None:
        """Manage the cached LLM configuration for the crank extension."""

    @config_crank_llm.command("set")
    @click.option("--api-base", required=True, help="LLM API endpoint URL.")
    @click.option("--api-key", required=True, help="API key for authentication.")
    @click.option("--model", required=True, help="Model name to use.")
    def config_llm_set(api_base: str, api_key: str, model: str) -> None:
        """Cache LLM credentials for the crank archiver.

        \033[2mExample:\033[0m
          zhihu config crank-llm set --api-base https://api.openai.com/v1 --api-key sk-xxx --model gpt-4
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
