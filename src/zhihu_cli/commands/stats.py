import click

from zhihu_cli.content.handlers.stats import get_item_stats
from zhihu_cli.output import (
    blank,
    echo,
    error,
    f_bold,
    f_dim,
    f_label,
    f_num,
    f_url,
    print_json,
)


def register_stats(main_group):
    @main_group.command("stats")
    @click.argument("url")
    @click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
    @click.option(
        "--with-share",
        is_flag=True,
        default=False,
        help="Include share count via creator API (author only)",
    )
    @click.option(
        "--with-pv",
        is_flag=True,
        default=False,
        help="Include page views (阅读量) via creator API (author only)",
    )
    @click.option(
        "--with-show",
        is_flag=True,
        default=False,
        help="Include impressions (展现量) via creator API (author only)",
    )
    def stats(url: str, output_json: bool, with_share: bool, with_pv: bool, with_show: bool) -> None:
        """Show engagement summary (赞同/收藏/评论/喜欢) for a Zhihu post.

        URL can be an article, answer, or pin (想法).

        Use --with-share / --with-pv / --with-show to also fetch data from the
        creator analytics API. This only works when you are the author of the content.
        """
        try:
            result = get_item_stats(url, with_share=with_share, with_pv=with_pv, with_show=with_show)
        except ValueError as e:
            error(f"{e}")
            raise SystemExit(1)

        if output_json:
            print_json(result)
            return

        echo(f"  {f_bold(result['title'])}")
        echo(f"  {f_url(result['url'])}")
        blank()
        echo(f"  {f_label('赞同 (voteup):')}  {f_num(result['voteup_count'])}")
        echo(f"  {f_label('收藏 (favorite):')} {f_num(result['favlists_count'])}")
        echo(f"  {f_label('评论 (comment):')}  {f_num(result['comment_count'])}")
        echo(f"  {f_label('喜欢 (thanks):')}   {f_num(result['thanks_count'])}")
        if with_pv:
            pv = result.get("pv")
            if pv is None:
                echo(f"  {f_label('阅读 (pv):')}       {f_dim('(not author / unavailable)')}")
            else:
                echo(f"  {f_label('阅读 (pv):')}       {f_num(pv)}")
        if with_show:
            show = result.get("show")
            if show is None:
                echo(f"  {f_label('展现 (show):')}     {f_dim('(not author / unavailable)')}")
            else:
                echo(f"  {f_label('展现 (show):')}     {f_num(show)}")
        if with_share:
            sc = result.get("share_count")
            if sc is None:
                echo(f"  {f_label('分享 (share):')}    {f_dim('(not author / unavailable)')}")
            else:
                echo(f"  {f_label('分享 (share):')}    {f_num(sc)}")
