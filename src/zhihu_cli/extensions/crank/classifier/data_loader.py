"""Training data inspection and summary utilities."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from zhihu_cli.extensions.crank.classifier.config import NEGATIVE_DIR
from zhihu_cli.extensions.crank.classifier.dataset import (
    collect_negative_samples,
    collect_positive_samples,
)


def count_positive_samples(hof_root: Path | None = None) -> int:
    """Count total positive samples across all HoF series directories."""
    if hof_root is None:
        from zhihu_cli.extensions.crank.monitor import SERIAL_PAPERS_DIR

        hof_root = Path(SERIAL_PAPERS_DIR)
    texts, _authors = collect_positive_samples(hof_root)
    return len(texts)


def count_negative_samples() -> int:
    """Count negative samples downloaded."""
    if not NEGATIVE_DIR.exists():
        return 0
    return len(list(NEGATIVE_DIR.glob("*.md")))


def print_data_summary(hof_root: Path | None = None) -> None:
    """Print a summary of training data stats."""
    if hof_root is None:
        from zhihu_cli.extensions.crank.monitor import SERIAL_PAPERS_DIR

        hof_root = Path(SERIAL_PAPERS_DIR)

    pos_texts, pos_authors = collect_positive_samples(hof_root)
    neg_texts, _neg_authors = collect_negative_samples()
    author_counts = Counter(pos_authors)

    print("Training Data Summary")
    print("=" * 50)
    print(f"Positive samples (crank): {len(pos_texts)}")
    print(f"  Authors: {len(author_counts)}")
    for author, count in author_counts.most_common():
        print(f"    {author}: {count}")
    print(f"Negative samples (normal): {len(neg_texts)}")
    print(f"Total: {len(pos_texts) + len(neg_texts)}")
