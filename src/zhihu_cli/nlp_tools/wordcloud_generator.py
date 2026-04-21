import argparse
import json
import os
import re

import jieba
import jieba.analyse
import matplotlib.pyplot as plt
from wordcloud import WordCloud

from zhihu_cli.nlp_tools import FONT_PATH, STOP_WORDS

# --- 配置区 ---
OUTPUT_FILE = "zhihu_wordcloud.png"


def extract_text_from_md(file_path, skip_metadata=False):
    """提取MD文件中的JSON元数据和正文内容"""
    try:
        with open(file_path, encoding="utf-8") as f:
            content = f.read()

            if not skip_metadata:
                return content

            # 1. 提取头部JSON
            parts = content.split("---", 1)
            text_to_analyze = ""

            if len(parts) >= 1:
                try:
                    meta = json.loads(parts[0])
                    # 合并问题标题和描述
                    text_to_analyze += meta.get("question_name", "") + " "
                    text_to_analyze += meta.get("question_detail", "") + " "
                except json.JSONDecodeError:
                    pass

            # 2. 提取正文
            if len(parts) > 1:
                text_to_analyze += parts[1]

            return text_to_analyze
    except Exception as e:
        print(f"读取失败 {file_path}: {e}")
        return ""


def is_stop_word(word):
    """判断是否为停用词"""
    return word in STOP_WORDS or len(word) == 1  # 也可以过滤单字词


def main(topk_words: int = 200, source_dir: str = "./downloads", only_print: bool = False):
    all_text = []
    print(f"正在扫描 {source_dir} 下的 Markdown 文件...")

    # 递归遍历所有文件夹
    for root, _, files in os.walk(source_dir):
        for file in files:
            if file.endswith(".md"):
                file_path = os.path.join(root, file)
                all_text.append(extract_text_from_md(file_path))

    text = " ".join(all_text)

    # 清洗掉特殊字符、代码块标签等
    text = re.sub(r"\$\$.*?\$\$", "", text, flags=re.DOTALL)
    text = re.sub(r"\$.*?\$", "", text)
    text = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
    text = re.sub(r"<.*?>", "", text)
    full_content = re.sub(r"[^\u4e00-\u9fa5]", "", text)  # 只保留中文字符

    keywords_raw = jieba.analyse.extract_tags(
        full_content,
        topK=topk_words * 2,  # 提取两倍数量，以便过滤后有足够词汇
        withWeight=True,
    )

    # 过滤停用词
    keywords = [(word, weight) for word, weight in keywords_raw if not is_stop_word(word)]

    # 如果过滤后不够 topk_words，可以再提取一些
    if len(keywords) < topk_words:
        print(f"警告：过滤后只剩 {len(keywords)} 个词，少于 {topk_words}")

    # 只取前 topk_words 个
    keywords = keywords[:topk_words]

    # 转换为词云需要的频率字典
    word_freq = {word: weight for word, weight in keywords}

    # 可选：打印前20个关键词供检查
    if only_print:
        print("\n关键词：")
        for i, (word, weight) in enumerate(keywords, 1):
            print(f"{i}. {word} ({weight:.4f})")
        return

    print("\n前20个关键词（已过滤停用词）：")
    for i, (word, weight) in enumerate(keywords[:20], 1):
        print(f"{i}. {word} ({weight:.4f})")

    # 生成词云
    print("\n生成词云图中...")
    wordcloud = WordCloud(
        font_path=FONT_PATH if os.path.exists(FONT_PATH) else None,
        width=1600,
        height=900,
        background_color="white",
        max_words=topk_words,
        colormap="viridis",
        stopwords=STOP_WORDS,
    ).generate_from_frequencies(word_freq)

    # 保存并显示
    plt.figure(figsize=(16, 9))
    plt.imshow(wordcloud, interpolation="bilinear")
    plt.axis("off")
    plt.savefig(OUTPUT_FILE, dpi=300)
    plt.show()
    print(f"完成！词云已保存至: {OUTPUT_FILE}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="生成词云图")
    parser.add_argument("--topk", type=int, default=200, help="要显示的关键词数量 (默认: 200)")
    parser.add_argument("--source_dir", type=str, default="./downloads", help="Markdown文件所在目录 (默认: downloads)")
    parser.add_argument("--only_print", action="store_true", help="仅打印关键词，不生成词云图")

    args = parser.parse_args()

    main(topk_words=args.topk, source_dir=args.source_dir, only_print=args.only_print)
