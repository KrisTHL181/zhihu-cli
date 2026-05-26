import os
import re
from collections import Counter
from pathlib import Path

import jieba
import networkx as nx
import numpy as np
import plotly.graph_objects as go

from zhihu_cli.nlp_tools import STOP_WORDS

DATA_DIR = Path.home() / ".zhihu-cli"


def load_and_tokenize(source_dir: str) -> tuple[list[list[str]], "Counter[str]"]:
    """Load markdown files, clean text, segment with jieba."""
    docs = []
    word_counter: Counter[str] = Counter()

    for root, _, files in os.walk(source_dir):
        for file in files:
            if file.endswith(".md"):
                file_path = os.path.join(root, file)
                try:
                    with open(file_path, encoding="utf-8") as f:
                        content = f.read()

                    parts = content.split("---", 2)
                    text = parts[-1] if len(parts) > 1 else parts[0]

                    text = re.sub(r"\$\$.*?\$\$", "", text, flags=re.DOTALL)
                    text = re.sub(r"\$.*?\$", "", text)
                    text = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
                    text = re.sub(r"<.*?>", "", text)
                    text = "".join(re.findall(r"[一-龥a-zA-Z0-9]+", text))

                    words = [w for w in jieba.lcut(text) if len(w) > 1 and w not in STOP_WORDS]
                    if words:
                        docs.append(words)
                        word_counter.update(words)
                except Exception as e:
                    print(f"Read failed {file}: {e}")

    return docs, word_counter


def build_cooccurrence_graph(
    docs: list[list[str]],
    word_counter: "Counter[str]",
    topk: int = 80,
    window_size: int = 5,
    min_edge_weight: int = 3,
) -> "nx.Graph | None":
    """Build word co-occurrence network from tokenized documents."""
    top_words = {word for word, _ in word_counter.most_common(topk)}

    edge_counter: Counter[tuple[str, str]] = Counter()
    for words in docs:
        filtered = [w for w in words if w in top_words]
        for i in range(len(filtered)):
            for j in range(i + 1, min(i + window_size + 1, len(filtered))):
                a, b = filtered[i], filtered[j]
                if a != b:
                    edge_counter[(a, b) if a < b else (b, a)] += 1

    if not edge_counter:
        print("No co-occurrence edges found. Try lowering --topk or --min-edge-weight.")
        return None

    G = nx.Graph()
    for word in top_words:
        G.add_node(word, frequency=word_counter[word])

    for (a, b), weight in edge_counter.items():
        if weight >= min_edge_weight:
            G.add_edge(a, b, weight=weight)

    return G


def visualize_network(
    G: "nx.Graph",
    output_path: str = str(DATA_DIR / "plots" / "zhihu_conetwork.png"),
) -> None:
    """Draw co-occurrence network with Plotly."""
    if G.number_of_nodes() == 0:
        print("Graph has no nodes.")
        return

    freqs = np.array([G.nodes[n]["frequency"] for n in G.nodes()])
    log_freqs = np.log1p(freqs)
    node_sizes = 8 + 32 * (log_freqs / log_freqs.max())

    has_edges = G.number_of_edges() > 0
    if has_edges:
        try:
            from networkx.algorithms.community import greedy_modularity_communities

            communities = greedy_modularity_communities(G)
            node_to_community: dict[str, int] = {}
            for i, comm in enumerate(communities):
                for node in comm:
                    node_to_community[node] = i
            node_colors = [node_to_community[n] for n in G.nodes()]
            colorbar_title = "Community"
        except Exception:
            degrees = dict(G.degree())
            node_colors = [degrees[n] for n in G.nodes()]
            colorbar_title = "Degree"
    else:
        node_colors = [G.nodes[n]["frequency"] for n in G.nodes()]
        colorbar_title = "Frequency"

    print(f"Computing layout for {G.number_of_nodes()} nodes...")
    pos = nx.spring_layout(G, k=3.5, iterations=80, seed=42, weight="weight")

    edge_trace: go.Scatter | None = None
    if has_edges:
        edge_x: list[float | None] = []
        edge_y: list[float | None] = []
        for u, v in G.edges():
            x0, y0 = pos[u]
            x1, y1 = pos[v]
            edge_x.extend([x0, x1, None])
            edge_y.extend([y0, y1, None])
        edge_trace = go.Scatter(
            x=edge_x,
            y=edge_y,
            mode="lines",
            line=dict(width=0.5, color="#999"),
            hoverinfo="none",
            showlegend=False,
        )

    node_x = [pos[n][0] for n in G.nodes()]
    node_y = [pos[n][1] for n in G.nodes()]
    node_labels = list(G.nodes())

    node_trace = go.Scatter(
        x=node_x,
        y=node_y,
        mode="markers+text",
        text=node_labels,
        textposition="middle center",
        textfont=dict(size=10, color="#333"),
        marker=dict(
            size=node_sizes.tolist(),
            color=node_colors,
            colorscale="Viridis",
            showscale=True,
            colorbar=dict(title=colorbar_title, thickness=15),
            line=dict(width=1, color="#fff"),
        ),
        hoverinfo="text",
        hovertext=[f"{n}<br>freq: {G.nodes[n]['frequency']}" for n in G.nodes()],
    )

    traces = [edge_trace, node_trace] if edge_trace else [node_trace]
    fig = go.Figure(
        data=traces,
        layout=go.Layout(
            title=f"Word Co-occurrence Network ({G.number_of_nodes()} words, {G.number_of_edges()} edges)",
            showlegend=False,
            hovermode="closest",
            margin=dict(b=20, l=5, r=5, t=40),
            xaxis=dict(showgrid=False, zeroline=False, showticklabels=False),
            yaxis=dict(showgrid=False, zeroline=False, showticklabels=False),
            template="plotly_white",
        ),
    )

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    try:
        fig.write_image(output_path, scale=2, width=1600, height=1200)
        print(f"Network graph saved to: {output_path}")
    except Exception:
        print("Tip: kaleido is not installed, unable to save PNG. Install with: pip install kaleido")

    fig.show()


def main(
    source_dir: str = str(DATA_DIR / "downloads"),
    topk: int = 80,
    window_size: int = 5,
    min_edge_weight: int = 3,
    output: str = str(DATA_DIR / "plots" / "zhihu_conetwork.png"),
) -> None:
    """Build and visualize word co-occurrence network from downloaded content."""
    print(f"Scanning {source_dir} for Markdown files...")
    docs, word_counter = load_and_tokenize(source_dir)

    if not docs:
        print("No documents found.")
        return

    total_words = sum(len(d) for d in docs)
    print(f"Loaded {len(docs)} documents, {total_words} words (segmented), {len(word_counter)} unique words")

    G = build_cooccurrence_graph(
        docs, word_counter, topk=topk, window_size=window_size, min_edge_weight=min_edge_weight
    )
    if G is None:
        return

    print(f"Network: {G.number_of_nodes()} nodes, {G.number_of_edges()} edges")
    if G.number_of_edges() > 0:
        top_edges = sorted(G.edges(data=True), key=lambda e: e[2]["weight"], reverse=True)[:topk]
        print(f"Top {len(top_edges)} co-occurrence pairs:")
        for u, v, data in top_edges:
            print(f"  {u} — {v}: {data['weight']}")

    visualize_network(G, output_path=output)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Word co-occurrence network visualization")
    parser.add_argument("--source-dir", default=str(DATA_DIR / "downloads"), help="Directory with Markdown files")
    parser.add_argument("--topk", type=int, default=80, help="Top N words to include")
    parser.add_argument("--window-size", type=int, default=5, help="Co-occurrence window size")
    parser.add_argument("--min-edge-weight", type=int, default=3, help="Minimum co-occurrence count to show edge")
    parser.add_argument(
        "--output", "-o", default=str(DATA_DIR / "plots" / "zhihu_conetwork.png"), help="Output image path"
    )

    args = parser.parse_args()
    main(
        source_dir=args.source_dir,
        topk=args.topk,
        window_size=args.window_size,
        min_edge_weight=args.min_edge_weight,
        output=args.output,
    )
