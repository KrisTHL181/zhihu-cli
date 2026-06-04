from typing import Any

from zhihu_cli.content.handlers import fmt_time
from zhihu_cli.content.handlers.requests import fetch_page_html, get_page_state


def parse_zvideo_metadata(item: dict[str, Any], author_data: dict[str, Any] | None = None) -> dict[str, Any]:
    """Extract structured metadata from a raw zvideo entity dict.

    Args:
        item: The raw zvideo entity from js-initialData.
        author_data: Optional resolved user entity for the author.
    """
    zvideo_id = item.get("id", "")
    title = item.get("title", "untitled")
    description = item.get("description", "")
    excerpt = item.get("excerpt", "")

    # Stats
    play_count = item.get("playCount", 0)
    voteup_count = item.get("voteupCount", 0)
    comment_count = item.get("commentCount", 0)
    share_count = item.get("shareCount", 0)
    favlists_count = item.get("favlistsCount", 0)

    # Timestamps
    created = item.get("publishedAt") or item.get("created", 0)
    updated = item.get("updatedAt") or item.get("updated", 0)

    # Duration
    video_info = item.get("video", {})
    duration = video_info.get("duration", 0)

    # Author info — the zvideo entity stores author as a urlToken string;
    # resolve it from author_data if provided.
    author = item.get("author", {})
    if isinstance(author, str):
        # author is a urlToken string like '81-33-11-13-31'
        author_url_token = author
        if author_data:
            author_name = author_data.get("name", author_url_token)
            author_headline = author_data.get("headline", "")
            author_id = author_data.get("id", "")
        else:
            author_name = author_url_token
            author_headline = ""
            author_id = ""
    else:
        author_url_token = author.get("urlToken", "")
        author_name = author.get("name", "unknown")
        author_headline = author.get("headline", "")
        author_id = author.get("id", "")

    # Topics
    topics = item.get("topics", [])
    topic_names = [t.get("name", "") for t in topics if t.get("name")]

    # URLs
    url = item.get("url", "")
    if not url and zvideo_id:
        url = f"https://www.zhihu.com/zvideo/{zvideo_id}"

    # Cover image
    image_url = item.get("imageUrl", "")
    begin_frame = video_info.get("beginFrame", {})
    cover_url = begin_frame.get("fHD") or begin_frame.get("hD") or image_url or ""

    # Video quality tiers (sorted best → worst)
    playlist = video_info.get("playlist", {})
    playlist_v2 = video_info.get("playlistV2", {})
    quality_tiers: list[dict[str, Any]] = []

    # Prefer h.265 playlistV2 over h.264 playlist for each tier
    seen_tiers: set[str] = set()
    for playlist_source in (playlist_v2, playlist):
        for tier in ("fhd", "hd", "sd", "ld"):
            if tier in seen_tiers:
                continue
            entry = playlist_source.get(tier)
            if entry:
                quality_tiers.append(
                    {
                        "tier": tier.upper(),
                        "width": entry.get("width"),
                        "height": entry.get("height"),
                        "bitrate": entry.get("bitrate"),
                        "duration": entry.get("duration"),
                        "size": entry.get("size"),
                        "format": entry.get("format"),
                        "fps": entry.get("fps"),
                        "url": entry.get("playUrl") or entry.get("url", ""),
                    }
                )
                seen_tiers.add(tier)

    return {
        "id": zvideo_id,
        "type": "zvideo",
        "title": title,
        "description": description,
        "excerpt": excerpt,
        "url": url,
        "cover_url": cover_url,
        "created_time": fmt_time(created),
        "updated_time": fmt_time(updated),
        "stats": {
            "play_count": play_count,
            "voteup_count": voteup_count,
            "comment_count": comment_count,
            "share_count": share_count,
            "favlists_count": favlists_count,
        },
        "duration": duration,
        "topics": topic_names,
        "author": {
            "name": author_name,
            "headline": author_headline,
            "url_token": author_url_token,
            "id": author_id,
        },
        "quality_tiers": quality_tiers,
    }


def get_best_video_url(metadata: dict[str, Any]) -> str | None:
    """Return the best-quality video URL from metadata quality tiers."""
    tiers = metadata.get("quality_tiers", [])
    if tiers:
        return tiers[0].get("url")
    return None


def _resolve_author(author_ref: str, users: dict[str, Any]) -> dict[str, Any] | None:
    """Resolve an author urlToken string to a user entity from the users map.

    The zvideo entity stores author as a urlToken string (e.g. '81-33-11-13-31').
    We search entities.users for the user whose urlToken matches.
    """
    for user in users.values():
        if isinstance(user, dict) and user.get("urlToken") == author_ref:
            return user
    return None


def scrape_zvideo(zvideo_url: str) -> tuple[dict[str, Any], str]:
    """Scrape a Zhihu zvideo page.

    Returns (metadata, markdown_content) where markdown_content embeds
    the zvideo metadata and a link to the best-quality video URL.
    """
    entities = get_page_state(fetch_page_html(zvideo_url))
    items = entities.get("zvideos", {})
    if not items:
        raise ValueError("No zvideo data found in page entities")

    item_data = next(iter(items.values()))

    # Resolve author: the zvideo entity stores author as a urlToken string;
    # we look up the full user entity from entities.users
    author_ref = item_data.get("author", "")
    author_data = None
    if isinstance(author_ref, str) and author_ref:
        users = entities.get("users", {})
        author_data = _resolve_author(author_ref, users)

    metadata = parse_zvideo_metadata(item_data, author_data)

    # Build a markdown representation
    md_parts = [
        f"# {metadata['title']}",
        "",
        f"**作者**: {metadata['author']['name']}",
        f"**发布时间**: {metadata['created_time']}",
        f"**时长**: {metadata['duration']:.1f}s",
        "",
    ]

    if metadata["description"]:
        md_parts.append(metadata["description"])
        md_parts.append("")

    if metadata["topics"]:
        md_parts.append(f"**话题**: {', '.join(metadata['topics'])}")
        md_parts.append("")

    md_parts.append("## 统计信息")
    md_parts.append("")
    stats = metadata["stats"]
    md_parts.append(f"- 播放: {stats['play_count']}")
    md_parts.append(f"- 赞同: {stats['voteup_count']}")
    md_parts.append(f"- 评论: {stats['comment_count']}")
    md_parts.append(f"- 分享: {stats['share_count']}")
    md_parts.append(f"- 收藏: {stats['favlists_count']}")
    md_parts.append("")

    md_parts.append("## 视频源")
    md_parts.append("")
    for tier in metadata["quality_tiers"]:
        size_mb = tier.get("size", 0) / (1024 * 1024) if tier.get("size") else 0
        md_parts.append(
            f"- **{tier['tier']}** "
            f"({tier.get('width', '?')}x{tier.get('height', '?')}, "
            f"{tier.get('bitrate', '?')}kbps, "
            f"{size_mb:.1f}MB)"
        )
        md_parts.append(f"  - {tier['url']}")
        md_parts.append("")

    if metadata["cover_url"]:
        md_parts.append(f"![封面]({metadata['cover_url']})")
        md_parts.append("")

    markdown = "\n".join(md_parts)
    return metadata, markdown
