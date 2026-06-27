"""People command group — look up user profiles and content."""

import re

import click

from zhihu_cli.content.handlers.people import (
    fetch_member_answers,
    fetch_member_articles,
    fetch_member_pins,
    fetch_member_profile,
    fetch_member_questions,
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
    f_url,
    heading,
    info,
    item_index,
    print_json,
    stat,
)

# ── helpers ──


def _extract_url_token(token_or_url: str) -> str:
    """Extract a Zhihu url_token from a full profile URL or return as-is."""
    m = re.search(r"zhihu\.com/people/([^/?]+)", token_or_url)
    if m:
        return m.group(1)
    return token_or_url.rstrip("/").split("/")[-1]


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
        blank()
    except ImportError:
        echo(f"\n{f_bold(profile.get('name', 'Unknown'))}")
        if headline := profile.get("headline"):
            echo(f"  {f_dim(headline)}")
        echo(f"  {f_url(f'https://www.zhihu.com/people/{profile.get("url_token", "")}')}")
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


def _list_content_section(
    fetch_fn,
    url_token: str,
    section_title: str,
    limit: int = 5,
    *,
    show_type: bool = False,
) -> list:
    """Fetch and display a content section. Returns the fetched items."""
    try:
        items = fetch_fn(url_token, limit=limit, max_items=limit)
    except Exception:
        return []

    if items:
        heading(f"Recent {len(items)} {section_title}")
        for item in items:
            _print_content_item(item, show_type=show_type)
    return items


def _display_following_items(items: list[dict], totals: int | None = None) -> None:
    """Display a list of following items in terminal mode."""
    for i, item in enumerate(items, 1):
        ttype = item.get("type", "?")

        if ttype == "user":
            name = item.get("name", "")
            headline = item.get("headline", "")
            is_followed = item.get("is_followed", False)
            is_following = item.get("is_following", False)
            mutual = f" {f_green('[互关]')}" if (is_followed and is_following) else ""
            f_cnt = item.get("follower_count", 0)
            a_cnt = item.get("answer_count", 0)
            art_cnt = item.get("articles_count", 0)
            stats = f"{f_label('followers:')} {f_num(f_cnt)}  {f_label('answers:')} {f_num(a_cnt)}  {f_label('articles:')} {f_num(art_cnt)}"
            echo(f"  {item_index(i)} {f_bold(name)}{mutual}")
            if headline:
                echo(f"    {f_dim(headline[:120])}")
            echo(f"    {f_dim(stats)}")
            echo(f"    {f_url(item.get('url', ''))}")

        elif ttype == "topic":
            name = item.get("name", "")
            intro = item.get("introduction", "") or item.get("excerpt", "")
            f_cnt = item.get("followers_count", 0)
            q_cnt = item.get("questions_count", 0)
            stats = f"{f_label('followers:')} {f_num(f_cnt)}  {f_label('questions:')} {f_num(q_cnt)}"
            echo(f"  {item_index(i)} {f_bold(name)} {f_tag('topic')}")
            if intro:
                echo(f"    {f_dim(intro[:120])}")
            echo(f"    {f_dim(stats)}")
            echo(f"    {f_url(item.get('url', ''))}")

        elif ttype == "question":
            title = item.get("title", "") or item.get("excerpt", "") or "(no title)"
            a_cnt = item.get("answer_count", 0)
            f_cnt = item.get("follower_count", 0)
            ctime = item.get("created_time", "")
            stats = f"{f_label('answers:')} {f_num(a_cnt)}  {f_label('followers:')} {f_num(f_cnt)}  {f_label('created:')} {f_meta(ctime)}"
            echo(f"  {item_index(i)} {f_bold(title[:120])}")
            echo(f"    {f_dim(stats)}")
            echo(f"    {f_url(item.get('url', ''))}")

        elif ttype == "column":
            title = item.get("title", "") or "(no title)"
            desc = item.get("description", "") or item.get("excerpt", "")
            creator = item.get("creator", "")
            f_cnt = item.get("followers_count", 0)
            art_cnt = item.get("articles_count", 0)
            stats = f"{f_label('followers:')} {f_num(f_cnt)}  {f_label('articles:')} {f_num(art_cnt)}"
            echo(f"  {item_index(i)} {f_bold(title)} {f_tag('column')}")
            if creator:
                echo(f"    {f_name(creator)}")
            if desc:
                echo(f"    {f_dim(desc[:120])}")
            echo(f"    {f_dim(stats)}")
            echo(f"    {f_url(item.get('url', ''))}")

        elif ttype == "collection":
            title = item.get("title", "") or "(no title)"
            desc = item.get("description", "")
            creator_name = item.get("creator_name", "")
            a_cnt = item.get("answer_count", 0)
            f_cnt = item.get("follower_count", 0)
            stats = f"{f_label('items:')} {f_num(a_cnt)}  {f_label('followers:')} {f_num(f_cnt)}"
            echo(f"  {item_index(i)} {f_bold(title)} {f_tag('collection')}")
            if creator_name:
                echo(f"    {f_label('by')} {f_name(creator_name)}")
            if desc:
                echo(f"    {f_dim(desc[:120])}")
            echo(f"    {f_dim(stats)}")
            echo(f"    {f_url(item.get('url', ''))}")

        blank()

    if items:
        total_str = f"/{totals}" if totals else ""
        echo(f"  {f_dim(f'── {len(items)}{total_str} items')}")


# ── register ──


def register_people(main_group):
    """Register the people command group on the main Click group."""

    @main_group.group()
    def people():
        """Look up user profiles and content."""

    @people.command("show")
    @click.argument("url_token")
    @click.option("--limit", "-n", type=int, default=5, help="Items per content type (default: 5)")
    @click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
    def people_show(url_token: str, limit: int, output_json: bool) -> None:
        """Display a user's profile and recent content across all types.

        URL_TOKEN can be a Zhihu url_token (e.g. "zhangsan") or a full profile URL
        (e.g. https://www.zhihu.com/people/zhangsan).
        """
        token = _extract_url_token(url_token)

        info(f"Fetching profile for {token}...")
        profile = fetch_member_profile(token)
        if profile is None:
            error(f"Could not fetch profile for '{token}'. Check the token and try again.")
            raise SystemExit(1)

        if output_json:
            result: dict = {"profile": profile}
            for key, fn in [
                ("answers", fetch_member_answers),
                ("articles", fetch_member_articles),
                ("pins", fetch_member_pins),
                ("questions", fetch_member_questions),
            ]:
                try:
                    result[key] = fn(token, limit=limit, max_items=limit)
                except Exception:
                    result[key] = []
            print_json(result)
            return

        _show_profile_rich(profile)

        _list_content_section(fetch_member_answers, token, "Answers", limit)
        _list_content_section(fetch_member_articles, token, "Articles", limit)
        _list_content_section(fetch_member_pins, token, "Pins", limit)
        _list_content_section(fetch_member_questions, token, "Questions", limit)

    @people.command("answers")
    @click.argument("url_token")
    @click.option("--limit", "-n", type=int, default=20, help="Max items (default: 20)")
    @click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
    def people_answers(url_token: str, limit: int, output_json: bool) -> None:
        """List a user's answers."""
        token = _extract_url_token(url_token)
        info(f"Fetching answers for {token}...")
        items = fetch_member_answers(token, max_items=limit)
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
    @click.option("--limit", "-n", type=int, default=20, help="Max items (default: 20)")
    @click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
    def people_articles(url_token: str, limit: int, output_json: bool) -> None:
        """List a user's articles."""
        token = _extract_url_token(url_token)
        info(f"Fetching articles for {token}...")
        items = fetch_member_articles(token, max_items=limit)
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
    @click.option("--limit", "-n", type=int, default=20, help="Max items (default: 20)")
    @click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
    def people_pins(url_token: str, limit: int, output_json: bool) -> None:
        """List a user's pins (想法)."""
        token = _extract_url_token(url_token)
        info(f"Fetching pins for {token}...")
        items = fetch_member_pins(token, max_items=limit)
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
    @click.option("--limit", "-n", type=int, default=20, help="Max items (default: 20)")
    @click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
    def people_questions(url_token: str, limit: int, output_json: bool) -> None:
        """List questions asked by a user."""
        token = _extract_url_token(url_token)
        info(f"Fetching questions for {token}...")
        items = fetch_member_questions(token, max_items=limit)
        if output_json:
            print_json(items)
            return
        if not items:
            info("No questions found (this endpoint may not be available).")
            return
        for item in items:
            _print_content_item(item)
        echo(f"  {f_dim(f'── {len(items)} questions total')}")
