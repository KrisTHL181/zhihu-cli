import argparse
import os
import re
from pathlib import Path
from typing import Any, Literal

import jieba
import numpy as np
import pandas as pd
import plotly.express as px
import yaml
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

    print(f"Evaluating K values via elbow method (2-{max_k})...")

    for k in ks:
        km = KMeans(n_clusters=k, random_state=42, n_init=10)
        labels = km.fit_predict(X_dense)

        inertias.append(km.inertia_)

        score = silhouette_score(X_dense, labels)
        silhouette_avg.append(score)
        print(f"K={k} | Inertia: {km.inertia_:.2f} | Silhouette: {score:.4f}")

    plt.figure(figsize=(12, 5))

    plt.subplot(1, 2, 1)
    plt.plot(ks, inertias, "bo-", markerfacecolor="red")
    plt.xlabel("Number of Clusters (K)")
    plt.ylabel("Inertia (SSE)")
    plt.title('Elbow Method (Look for the "Elbow")')
    plt.grid(True)

    plt.subplot(1, 2, 2)
    plt.plot(ks, silhouette_avg, "gs-")
    plt.xlabel("Number of Clusters (K)")
    plt.ylabel("Silhouette Score")
    plt.title("Silhouette Score")
    plt.grid(True)

    plt.tight_layout()
    plt.show()


def load_and_clean_data(source_dir: str) -> tuple[list[str], list[str]]:
    documents = []
    file_names = []

    print(f"Reading MD files in {source_dir}...")

    for root, dirs, files in os.walk(source_dir):
        for file in files:
            if file.endswith(".md"):
                file_path = os.path.join(root, file)
                try:
                    with open(file_path, encoding="utf-8") as f:
                        content = f.read()

                        # Extract body (skip YAML header)
                        parts = content.split("---", 2)
                        text = parts[-1] if len(parts) > 1 else parts[0]

                        # Prefer title from YAML frontmatter, fall back to filename
                        short_name = file.replace(".md", "")
                        if len(parts) > 2:
                            try:
                                meta = yaml.safe_load(parts[1])
                                if isinstance(meta, dict) and meta.get("title"):
                                    short_name = meta["title"]
                            except yaml.YAMLError:
                                pass

                        # Clean: remove formulas, code blocks, HTML, special symbols
                        text = re.sub(r"\$\$.*?\$\$", "", text, flags=re.DOTALL)
                        text = re.sub(r"\$.*?\$", "", text)
                        text = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
                        text = re.sub(r"<.*?>", "", text)
                        text = "".join(re.findall(r"[一-龥a-zA-Z0-9]+", text))

                        # Tokenize
                        words = jieba.cut(text)
                        cleaned_text = " ".join([w for w in words if len(w) > 1 and w not in stop_words])

                        if cleaned_text.strip():
                            documents.append(cleaned_text)
                            file_names.append(short_name)

                except Exception as e:
                    print(f"Read failed {file}: {e}")

    return documents, file_names


def process_clusters(documents: list[str], n_clusters: int) -> tuple[Any, np.ndarray, TfidfVectorizer, KMeans]:
    print("Performing TF-IDF vectorization and K-Means clustering...")
    vectorizer = TfidfVectorizer(max_features=1500, min_df=2, max_df=0.95)
    X = vectorizer.fit_transform(documents)

    kmeans = KMeans(n_clusters=n_clusters, random_state=42, n_init=10)
    labels = kmeans.fit_predict(X)

    return X, labels, vectorizer, kmeans


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
        print("Executing hybrid reduction mode: TF-IDF -> PCA (50D) -> t-SNE (2D)...")
        pca_50 = PCA(n_components=min(50, X_dense.shape[0], X_dense.shape[1]), random_state=42)
        X_pca_50 = pca_50.fit_transform(X_dense)
        reducer = TSNE(
            n_components=2, random_state=42, init="pca", learning_rate="auto", perplexity=min(30, len(file_names) - 1)
        )
        X_reduced = reducer.fit_transform(X_pca_50)
        method_name = "PCA + t-SNE (Hybrid)"
    elif mode == "tsne":
        print("Executing direct t-SNE reduction...")
        reducer = TSNE(
            n_components=2, random_state=42, init="pca", learning_rate="auto", perplexity=min(30, len(file_names) - 1)
        )
        X_reduced = reducer.fit_transform(X_dense)
        method_name = "t-SNE"
    else:
        print("Executing pure PCA reduction...")
        reducer = PCA(n_components=2, random_state=42)
        X_reduced = reducer.fit_transform(X_dense)
        method_name = "PCA"

    terms = vectorizer.get_feature_names_out()
    order_centroids = kmeans.cluster_centers_.argsort()[:, ::-1]
    cluster_info = {}
    for i in range(n_clusters):
        top_terms = [terms[ind] for ind in order_centroids[i, :n_terms]]
        cluster_info[i] = f"Cluster {i}: {'/'.join(top_terms)}"

    print(f"\n--- Clustering Report ({method_name}) ---")
    for i, info in cluster_info.items():
        print(f"  {info}")

    df = pd.DataFrame(
        {
            "Dim1": X_reduced[:, 0],
            "Dim2": X_reduced[:, 1],
            "Cluster ID": labels,
            "Cluster Label": [cluster_info[label] for label in labels],
            "Filename": file_names,
        }
    )

    fig = px.scatter(
        df,
        x="Dim1",
        y="Dim2",
        color="Cluster Label",
        hover_name="Filename",
        title=f"Zhihu Content Knowledge Graph - Reduction Mode: {method_name}",
        template="plotly_white",
        color_discrete_sequence=px.colors.qualitative.Prism,
        category_orders={"Cluster Label": sorted(list(cluster_info.values()))},
    )

    fig.update_traces(
        marker=dict(size=12, opacity=0.8, line=dict(width=1, color="White")), selector=dict(mode="markers")
    )

    try:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        fig.write_image(output_path, scale=2, width=1200, height=800)
        print(f"\nImage saved to: {output_path}")
    except Exception:
        print("\nTip: kaleido is not installed, unable to save PNG automatically.")

    fig.show()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Zhihu Article Text Clustering Tool")
    parser.add_argument("--source_dir", default=str(DATA_DIR / "downloads"), help="Source data directory")
    parser.add_argument("--output", default=str(DATA_DIR / "plots" / "zhihu_clusters.png"), help="Output image path")
    parser.add_argument("--n_clusters", type=int, default=8, help="Number of clusters")
    parser.add_argument("--n_terms", type=int, default=10, help="Top terms per cluster")
    parser.add_argument(
        "--mode",
        choices=["pca", "tsne", "hybrid"],
        default="pca",
        help="Reduction mode: pca (linear), tsne (nonlinear), hybrid (PCA + t-SNE)",
    )
    parser.add_argument("--evaluate_k", action="store_true", help="Evaluate silhouette scores to suggest optimal K")

    args = parser.parse_args()

    docs, filenames = load_and_clean_data(args.source_dir)

    if args.evaluate_k:
        print("\nEvaluating silhouette scores for different K values...")
        vectorizer = TfidfVectorizer(max_features=1500, min_df=2, max_df=0.95)
        X = vectorizer.fit_transform(docs)
        find_best_k(X, max_k=20)
        print("\nEvaluation complete. Choose a suitable K and re-run the program.")
        exit(0)

    if not docs:
        print("Error: no valid documents found.")
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
