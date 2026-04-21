import argparse
import os
import re

import matplotlib.pyplot as plt
import numpy as np


def count_words(filepath, no_code=False):
    with open(filepath, encoding="utf-8") as f:
        content = f.read()
        # 1. 拆分文件，只取正文部分
        parts = content.split("---", 1)
        if len(parts) < 2:
            return 0
        body = parts[1]

        # 2. 清洗数据：去除LaTeX公式标记、空白字符等
        # 简单去除 $...$ 和 \begin...\end 等结构，只统计纯文字
        clean_text = re.sub(r"\$.*?\$", "", body)  # 去除行内公式
        clean_text = re.sub(r"\\begin\{.*?\}.*?\\end\{.*?\}", "", clean_text, flags=re.DOTALL)
        clean_text = re.sub(r"\s+", "", clean_text)  # 去除所有空白
        if no_code:
            clean_text = re.sub(r"```.*?```", "", clean_text, flags=re.DOTALL)

        return len(clean_text)


parser = argparse.ArgumentParser(description="Count words in Markdown files")
parser.add_argument("--folder", default="./downloads/answers", help="Folder containing Markdown files")
parser.add_argument("--no-code", action="store_true", help="Only count text, ignore code blocks")
args = parser.parse_args()

word_counts = []

for filename in os.listdir(args.folder):
    if filename.endswith(".md"):
        word_counts.append(count_words(os.path.join(args.folder, filename), no_code=args.no_code))


print(f"统计了 {len(word_counts)} 个回答的字数")
print(f"平均字数: {np.mean(word_counts):.2f}")
print(f"标准差: {np.std(word_counts):.2f}")
print(f"CV: {(np.std(word_counts) / np.mean(word_counts)):.2f}")
print(f"10% 分位数: {np.percentile(word_counts, 10)}")
print(f"50% 分位数: {np.percentile(word_counts, 50)}")
print(f"90% 分位数: {np.percentile(word_counts, 90)}")
print(f"99% 分位数: {np.percentile(word_counts, 99)}")
print(f"最大字数: {max(word_counts)}")

file_counts = {}
for filename in os.listdir(args.folder):
    if filename.endswith(".md"):
        count = count_words(os.path.join(args.folder, filename), no_code=args.no_code)
        file_counts[filename] = count

# 按字数降序排列
sorted_files = sorted(file_counts.items(), key=lambda item: item[1], reverse=True)

print("\n--- 字数最多的前 10 篇回答 ---")
for filename, count in sorted_files[:10]:
    print(f"{count} 字: {filename}")

# 绘图
plt.figure(figsize=(10, 6))
plt.hist(word_counts, bins=15, color="skyblue", edgecolor="black", alpha=0.7)
plt.axvline(
    np.mean(word_counts), color="red", linestyle="dashed", linewidth=1, label=f"Mean: {int(np.mean(word_counts))} words"
)

plt.title("Distribution of Answer Lengths (Markdown Body)")
plt.xlabel("Word Count (Cleaned)")
plt.ylabel("Frequency")
plt.legend()
plt.grid(axis="y", alpha=0.3)
plt.show()
