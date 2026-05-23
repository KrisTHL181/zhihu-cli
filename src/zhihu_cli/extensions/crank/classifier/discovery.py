"""Search-based discovery of new crank authors using Zhihu search API.

Pipeline: search with crank keywords → classify each result → aggregate by author →
report authors with high crank-to-normal ratio.
"""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from zhihu_cli.extensions.crank.classifier.config import CRANK_KEYWORDS, DISCOVERY_THRESHOLD
from zhihu_cli.extensions.crank.classifier.model import (
    load_model,
    model_is_loaded,
    predict_from_article_dict,
)


@dataclass
class DiscoveryResult:
    author_name: str
    author_url: str
    crank_count: int
    total_articles: int
    crank_ratio: float
    articles: list[dict[str, Any]] = field(default_factory=list)


def discover_cranks(
    *,
    keywords: list[str] | None = None,
    max_per_keyword: int = 50,
    threshold: float = DISCOVERY_THRESHOLD,
    min_articles_for_author: int = 2,
    min_crank_ratio: float = 0.5,
    delay: float = 1.5,
) -> list[DiscoveryResult]:
    """Search for crank content and identify potential new crank authors.

    For each keyword, searches Zhihu articles, classifies them with BERT,
    then groups by author. Authors with >= min_articles and crank_ratio >= min_crank_ratio
    are returned sorted by crank ratio descending.
    """
    from zhihu_cli.content.handlers.search import search_articles

    if not model_is_loaded():
        load_model()

    keywords = keywords or CRANK_KEYWORDS

    author_articles: dict[str, list[tuple[str, bool, float, str, str]]] = defaultdict(list)

    total_searched = 0

    print("=" * 60)
    print("Crank Discovery via Zhihu Search")
    print("=" * 60)

    for keyword in keywords:
        print(f"\nSearching: '{keyword}'")
        try:
            articles = search_articles(keyword, limit=20, max_items=max_per_keyword)
        except Exception as e:
            print(f"  [error] Search failed: {e}")
            continue

        total_searched += len(articles)
        print(f"  Found {len(articles)} articles")

        for art in articles:
            author = art.get("author", {})
            author_name = author.get("name", "unknown")
            url = art.get("url", "")
            title = art.get("title", "")

            result = predict_from_article_dict(art, threshold=threshold)
            is_c = result["label"] == "crank"
            prob = result["probability"]

            author_articles[author_name].append((url, is_c, prob, title, author.get("url_token", "")))

        time.sleep(delay)

    # Aggregate and filter
    results: list[DiscoveryResult] = []
    for author_name, arts in author_articles.items():
        total = len(arts)
        if total < min_articles_for_author:
            continue

        crank_count = sum(1 for _, is_c, _, _, _ in arts if is_c)
        crank_ratio = crank_count / total

        if crank_ratio < min_crank_ratio:
            continue

        crank_articles = sorted(
            [(url, prob, title) for url, is_c, prob, title, _ in arts if is_c],
            key=lambda x: x[1],
            reverse=True,
        )[:10]

        token = arts[0][4]
        author_url = f"https://www.zhihu.com/people/{token}" if token else ""

        results.append(
            DiscoveryResult(
                author_name=author_name,
                author_url=author_url,
                crank_count=crank_count,
                total_articles=total,
                crank_ratio=crank_ratio,
                articles=[{"url": u, "probability": p, "title": t} for u, p, t in crank_articles],
            )
        )

    results.sort(key=lambda r: r.crank_ratio, reverse=True)

    print(f"\n{'=' * 60}")
    print(f"Searched {total_searched} articles")
    print(f"Found {len(results)} potential crank authors (crank_ratio >= {min_crank_ratio})")
    print(f"{'=' * 60}")

    return results


def discover_report(results: list[DiscoveryResult], show_articles: int = 3) -> None:
    """Print a human-readable discovery report."""
    if not results:
        print("\nNo potential crank authors found.")
        return

    for i, result in enumerate(results, 1):
        print(f"\n[{i}] {result.author_name}  (crank ratio: {result.crank_ratio:.0%})")
        print(f"    {result.crank_count}/{result.total_articles} articles classified as crank")
        if result.author_url:
            print(f"    {result.author_url}")
        print("    Top articles:")
        for j, art in enumerate(result.articles[:show_articles], 1):
            prob_pct = art["probability"] * 100
            print(f"      {j}. [{prob_pct:.0f}%] {art['title']}")
            print(f"         {art['url']}")
