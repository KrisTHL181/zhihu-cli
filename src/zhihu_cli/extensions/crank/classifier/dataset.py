"""PyTorch Dataset and author-stratified train/val split for crank classifier.

Positive samples come from Hall of Flames series directories.
Negative samples come from scraped normal science articles.

The author-stratified split prevents the model from overfitting to individual
author writing style (e.g. 杨学志 with 165 papers).
"""

from __future__ import annotations

import random
from pathlib import Path
from typing import Any

from zhihu_cli.extensions.crank.classifier.config import (
    NEGATIVE_DIR,
)
from zhihu_cli.extensions.crank.classifier.text_cleaner import (
    clean_text,
    prepare_for_bert,
)


def _read_sample(filepath: Path) -> str | None:
    """Read and clean a single .md file. Returns None on failure or empty result."""
    try:
        text = filepath.read_text(encoding="utf-8")
        cleaned = clean_text(text)
        if len(cleaned) > 100:
            return cleaned
        return None
    except (OSError, UnicodeDecodeError):
        return None


def collect_positive_samples(hof_root: Path) -> tuple[list[str], list[str]]:
    """Collect all HoF paper contents with their author labels.

    Returns (texts, authors) where texts[i] is cleaned text, authors[i] is author name.
    """
    texts: list[str] = []
    authors: list[str] = []

    if not hof_root.is_dir():
        return texts, authors

    for series_dir in sorted(hof_root.iterdir()):
        if not series_dir.is_dir() or series_dir.name.startswith("."):
            continue

        dir_author = series_dir.name.split("-")[0].strip()

        for md_file in sorted(series_dir.glob("*.md")):
            if md_file.name == "README.md":
                continue
            text = _read_sample(md_file)
            if text:
                texts.append(text)
                authors.append(dir_author)

    return texts, authors


def collect_negative_samples() -> tuple[list[str], list[str]]:
    """Collect negative (normal science) samples from scraped articles.

    Returns (texts, authors) where authors are all "normal_science".
    """
    texts: list[str] = []
    authors: list[str] = []

    if not NEGATIVE_DIR.exists():
        return texts, authors

    for md_file in sorted(NEGATIVE_DIR.glob("*.md")):
        text = _read_sample(md_file)
        if text:
            texts.append(text)
            authors.append("normal_science")

    return texts, authors


def create_stratified_splits(
    positive_texts: list[str],
    positive_authors: list[str],
    negative_texts: list[str],
    negative_authors: list[str],
    val_ratio: float = 0.2,
    seed: int = 42,
) -> tuple[list[str], list[int], list[str], list[int]]:
    """Create train/val split — hold out entire AUTHORS for validation.

    This is the critical design that prevents author-specific overfitting.
    For negative samples: simple random split (single pseudo-author class).
    """
    rng = random.Random(seed)

    # Positive: author-stratified
    pos_by_author: dict[str, list[str]] = {}
    for text, author in zip(positive_texts, positive_authors):
        pos_by_author.setdefault(author, []).append(text)

    all_authors = list(pos_by_author.keys())
    rng.shuffle(all_authors)

    n_val_authors = max(1, int(len(all_authors) * val_ratio))
    val_authors = set(all_authors[:n_val_authors])

    train_pos: list[str] = []
    val_pos: list[str] = []
    for author, txts in pos_by_author.items():
        if author in val_authors:
            val_pos.extend(txts)
        else:
            train_pos.extend(txts)

    # Negative: random split
    neg_indices = list(range(len(negative_texts)))
    rng.shuffle(neg_indices)
    n_val_neg = max(1, int(len(negative_texts) * val_ratio))

    train_neg = [negative_texts[i] for i in neg_indices[n_val_neg:]]
    val_neg = [negative_texts[i] for i in neg_indices[:n_val_neg]]

    # Combine
    train_texts = train_pos + train_neg
    train_labels = [1] * len(train_pos) + [0] * len(train_neg)
    val_texts = val_pos + val_neg
    val_labels = [1] * len(val_pos) + [0] * len(val_neg)

    # Shuffle
    train_combined = list(zip(train_texts, train_labels))
    rng.shuffle(train_combined)
    if train_combined:
        train_texts, train_labels = zip(*train_combined)

    val_combined = list(zip(val_texts, val_labels))
    rng.shuffle(val_combined)
    if val_combined:
        val_texts, val_labels = zip(*val_combined)

    return list(train_texts), list(train_labels), list(val_texts), list(val_labels)


def load_and_split_dataset(hof_root: Path, seed: int = 42) -> dict[str, Any]:
    """Full data loading pipeline — returns raw texts + labels (no tokenization yet).

    Tokenization happens later in the training loop so we can use the tokenizer
    from the loaded model (ensures vocabulary consistency).
    """
    print("Collecting positive samples (Hall of Flames)...")
    pos_texts, pos_authors = collect_positive_samples(hof_root)
    print(f"  Found {len(pos_texts)} positive samples from {len(set(pos_authors))} authors")

    print("Collecting negative samples (normal science)...")
    neg_texts, neg_authors = collect_negative_samples()
    print(f"  Found {len(neg_texts)} negative samples")

    if not pos_texts:
        raise ValueError("No positive samples found. Check HoF path.")
    if not neg_texts:
        raise ValueError("No negative samples found. Run 'zhihu crank classify collect-negatives' first.")

    train_texts, train_labels, val_texts, val_labels = create_stratified_splits(
        pos_texts,
        pos_authors,
        neg_texts,
        neg_authors,
        seed=seed,
    )

    train_pos = sum(1 for label in train_labels if label == 1)
    train_neg = sum(1 for label in train_labels if label == 0)
    val_pos = sum(1 for label in val_labels if label == 1)
    val_neg = sum(1 for label in val_labels if label == 0)
    print(f"Train: {len(train_texts)} samples ({train_pos} pos / {train_neg} neg)")
    print(f"Val:   {len(val_texts)} samples ({val_pos} pos / {val_neg} neg)")

    # Apply title-weight encoding (use empty title since body is full text)
    train_enc = [prepare_for_bert("", t) for t in train_texts]
    val_enc = [prepare_for_bert("", t) for t in val_texts]

    return {
        "train_texts": train_enc,
        "train_labels": train_labels,
        "val_texts": val_enc,
        "val_labels": val_labels,
    }
