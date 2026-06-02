"""Social network graph visualization for Zhihu following/follower relationships.

Builds a directed ego-network graph centered on a user, showing:
- Followees (people the user follows)
- Followers (people who follow the user)
- Mutual follows (both directions)

Uses NetworkX for graph construction and matplotlib for rendering.
"""

from __future__ import annotations

import sys
from pathlib import Path

import networkx as nx
import numpy as np
import plotly.graph_objects as go

DATA_DIR = Path.home() / ".zhihu-cli"


# ── graph construction ────────────────────────────────────────────────────────


def build_social_graph(
    url_token: str,
    max_followees: int | None = None,
    max_followers: int | None = None,
) -> nx.DiGraph:
    """Build a directed ego-network graph from Zhihu following/follower data.

    Args:
        url_token: The user's url_token to analyze.
        max_followees: Max number of followees to fetch (None = until exhausted).
        max_followers: Max number of followers to fetch (None = until exhausted).

    Returns:
        A ``nx.DiGraph`` where each node has ``name``, ``follower_count``,
        ``is_central``, and optionally ``is_mutual`` attributes.  Edges have a
        ``relation`` attribute set to ``"followee"`` or ``"follower"``.
    """
    from zhihu_cli.content.handlers.following import fetch_followees, fetch_followers
    from zhihu_cli.content.handlers.people import fetch_member_profile

    G = nx.DiGraph()

    # ── central user ──────────────────────────────────────────────────────
    print(f"Fetching profile for {url_token} …")
    profile = fetch_member_profile(url_token)
    if profile:
        central_name: str = profile.get("name", url_token)
        G.add_node(
            url_token,
            name=central_name,
            follower_count=profile.get("follower_count", 0),
            following_count=profile.get("following_count", 0),
            is_central=True,
            is_mutual=False,
        )
        print(
            f"  {central_name}  关注: {profile.get('following_count', 0)}  关注者: {profile.get('follower_count', 0)}"  # noqa: ISC001
        )
    else:
        print("  ⚠  Could not fetch profile, using url_token as label.")
        G.add_node(
            url_token,
            name=url_token,
            follower_count=0,
            following_count=0,
            is_central=True,
            is_mutual=False,
        )
        central_name = url_token

    # ── followees (关注的用户) ─────────────────────────────────────────────
    print(f"Fetching followees (max: {max_followees or 'all'}) …")
    followees = fetch_followees(url_token, max_items=max_followees)
    followee_tokens: set[str] = set()
    for item in followees:
        token: str = item.get("url_token", "")
        if not token:
            continue
        followee_tokens.add(token)
        if token not in G:
            G.add_node(
                token,
                name=item.get("name", token),
                follower_count=item.get("follower_count", 0),
                headline=item.get("headline", ""),
                is_central=False,
                is_mutual=False,
            )
        G.add_edge(url_token, token, relation="followee")
    print(f"  {len(followees)} followees fetched")

    # ── followers (关注者 / 粉丝) ──────────────────────────────────────────
    print(f"Fetching followers (max: {max_followers or 'all'}) …")
    followers = fetch_followers(url_token, max_items=max_followers)
    follower_tokens: set[str] = set()
    for item in followers:
        token = item.get("url_token", "")
        if not token:
            continue
        follower_tokens.add(token)
        if token not in G:
            G.add_node(
                token,
                name=item.get("name", token),
                follower_count=item.get("follower_count", 0),
                headline=item.get("headline", ""),
                is_central=False,
                is_mutual=False,
            )
        G.add_edge(token, url_token, relation="follower")
    print(f"  {len(followers)} followers fetched")

    # ── mark mutual follows ───────────────────────────────────────────────
    mutual = followee_tokens & follower_tokens
    for token in mutual:
        G.nodes[token]["is_mutual"] = True
    if mutual:
        print(f"  ⇄ {len(mutual)} mutual follows detected")

    return G


# ── graph statistics ──────────────────────────────────────────────────────────


def print_graph_stats(G: nx.DiGraph) -> None:
    """Print a human-readable statistics summary for the social graph."""
    central_nodes = [n for n, d in G.nodes(data=True) if d.get("is_central")]
    central = central_nodes[0] if central_nodes else None

    mutual_nodes = [n for n, d in G.nodes(data=True) if d.get("is_mutual")]
    followee_only = [n for n in (G.successors(central) if central else []) if n not in mutual_nodes]
    follower_only = [n for n in (G.predecessors(central) if central else []) if n not in mutual_nodes]

    print()
    print("=" * 60)
    print("  Social Graph Statistics")
    print("=" * 60)
    print(f"  Total nodes:     {G.number_of_nodes()}")
    print(f"  Total edges:     {G.number_of_edges()}")
    if central:
        name = G.nodes[central].get("name", central)
        print(f"  Central user:    {name}")
        print(f"    Followees:     {len(followee_only) + len(mutual_nodes)}")
        print(f"    Followers:     {len(follower_only) + len(mutual_nodes)}")
        print(f"    Mutual:        {len(mutual_nodes)}")
    print()

    # Top nodes by degree
    degrees = dict(G.degree())
    top = sorted(degrees.items(), key=lambda x: x[1], reverse=True)[:10]
    print("  Top nodes by degree:")
    for node, deg in top:
        name = G.nodes[node].get("name", node)
        fc = G.nodes[node].get("follower_count", 0)
        marks = ""
        if G.nodes[node].get("is_central"):
            marks += "★"
        if G.nodes[node].get("is_mutual"):
            marks += "⇄"
        print(f"    {marks:<3s} {name[:20]:<20s}  deg={deg:<4d}  followers={fc}")

    # Mutual follows list
    if mutual_nodes:
        print(f"\n  Mutual follows ({len(mutual_nodes)}):")
        for n in mutual_nodes[:20]:
            name = G.nodes[n].get("name", n)
            print(f"    ⇄ {name}")
        if len(mutual_nodes) > 20:
            print(f"    … and {len(mutual_nodes) - 20} more")

    print("=" * 60)
    print()


# ── visualization ─────────────────────────────────────────────────────────────


def visualize_social_graph(
    G: nx.DiGraph,
    output_path: str = "",
    layout: str = "spring",
    width: int = 1600,
    height: int = 1200,
) -> None:
    """Render the social graph to a PNG file with Plotly.

    Args:
        G: The directed social graph.
        output_path: Where to save the image.  Defaults to
            ``~/.zhihu-cli/plots/zhihu_social_graph.png``.
        layout: One of ``spring``, ``kamada_kawai``, ``circular``, ``shell``.
        width: Image width in pixels.
        height: Image height in pixels.
    """
    if not output_path:
        output_path = str(DATA_DIR / "plots" / "zhihu_social_graph.png")

    if G.number_of_nodes() == 0:
        print("Graph has no nodes to visualize.")
        return

    # ── identify central node ─────────────────────────────────────────────
    central_nodes = [n for n, d in G.nodes(data=True) if d.get("is_central")]
    central = central_nodes[0] if central_nodes else list(G.nodes())[0]
    central_name = G.nodes[central].get("name", central)

    # ── classify nodes ────────────────────────────────────────────────────
    mutual_set: set[str] = {n for n, d in G.nodes(data=True) if d.get("is_mutual")}
    followee_set: set[str] = set(G.successors(central)) - mutual_set
    follower_set: set[str] = set(G.predecessors(central)) - mutual_set

    # Assign each node a category index for colour / legend.
    # 0 = 自己 (central), 1 = 互相关注 (mutual), 2 = 关注的用户 (followee),
    # 3 = 关注者 (follower)
    cat_map: dict[str, int] = {}
    for n in G.nodes():
        if G.nodes[n].get("is_central"):
            cat_map[n] = 0
        elif G.nodes[n].get("is_mutual"):
            cat_map[n] = 1
        elif n in followee_set:
            cat_map[n] = 2
        else:
            cat_map[n] = 3

    # ── compute layout ────────────────────────────────────────────────────
    print(f"Computing {layout} layout for {G.number_of_nodes()} nodes …")

    if layout == "spring":
        pos = nx.spring_layout(G, k=4.0, iterations=100, seed=42)
    elif layout == "kamada_kawai":
        pos = nx.kamada_kawai_layout(G)
    elif layout == "circular":
        pos = nx.circular_layout(G)
    elif layout == "shell":
        others = [n for n in G.nodes() if n != central]
        pos = nx.shell_layout(G, nlist=[[central], others])
    else:
        pos = nx.spring_layout(G, k=4.0, iterations=100, seed=42)

    # ── node sizes ────────────────────────────────────────────────────────
    def _node_size(node: str) -> float:
        if G.nodes[node].get("is_central"):
            return 28
        fc = G.nodes[node].get("follower_count", 0)
        return max(5, min(18, 5 + 4 * np.log1p(fc)))

    # ── edge traces ───────────────────────────────────────────────────────
    edge_traces: list[go.Scatter] = []

    followee_edges = [(u, v) for u, v, d in G.edges(data=True) if d.get("relation") == "followee"]
    follower_edges = [(u, v) for u, v, d in G.edges(data=True) if d.get("relation") == "follower"]

    def _make_edge_trace(edges: list[tuple[str, str]], color: str, name: str) -> go.Scatter | None:
        if not edges:
            return None
        edge_x: list[float | None] = []
        edge_y: list[float | None] = []
        for u, v in edges:
            x0, y0 = pos[u]
            x1, y1 = pos[v]
            edge_x.extend([x0, x1, None])
            edge_y.extend([y0, y1, None])
        return go.Scatter(
            x=edge_x,
            y=edge_y,
            mode="lines",
            line={"width": 0.6, "color": color},
            opacity=0.3,
            hoverinfo="none",
            showlegend=True,
            name=name,
        )

    fe_trace = _make_edge_trace(followee_edges, "#3498db", "关注的用户 →")
    if fe_trace:
        edge_traces.append(fe_trace)

    fr_trace = _make_edge_trace(follower_edges, "#f39c12", "→ 关注者")
    if fr_trace:
        edge_traces.append(fr_trace)

    # ── node traces (one per category — Plotly draw order = z-order) ──────
    CATEGORIES = [
        ("#e74c3c", "自己"),
        ("#2ecc71", "互相关注"),
        ("#3498db", "关注的用户"),
        ("#f39c12", "关注者"),
    ]

    node_traces: list[go.Scatter] = []
    for ci, (color, label) in enumerate(CATEGORIES):
        nodelist = [n for n, c in cat_map.items() if c == ci]
        if not nodelist:
            continue
        nx_vals = [pos[n][0] for n in nodelist]
        ny_vals = [pos[n][1] for n in nodelist]
        sizes = [_node_size(n) for n in nodelist]

        hover_texts: list[str] = []
        for n in nodelist:
            name = G.nodes[n].get("name", n)
            fc = G.nodes[n].get("follower_count", 0)
            headline = G.nodes[n].get("headline", "")
            parts = [f"<b>{name}</b>", f"关注者: {fc}"]
            if headline:
                parts.append(headline[:80])
            hover_texts.append("<br>".join(parts))

        node_traces.append(
            go.Scatter(
                x=nx_vals,
                y=ny_vals,
                mode="markers+text",
                marker={
                    "size": sizes,
                    "color": color,
                    "line": {"width": 0.8, "color": "#ffffff"},
                },
                text=[G.nodes[n].get("name", n) for n in nodelist],
                textposition="middle center",
                textfont={"size": 8, "color": "#222222"},
                hoverinfo="text",
                hovertext=hover_texts,
                name=f"{label} ({len(nodelist)})",
            )
        )

    # ── figure ────────────────────────────────────────────────────────────
    n_followees = len(followee_set) + len(mutual_set)
    n_followers = len(follower_set) + len(mutual_set)

    title_text = (
        f"知乎关注网络社交图谱 — {central_name}<br>"
        f"<sup>"
        f"节点 {G.number_of_nodes()}  ·  边 {G.number_of_edges()}  ·  "
        f"关注 {n_followees}  ·  关注者 {n_followers}  ·  互关 {len(mutual_set)}"
        f"</sup>"
    )

    fig = go.Figure(
        data=edge_traces + node_traces,
        layout=go.Layout(
            title={"text": title_text, "font": {"size": 18}},
            showlegend=True,
            legend={
                "x": 1.02,
                "y": 1,
                "xanchor": "left",
                "font": {"size": 11},
                "bgcolor": "rgba(255,255,255,0.85)",
                "bordercolor": "#cccccc",
                "borderwidth": 1,
            },
            hovermode="closest",
            margin={"b": 20, "l": 20, "r": 20, "t": 80},
            xaxis={"showgrid": False, "zeroline": False, "showticklabels": False},
            yaxis={"showgrid": False, "zeroline": False, "showticklabels": False},
            template="plotly_white",
            width=width,
            height=height,
        ),
    )

    # ── save ──────────────────────────────────────────────────────────────
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    try:
        fig.write_image(output_path, scale=2, width=width, height=height)
        print(f"Social graph saved to: {output_path}")
    except Exception:
        print("Tip: kaleido is not installed, unable to save PNG. Install with: pip install kaleido")

    fig.show()


# ── main entry point ──────────────────────────────────────────────────────────


def main(
    url_token: str | None = None,
    max_followees: int | None = None,
    max_followers: int | None = None,
    output: str = "",
    layout: str = "spring",
    no_viz: bool = False,
) -> None:
    """Build and visualize a Zhihu social network graph.

    Args:
        url_token: User url_token to analyze.  Auto-detects the logged-in
            user when omitted.
        max_followees: Cap on followees to fetch.
        max_followers: Cap on followers to fetch.
        output: Output image path.
        layout: Graph layout algorithm.
        no_viz: If ``True``, print statistics only — no image is generated.
    """
    from zhihu_cli.content.handlers.following import get_my_url_token

    if not output:
        output = str(DATA_DIR / "plots" / "zhihu_social_graph.png")

    # Resolve url_token
    if not url_token:
        url_token = get_my_url_token()
        if not url_token:
            print(
                "Error: Cannot detect your url_token.  Authenticate first (zhihu auth login) or pass --url-token.",
                file=sys.stderr,
            )
            sys.exit(1)
        print(f"Using logged-in user: {url_token}")

    # Build the graph
    G = build_social_graph(
        url_token,
        max_followees=max_followees,
        max_followers=max_followers,
    )

    if G.number_of_nodes() <= 1:
        print("Graph is empty (only the central node).  No followees or followers were returned.")
        return

    # Stats
    print_graph_stats(G)

    # Visualize
    if not no_viz:
        visualize_social_graph(G, output_path=output, layout=layout)


# ── standalone entry point ────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Zhihu social network graph visualization")
    parser.add_argument(
        "--url-token",
        help="User url_token to analyze (auto-detects logged-in user if omitted)",
    )
    parser.add_argument(
        "--max-followees",
        type=int,
        default=200,
        help="Max followees to fetch (default: 200)",
    )
    parser.add_argument(
        "--max-followers",
        type=int,
        default=200,
        help="Max followers to fetch (default: 200)",
    )
    parser.add_argument(
        "--output",
        "-o",
        default=str(DATA_DIR / "plots" / "zhihu_social_graph.png"),
        help="Output image path",
    )
    parser.add_argument(
        "--layout",
        choices=["spring", "kamada_kawai", "circular", "shell"],
        default="spring",
        help="Graph layout algorithm (default: spring)",
    )
    parser.add_argument(
        "--no-viz",
        action="store_true",
        help="Print statistics only, skip image generation",
    )

    args = parser.parse_args()
    main(
        url_token=args.url_token or None,
        max_followees=args.max_followees,
        max_followers=args.max_followers,
        output=args.output,
        layout=args.layout,
        no_viz=args.no_viz,
    )
