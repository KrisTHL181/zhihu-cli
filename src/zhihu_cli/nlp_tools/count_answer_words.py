import argparse
import os
import re
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np


def count_words(filepath: str, no_code: bool = False) -> int:
    with open(filepath, encoding="utf-8") as f:
        content = f.read()
        # Split file, extract body only
        parts = content.split("---", 1)
        if len(parts) < 2:
            return 0
        body = parts[1]

        # Clean: remove LaTeX markers, whitespace
        clean_text = re.sub(r"\$.*?\$", "", body)
        clean_text = re.sub(r"\\begin\{.*?\}.*?\\end\{.*?\}", "", clean_text, flags=re.DOTALL)
        clean_text = re.sub(r"\s+", "", clean_text)
        if no_code:
            clean_text = re.sub(r"```.*?```", "", clean_text, flags=re.DOTALL)

        return len(clean_text)


def main() -> None:
    parser = argparse.ArgumentParser(description="Count words in Markdown files")
    parser.add_argument(
        "--folder",
        default=str(Path.home() / ".zhihu-cli" / "downloads" / "answers"),
        help="Folder containing Markdown files",
    )
    parser.add_argument("--no-code", action="store_true", help="Only count text, ignore code blocks")
    args = parser.parse_args()

    word_counts = []

    for filename in os.listdir(args.folder):
        if filename.endswith(".md"):
            word_counts.append(count_words(os.path.join(args.folder, filename), no_code=args.no_code))

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
    for filename in os.listdir(args.folder):
        if filename.endswith(".md"):
            count = count_words(os.path.join(args.folder, filename), no_code=args.no_code)
            file_counts[filename] = count

    sorted_files = sorted(file_counts.items(), key=lambda item: item[1], reverse=True)

    print("\n--- Top 10 Longest Answers ---")
    for filename, count in sorted_files[:10]:
        print(f"{count} words: {filename}")

    plt.figure(figsize=(10, 6))
    plt.hist(word_counts, bins=15, color="skyblue", edgecolor="black", alpha=0.7)
    plt.axvline(
        np.mean(word_counts),
        color="red",
        linestyle="dashed",
        linewidth=1,
        label=f"Mean: {int(np.mean(word_counts))} words",
    )

    plt.title("Distribution of Answer Lengths (Markdown Body)")
    plt.xlabel("Word Count (Cleaned)")
    plt.ylabel("Frequency")
    plt.legend()
    plt.grid(axis="y", alpha=0.3)
    plt.show()


if __name__ == "__main__":
    main()
