"""People command group — look up user profiles and content."""

from __future__ import annotations

import click

from zhihu_cli.commands._helpers import _extract_url_token
from zhihu_cli.content.handlers.people import (
    fetch_member_activities,
    fetch_member_answers,
    fetch_member_articles,
    fetch_member_error_message,
    fetch_member_pins,
    fetch_member_profile,
    fetch_member_questions,
)
from zhihu_cli.content.handlers.waterfall import PAGE_SIZE
from zhihu_cli.output import (
    blank,
    echo,
    error,
    f_bold,
    f_dim,
    f_green,
    f_label,
    f_meta,
    f_num,
    f_tag,
    f_url,
    heading,
    info,
    print_json,
    set_json_mode,
    stat,
    warning,
)

# ── helpers ──


def _print_stat(label: str, value: int) -> None:
    """Print a labeled stat line with dimmed label."""
    stat(label, value)


def _print_content_item(item: dict, show_type: bool = False) -> None:
    """Print a single content item in a compact format."""
    ttype = item.get("type", "")
    type_label = f"{f_tag(ttype)} " if show_type else ""
    title = item.get("title", "") or item.get("excerpt", "") or "(no title)"
    created = item.get("created_time", "")
    votes = item.get("voteup_count", 0)
    comments = item.get("comment_count", 0)

    parts = [f_meta(created)]
    if votes:
        parts.append(f_green(f"+{votes}"))
    if comments:
        parts.append(f"{f_num(comments)} {f_dim('comments')}")
    if "answer_count" in item and item["answer_count"]:
        parts.append(f"{f_num(item['answer_count'])} {f_dim('answers')}")
    if "follower_count" in item and item["follower_count"]:
        parts.append(f"{f_num(item['follower_count'])} {f_dim('followers')}")

    echo(f"  {type_label}{f_bold(title[:100])}")
    echo(f"  {f_dim('  '.join(parts))}")
    echo(f"  {f_url(item.get('url', ''))}")
    blank()


def _print_activity_item(item: dict) -> None:
    """Print a single parsed activity feed item."""
    verb = item.get("action_text", "")
    title = item.get("title", "") or item.get("excerpt", "") or "(no title)"
    created = item.get("created_time", "")
    url = item.get("url", "")
    votes = item.get("voteup_count", 0)
    comments = item.get("comment_count", 0)
    ttype = item.get("target_type", "")
    sticky = item.get("is_sticky", False)

    # Label: action_text + optional type tag
    prefix = f_tag(ttype) if ttype else ""
    sticky_marker = f_dim(" [置顶]") if sticky else ""
    echo(f"  {prefix} {f_bold(verb)}{sticky_marker}")

    echo(f"  {f_bold(title[:120])}")
    parts = [f_meta(created)]
    if votes:
        parts.append(f_green(f"+{votes}"))
    if comments:
        parts.append(f"{f_num(comments)} {f_dim('comments')}")
    echo(f"  {f_dim('  '.join(parts))}")
    if url:
        echo(f"  {f_url(url)}")
    blank()


def _echo_profile_extras(profile: dict) -> None:
    """Echo the extra profile attributes only available from the API."""
    attrs = []
    following = profile.get("is_following")
    if following is not None:
        attrs.append(f"following: {'yes' if following else 'no'}")
    if ip := profile.get("ip_info"):
        attrs.append(ip)
    if business := profile.get("business"):
        attrs.append(business)
    if school := ", ".join(profile.get("educations", [])):
        attrs.append(school)
    if company := ", ".join(profile.get("employments", [])):
        attrs.append(company)
    if attrs:
        echo(f"  {f_label('About:')} {f_dim('  ·  '.join(attrs))}")


def _show_profile_rich(profile: dict) -> None:
    """Display a user profile using Rich if available, otherwise plain text."""
    try:
        from rich.console import Console
        from rich.panel import Panel
        from rich.table import Table
        from rich.text import Text

        console = Console()
        name = profile.get("name", "Unknown")
        headline = profile.get("headline", "")
        url_token = profile.get("url_token", "")

        header = Text(name, style="bold cyan")
        if headline:
            header.append(f"\n{headline}", style="dim")

        table = Table(show_header=False, box=None, padding=(0, 2))
        table.add_column(style="dim")
        table.add_column()
        table.add_column(style="dim")
        table.add_column()
        table.add_row(
            f"followers: {profile.get('follower_count', 0)}",
            f"following: {profile.get('following_count', 0)}",
            f"answers: {profile.get('answer_count', 0)}",
            f"articles: {profile.get('articles_count', 0)}",
        )
        table.add_row(
            f"pins: {profile.get('pins_count', 0)}",
            f"questions: {profile.get('question_count', 0)}",
            f"upvotes: {profile.get('voteup_count', 0)}",
            f"thanked: {profile.get('thanked_count', 0)}",
        )

        panel = Panel(
            table,
            title=header.plain[:40],
            border_style="cyan",
            padding=(1, 2),
        )
        console.print(panel)
        echo(f"  {f_label('Profile:')} {f_url(f'https://www.zhihu.com/people/{url_token}')}")
        uid = profile.get("uid", "")
        if uid:
            echo(f"  {f_label('User ID:')} {f_num(uid)}")
        user_hash = profile.get("id", "")
        if user_hash:
            echo(f"  {f_label('User Hash:')} {f_dim(user_hash)}")
        _echo_profile_extras(profile)
        blank()
    except ImportError:
        echo(f"\n{f_bold(profile.get('name', 'Unknown'))}")
        if headline := profile.get("headline"):
            echo(f"  {f_dim(headline)}")
        url_token = profile.get("url_token", "")
        echo(f"  {f_url(f'https://www.zhihu.com/people/{url_token}')}")
        uid = profile.get("uid", "")
        if uid:
            echo(f"  {f_label('User ID:')} {f_num(uid)}")
        user_hash = profile.get("id", "")
        if user_hash:
            echo(f"  {f_label('User Hash:')} {f_dim(user_hash)}")
        blank()
        _print_stat("Followers", profile.get("follower_count", 0))
        _print_stat("Following", profile.get("following_count", 0))
        _print_stat("Answers", profile.get("answer_count", 0))
        _print_stat("Articles", profile.get("articles_count", 0))
        _print_stat("Pins", profile.get("pins_count", 0))
        _print_stat("Questions", profile.get("question_count", 0))
        _print_stat("Upvotes received", profile.get("voteup_count", 0))
        blank()


# One row per content section of ``people show``: JSON key, display label, and
# the handler that fetches it.  Both output branches read this table, so a
# section cannot be added to one and forgotten in the other.
_CONTENT_SECTIONS = (
    ("answers", "Answers", fetch_member_answers),
    ("articles", "Articles", fetch_member_articles),
    ("pins", "Pins", fetch_member_pins),
    ("questions", "Questions", fetch_member_questions),
)


def _fetch_section(
    fetch_fn,
    url_token: str,
    section_title: str,
    max_items: int | None = None,
) -> list | None:
    """Fetch one content section, reporting failure instead of hiding it.

    A section that fails is not fatal — ``people show`` renders whichever
    sections do come back — but the failure is announced rather than folded
    into an empty list.  The warning goes to whichever stream
    :func:`~zhihu_cli.output.warning` is using for the current mode, so a
    ``--json`` run keeps stdout parseable.

    :param fetch_fn: Handler taking ``(url_token, limit, max_items)``.
    :param url_token: The profile to read.
    :param section_title: Display name, used in the failure warning.
    :param max_items: Cap on items fetched per section.
    :returns: the fetched items, or ``None`` when the fetch failed.  An empty
        list means the user genuinely has no content of that type, which is
        not an error and is not reported.
    """
    try:
        return fetch_fn(url_token, limit=PAGE_SIZE, max_items=max_items)
    except Exception as e:
        warning(f"Could not fetch {section_title}: {e}")
        return None


def _list_content_section(
    fetch_fn,
    url_token: str,
    section_title: str,
    max_items: int | None = None,
    *,
    show_type: bool = False,
) -> list | None:
    """Fetch and display a content section.

    :returns: the fetched items, or ``None`` when the fetch failed.
    """
    items = _fetch_section(fetch_fn, url_token, section_title, max_items)
    if items:
        heading(f"Recent {len(items)} {section_title}")
        for item in items:
            _print_content_item(item, show_type=show_type)
    return items


# ── register ──


def register_people(main_group):
    """Register the people command group on the main Click group."""

    @main_group.group()
    def people():
        """Look up user profiles and content."""

    @people.command("show")
    @click.argument("url_token")
    @click.option(
        "--max",
        "-n",
        "max_items",
        type=int,
        default=5,
        help="Max items per content type (default: 5 — this is a profile overview, not a full dump)",
    )
    @click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
    def people_show(url_token: str, max_items: int | None, output_json: bool) -> None:
        """Display a user's profile and recent content across all types.

        URL_TOKEN can be a Zhihu url_token (e.g. "zhangsan") or a full profile URL
        (e.g. https://www.zhihu.com/people/zhangsan).
        """
        set_json_mode(output_json)
        token = _extract_url_token(url_token)

        info(f"Fetching profile for {token}...")
        profile = fetch_member_profile(token)
        if profile is None:
            reason = fetch_member_error_message(token)
            if reason:
                error(f"Profile unavailable: {reason}")
            else:
                error(f"Could not fetch profile for '{token}'. Check the token and try again.")
            raise SystemExit(1)

        if output_json:
            result: dict = {"profile": profile}
            for key, label, fn in _CONTENT_SECTIONS:
                result[key] = _fetch_section(fn, token, label, max_items)
            print_json(result)
            return

        _show_profile_rich(profile)

        for _key, label, fn in _CONTENT_SECTIONS:
            _list_content_section(fn, token, label, max_items)

    @people.command("answers")
    @click.argument("url_token")
    @click.option("--max", "-n", "max_items", type=int, default=None, help="Max total items (default: all)")
    @click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
    def people_answers(url_token: str, max_items: int | None, output_json: bool) -> None:
        """List a user's answers."""
        set_json_mode(output_json)
        token = _extract_url_token(url_token)
        info(f"Fetching answers for {token}...")
        items = fetch_member_answers(token, limit=PAGE_SIZE, max_items=max_items)
        if output_json:
            print_json(items)
            return
        if not items:
            info("No answers found.")
            return
        for item in items:
            _print_content_item(item)
        echo(f"  {f_dim(f'── {len(items)} answers total')}")

    @people.command("articles")
    @click.argument("url_token")
    @click.option("--max", "-n", "max_items", type=int, default=None, help="Max total items (default: all)")
    @click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
    def people_articles(url_token: str, max_items: int | None, output_json: bool) -> None:
        """List a user's articles."""
        set_json_mode(output_json)
        token = _extract_url_token(url_token)
        info(f"Fetching articles for {token}...")
        items = fetch_member_articles(token, limit=PAGE_SIZE, max_items=max_items)
        if output_json:
            print_json(items)
            return
        if not items:
            info("No articles found.")
            return
        for item in items:
            _print_content_item(item)
        echo(f"  {f_dim(f'── {len(items)} articles total')}")

    @people.command("pins")
    @click.argument("url_token")
    @click.option("--max", "-n", "max_items", type=int, default=None, help="Max total items (default: all)")
    @click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
    def people_pins(url_token: str, max_items: int | None, output_json: bool) -> None:
        """List a user's pins (想法)."""
        set_json_mode(output_json)
        token = _extract_url_token(url_token)
        info(f"Fetching pins for {token}...")
        items = fetch_member_pins(token, limit=PAGE_SIZE, max_items=max_items)
        if output_json:
            print_json(items)
            return
        if not items:
            info("No pins found.")
            return
        for item in items:
            t = f_meta(item.get("created_time", ""))
            content = item.get("content_text", "") or item.get("excerpt", "")
            v = item.get("voteup_count", 0)
            c = item.get("comment_count", 0)
            echo(f"  {f_dim(content[:120])}")
            echo(f"  {t}  {f_green(f'+{v}')}  {f_num(c)} {f_dim('comments')}")
            echo(f"  {f_url(item.get('url', ''))}")
            blank()
        echo(f"  {f_dim(f'── {len(items)} pins total')}")

    @people.command("questions")
    @click.argument("url_token")
    @click.option("--max", "-n", "max_items", type=int, default=None, help="Max total items (default: all)")
    @click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
    def people_questions(url_token: str, max_items: int | None, output_json: bool) -> None:
        """List questions asked by a user."""
        set_json_mode(output_json)
        token = _extract_url_token(url_token)
        info(f"Fetching questions for {token}...")
        items = fetch_member_questions(token, limit=PAGE_SIZE, max_items=max_items)
        if output_json:
            print_json(items)
            return
        if not items:
            info("No questions found (this endpoint may not be available).")
            return
        for item in items:
            _print_content_item(item)
        echo(f"  {f_dim(f'── {len(items)} questions total')}")

    @people.command("activities")
    @click.argument("url_token")
    @click.option("--max", "-n", "max_items", type=int, default=None, help="Max total items (default: all)")
    @click.option(
        "--creations-only", is_flag=True, default=False, help="Only show content creations (answers, pins, articles)"
    )
    @click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
    def people_activities(url_token: str, max_items: int | None, creations_only: bool, output_json: bool) -> None:
        """List a user's recent activities (new answers, articles, pins, etc.).

        By default shows ALL activities including votes and follows.
        Use --creations-only to filter to content creation only.
        """
        set_json_mode(output_json)
        token = _extract_url_token(url_token)
        info(f"Fetching activities for {token}...")
        items = fetch_member_activities(token, limit=PAGE_SIZE, max_items=max_items, creations_only=creations_only)
        if output_json:
            print_json(items)
            return
        if not items:
            info("No activities found.")
            return
        for item in items:
            _print_activity_item(item)
        echo(f"  {f_dim(f'── {len(items)} activities total')}")
