"""Agora (众裁) command group — review reported comments and vote."""

from __future__ import annotations

import os

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
    error,
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
    set_json_mode,
    success,
    warning,
)
from zhihu_cli.prompts import AGORA_VOTE_SYSTEM_PROMPT


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

    @agora.command("ai")
    @click.argument("discussion_id", required=False)
    @click.option(
        "--model",
        default=None,
        help="LLM model override (default: from cached config).",
    )
    @click.option(
        "--api-base",
        default=None,
        help="LLM API endpoint override.",
    )
    @click.option(
        "--api-key",
        default=None,
        help="LLM API key override.",
    )
    @click.option(
        "--json",
        "output_json",
        is_flag=True,
        default=False,
        help="Output one JSON object per vote as JSON lines.",
    )
    def agora_ai(
        discussion_id: str | None,
        model: str | None,
        api_base: str | None,
        api_key: str | None,
        output_json: bool,
    ) -> None:
        """Use AI to automatically vote on agora discussions (AI 自动投票).

        With DISCUSSION_ID, votes on that specific discussion once.

        Without DISCUSSION_ID, continuously fetches the next pending
        discussion, lets the LLM decide the vote, casts it, and repeats
        until all pending discussions are exhausted.

        Requires LLM config via ``zhihu config llm set`` or the
        ``LLM_API_BASE`` / ``LLM_API_KEY`` / ``LLM_MODEL`` env vars.

        \b
        Examples:
          zhihu agora ai                    # auto-vote all pending
          zhihu agora ai <discussion_id>     # vote on a specific one
          zhihu agora ai --json             # JSON-lines output for piping
        """
        set_json_mode(output_json)
        # Pre-load LLM config once (fail fast if not configured)
        _preflight = _call_llm_for_vote(
            {
                "comment": {"content": "test"},
                "report_reason": "",
                "report_note": "",
                "origin_title": "",
                "origin_url": "",
            },
            api_base=api_base,
            api_key=api_key,
            model=model,
            dry_run=True,
        )
        if _preflight is None:
            raise SystemExit(1)

        if discussion_id:
            # ── single-discussion mode ──────────────────────────────────
            _ai_vote_one(discussion_id, model, api_base, api_key, output_json)
        else:
            # ── loop-until-exhausted mode ───────────────────────────────
            voted_count = 0
            seen_ids: set[str] = set()
            while True:
                try:
                    data = fetch_court_page()
                except ValueError as e:
                    raise click.ClickException(str(e))

                disc = data.get("current_discussion")
                if not disc:
                    if voted_count == 0:
                        info("No pending discussions.")
                    else:
                        success(f"All done — {voted_count} discussion(s) voted.")
                    return

                disc_id = disc.get("id", data.get("discussion_id", ""))
                if not disc_id:
                    info("Could not resolve discussion ID, stopping.")
                    return

                # Safety: break if we've seen this ID before (prevents infinite loop)
                if disc_id in seen_ids:
                    info(f"Revisiting {disc_id} — stopping to avoid loop.")
                    return
                seen_ids.add(disc_id)

                # Skip if already voted
                if disc.get("my_vote", ""):
                    info(f"Already voted on {disc_id}, skipping.")
                    continue

                _ai_vote_one_disc(disc, disc_id, model, api_base, api_key, output_json)
                voted_count += 1


# ── AI voting helpers ────────────────────────────────────────────────────────


def _build_agora_vote_prompt(discussion: dict) -> str:
    """Build the user prompt for LLM voting from a discussion dict.

    :param discussion: Parsed discussion dict.
    :returns: Prompt string.
    """
    comment = discussion.get("comment", {})
    comment_content = comment.get("content", "(no content)")

    report_reason = discussion.get("report_reason", "")
    report_note = discussion.get("report_note", "")

    origin_title = discussion.get("origin_title", "")
    origin_url = discussion.get("origin_url", "")

    parts = ["## 被举报的评论\n"]
    parts.append(f"{comment_content}\n")

    parts.append("## 举报理由\n")
    parts.append(f"{report_reason}\n")
    if report_note:
        parts.append(f"补充说明：{report_note}\n")

    if origin_title:
        parts.append(f"## 评论所在的内容\n{origin_title}\n")
    if origin_url:
        parts.append(f"链接：{origin_url}\n")

    parts.append("请根据以上信息判断该评论是否违规，并只输出投票标签。")
    return "\n".join(parts)


def _resolve_llm_config(
    *,
    api_base: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
) -> tuple[str, str, str] | None:
    """Resolve LLM config from args, env, and cached file.

    :returns: ``(api_base, api_key, model)`` or ``None`` if api_key is missing.
    """
    try:
        from zhihu_cli.extensions.crank.archiver import load_llm_config
    except ImportError:
        error("Cannot import LLM config loader (crank extension not available).")
        return None

    cached = load_llm_config()

    _api_base = api_base or os.environ.get("LLM_API_BASE") or cached.get("api_base", "https://api.openai.com/v1")
    _api_key = api_key or os.environ.get("LLM_API_KEY") or cached.get("api_key", "")
    _model = model or os.environ.get("LLM_MODEL") or cached.get("model", "gpt-4o-mini")

    if not _api_key:
        error(
            "LLM API key not configured. Set it via:\n"
            "  zhihu config llm set --api-base <URL> --api-key <KEY> --model <NAME>\n"
            "Or set the LLM_API_KEY environment variable."
        )
        return None

    return _api_base, _api_key, _model


def _call_llm_for_vote(
    discussion: dict,
    *,
    api_base: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
    dry_run: bool = False,
) -> str | None:
    """Send discussion content to LLM and get a vote recommendation.

    :param discussion: Parsed discussion dict.
    :param api_base: Optional API endpoint override.
    :param api_key: Optional API key override.
    :param model: Optional model name override.
    :param dry_run: If True, only validate config without calling the LLM.
    :returns: Vote label (affirmative/abstain/dissenting) or None on failure.
    """
    resolved = _resolve_llm_config(api_base=api_base, api_key=api_key, model=model)
    if resolved is None:
        return None

    _api_base, _api_key, _model = resolved

    if dry_run:
        # Config is valid — signal success without an actual LLM call.
        return "affirmative"  # any non-None value works

    prompt = _build_agora_vote_prompt(discussion)

    try:
        from openai import OpenAI  # type: ignore[import-untyped]
    except ImportError:
        error("The 'openai' package is required for AI voting. Install with: pip install openai")
        return None

    client = OpenAI(base_url=_api_base, api_key=_api_key)

    try:
        response = client.chat.completions.create(
            model=_model,
            messages=[
                {"role": "system", "content": AGORA_VOTE_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            temperature=0.3,
            max_tokens=16,
            extra_body={"thinking": {"type": "disabled"}},
        )
        raw = response.choices[0].message.content
        if not raw:
            error("LLM returned empty response.")
            return None
        vote = raw.strip().lower()
        for v in VALID_VOTES:
            if v in vote:
                return v
        error(f"LLM returned unrecognized vote: {vote!r}")
        return None
    except Exception as e:
        error(f"LLM call failed: {e}")
        return None


def _ai_vote_one(
    discussion_id: str,
    model: str | None,
    api_base: str | None,
    api_key: str | None,
    output_json: bool,
) -> None:
    """Fetch a specific discussion, call the LLM, and cast the vote.

    :param discussion_id: The agora discussion ID.
    """
    try:
        data = fetch_court_page(discussion_id=discussion_id)
    except ValueError as e:
        raise click.ClickException(str(e))

    disc = data.get("current_discussion")
    if not disc:
        info(f"No discussion data found for ID: {discussion_id}")
        return

    disc_id = disc.get("id", discussion_id)
    _ai_vote_one_disc(disc, disc_id, model, api_base, api_key, output_json)


def _ai_vote_one_disc(
    disc: dict,
    disc_id: str,
    model: str | None,
    api_base: str | None,
    api_key: str | None,
    output_json: bool,
) -> None:
    """Call the LLM on *disc* and cast the vote.

    :param disc: Parsed discussion dict.
    :param disc_id: Discussion ID string.
    """
    comment = disc.get("comment", {})
    content = comment.get("content", "(no content)")
    report_reason = disc.get("report_reason", "")
    origin_title = disc.get("origin_title", "")

    # Display context
    if not output_json:
        section(f"AI 投票 — {disc_id}")
        echo(f"  {f_label('举报理由:')} {click.style(report_reason, fg='red', bold=True)}")
        echo(f"  {f_label('评论内容:')} {content[:200]}")
        if origin_title:
            echo(f"  {f_label('所在内容:')} {f_bold(origin_title)}")
        echo(f"  {f_dim('正在调用 AI 分析...')}")

    vote = _call_llm_for_vote(
        disc,
        api_base=api_base,
        api_key=api_key,
        model=model,
    )

    if not vote:
        error("AI voting failed — skipping this discussion.")
        return

    label = VOTE_LABELS.get(vote, vote)

    # Cast the vote
    try:
        vote_result = vote_discussion(disc_id, vote)
    except ValueError as e:
        raise click.BadParameter(str(e))

    result = {
        "discussion_id": disc_id,
        "report_reason": report_reason,
        "comment_content": content,
        "origin_title": origin_title,
        "ai_vote": vote,
        "ai_vote_label": VOTE_LABELS.get(vote, vote),
        "affirmative_count": vote_result["affirmative_count"],
        "dissenting_count": vote_result["dissenting_count"],
        "blind_test_wrong": vote_result["blind_test_wrong"],
        "blind_test_today_wrong_count": vote_result["blind_test_today_wrong_count"],
    }

    if output_json:
        print_json(result)
        return

    blank()
    success(f"已投票: {label}")
    echo(f"  {f_label('赞同 (affirmative):')} {f_num(vote_result['affirmative_count'])}")
    echo(f"  {f_label('反对 (dissenting):')}  {f_num(vote_result['dissenting_count'])}")
    if vote_result["blind_test_wrong"]:
        warning(f"盲测错误 (blind test wrong) — 今日: {f_num(vote_result['blind_test_today_wrong_count'])}")
    blank()


# ── original helper ──────────────────────────────────────────────────────────


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
