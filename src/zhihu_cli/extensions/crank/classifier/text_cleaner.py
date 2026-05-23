"""Unified text cleaning pipeline for BERT classification.

Unlike the existing nlp_tools/ pipeline, this does NOT use jieba tokenization
because BERT uses its own WordPiece tokenizer. Cleaning focuses on removing
markup noise while preserving Chinese + English characters for BERT.
"""

from __future__ import annotations

import re


def strip_yaml_frontmatter(text: str) -> str:
    """Remove YAML frontmatter delimited by ``---...---``."""
    if text.startswith("---"):
        idx = text.find("---", 3)
        if idx != -1:
            return text[idx + 3 :]
    return text


def clean_text(text: str) -> str:
    """Clean article text for BERT input.

    Pipeline: strip YAML → remove LaTeX → remove code blocks →
    remove HTML tags → remove markdown image/ref syntax → collapse whitespace.
    """
    text = strip_yaml_frontmatter(text)
    # Remove display math: $$...$$
    text = re.sub(r"\$\$.*?\$\$", "", text, flags=re.DOTALL)
    # Remove inline math: $...$
    text = re.sub(r"\$.*?\$", "", text)
    # Remove code blocks
    text = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
    # Remove HTML tags
    text = re.sub(r"<[^>]+>", "", text)
    # Remove markdown images, keep link text
    text = re.sub(r"!\[.*?\]\(.*?\)", "", text)
    text = re.sub(r"\[([^\]]*)\]\(.*?\)", r"\1", text)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text


def clean_excerpt(text: str) -> str:
    """Light cleaning for API excerpt text (already plain text, just strip markup)."""
    text = re.sub(r"<em>|</em>", "", text)
    text = re.sub(r"http\S+", "", text)
    return text.strip()


def prepare_for_bert(
    title: str,
    body: str,
    *,
    title_weight: float = 2.0,
) -> str:
    """Build input string for BERT tokenization.

    Title is repeated ``title_weight`` times for emphasis, since crank signals
    (grandiose claims, pseudoscientific terminology) are most concentrated there.
    """
    clean_title = clean_excerpt(title)
    clean_body = clean_text(body) if body else ""

    repeats = max(1, int(round(title_weight)))
    title_part = " ".join([clean_title] * repeats)

    if clean_body:
        return f"{title_part} [SEP] {clean_body}"
    return title_part


def prepare_bert_from_article_dict(article: dict) -> str:
    """Prepare BERT input from a Zhihu API article dict.

    Prefers ``_body`` (full markdown from scrape_article) over ``excerpt`` (API snippet).
    """
    title = article.get("title", "")
    body = article.get("_body", article.get("excerpt", ""))
    return prepare_for_bert(title, body)
