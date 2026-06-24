"""Search command group for zhihu-cli."""

import click

from zhihu_cli.content.handlers.search import search_articles, search_questions, search_topics, search_users
from zhihu_cli.output import (
    blank,
    echo,
    f_bold,
    f_dim,
    f_label,
    f_meta,
    f_name,
    f_num,
    f_url,
    info,
    item_index,
    print_json,
)


def register_search(main_group):
    """Register the search command group onto *main_group*."""

    @main_group.group()
    def search() -> None:
        """Search Zhihu for questions, articles, users, and topics."""

    @search.command("question")
    @click.argument("query")
    @click.option("--limit", type=int, default=20, help="Items per page")
    @click.option("--max", "-n", "max_items", type=int, default=20, help="Max total items")
    @click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
    def search_question_cmd(query: str, limit: int, max_items: int | None, output_json: bool) -> None:
        """Search Zhihu questions by keyword."""
        items = search_questions(query, limit=limit, max_items=max_items)
        if output_json:
            print_json(items)
            return
        for i, q in enumerate(items, 1):
            echo(f"  {item_index(i)} {f_bold(q['title'])}")
            echo(
                f"    {f_num(q['answer_count'])} {f_dim('answers')}  {f_num(q['follower_count'])} {f_dim('followers')}"
            )
            echo(f"    {f_label('updated:')} {f_meta(q['updated_time'])}")
            echo(f"    {f_url(q['url'])}")
            blank()
        if not items:
            info(f"No questions found for '{query}'.")

    @search.command("article")
    @click.argument("query")
    @click.option("--limit", type=int, default=20, help="Items per page")
    @click.option("--max", "-n", "max_items", type=int, default=20, help="Max total items")
    @click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
    def search_article_cmd(query: str, limit: int, max_items: int | None, output_json: bool) -> None:
        """Search Zhihu articles by keyword."""
        items = search_articles(query, limit=limit, max_items=max_items)
        if output_json:
            print_json(items)
            return
        for i, a in enumerate(items, 1):
            echo(f"  {item_index(i)} {f_bold(a['title'])}")
            echo(f"    {f_label('by')} {f_name(a['author']['name'])}  {f_num(a['voteup_count'])} {f_dim('upvotes')}")
            if a["excerpt"]:
                echo(f"    {f_dim(a['excerpt'][:120])}")
            echo(f"    {f_meta(a['created_time'])}")
            echo(f"    {f_url(a['url'])}")
            blank()
        if not items:
            info(f"No articles found for '{query}'.")

    @search.command("user")
    @click.argument("query")
    @click.option("--limit", type=int, default=20, help="Items per page")
    @click.option("--max", "-n", "max_items", type=int, default=20, help="Max total items")
    @click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
    def search_user_cmd(query: str, limit: int, max_items: int | None, output_json: bool) -> None:
        """Search Zhihu users by keyword."""
        items = search_users(query, limit=limit, max_items=max_items)
        if output_json:
            print_json(items)
            return
        for i, u in enumerate(items, 1):
            echo(f"  {item_index(i)} {f_name(u['name'])}  ({f_dim(u['gender'])})")
            if u["headline"]:
                echo(f"    {f_dim(u['headline'])}")
            echo(
                f"    {f_num(u['follower_count'])} {f_dim('followers')}  {f_num(u['answer_count'])} {f_dim('answers')}  {f_num(u['articles_count'])} {f_dim('articles')}"
            )
            echo(f"    {f_url(u['url'])}")
            blank()
        if not items:
            info(f"No users found for '{query}'.")

    @search.command("topic")
    @click.argument("query")
    @click.option("--limit", type=int, default=20, help="Items per page")
    @click.option("--max", "-n", "max_items", type=int, default=20, help="Max total items")
    @click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
    def search_topic_cmd(query: str, limit: int, max_items: int | None, output_json: bool) -> None:
        """Search Zhihu topics by keyword."""
        items = search_topics(query, limit=limit, max_items=max_items)
        if output_json:
            print_json(items)
            return
        for i, t in enumerate(items, 1):
            echo(f"  {item_index(i)} {f_bold(t['name'])}")
            intro = t["introduction"] or t["excerpt"]
            if intro:
                echo(f"    {f_dim(intro[:120])}")
            echo(
                f"    {f_num(t['questions_count'])} {f_dim('questions')}  {f_num(t['followers_count'])} {f_dim('followers')}"
            )
            echo(f"    {f_url(t['url'])}")
            blank()
        if not items:
            info(f"No topics found for '{query}'.")
