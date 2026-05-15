import argparse
import os
import re
from pathlib import Path
from typing import Any, Literal

import jieba
import numpy as np
import pandas as pd
import plotly.express as px
from sklearn.cluster import KMeans
from sklearn.decomposition import PCA
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.manifold import TSNE
from sklearn.metrics import silhouette_score

from zhihu_cli.nlp_tools import STOP_WORDS as stop_words

DATA_DIR = Path.home() / ".zhihu-cli"


def find_best_k(X: Any, max_k: int = 20) -> None:
    from matplotlib import pyplot as plt

    inertias = []
    silhouette_avg = []
    X_dense = np.asarray(X.todense())
    ks = range(2, max_k + 1)

    print(f"正在通过肘部法评估 K 值 (2-{max_k})...")

    for k in ks:
        km = KMeans(n_clusters=k, random_state=42, n_init=10)
        labels = km.fit_predict(X_dense)

        # 1. 记录 Inertia (用于肘部法)
        inertias.append(km.inertia_)

        # 2. 记录轮廓系数 (用于辅助)
        score = silhouette_score(X_dense, labels)
        silhouette_avg.append(score)
        print(f"K={k} | Inertia: {km.inertia_:.2f} | Silhouette: {score:.4f}")

    # 绘制肘部法曲线
    plt.figure(figsize=(12, 5))

    # 子图1: 肘部法 (Inertia)
    plt.subplot(1, 2, 1)
    plt.plot(ks, inertias, "bo-", markerfacecolor="red")
    plt.xlabel("Number of Clusters (K)")
    plt.ylabel("Inertia (SSE)")
    plt.title('Elbow Method (Look for the "Elbow")')
    plt.grid(True)

    # 子图2: 轮廓系数
    plt.subplot(1, 2, 2)
    plt.plot(ks, silhouette_avg, "gs-")
    plt.xlabel("Number of Clusters (K)")
    plt.ylabel("Silhouette Score")
    plt.title("Silhouette Score")
    plt.grid(True)

    plt.tight_layout()
    plt.show()


# --- 1. 文本加载与清洗 ---
def load_and_clean_data(source_dir: str) -> tuple[list[str], list[str]]:
    documents = []
    file_names = []

    print(f"正在读取 {source_dir} 下的 MD 文件...")

    for root, dirs, files in os.walk(source_dir):
        for file in files:
            if file.endswith(".md"):
                file_path = os.path.join(root, file)
                try:
                    with open(file_path, encoding="utf-8") as f:
                        content = f.read()

                        # 提取正文（去掉YAML头部）
                        parts = content.split("---", 2)
                        text = parts[-1] if len(parts) > 1 else parts[0]

                        # 清洗：去掉公式、代码块、HTML、特殊符号
                        text = re.sub(r"\$\$.*?\$\$", "", text, flags=re.DOTALL)
                        text = re.sub(r"\$.*?\$", "", text)
                        text = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
                        text = re.sub(r"<.*?>", "", text)
                        text = "".join(re.findall(r"[\u4e00-\u9fa5a-zA-Z0-9]+", text))

                        # 分词
                        words = jieba.cut(text)
                        cleaned_text = " ".join([w for w in words if len(w) > 1 and w not in stop_words])

                        if cleaned_text.strip():
                            documents.append(cleaned_text)
                            # 缩短文件名，用于交互显示
                            short_name = file.replace(".md", "").split("_ Kris谭")[0]
                            file_names.append(short_name)

                except Exception as e:
                    print(f"读取失败 {file}: {e}")

    return documents, file_names


# --- 2. 向量化与聚类 ---
def process_clusters(documents: list[str], n_clusters: int) -> tuple[Any, np.ndarray, TfidfVectorizer, KMeans]:
    print("正在进行 TF-IDF 向量化与 K-Means 聚类...")
    vectorizer = TfidfVectorizer(max_features=1500, min_df=2, max_df=0.95)
    X = vectorizer.fit_transform(documents)

    kmeans = KMeans(n_clusters=n_clusters, random_state=42, n_init=10)
    labels = kmeans.fit_predict(X)

    return X, labels, vectorizer, kmeans


# --- 3. 核心绘图逻辑 (Plotly版) ---
def visualize_with_plotly(
    X: Any,
    labels: np.ndarray,
    file_names: list[str],
    vectorizer: TfidfVectorizer,
    kmeans: KMeans,
    n_clusters: int,
    mode: Literal["pca", "tsne", "hybrid"] = "pca",
    output_path: str = str(DATA_DIR / "plots" / "cluster.png"),
    n_terms: int = 10,
) -> None:
    X_dense = np.asarray(X.todense())

    if mode == "hybrid":
        print("执行混合降维模式: TF-IDF -> PCA (50D) -> t-SNE (2D)...")
        # 1. PCA 降噪
        pca_50 = PCA(n_components=min(50, X_dense.shape[0], X_dense.shape[1]), random_state=42)
        X_pca_50 = pca_50.fit_transform(X_dense)
        # 2. t-SNE 聚拢
        reducer = TSNE(
            n_components=2, random_state=42, init="pca", learning_rate="auto", perplexity=min(30, len(file_names) - 1)
        )
        X_reduced = reducer.fit_transform(X_pca_50)
        method_name = "PCA + t-SNE (Hybrid)"
    elif mode == "tsne":
        print("执行直接 t-SNE 降维...")
        reducer = TSNE(
            n_components=2, random_state=42, init="pca", learning_rate="auto", perplexity=min(30, len(file_names) - 1)
        )
        X_reduced = reducer.fit_transform(X_dense)
        method_name = "t-SNE"
    else:
        print("执行纯 PCA 降维...")
        reducer = PCA(n_components=2, random_state=42)
        X_reduced = reducer.fit_transform(X_dense)
        method_name = "PCA"

    # 提取每个簇的关键词作为图例
    terms = vectorizer.get_feature_names_out()
    order_centroids = kmeans.cluster_centers_.argsort()[:, ::-1]
    cluster_info = {}
    for i in range(n_clusters):
        top_terms = [terms[ind] for ind in order_centroids[i, :n_terms]]
        cluster_info[i] = f"簇 {i}: {'/'.join(top_terms)}"

    print(f"\n--- 聚类报告 ({method_name}) ---")
    for i, info in cluster_info.items():
        print(f"  {info}")

    # 构建 DataFrame
    df = pd.DataFrame(
        {
            "Dim1": X_reduced[:, 0],
            "Dim2": X_reduced[:, 1],
            "Cluster ID": labels,
            "知识簇分类": [cluster_info[label] for label in labels],
            "文件名": file_names,
        }
    )

    # 使用 Plotly Express 绘图
    fig = px.scatter(
        df,
        x="Dim1",
        y="Dim2",
        color="知识簇分类",
        hover_name="文件名",
        title=f"知乎内容知识图谱 - 降维模式: {method_name}",
        template="plotly_white",
        color_discrete_sequence=px.colors.qualitative.Prism,
        category_orders={"知识簇分类": sorted(list(cluster_info.values()))},
    )

    # 优化散点样式
    fig.update_traces(
        marker=dict(size=12, opacity=0.8, line=dict(width=1, color="White")), selector=dict(mode="markers")
    )

    # 保存为静态图片
    try:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        fig.write_image(output_path, scale=2, width=1200, height=800)
        print(f"\nImage saved to: {output_path}")
    except Exception:
        print("\n提示: 未安装 kaleido，无法自动保存 PNG。")

    fig.show()


# --- 主流程 ---
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="知乎文章文本聚类工具")
    parser.add_argument("--source_dir", default=str(DATA_DIR / "downloads"), help="Source data directory")
    parser.add_argument("--output", default=str(DATA_DIR / "plots" / "zhihu_clusters.png"), help="Output image path")
    parser.add_argument("--n_clusters", type=int, default=8, help="聚类数量")
    parser.add_argument("--n_terms", type=int, default=10, help="每个簇显示的关键词数量")
    parser.add_argument(
        "--mode",
        choices=["pca", "tsne", "hybrid"],
        default="pca",
        help="降维模式: pca (线性), tsne (直接非线性), hybrid (PCA降噪后接t-SNE)",
    )
    parser.add_argument("--evaluate_k", action="store_true", help="评估不同 K 值的轮廓系数，建议最佳 K")

    args = parser.parse_args()

    docs, filenames = load_and_clean_data(args.source_dir)

    if args.evaluate_k:
        print("\n评估不同 K 值的轮廓系数...")
        vectorizer = TfidfVectorizer(max_features=1500, min_df=2, max_df=0.95)
        X = vectorizer.fit_transform(docs)
        find_best_k(X, max_k=20)
        print("\n评估完成。请根据输出结果选择合适的 K 值后再次运行程序。")
        exit(0)

    if not docs:
        print("错误: 未找到有效文档。")
    else:
        X_tfidf, labels, vec, model = process_clusters(docs, args.n_clusters)
        visualize_with_plotly(
            X_tfidf,
            labels,
            filenames,
            vec,
            model,
            args.n_clusters,
            mode=args.mode,
            output_path=args.output,
            n_terms=args.n_terms,
        )
