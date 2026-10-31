# Zhihu Toolkit for DeepSeek Harness

Bring your Zhihu account into a DeepSeek Harness session.
This plugin hands the agent the same Zhihu toolbelt you already use at the terminal —
search, read, download, publish, and analyze — so you can work with Zhihu from the
conversation instead of dropping into a shell.

It is an adapter, not a second implementation. Every command and flag of
[`zhihu-cli`](https://github.com/KrisTHL181/zhihu-toolkit) works through it, and whatever you
already know about the CLI applies here unchanged.

## What it lets you do

**Read and research.** Search questions, articles, users, and topics with tunable depth
and count. Pull a question and its full answer thread, an article, a pin, or a comment
section. Look up someone's profile and their posts. Read your own feed, hot list, drafts,
and inbox.

**Save things for later.** Download any post as clean Markdown — YAML frontmatter,
rendered footnotes, LaTeX handled — into `~/.zhihu-cli/downloads/`, organized by type.
Batch-scrape a user's content lists or your own activity history to JSON, and reshape the
exports with the universal converter.

**Write and manage.** Publish and edit answers, articles, and pins written in Markdown
(the Markdown-to-HTML step goes through Zhihu's own renderer). Manage drafts, collections,
and paid-consultation answers.

**Interact.** Vote, thank, follow, block, and comment. Send and receive direct messages.

**Analyze.** Creator analytics: income, follower detail, per-content daily metrics, trend
charts with EMA and Bollinger/MACD indicators, derivative analysis. Plus NLP over your
downloaded library — word frequency, word clouds, K-means clustering.

**Talk to Zhihu's own AI.** 知乎直答 (Zhihu Direct Answer) and the Zhihu chat bot.

The agent discovers the exact command surface at runtime, so it always matches the version
you have installed rather than a list frozen into this plugin.

## Requirements

- **Python 3.12+** with the toolkit installed:
  ```bash
  pipx install zhihu-toolkit     # or: pip install zhihu-toolkit
  ```
- **`zhihu-mcp` on `PATH`** — it ships with the toolkit. If your install isn't on `PATH`
  (an unactivated virtualenv, a renamed shim), point the plugin at it instead:
  ```bash
  ZHIHU_MCP_COMMAND=/path/to/venv/bin/zhihu-mcp
  # or invoke it as a module
  ZHIHU_MCP_COMMAND=/path/to/venv/bin/python
  ZHIHU_MCP_ARGS="-m zhihu_cli.mcp.server"
  ```
- **Log in once from a terminal.** Authentication is interactive and cannot be driven from
  a conversation:
  ```bash
  zhihu auth paste      # paste a cURL from the browser devtools
  zhihu auth login      # or scan a QR code with the app
  zhihu auth status     # verify
  ```
  Multiple accounts are supported through profiles; the plugin uses whichever is active.

## Install

Through the Harness plugin manager:

```
plugin_manager   action=install_bundle   target=@kristhl181/dsh-plugin-zhihu
```

In the Web UI the same thing lives on the **Plugins** page. Working from a checkout of
this repository, install the directory directly instead:
`target=/absolute/path/to/zhihu-cli/dsh-plugin`.

The install is profile-wide — every session in that profile gets the tools, and it
survives restarts. To remove it:

```
plugin_manager   action=remove_bundle   target=@kristhl181/dsh-plugin-zhihu
```

## What to expect

- **Two tools, not two hundred.** The agent gets `zhihu_help` to walk the command tree and
  `zhihu_run` to execute any command. That is deliberate: a command added to the CLI shows
  up here immediately, with no change to this plugin.
- **Read commands take `--json`.** The agent is instructed to use it, so results come back
  parseable instead of as styled terminal text. `publish`, `config`, and `convert` print
  plain text and are passed through as-is.
- **No deadline is imposed here.** A command runs until it finishes; how long to wait
  before giving up is the MCP client's decision, not the server's. Nothing is killed
  mid-run.
- **Commands that can never finish are refused before they run.** `chat interactive`
  and `interact comment interactive` take over the screen; `listen messages`,
  `listen notifications`, `chat bot`, and `tools loop` stream until interrupted. None
  can finish over MCP, so the server rejects those paths up front and asks you to run
  them yourself rather than retrying them.
- **Nothing is sandboxed.** Every command group is reachable, so `publish`, `interact`,
  `chat`, and `agora` are as available as reads. If you want the agent kept read-only, the
  Harness tool-approval and permission settings are the right place to enforce that.
- **Credentials are not copied anywhere.** This plugin starts the toolkit's own server; the
  CLI reads its credentials and writes its downloads exactly where it always has, under
  `~/.zhihu-cli/`.

## Maintaining this directory

**Version.** `package.json` carries the placeholder `0.0.0`; the real version is injected at
release time from the git tag, so it always matches the toolkit version
(`v0.2.1rc1` → `0.2.1-rc.1`, mapped from PEP 440 to SemVer by `scripts/sync-version.mjs`).
The `prepublishOnly` hook runs that script with `--check`, which refuses to publish the
placeholder. No lifecycle hook ever *writes* the tracked manifest — that would dirty the
working tree on every install. The `.github/workflows/publish.yml` `publish-npm` job owns
the write, authenticating over npm trusted publishing (OIDC, no token).

**Two traps in this repository.**

1. The root `.gitignore` starts with `*.json`, which silently swallows this directory's
   `package.json` and `locale/*.json` — CI would check out a plugin with no manifest. The
   rule below it puts them back:
   ```gitignore
   !dsh-plugin/**/*.json
   ```
2. Do not add a `prepare` or `postinstall` hook that rewrites `package.json`. Only CI's
   throwaway checkout should mutate it.

`ruff check .` and `ruff format --check .` ignore this directory, and
`packages.find where = ["src"]` keeps it out of the wheel. There is no `MANIFEST.in`, and
setuptools-scm seeds the sdist from the git-tracked file set, so once committed this
directory will ride along in the Python sdist — a few KB. Add a `MANIFEST.in` if you would
rather exclude it.

**How it works.** `cordis.patch.yml` inserts one row: the shipped
`@deepseek-ai/dsh-mcp-client` bridge, pointed at the `zhihu-mcp` stdio server that the
toolkit declares in its own `pyproject.toml`. There is no JavaScript of our own. The
command is resolved through `PATH` rather than hardcoded, which works because the Harness
forwards `PATH` and `HOME` to the child (only credential-shaped names and `DSH_*` are
scrubbed), so no `env` block is needed either.
