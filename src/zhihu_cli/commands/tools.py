"""Tools command group — creator analytics, NLP text analysis, and periodic looping."""

import json
import re
import subprocess
import time
from pathlib import Path

import click

from zhihu_cli.content.handlers import get_data_dir
from zhihu_cli.content.handlers.cache_manager import cache_manager
from zhihu_cli.output import (
    echo,
    error,
    f_label,
    f_num,
    f_path,
    heading,
    info,
    print_json,
    success,
    warning,
)


def register_tools(main_group):
    """Register the tools command group and all sub-commands onto *main_group*."""

    def _parse_period(period: str) -> int:
        """Parse a period string like '60', '5m', '1h', '2d' into seconds."""
        period = period.strip().lower()
        m = re.match(r"^(\d+)([smhd]?)$", period)
        if not m:
            raise click.BadParameter(
                f"Invalid period {period!r}. Use an integer (seconds) or suffix s/m/h/d, e.g. '60', '5m', '1h', '2d'."
            )
        value = int(m.group(1))
        unit = m.group(2) or "s"
        multipliers: dict[str, int] = {"s": 1, "m": 60, "h": 3600, "d": 86400}
        return value * multipliers[unit]

    @main_group.group()
    def tools() -> None:
        """Analysis tools — creator analytics, NLP text analysis, and periodic looping."""

    @tools.command("loop")
    @click.argument("command", type=str)
    @click.argument("period", type=str)
    @click.argument("target_jsonl_file", type=click.Path())
    @click.option("--once", is_flag=True, default=False, help="Run once and exit (no looping).")
    @click.option("--override", is_flag=True, default=False, help="Override existing file instead of appending.")
    def tools_loop(command: str, period: str, target_jsonl_file: str, once: bool, override: bool) -> None:
        """Run a zhihu subcommand periodically, appending compact JSON to a JSONL file.

        COMMAND   – zhihu subcommand to run (e.g. "browse hot", "stats <url>").

        PERIOD    – interval between runs: an integer (seconds) or with suffix
                    s / m / h / d, e.g. "60", "5m", "1h", "2d".  Max unit is day.

        TARGET_JSONL_FILE – path to the output JSONL file (one compact JSON
                    object per line).
        """
        period_seconds = _parse_period(period)
        cmd_parts = ["zhihu"] + command.split() + ["--json"]

        heading(f"Loop: zhihu {command} --json  →  {target_jsonl_file}")
        info(f"Period: {period} ({period_seconds}s)")

        while True:
            try:
                result = subprocess.run(cmd_parts, capture_output=True, text=True, timeout=300)
            except subprocess.TimeoutExpired:
                error("Command timed out (300 s)")
            except FileNotFoundError:
                error("'zhihu' not found on PATH — is the CLI installed?")
                raise SystemExit(1)
            else:
                if result.returncode != 0:
                    error(f"Command exited with code {result.returncode}")
                    if result.stderr:
                        error(result.stderr.strip())
                elif not result.stdout.strip():
                    warning("Command produced no output")
                else:
                    try:
                        data = json.loads(result.stdout)
                    except json.JSONDecodeError:
                        error("Command output is not valid JSON — skipping")
                    else:
                        ts = time.time()
                        if isinstance(data, dict):
                            data["time"] = ts
                        else:
                            data = {"time": ts, "data": data}
                        compact = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
                        Path(target_jsonl_file).parent.mkdir(parents=True, exist_ok=True)
                        with open(target_jsonl_file, "a" if not override else "w", encoding="utf-8") as fh:
                            fh.write(compact + "\n")
                        ts_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))
                        action = "Appended" if not override else "Written"
                        success(f"[{ts_str}] {action} to {target_jsonl_file}")

            if once:
                break
            time.sleep(period_seconds)

    @tools.group("creator")
    def tools_creator() -> None:
        """Zhihu creator analytics."""

    @tools_creator.group("income")
    def tools_creator_income() -> None:
        """Income analytics (收益分析)."""

    @tools_creator_income.command("fetch")
    def creator_income_fetch() -> None:
        """Fetch incremental income data from Zhihu creator API."""
        from zhihu_cli.creator_tools.parse_zhihu_incomes import run_task

        run_task()

    @tools_creator_income.command("monthly")
    @click.option(
        "--file",
        "-f",
        "file_path",
        default=str(get_data_dir() / "exports" / "zhihu_income_report.json"),
        help="Income report JSON",
    )
    @click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
    def creator_monthly(file_path: str, output_json: bool) -> None:
        """Print monthly income summary table."""
        from zhihu_cli.creator_tools.analyze_monthly_income import analyze_monthly_income, get_monthly_income_data

        if output_json:
            print_json(get_monthly_income_data(file_path))
            return
        analyze_monthly_income(file_path)

    @tools_creator_income.command("plot")
    def creator_plot() -> None:
        """Generate basic income plot (bar chart + EMA + trend)."""
        from zhihu_cli.creator_tools.plot_zhihu_incomes import plot_analysis

        plot_analysis()
        success(f"Saved {f_path(str(get_data_dir() / 'plots' / 'income_analysis.png'))}")

    @tools_creator_income.command("advanced")
    def creator_advanced() -> None:
        """Generate advanced analysis plot (Bollinger + MACD)."""
        from zhihu_cli.creator_tools.plot_zhihu_incomes_advanced import plot_advanced_analysis

        plot_advanced_analysis()
        success(f"Saved {f_path(str(get_data_dir() / 'plots' / 'income_advanced_analysis.png'))}")

    @tools_creator_income.command("derivative")
    def creator_derivative() -> None:
        """Generate derivative analysis plot (velocity, acceleration, jerk)."""
        from zhihu_cli.creator_tools.derivative_analysis import plot_derivative_analysis

        plot_derivative_analysis()
        success(f"Saved {f_path(str(get_data_dir() / 'plots' / 'derivative_analysis.png'))}")

    @tools_creator_income.command("weekday")
    def creator_weekday() -> None:
        """Generate weekday income distribution plot."""
        from zhihu_cli.creator_tools.weekday_income_analysis import plot_weekday_analysis

        plot_weekday_analysis()
        success(f"Saved {f_path(str(get_data_dir() / 'plots' / 'weekday_income_analysis.png'))}")

    @tools_creator.command("metrics")
    @click.option("--aggr", is_flag=True, help="Use aggregated endpoint (single datapoint per content)")
    def creator_metrics(aggr: bool) -> None:
        """Fetch per-content daily metrics from Zhihu API."""
        from zhihu_cli.creator_tools.parse_content_datas import run_batch_daily_analysis

        run_batch_daily_analysis(use_aggr=aggr)

    @tools_creator.command("growth")
    @click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
    def creator_growth(output_json: bool) -> None:
        """Fetch and display creator growth-level (创作分) data."""
        from zhihu_cli.creator_tools.growth_level import show_growth_level

        show_growth_level(json_output=output_json)

    @tools_creator.command("score")
    def creator_score() -> None:
        """Fetch incremental creator score detail (创作分明细) from Zhihu API."""
        from zhihu_cli.creator_tools.parse_score_detail import run_task

        run_task()

    @tools_creator.command("plot")
    @click.option("--no-pv", is_flag=True, default=False, help="Exclude PV (page view) metric from the plot")
    @click.option("--no-show", is_flag=True, default=False, help="Exclude Show metric from the plot")
    def creator_metrics_plot(no_pv: bool, no_show: bool) -> None:
        """Plot content metrics charts from content_metrics/*.json data."""
        from zhihu_cli.creator_tools.plot_content_metrics import plot_content_metrics

        plot_content_metrics(no_pv=no_pv, no_show=no_show)

    @tools_creator_income.command("income")
    @click.option("--start-date", default=None, help="Start date (YYYY-MM-DD), default: 30 days ago")
    @click.option("--end-date", default=None, help="End date (YYYY-MM-DD), default: today")
    @click.option("--order-field", default="content_publish_at", help="Sort field (default: content_publish_at)")
    @click.option("--order-sort", default="desc", help="Sort direction (default: desc)")
    @click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
    def creator_income(
        start_date: str | None,
        end_date: str | None,
        order_field: str,
        order_sort: str,
        output_json: bool,
    ) -> None:
        """Fetch per-content income detail (单篇内容盐粒) from Zhihu API."""
        from zhihu_cli.creator_tools.parse_income_range import run_task

        run_task(
            start_date=start_date,
            end_date=end_date,
            order_field=order_field,
            order_sort=order_sort,
            json_output=output_json,
        )

    @tools_creator.group("follower")
    def tools_creator_follower() -> None:
        """Follower analytics (关注者分析)."""

    @tools_creator_follower.command("fetch")
    def creator_follower_fetch() -> None:
        """Fetch follower detail data from Zhihu API (from configured start-date to today)."""
        from datetime import date, datetime

        from zhihu_cli.creator_tools.parse_follower_detail import run_task

        start_str = cache_manager.get_start_date()
        start = datetime.strptime(start_str, "%Y-%m-%d").date()
        days = (date.today() - start).days
        run_task(days=days)

    @tools_creator_follower.command("analysis")
    def creator_follower_analysis() -> None:
        """Fetch follower profile/demographics (关注者画像) from Zhihu API."""
        from zhihu_cli.creator_tools.parse_follower_profile import run_task

        run_task()

    @tools_creator_follower.command("plot")
    def creator_follower_plot() -> None:
        """Plot follower detail line chart from follower_detail.json."""
        from zhihu_cli.creator_tools.plot_follower_detail import plot_follower_detail

        plot_follower_detail()

    @tools.group("nlp")
    def tools_nlp() -> None:
        """NLP text analysis on downloaded Markdown files."""

    @tools_nlp.command("count")
    @click.option("--folder", default=str(get_data_dir() / "downloads" / "answers"), help="Folder with Markdown files")
    @click.option("--no-code", is_flag=True, help="Exclude code blocks")
    @click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
    def nlp_count(folder: str, no_code: bool, output_json: bool) -> None:
        """Count words in downloaded Markdown files."""
        import numpy as np

        from zhihu_cli.nlp_tools.count_answer_words import count_words

        word_counts = []
        for filepath in Path(folder).rglob("*.md"):
            word_counts.append(count_words(str(filepath), no_code=no_code))

        if not word_counts:
            if output_json:
                print_json({"files": 0})
            else:
                info("No markdown files found.")
            return

        wc = [int(x) for x in word_counts]
        if output_json:
            print_json(
                {
                    "files": len(wc),
                    "mean": round(float(np.mean(wc)), 1),
                    "std": round(float(np.std(wc)), 1),
                    "p50": int(np.percentile(wc, 50)),
                    "p90": int(np.percentile(wc, 90)),
                    "max": int(max(wc)),
                }
            )
            return

        echo(f"  {f_label('Files:')} {f_num(len(word_counts))}")
        echo(
            f"  {f_label('Mean:')} {f_num(f'{np.mean(word_counts):.0f}')}  {f_label('Std:')} {f_num(f'{np.std(word_counts):.0f}')}"
        )
        echo(
            f"  {f_label('P50:')} {f_num(f'{np.percentile(word_counts, 50):.0f}')}  {f_label('P90:')} {f_num(f'{np.percentile(word_counts, 90):.0f}')}"
        )
        echo(f"  {f_label('Max:')} {f_num(max(word_counts))}")

    @tools_nlp.command("wordcloud")
    @click.option("--source-dir", default=str(get_data_dir() / "downloads"), help="Directory with Markdown files")
    @click.option("--topk", type=int, default=200, help="Top K keywords")
    @click.option("--only-print", is_flag=True, help="Only print keywords, skip image generation")
    def nlp_wordcloud(source_dir: str, topk: int, only_print: bool) -> None:
        """Generate a word cloud from downloaded content."""
        from zhihu_cli.nlp_tools.wordcloud_generator import main as wc_main

        wc_main(topk_words=topk, source_dir=source_dir, only_print=only_print)

    @tools_nlp.command("cluster")
    @click.option("--source-dir", default=str(get_data_dir() / "downloads"), help="Directory with Markdown files")
    @click.option("--output", "-o", default=str(get_data_dir() / "plots" / "zhihu_clusters.png"), help="Output image")
    @click.option("--n-clusters", type=int, default=8, help="Number of clusters")
    @click.option("--n-terms", type=int, default=10, help="Top terms per cluster")
    @click.option(
        "--mode", type=click.Choice(["pca", "tsne", "hybrid"]), default="pca", help="Dimensionality reduction"
    )
    @click.option("--evaluate-k", is_flag=True, help="Run elbow/silhouette analysis to help choose K")
    def nlp_cluster(source_dir: str, output: str, n_clusters: int, n_terms: int, mode: str, evaluate_k: bool) -> None:
        """KMeans cluster visualization of downloaded content."""
        from zhihu_cli.nlp_tools.cluster_visualizer import (
            find_best_k,
            load_and_clean_data,
            process_clusters,
            visualize_with_plotly,
        )

        documents, file_names = load_and_clean_data(source_dir)
        if not documents:
            error("No documents found.")
            return

        if evaluate_k:
            from sklearn.feature_extraction.text import TfidfVectorizer

            vectorizer = TfidfVectorizer(max_features=1500, min_df=2, max_df=0.95)
            X = vectorizer.fit_transform(documents)
            find_best_k(X, max_k=20)
            info("Check the elbow/silhouette plot to choose K.")
            return

        X, labels, vectorizer, kmeans = process_clusters(documents, n_clusters)
        visualize_with_plotly(
            X, labels, file_names, vectorizer, kmeans, n_clusters, mode=mode, output_path=output, n_terms=n_terms
        )  # type: ignore[arg-type]
        success(f"Saved {f_path(output)}")

    @tools_nlp.command("conetwork")
    @click.option("--source-dir", default=str(get_data_dir() / "downloads"), help="Directory with Markdown files")
    @click.option("--topk", type=int, default=80, help="Top N words to include in network")
    @click.option("--window-size", type=int, default=5, help="Co-occurrence window size within documents")
    @click.option("--min-edge-weight", type=int, default=3, help="Minimum co-occurrence count to show edge")
    @click.option(
        "--output", "-o", default=str(get_data_dir() / "plots" / "zhihu_conetwork.png"), help="Output image path"
    )
    def nlp_conetwork(source_dir: str, topk: int, window_size: int, min_edge_weight: int, output: str) -> None:
        """Word co-occurrence network visualization of downloaded content."""
        from zhihu_cli.nlp_tools.cooccurrence_network import main as conetwork_main

        conetwork_main(
            source_dir=source_dir,
            topk=topk,
            window_size=window_size,
            min_edge_weight=min_edge_weight,
            output=output,
        )

    @tools_nlp.command("graph")
    @click.option(
        "--url-token", default=None, help="User url_token to analyze (auto-detects logged-in user if omitted)"
    )
    @click.option("--max-followees", type=int, default=200, help="Max followees to fetch")
    @click.option("--max-followers", type=int, default=200, help="Max followers to fetch")
    @click.option(
        "--output", "-o", default="", help="Output image path (default: ~/.zhihu-cli/plots/zhihu_social_graph.png)"
    )
    @click.option(
        "--layout",
        type=click.Choice(["spring", "kamada_kawai", "circular", "shell"]),
        default="spring",
        help="Graph layout algorithm",
    )
    @click.option("--no-viz", is_flag=True, help="Print statistics only, skip image generation")
    @click.option("--depth", type=int, default=1, help="Graph depth: 1=ego-network, ≥2=recursively expand (default: 1)")
    @click.option("--max-expand", type=int, default=20, help="Max nodes to expand per hop level (default: 20)")
    @click.option("--max-per-node", type=int, default=50, help="Max followees fetched per expanded node (default: 50)")
    def nlp_graph(
        url_token: str | None,
        max_followees: int,
        max_followers: int,
        output: str,
        layout: str,
        no_viz: bool,
        depth: int,
        max_expand: int,
        max_per_node: int,
    ) -> None:
        """Social graph visualization of following/follower relationships."""
        from zhihu_cli.nlp_tools.graph import main as graph_main

        graph_main(
            url_token=url_token,
            max_followees=max_followees,
            max_followers=max_followers,
            output=output,
            layout=layout,
            no_viz=no_viz,
            depth=depth,
            max_expand=max_expand,
            max_per_node=max_per_node,
        )
