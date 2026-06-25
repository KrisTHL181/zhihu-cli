"""zhihu CLI — unified entry point for all Zhihu operations."""

import click

from zhihu_cli.commands.agora import register_agora
from zhihu_cli.commands.auth import register_auth
from zhihu_cli.commands.browse import register_browse
from zhihu_cli.commands.chat import register_chat
from zhihu_cli.commands.config import register_config
from zhihu_cli.commands.convert import register_convert
from zhihu_cli.commands.daemon import register_daemon
from zhihu_cli.commands.download import register_download
from zhihu_cli.commands.interact import register_interact
from zhihu_cli.commands.listen import register_listen
from zhihu_cli.commands.people import register_people
from zhihu_cli.commands.profile import register_profile
from zhihu_cli.commands.publish import register_publish
from zhihu_cli.commands.scrape import register_scrape
from zhihu_cli.commands.search import register_search
from zhihu_cli.commands.stats import register_stats
from zhihu_cli.commands.tools import register_tools
from zhihu_cli.extensions import discover_extensions


@click.group()
@click.version_option(version="0.1.0", prog_name="zhihu")
def main() -> None:
    """zhihu — Zhihu scraping, automation, and analysis toolkit.

    Authenticate once with \033[1mzhihu auth paste\033[0m, then use any command.
    """


# ── register built-in command groups ───────────────────────────────────────

register_agora(main)
register_auth(main)
register_browse(main)
register_chat(main)
register_config(main)
register_convert(main)
register_daemon(main)
register_download(main)
register_interact(main)
register_listen(main)
register_people(main)
register_profile(main)
register_publish(main)
register_scrape(main)
register_search(main)
register_stats(main)
register_tools(main)

# ── extensions ─────────────────────────────────────────────────────────────

# Auto-discover and register extension command groups.
for _ext_mod in discover_extensions():
    _ext_mod.register_cli(main)

if __name__ == "__main__":
    main()
