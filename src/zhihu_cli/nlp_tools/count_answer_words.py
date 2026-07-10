from __future__ import annotations

import argparse
import re
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np


def count_words(filepath: str, no_code: bool = False, no_latex: bool = False, no_frontmatter: bool = False) -> int:
    with open(filepath, encoding="utf-8") as f:
        content = f.read()
        if no_frontmatter:
            # Split file, extract body only (compatible with YAML frontmatter and legacy JSON format)
            parts = content.split("---", 2)
            if len(parts) < 2:
                return 0
            body = parts[-1]
        else:
            body = content

        # Clean: remove LaTeX markers, whitespace
        clean_text = re.sub(r"\s+", "", body)
        if no_latex:
            clean_text = re.sub(r"\$.*?\$", "", clean_text)
            clean_text = re.sub(r"\\begin\{.*?\}.*?\\end\{.*?\}", "", clean_text, flags=re.DOTALL)
        if no_code:
            clean_text = re.sub(r"```.*?```", "", clean_text, flags=re.DOTALL)

        return len(clean_text)


def plot_cdf(word_counts: list[int], log_scale: bool = False) -> None:
    """Plot the Cumulative Distribution Function (CDF) of character counts.

    :param word_counts: List of character counts for each document.
    :param log_scale: If True, use logarithmic scale for the x-axis.
    """
    sorted_counts = np.sort(word_counts)
    cumulative = np.arange(1, len(sorted_counts) + 1) / len(sorted_counts)

    plt.figure(figsize=(10, 6))
    plt.step(sorted_counts, cumulative, where="post", color="steelblue", linewidth=2)
    plt.axhline(
        0.5, color="red", linestyle="dashed", linewidth=1, label=f"Median: {int(np.median(word_counts)):,} chars"
    )
    plt.axvline(np.median(word_counts), color="red", linestyle="dotted", linewidth=0.8, alpha=0.5)

    title = "CDF of Answer Lengths"
    xlabel = "Character Count"
    if log_scale:
        plt.xscale("log")
        title += " (Log Scale)"
        xlabel += " (log scale)"

    plt.title(title)
    plt.xlabel(xlabel)
    plt.ylabel("Cumulative Probability")
    plt.legend()
    plt.grid(alpha=0.3)
    plt.ylim(0, 1.02)
    plt.tight_layout()
    plt.show()


def main() -> None:
    parser = argparse.ArgumentParser(description="Count characters in Markdown files")
    parser.add_argument(
        "--folder",
        default=str(Path.home() / ".zhihu-cli" / "downloads" / "answers"),
        help="Folder containing Markdown files",
    )
    parser.add_argument("--no-code", action="store_true", help="Only count text, ignore code blocks")
    parser.add_argument("--no-latex", action="store_true", help="Exclude LaTeX blocks from count")
    parser.add_argument("--no-frontmatter", action="store_true", help="Exclude YAML frontmatter from count")
    parser.add_argument("--plot-cdf", action="store_true", help="Plot CDF instead of histogram")
    parser.add_argument("--log", action="store_true", help="Use logarithmic scale for character count axis")
    args = parser.parse_args()

    word_counts = []

    for filepath in Path(args.folder).rglob("*.md"):
        word_counts.append(
            count_words(str(filepath), no_code=args.no_code, no_latex=args.no_latex, no_frontmatter=args.no_frontmatter)
        )

    if not word_counts:
        print("No markdown files found.")
        return

    print(f"Analyzed {len(word_counts)} answers")
    print(f"Mean: {np.mean(word_counts):.2f}")
    print(f"Std: {np.std(word_counts):.2f}")
    print(f"CV: {(np.std(word_counts) / np.mean(word_counts)):.2f}")
    print(f"P10: {np.percentile(word_counts, 10)}")
    print(f"P50: {np.percentile(word_counts, 50)}")
    print(f"P90: {np.percentile(word_counts, 90)}")
    print(f"P99: {np.percentile(word_counts, 99)}")
    print(f"Max: {max(word_counts)}")

    file_counts = {}
    for filepath in Path(args.folder).rglob("*.md"):
        count = count_words(
            str(filepath), no_code=args.no_code, no_latex=args.no_latex, no_frontmatter=args.no_frontmatter
        )
        file_counts[filepath.name] = count

    sorted_files = sorted(file_counts.items(), key=lambda item: item[1], reverse=True)

    print("\n--- Top 10 Longest Answers ---")
    for filename, count in sorted_files[:10]:
        print(f"{count} chars: {filename}")

    if args.plot_cdf:
        plot_cdf(word_counts, log_scale=args.log)
    else:
        plt.figure(figsize=(10, 6))
        plt.hist(word_counts, bins=15, color="skyblue", edgecolor="black", alpha=0.7)
        plt.axvline(
            np.mean(word_counts),
            color="red",
            linestyle="dashed",
            linewidth=1,
            label=f"Mean: {int(np.mean(word_counts)):,} chars",
        )

        plt.title("Distribution of Answer Lengths (Markdown Body)")
        plt.xlabel("Character Count")
        plt.ylabel("Frequency")
        plt.legend()
        plt.grid(axis="y", alpha=0.3)
        plt.tight_layout()
        plt.show()


if __name__ == "__main__":
    main()
