<div align="center">

# 🧢 zhihu-cli

**The terminal is your new Zhihu HQ.**

```
browse · download · search · publish · analyze — all from the command line.
```

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![GitHub Repo stars](https://img.shields.io/github/stars/KrisTHL181/zhihu-cli?style=flat)](https://github.com/KrisTHL181/zhihu-cli/stargazers)

</div>

---

**zhihu-cli** turns your terminal into a full-featured Zhihu client. Authenticate once, then browse, search, download, publish, and analyze — without ever opening a browser.

> ⚠️ This is an **unofficial** CLI for Zhihu. Use at your own risk. Not affiliated with Zhihu Inc.

---

## 🚀 Quick Start

```bash
# Install
pip install -e .

# Optional extras
pip install -e ".[nlp]"          # word clouds, clustering
pip install -e ".[creator]"      # income charts, trends
pip install -e ".[classifier]"   # ML-powered content classification
```

### 1. Authenticate

```bash
zhihu auth paste          # paste a cURL from browser DevTools
zhihu auth login          # scan a QR code with the Zhihu App
```

### 2. Verify

```bash
zhihu auth status         # check login status and active profile
```

---

## ✨ What You Can Do

### 📖 Read & Browse

Browse Zhihu's feeds, hot lists, questions, articles, pins, and comments — all rendered in your terminal with a Rich-powered pager. Explore the real-time trending list with excerpts, scroll through your personalized recommend feed, or dive into a question and its full answer thread without clicking a single link.

### 💾 Download for Offline

Save any Zhihu content (articles, answers, questions, pins, videos) as clean Markdown with YAML frontmatter. Download individual pieces or batch-process from a manifest. Everything lands in `~/.zhihu-cli/downloads/`, organized by type, ready for your note-taking system or offline reading.

### 🔍 Search

Search questions, articles, users, and topics across Zhihu. Fine-tune result depth and quantity — all from the command line.

### ✍️ Publish & Edit

Write answers and articles in Markdown, publish them to Zhihu with one command. Need to update? Modify existing answers and articles from your local files. Markdown-to-HTML conversion happens automatically via Zhihu's own rendering pipeline.

### 💬 Interact

Vote, thank, follow, block, and comment — full social interaction without the browser. Manage collections: create, populate, and organize them. Send and receive direct messages, and stream real-time notifications and chat via MQTT.

### 🕸️ Scrape & Export

Batch-export your creations, activity history, answers, and articles as structured JSON. Use the universal converter to merge and normalize across formats for your own data pipeline.

### 📊 Analyze

**Creator analytics** — pull your Zhihu income data and visualize it: monthly summaries, trend charts (with EMA), advanced indicators (Bollinger Bands, MACD), derivative analysis (velocity/acceleration/jerk), weekday breakdowns, and per-content daily metrics. Charts are saved as PNGs.

**NLP tools** — run word frequency analysis, generate word clouds, and perform K-means clustering on your downloaded content. All operate across your full Markdown library.

### 🔐 Multi-Account

Save multiple profiles from different accounts, switch between them with a single command. Each profile keeps its own auth credentials and cookies.

---

## 🏗️ Architecture

```
src/zhihu_cli/
├── main.py                  # Click CLI — command group hub
├── output.py                # styled terminal output (Rich)
├── content/
│   ├── handlers/            # one file per Zhihu domain
│   └── utils/               # HTML↔Markdown, ZSE v4 signing
├── creator_tools/           # income analytics + plotting
├── nlp_tools/               # word count, wordcloud, clustering
└── extensions/              # plugin system (auto-discovered)
```

| Layer | What It Does |
|---|---|
| **🔐 Auth** | Browser Cookie + User-Agent headers from cURL paste or QR code login, cached per-profile |
| **✍️ Signing** | Every Zhihu request gets auto-signed with `x-zse-93` / `x-zse-96` headers via ZSE v4 cipher |
| **🌐 Requests** | `curl_cffi` impersonates Chrome's TLS fingerprint to avoid detection |
| **📄 Extraction** | Prefers direct API calls; falls back to HTML page scraping with `js-initialData` parsing |
| **📝 Markdown** | HTML → LaTeX preprocessing → recursive traversal → clean Markdown. Reverse for publishing |
| **🔌 Extensions** | Drop a plugin in `extensions/` with `register_cli(group)` — auto-discovered at startup |

---

## 🧪 Extras

### Optional Dependencies

```bash
pip install -e ".[nlp]"          # jieba, matplotlib, wordcloud, scikit-learn...
pip install -e ".[creator]"      # matplotlib, numpy, pandas, seaborn...
pip install -e ".[classifier]"   # torch, transformers, scikit-learn...
```

### Shell Completions

```bash
eval "$(python autocomp.py)"
```

---

## 🤝 Contributing

PRs welcome! The codebase is structured for extensibility:

- **New command?** Add a file to `commands/` with a `register_<name>(group)` function, import it in `main.py`.
- **New handler?** Add a file to `content/handlers/` — follow the existing patterns.
- **New extension?** Create `extensions/<name>/` with an `__init__.py` exposing `register_cli(group)`.

Run the formatter before submitting:

```bash
pre-commit run --all-files
ruff check .
ruff format .
```

---

## 📜 License

MIT

---

<div align="center">

*Made with ❤️ and way too many terminal sessions.*

[Report a bug](https://github.com/KrisTHL181/zhihu-cli/issues) · [Fork this repository](https://github.com/KrisTHL181/zhihu-cli/fork) · Star ⭐ this repo!

</div>
