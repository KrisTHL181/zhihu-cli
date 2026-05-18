# zhihu-cli

Zhihu scraping, automation, and analysis toolkit for the terminal.

Authenticate once, then download, browse, search, publish, and analyze — all from the command line.

> This is an unofficial CLI for Zhihu. Use at your own risk. Not affiliated with Zhihu.

## Installation

```bash
pip install -e .
```

Requires Python 3.10+.

## Quick Start

```bash
# 1. Authenticate (choose one)
zhihu auth paste              # paste cURL from browser DevTools
zhihu auth login              # QR code login with the Zhihu App

# 2. Check status
zhihu auth status

# 3. Start using
zhihu browse feed --type recommend --markdown
zhihu download article https://zhuanlan.zhihu.com/p/123456
```

## Authentication

Two methods, both cached per-profile under `~/.zhihu-cli/cache/profiles/`:

| Command | Description |
|---|---|
| `zhihu auth paste` | Paste a cURL command copied from browser DevTools (Network tab → Copy as cURL). Parses headers (Cookie, User-Agent, etc.) automatically. |
| `zhihu auth login` | QR code login. Displays a QR code in the terminal — scan with the Zhihu App (My → Settings → Scan). |
| `zhihu auth status` | Show active profile and header/cookie status. |
| `zhihu auth clear` | Remove cached headers. |

Use `--profile` (`-p`) with `auth paste` or `auth login` to save to a named profile:

```bash
zhihu auth paste --profile work
zhihu auth login --profile personal
```

## Multi-Profile Support

Manage multiple accounts with named profiles:

```bash
zhihu profile list              # list all profiles (* marks active)
zhihu profile switch <name>     # switch active profile
zhihu profile current           # show active profile
zhihu profile delete <name>     # delete a profile
```

## Commands

### Download — save content as Markdown

```bash
zhihu download article <url>                    # single article → .md
zhihu download question <url>                   # question + all answers → .md
zhihu download pin <url>                        # single pin → .md
zhihu download batch-answers -i assets.json     # batch download answers from asset JSON
zhihu download batch-articles -i assets.json    # batch download articles from asset JSON
```

Output goes to `~/.zhihu-cli/downloads/` by default. Use `-o` to override.

### Browse — read in the terminal

```bash
zhihu browse answers <url>             # stream answers with Rich pager
zhihu browse comments <url>            # view comment tree
zhihu browse feed --type recommend     # stream recommend/follow feed
zhihu browse hot                       # real-time hot list
zhihu browse hot -v                    # hot list with excerpts and details
zhihu browse notifications             # your notifications
```

Options for `browse feed`:
- `--type recommend|follow` — feed type
- `--markdown` — convert HTML to Markdown inline
- `--limit` / `--max` — pagination control
- `--output` / `-o` — save to JSON
- `--verbose` / `-v` — print items while fetching

### Search — find content

```bash
zhihu search question <query>   --limit 20 --max 50
zhihu search article <query>    --limit 20 --max 50
zhihu search user <query>       --limit 20 --max 50
zhihu search topic <query>      --limit 20 --max 50
```

### Interact — social actions

```bash
# Voting
zhihu interact vote up <url>         # upvote answer or question
zhihu interact vote neutral <url>    # remove vote
zhihu interact vote down <url>       # downvote

# Thanks
zhihu interact thank add <answer_id>
zhihu interact thank remove <answer_id>

# Follow
zhihu interact follow user <user_id>
zhihu interact follow question <question_id>
zhihu interact follow unfollow-user <user_id>
zhihu interact follow unfollow-question <question_id>

# Block
zhihu interact block add <user_id>
zhihu interact block remove <user_id>

# Comments
zhihu interact comment post <url> <content>
zhihu interact comment delete <comment_id>

# Collections
zhihu interact collect add <url> [-c collection_id]
zhihu interact collect remove <url> -c <collection_id>
zhihu interact collect create <title> [-d description] [--public/--private]
zhihu interact collect delete <collection_id>
```

### Publish — create and edit content

```bash
zhihu publish answer <question_id> -f answer.md
zhihu publish modify-answer <answer_id> -f answer.md
zhihu publish article <title> -f article.md
zhihu publish modify-article <article_id> <title> -f article.md
```

Reads Markdown from file or stdin (`-f -`). Markdown is converted to HTML via Zhihu's paste/parse API before publishing.

### Chat — messages

```bash
zhihu chat inbox                   # list recent conversations
zhihu chat history <chat_id>       # read messages
zhihu chat send <user_id> <text>   # send a message
```

### Listen — real-time notifications

```bash
zhihu listen <url_token> --topic notification   # notification stream
zhihu listen <url_token> --topic imchat          # message stream
```

Connects via MQTT. Press Ctrl+C to stop.

### Scrape — batch exports

```bash
zhihu scrape creations -o all_assets.json       # fetch all user creation IDs
zhihu scrape activities -o activities.json      # fetch activity feed (paste API cURL)
zhihu scrape answers -o answers.json            # fetch answer list (paste API cURL)
zhihu scrape articles -o articles.json          # fetch article list (paste API cURL)
```

### Convert — normalize export formats

```bash
zhihu convert universal file1.json file2.json -o unified.json   # merge + normalize
zhihu convert user-act activities.json -o assets.json            # activities → assets
zhihu convert draft <question_url> -o draft.md                   # fetch draft as Markdown
```

### Tools — analytics

#### Income Analytics (`zhihu tools income`)

```bash
zhihu tools income fetch         # fetch creator income data
zhihu tools income monthly       # monthly summary table
zhihu tools income plot          # bar chart + EMA + trend
zhihu tools income advanced      # Bollinger + MACD analysis
zhihu tools income derivative    # velocity, acceleration, jerk
zhihu tools income weekday       # weekday distribution
zhihu tools income metrics       # per-content daily metrics
```

Plots saved to `~/.zhihu-cli/plots/`.

#### NLP Text Analysis (`zhihu tools nlp`)

```bash
zhihu tools nlp count            # word count statistics
zhihu tools nlp wordcloud        # generate word cloud
zhihu tools nlp cluster          # KMeans clustering visualization
zhihu tools nlp cluster --evaluate-k   # elbow/silhouette analysis
```

Operates on Markdown files in `~/.zhihu-cli/downloads/`.

## Architecture

```
src/zhihu_cli/
├── main.py                  # Click CLI — all command groups defined here
├── content/
│   ├── handlers/            # API-interaction layer (one file per Zhihu domain)
│   │   ├── requests.py      # ZhihuSession — auto ZSE signing + TLS impersonation
│   │   ├── article.py       # article scraping
│   │   ├── question.py      # question + answer scraping, voting
│   │   ├── pin.py           # pin scraping
│   │   ├── feed.py          # recommend/follow feed streaming
│   │   ├── comments.py      # comment tree fetching, post/delete
│   │   ├── publishing.py    # answer/article publish + modify
│   │   ├── people.py        # follow/unfollow/block
│   │   ├── collection.py    # collection management
│   │   ├── chat.py          # inbox, history, send
│   │   ├── imchat.py        # MQTT real-time listener
│   │   ├── auth_login.py    # QR code login flow
│   │   ├── cache_manager.py # profile persistence + header cache
│   │   ├── search.py        # question/article/user/topic search
│   │   ├── hot.py           # real-time hot list
│   │   ├── notifications.py # notification feed
│   │   ├── draft.py         # draft → Markdown conversion
│   │   └── waterfall.py     # generic paginated API streamer
│   ├── utils/
│   │   ├── html2markdown.py # HTML → Markdown (LaTeX, tables, Zhihu-specific markup)
│   │   ├── markdown2html.py # Markdown → HTML via Zhihu paste API
│   │   └── zse.py           # ZSE v4 encryption (pure Python, ported from Rust)
│   ├── download_contents.py # content downloader — Markdown + JSON metadata
│   └── universal_converter.py  # normalize export JSON to unified format
├── money_tools/             # creator income analytics + plotting
├── nlp_tools/               # word count, wordcloud, KMeans clustering
└── extensions/              # auto-discovered plugin system
    └── crank/               # paper monitoring + archiving extension
```

### Key Design Decisions

- **Request signing**: Every request to `www.zhihu.com` / `api.zhihu.com` is automatically signed with `x-zse-93` and `x-zse-96` headers via the ZSE v4 cipher.
- **Browser impersonation**: Uses `curl_cffi` to mimic Chrome's TLS fingerprint.
- **Content extraction**: Two paths — page scraping (`js-initialData` embedded JSON) and direct API calls.
- **Markdown pipeline**: HTML → LaTeX preprocessing → recursive BeautifulSoup traversal → Markdown. Reverse for publishing: Markdown → paste API → HTML → publish API.
- **Extension system**: Plugins in `src/zhihu_cli/extensions/` are auto-discovered at startup by scanning for subdirectories with `register_cli(group)` callables.

## Data Directory

All runtime data lives under `~/.zhihu-cli/`:

| Path | Purpose |
|---|---|
| `cache/profiles/` | Named profile headers (JSON) |
| `downloads/articles/` | Downloaded articles (Markdown) |
| `downloads/answers/` | Downloaded answers (Markdown) |
| `downloads/questions/` | Downloaded questions (Markdown) |
| `exports/` | JSON manifests and scraped data |
| `plots/` | Generated chart images (PNG) |

## How It Works

**Authentication** uses browser headers (Cookie, User-Agent, x-zse-96, etc.) extracted from a cURL paste or generated via QR code login. These are cached per-profile.

**Request signing** happens transparently — `ZhihuSession` (a `curl_cffi.Session` subclass) intercepts every request to Zhihu domains and injects ZSE signature headers. The signature is built from `{zse_version}+{path}+{d_c0}[+{body}]`, MD5-hashed, then encrypted with the ZSE v4 cipher.

**Content extraction** prefers direct API calls where possible, falling back to HTML page scraping with `js-initialData` parsing for pages that don't expose a clean API.

## License

MIT
