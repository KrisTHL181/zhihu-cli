import argparse
import json
import os
import re
from pathlib import Path

import jieba
import jieba.analyse
import matplotlib.pyplot as plt
from wordcloud import WordCloud

from zhihu_cli.nlp_tools import FONT_PATH, STOP_WORDS

DATA_DIR = Path.home() / ".zhihu-cli"
OUTPUT_FILE: str = str(DATA_DIR / "plots" / "zhihu_wordcloud.png")


def extract_text_from_md(file_path: str, skip_metadata: bool = False) -> str:
    """Extract JSON metadata and body text from a Markdown file."""
    try:
        with open(file_path, encoding="utf-8") as f:
            content = f.read()

            if not skip_metadata:
                return content

            parts = content.split("---", 1)
            text_to_analyze = ""

            if len(parts) >= 1:
                try:
                    meta = json.loads(parts[0])
                    text_to_analyze += meta.get("question_name", "") + " "
                    text_to_analyze += meta.get("question_detail", "") + " "
                except json.JSONDecodeError:
                    pass

            if len(parts) > 1:
                text_to_analyze += parts[1]

            return text_to_analyze
    except Exception as e:
        print(f"Read failed {file_path}: {e}")
        return ""


def is_stop_word(word: str) -> bool:
    """Check if word is a stop word."""
    return word in STOP_WORDS or len(word) == 1


def main(topk_words: int = 200, source_dir: str | None = None, only_print: bool = False) -> None:
    if source_dir is None:
        source_dir = str(DATA_DIR / "downloads")
    all_text = []
    print(f"Scanning {source_dir} for Markdown files...")

    for root, _, files in os.walk(source_dir):
        for file in files:
            if file.endswith(".md"):
                file_path = os.path.join(root, file)
                all_text.append(extract_text_from_md(file_path))

    text = " ".join(all_text)

    # Clean special chars, code blocks, etc.
    text = re.sub(r"\$\$.*?\$\$", "", text, flags=re.DOTALL)
    text = re.sub(r"\$.*?\$", "", text)
    text = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
    text = re.sub(r"<.*?>", "", text)
    full_content = re.sub(r"[^一-龥]", "", text)

    keywords_raw = jieba.analyse.extract_tags(
        full_content,
        topK=topk_words * 2,
        withWeight=True,
    )

    keywords = [(word, weight) for word, weight in keywords_raw if not is_stop_word(word)]

    if len(keywords) < topk_words:
        print(f"Warning: only {len(keywords)} words left after filtering, fewer than {topk_words}")

    keywords = keywords[:topk_words]

    word_freq = {word: weight for word, weight in keywords}

    if only_print:
        print("\nKeywords:")
        for i, (word, weight) in enumerate(keywords, 1):
            print(f"{i}. {word} ({weight:.4f})")
        return

    print("\nTop 20 keywords (stop words filtered):")
    for i, (word, weight) in enumerate(keywords[:20], 1):
        print(f"{i}. {word} ({weight:.4f})")

    print("\nGenerating word cloud...")
    wordcloud = WordCloud(
        font_path=FONT_PATH if os.path.exists(FONT_PATH) else None,
        width=1600,
        height=900,
        background_color="white",
        max_words=topk_words,
        colormap="viridis",
        stopwords=STOP_WORDS,
    ).generate_from_frequencies(word_freq)

    plt.figure(figsize=(16, 9))
    plt.imshow(wordcloud, interpolation="bilinear")
    plt.axis("off")
    Path(OUTPUT_FILE).parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(OUTPUT_FILE, dpi=300)
    plt.show()
    print(f"Word cloud saved to: {OUTPUT_FILE}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate word cloud image")
    parser.add_argument("--topk", type=int, default=200, help="Number of keywords (default: 200)")
    parser.add_argument("--source_dir", type=str, default=str(DATA_DIR / "downloads"), help="Markdown file directory")
    parser.add_argument("--only_print", action="store_true", help="Only print keywords, skip image generation")

    args = parser.parse_args()

    main(topk_words=args.topk, source_dir=args.source_dir, only_print=args.only_print)
