# zhihu-mcp

<!-- mcp-name: io.github.KrisTHL181/zhihu-mcp -->

An [MCP](https://modelcontextprotocol.io) server that exposes [Zhihu](https://www.zhihu.com) — China's largest Q&A platform — to AI agents over stdio.

This package is a thin alias: it installs [`zhihu-toolkit[mcp]`](https://pypi.org/project/zhihu-toolkit/) and provides the `zhihu-mcp` executable. The server itself is implemented in `zhihu-toolkit`.

## Install

```bash
uvx zhihu-mcp          # run without installing
pip install zhihu-mcp  # or install it
```

Requires **Python 3.10+**.

## Use

Add the server to any MCP client (Claude Code, Claude Desktop, Cursor, …):

```json
{
  "mcpServers": {
    "zhihu": {
      "command": "zhihu-mcp"
    }
  }
}
```

Then ask, for example:

> Search Zhihu for articles about Rust async, pick three, and download them as Markdown.

The server exposes one typed tool per CLI command, each with a JSON Schema derived from that command's own parameters, plus the `zhihu_help` and `zhihu_run` escape hatches. The full surface consumes a lot of context. Newer `zhihu-toolkit` releases add `zhihu-mcp --groups browse,search`, which narrows it to selected command subtrees.

Authentication is shared with the `zhihu` CLI. Log in once with `zhihu auth login`, `zhihu auth paste`, or `zhihu auth import`, and the MCP server uses the same profile.

## MCP Registry

The repository's `server.json` is the manifest for the [official MCP Registry](https://registry.modelcontextprotocol.io). Its two `version` strings are the placeholder `0.0.0` in the working tree — do not replace it with a real release number. The `publish-mcp.yml` workflow rewrites both from the tag-derived build version, publishes the entry with GitHub OIDC (no secret needed), and uploads the same manifest as the `mcp-registry-manifest` artifact. The artifact is the manual fallback if the automated publish fails:

```bash
mcp-publisher login github
mcp-publisher publish
```

The version in the manifest must match a release that actually exists on PyPI, or the registry rejects the entry.

## Links

- Source, documentation, and the full command reference: <https://github.com/KrisTHL181/zhihu-toolkit>
- The underlying toolkit: [`zhihu-toolkit`](https://pypi.org/project/zhihu-toolkit/)

MIT licensed. Unofficial project, not affiliated with Zhihu.
