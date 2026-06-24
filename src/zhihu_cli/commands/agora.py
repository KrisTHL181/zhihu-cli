"""Agora (众裁) command group — review reported comments and vote."""

import click

from zhihu_cli.content.handlers import fmt_time
from zhihu_cli.content.handlers.agora import (
    VALID_VOTES,
    VOTE_LABELS,
    fetch_agora_me,
    fetch_comment_detail,
    fetch_court_page,
    fetch_reviews,
    vote_discussion,
)
from zhihu_cli.output import (
    blank,
    echo,
    f_bold,
    f_dim,
    f_green,
    f_label,
    f_meta,
    f_name,
    f_num,
    f_tag,
    f_title,
    f_url,
    info,
    item_index,
    print_json,
    section,
    warning,
)


def register_agora(main_group):
    """Register the agora command group onto *main_group*."""

    @main_group.group()
    def agora() -> None:
        """众裁 (community moderation) — review reported comments and vote."""

    @agora.command("next")
    @click.option("--discussion-id", "-d", default=None, help="Specific discussion ID to fetch")
    @click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
    def agora_next(discussion_id: str | None, output_json: bool) -> None:
        """Get the next agora discussion to judge (众裁案例).

        Fetches the court page and extracts the current discussion case.
        Use -d to request a specific discussion by ID.
        """
        try:
            data = fetch_court_page(discussion_id=discussion_id)
        except ValueError as e:
            raise click.ClickException(str(e))

        if output_json:
            print_json(data)
            return

        juror = data.get("juror_info", {})

        # Show juror status header
        if juror.get("is_juror"):
            today = juror.get("today_jury_count", 0)
            max_day = juror.get("max_day_jury_count", 20)
            remaining = max(0, max_day - today)
            echo(
                f"  {f_title('众裁官')} | {f_label('总投票:')} {f_num(juror.get('vote_count', 0))} | {f_label('今日:')} {f_num(today)}/{f_num(max_day)} ({f_label('剩余')} {f_num(remaining)})"
            )
        else:
            warning("你尚不是众裁官")

        disc = data.get("current_discussion")
        if not disc:
            disc_id = data.get("discussion_id", "")
            if disc_id:
                section(f"Discussion ID: {disc_id}")
                info("Discussion data not in initialData. Try fetching details with 'zhihu agora detail {disc_id}'.")
            else:
                section("No pending discussions. Check back later!")
            return

        blank()

        # Report reason
        reason = disc.get("report_reason", "")
        note = disc.get("report_note", "")
        echo(f"  {click.style(f'举报理由: {reason}', fg='red', bold=True)}")
        if note:
            echo(f"    {note}")
        blank()

        # The reported comment
        comment = disc.get("comment", {})
        _print_agora_comment(comment, disc.get("reported_user", ""))
        blank()

        # Origin context
        origin_title = disc.get("origin_title", "")
        origin_url = disc.get("origin_url", "")
        if origin_title:
            echo(f"  {f_label('评论所在内容:')} {f_bold(origin_title)}")
        if origin_url:
            echo(f"    {f_url(origin_url)}")
        blank()

        # Status
        status = disc.get("status", "")
        my_vote = disc.get("my_vote", "")
        status_str = f"状态: {status}"
        if my_vote:
            status_str += f"  我的投票: {my_vote}"
        echo(f"  {f_dim(status_str)}")

        if not my_vote and status == "Voting":
            blank()
            echo(
                "投票: zhihu agora vote {} -v {{affirmative,abstain,dissenting}}".format(
                    disc.get("id", data.get("discussion_id", "<id>"))
                )
            )

    @agora.command("me")
    @click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
    def agora_me(output_json: bool) -> None:
        """Show your agora (众裁) juror status and statistics."""
        data = fetch_agora_me()

        if output_json:
            print_json(data)
            return

        juror = data.get("juror_info", {})

        if not data.get("is_juror"):
            info("You are not a juror (众裁官).")
            return

        echo(f"  {f_green(f_bold('众裁官 (Juror)'))}")
        blank()
        echo(f"  {f_label('总投票 (total votes):')}      {f_num(juror.get('vote_count', 0))}")
        echo(f"  {f_label('总评审 (total reviews):')}     {f_num(juror.get('review_count', 0))}")
        echo(f"  {f_label('评审获赞 (review likes):')}    {f_num(juror.get('review_liked_count', 0))}")
        blank()
        echo(
            f"  {f_label('今日已裁 (today judged):')}    {f_num(juror.get('today_jury_count', 0))} / {f_num(juror.get('max_day_jury_count', 20))}"
        )
        blank()
        echo(f"  {f_label('本周投票 (week votes):')}      {f_num(juror.get('week_vote_count', 0))}")
        echo(f"  {f_label('本周评审 (week reviews):')}    {f_num(juror.get('week_review_count', 0))}")
        echo(f"  {f_label('本周获赞 (week likes):')}      {f_num(juror.get('week_review_liked_count', 0))}")

    @agora.command("reviews")
    @click.argument("discussion_id")
    @click.option("--limit", type=int, default=20, help="Items per page")
    @click.option("--max", "-n", "max_items", type=int, default=None, help="Max total items")
    @click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
    def agora_reviews(discussion_id: str, limit: int, max_items: int | None, output_json: bool) -> None:
        """List review cases in an agora discussion."""
        items = fetch_reviews(discussion_id, limit=limit, max_items=max_items)

        if output_json:
            print_json(items)
            return

        if not items:
            info("No review cases found.")
            return

        for i, item in enumerate(items, 1):
            comment_content = item.get("comment_content", "") or "(no content)"
            author = item.get("comment_author", {})
            author_name = author.get("name", "unknown") if isinstance(author, dict) else str(author)
            reason = item.get("reason", "") or "(no reason)"
            status = item.get("status", "")
            my_vote = item.get("my_vote", "")

            status_str = f" {f_tag(status)}" if status else ""
            vote_str = f" {f_label('my_vote=')}{my_vote}" if my_vote else ""

            echo(f"  {item_index(i)} {f_bold(author_name)}{status_str}{vote_str}")
            echo(f"    {f_label('comment:')} {comment_content[:200]}")
            if reason:
                echo(f"    {f_label('reason:')} {reason}")
            echo(
                f"    {f_label('赞同:')} {f_num(item.get('affirmative_count', 0))}  {f_label('反对:')} {f_num(item.get('dissenting_count', 0))}"
            )
            blank()

    @agora.command("detail")
    @click.argument("discussion_id")
    @click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
    def agora_detail(discussion_id: str, output_json: bool) -> None:
        """Show the reported comment detail for an agora discussion."""
        detail = fetch_comment_detail(discussion_id)

        if output_json:
            print_json(detail)
            return

        comment = detail.get("comment", {})
        author = comment.get("author", {})

        echo(f"  {f_label('Resource:')} {detail.get('resource_id', '?')}")
        echo(f"  {f_label('Reported comment ID:')} {detail.get('reported_comment_id', '?')}")
        blank()

        author_name = author.get("name", "unknown")
        echo(f"  {f_label('Author:')} {f_bold(author_name)}")
        if author.get("headline"):
            echo(f"    {author['headline']}")
        echo(f"    {f_label('url_token:')} {author.get('url_token', '?')}")
        blank()

        cid = comment.get("id", "?")
        echo(f"  {f_label(f'Comment (id={cid}):')}")
        echo(f"    {comment.get('content', '(no content)')}")
        blank()
        echo(
            f"    {f_label('created:')} {f_meta(str(comment.get('created_time', '?')))}  "
            f"{f_label('votes:')} {f_num(comment.get('vote_count', 0))}  "
            f"{f_label('child_comments:')} {f_num(comment.get('child_comment_count', 0))}"
        )
        echo(f"    {f_label('url:')} {f_url(str(comment.get('url', '?')))}")
        blank()

        children = detail.get("child_comments", [])
        if children:
            echo(f"  {f_label(f'Child comments ({len(children)}):')}")
            for cc in children:
                cc_content = cc.get("content", "")[:150]
                cc_author = cc.get("author", {}).get("member", {}).get("name", "?")
                echo(f"    [{f_name(cc_author)}] {cc_content}")

    @agora.command("vote")
    @click.argument("discussion_id")
    @click.option(
        "--vote",
        "-v",
        "vote_type",
        required=True,
        type=click.Choice(VALID_VOTES),
        help="Vote choice",
    )
    @click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
    def agora_vote(discussion_id: str, vote_type: str, output_json: bool) -> None:
        """Cast a vote on an agora discussion (众裁投票).

        \b
        Vote types:
          affirmative  — 赞同 (agree the comment should be removed)
          abstain      — 弃权 (abstain)
          dissenting   — 反对 (dissent, the comment should stay)
        """
        try:
            result = vote_discussion(discussion_id, vote_type)
        except ValueError as e:
            raise click.BadParameter(str(e))

        if output_json:
            print_json(result)
            return

        label = VOTE_LABELS.get(vote_type, vote_type)
        echo(f"  {f_label('Vote:')} {label}")
        echo(f"  {f_label('赞同 (affirmative):')} {f_num(result['affirmative_count'])}")
        echo(f"  {f_label('反对 (dissenting):')}  {f_num(result['dissenting_count'])}")
        if result["blind_test_wrong"]:
            warning("盲测错误 (blind test wrong)")
            echo(f"  {f_label('今日盲测错误:')} {f_num(result['blind_test_today_wrong_count'])}")


def _print_agora_comment(comment: dict, reported_user: str) -> None:
    """Print a single comment block for agora display."""
    author = comment.get("author", {})
    author_name = author.get("name", "unknown") if isinstance(author, dict) else str(author)
    headline = author.get("headline", "") if isinstance(author, dict) else ""
    content = comment.get("content", "(no content)")
    created = comment.get("created_time", 0)
    votes = comment.get("vote_count", 0)
    url = comment.get("url", "")

    reported_str = f" ({reported_user})" if reported_user else ""
    echo(f"  {f_label('被举报评论')} — {f_bold(author_name)}{reported_str}")
    if headline:
        echo(f"    {f_dim(headline)}")
    blank()
    echo(f"    {content}")
    blank()
    echo(f"    {f_label('赞同:')} {f_num(votes)}  {f_label('时间:')} {f_meta(fmt_time(created))}  {f_url(url)}")
