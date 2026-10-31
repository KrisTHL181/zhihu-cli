"""MCP (Model Context Protocol) bridge for the zhihu CLI.

The generic implementation lives in :mod:`click2mcp`; :mod:`zhihu_cli.mcp.generate`
holds the zhihu-specific configuration and :mod:`zhihu_cli.mcp.server` is the thin
adapter that serves it over stdio.
"""
