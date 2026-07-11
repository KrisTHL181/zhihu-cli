"""Interactive comment browser with keyboard navigation and inline reply.

Provides a Textual-based terminal UI for browsing threaded comments
and replying to them on Zhihu items (answers, articles, pins, questions).
"""

from __future__ import annotations

from typing import Any

from zhihu_cli.content.handlers import fmt_time
from zhihu_cli.content.handlers.comments import (
    cancel_dislike_comment,
    cancel_like_comment,
    comment_item,
    dislike_comment,
    fetch_root_comments,
    like_comment,
)

# ── Flatten threaded comment tree for linear keyboard navigation ──────────


def _flatten_comments(
    comments: list[dict[str, Any]],
    depth: int = 0,
) -> list[dict[str, Any]]:
    """Flatten a threaded comment tree into a linear list for keyboard navigation.

    Each item is a dict with ``comment`` (the original comment dict), ``depth``
    (nesting level), and ``is_root`` (True for top-level comments).

    :param comments: Threaded comment tree (as returned by :func:`fetch_root_comments`).
    :param depth: Current nesting depth (0 for root comments).
    :returns: Flat list of navigation items.
    """
    result: list[dict[str, Any]] = []
    for c in comments:
        result.append({"comment": c, "depth": depth, "is_root": depth == 0})
        if c.get("child_comments"):
            result.extend(_flatten_comments(c["child_comments"], depth + 1))
    return result


def _truncate(text: str, max_len: int = 70) -> str:
    """Truncate *text* to at most *max_len* characters."""
    text = text.replace("\n", " ").strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


# ── Textual widgets ──────────────────────────────────────────────────────


class _CommentItem:
    """Renderable data for a single comment line in the ListView.

    Not a Textual widget itself — stores pre-built Rich markup strings
    that the :class:`_CommentList` widget renders.
    """

    __slots__ = ("item", "index", "total", "vote_state", "line1", "line2")

    def __init__(
        self,
        item: dict[str, Any],
        index: int,
        total: int,
        vote_state: str | None = None,
    ) -> None:
        self.item = item
        self.index = index
        self.total = total
        self.vote_state = vote_state
        self.line1 = ""
        self.line2 = ""
        self._build_lines(selected=False)

    def _build_lines(self, selected: bool) -> None:
        """Rebuild both display lines as Rich :class:`~rich.text.Text` objects.

        Uses ``Text`` instead of raw markup strings so that author names and
        comment content are displayed literally — brackets, backslashes and
        other markup-significant characters are never interpreted as tags.
        """
        from rich.text import Text

        c = self.item["comment"]
        depth = self.item["depth"]
        author = c.get("author", "anonymous")
        content_text = _truncate(c.get("content", ""), 80)

        # ── Line 1: selection marker + vote + indent + author + stats ──
        line1 = Text()

        # Selection marker
        if selected:
            line1.append("▶", style="bold #b4befe")
            line1.append(" ☐ ", style="bold #b4befe")
        else:
            line1.append("  ☐ ")

        # Vote indicator
        if self.vote_state == "liked":
            line1.append("♥️ ", style="bold #f38ba8")
        elif self.vote_state == "disliked":
            line1.append("💔 ", style="#585b70")
        else:
            line1.append("  ")

        # Indentation for child comments
        if depth > 0:
            indent = "  " * (depth - 1) + " ↳ "
            line1.append(indent, style="#7f849c")
        elif depth == 0 and self.total > 1:
            # Root comment serial number
            serial = str(self.index + 1)
            line1.append(f"{serial} ", style="bold #fab387")

        # Author name — plain text, no markup parsing
        line1.append(author, style="bold #a6e3a1")

        # Stats: likes, time
        stats_parts: list[str] = []
        stats_parts.append(f"👍 {c.get('like_count', 0)}")
        if c.get("dislike_count", 0):
            stats_parts.append(f"👎 {c.get('dislike_count', 0)}")
        created = c.get("created_time")
        if created:
            stats_parts.append(f"{fmt_time(created)}")
        child_count = len(c.get("child_comments", []))
        if child_count:
            stats_parts.append(f"({child_count} replies)")

        line1.append(f"  {'  '.join(stats_parts)}", style="#89b4fa")

        self.line1 = line1

        # ── Line 2: content preview ──
        prefix = "      "  # after select+checkbox+leading space
        if depth > 0:
            prefix += "  " * (depth - 1) + "   "  # indent + ↳

        line2 = Text()
        line2.append(f"{prefix}{content_text}", style="#cdd6f4")
        self.line2 = line2

    def update_highlight(self, selected: bool) -> None:
        """Rebuild line1 to reflect selection state."""
        self._build_lines(selected)


# ── Main TUI entry point ────────────────────────────────────────────────


def run_interactive_comments(item_type: str, item_id: str) -> None:
    """Launch the interactive comment browser TUI.

    Fetches the full comment tree for *item_type*/*item_id*, then displays a
    keyboard-navigable list.  Press :kbd:`Enter` on any comment to open an
    inline reply panel on the right side of the screen.

    :param item_type: Resource type (``"answers"``, ``"articles"``, ``"pins"``, ``"questions"``).
    :param item_id: Resource ID.
    """
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.containers import Horizontal, Vertical
    from textual.widgets import Footer, Header, Input, ListItem, ListView, Static

    from zhihu_cli.output import error, info

    # ── Fetch comments ──────────────────────────────────────────────────
    info(f"Fetching comments for {item_type}/{item_id}...")
    try:
        comments = list(fetch_root_comments(item_type, item_id))
    except Exception as e:
        error(f"Failed to fetch comments: {e}")
        return

    if not comments:
        info("No comments found.")
        return

    flat_items = _flatten_comments(comments)

    # ── Build CommentItem list and optional reply panel ─────────────────

    class CommentListItem(ListItem):
        """A single comment row in the ListView."""

        def __init__(self, ci: _CommentItem) -> None:
            super().__init__()
            self._ci = ci
            self._line1 = Static(ci.line1, classes="comment-line1")
            self._line2 = Static(ci.line2, classes="comment-line2")

        def compose(self) -> ComposeResult:
            yield self._line1
            yield self._line2

        def watch_highlighted(self, value: bool) -> None:  # noqa: FBT001
            """Update the selection marker when highlight state changes."""
            self._ci.update_highlight(value)
            self._line1.update(self._ci.line1)

    # ── App ─────────────────────────────────────────────────────────────

    class CommentBrowserApp(App[None]):
        """Textual TUI for browsing and replying to Zhihu comments."""

        BINDINGS = [
            Binding("up,k", "cursor_up", "上移", show=False),
            Binding("down,j", "cursor_down", "下移", show=False),
            Binding("pageup", "jump_root_prev", "上个根评论", show=False),
            Binding("pagedown", "jump_root_next", "下个根评论", show=False),
            Binding("home", "go_top", "顶部", show=False),
            Binding("end", "go_bottom", "底部", show=False),
            Binding("enter", "reply_or_submit", "回复", show=False, priority=True),
            Binding("tab", "toggle_like", "点赞", show=False, priority=True),
            Binding("shift+tab", "toggle_dislike", "点踩", show=False, priority=True),
            Binding("escape", "cancel_reply", "取消回复", show=False),
            Binding("ctrl+r", "refresh", "刷新", show=False),
            Binding("q,ctrl+q", "quit_app", "退出", show=True),
        ]

        CSS = """
        Screen {
            background: #1e1e2e;
        }

        .comment-line1 {
            color: #cdd6f4;
            padding: 0 1;
            height: 1;
        }

        .comment-line2 {
            color: #cdd6f4;
            padding: 0 1;
            height: 1;
        }

        ListView {
            height: 1fr;
            background: #1e1e2e;
        }

        ListView > ListItem {
            padding: 0;
            height: auto;
        }

        ListView > ListItem.--highlight {
            background: #313244;
        }

        #reply-panel {
            width: 44;
            dock: right;
            background: #181825;
            border-left: solid #313244;
            padding: 1;
            height: 1fr;
        }

        #reply-header {
            color: #cba6f7;
            text-style: bold;
            padding: 0 1;
        }

        #reply-info {
            color: #a6e3a1;
            padding: 0 1;
        }

        #reply-hint {
            color: #6c7086;
            text-style: italic;
            padding: 0 1;
        }

        Input#reply-input {
            margin: 1 0;
            background: #313244;
            color: #cdd6f4;
            border: none;
        }

        Input#reply-input:focus {
            background: #45475a;
        }

        Header {
            background: #313244;
            color: #cba6f7;
        }

        Footer {
            background: #313244;
            color: #6c7086;
        }
        """

        def __init__(self) -> None:
            super().__init__()
            self._flat_items = flat_items
            self._item_type = item_type
            self._item_id = item_id
            self._votes: dict[str, str | None] = {}  # comment_id -> "liked" | "disliked" | None
            self._replying: bool = False
            self._reply_target: dict[str, Any] | None = None

        def compose(self) -> ComposeResult:
            yield Header()
            with Horizontal():
                yield ListView(
                    *self._build_list_items(),
                    id="comments",
                )
                with Vertical(id="reply-panel"):
                    yield Static("", id="reply-header")
                    yield Static("", id="reply-info")
                    yield Static(
                        "Type your reply below.\nEnter to send, Esc to cancel.",
                        id="reply-hint",
                    )
                    yield Input(id="reply-input", placeholder="输入回复...")
            yield Footer()

        def on_mount(self) -> None:
            """Initialise UI state after mount."""
            self.title = f"Comments @ {self._item_type}/{self._item_id}"
            self._list = self.query_one("#comments", ListView)
            self._reply_panel = self.query_one("#reply-panel", Vertical)
            self._reply_panel.display = False
            self._update_header()
            self._list.focus()

        # ── Helpers ─────────────────────────────────────────────────

        def _build_list_items(self) -> list[CommentListItem]:
            """Build :class:`CommentListItem` instances for all flat items."""
            items: list[CommentListItem] = []
            for i, fi in enumerate(self._flat_items):
                cid = fi["comment"].get("id")
                vote = self._votes.get(cid) if cid else None
                ci = _CommentItem(fi, i, len(self._flat_items), vote)
                items.append(CommentListItem(ci))
            return items

        def _rebuild_list(self, keep_index: bool = True) -> None:  # noqa: FBT001,FBT002
            """Clear and repopulate the ListView with current state."""
            old_index = self._list.index if self._list.index is not None else 0
            self._list.clear()
            self._list.extend(self._build_list_items())
            if keep_index and old_index < len(self._flat_items):
                self._list.index = old_index

        def _update_header(self) -> None:
            """Update the Header subtitle with current comment count."""
            total = len(self._flat_items)
            self.sub_title = f"{total} comments"

        def _get_selected_item(self) -> dict[str, Any] | None:
            """Return the flat item dict at the current cursor position."""
            idx = self._list.index
            if idx is not None and 0 <= idx < len(self._flat_items):
                return self._flat_items[idx]
            return None

        def _get_selected_comment_id(self) -> str | None:
            """Return the comment ID of the currently selected item."""
            item = self._get_selected_item()
            if item is None:
                return None
            return item["comment"].get("id")  # type: ignore[no-any-return]

        def _notify_status(self, message: str, is_error: bool = False) -> None:  # noqa: FBT001,FBT002
            """Show a notification toast in the UI."""
            if is_error:
                self.notify(message, severity="error", timeout=4)
            else:
                self.notify(message, timeout=3)

        # ── Reply panel ──────────────────────────────────────────────

        def _enter_reply_mode(self) -> None:
            """Show the reply panel and focus the input."""
            item = self._get_selected_item()
            if item is None:
                return
            c = item["comment"]
            self._replying = True
            self._reply_target = c

            author = c.get("author", "anonymous")
            content = c.get("content", "")

            from rich.text import Text

            header = Text()
            header.append("┌─ Replying to ", style="bold #cba6f7")
            header.append("─" * 22, style="bold #cba6f7")
            self.query_one("#reply-header", Static).update(header)

            info = Text()
            info.append("│  ", style="bold #cba6f7")
            info.append(author, style="bold #a6e3a1")
            info.append("\n")
            info.append('│  "', style="#cba6f7")
            info.append(content, style="#7f849c")
            info.append('"', style="#cba6f7")
            self.query_one("#reply-info", Static).update(info)

            self._reply_panel.display = True
            inp = self.query_one("#reply-input", Input)
            inp.clear()
            inp.focus()

        def _cancel_reply(self) -> None:
            """Hide the reply panel and return focus to the list."""
            self._replying = False
            self._reply_target = None
            self._reply_panel.display = False
            self._list.focus()

        def _submit_reply(self) -> None:
            """Submit the reply text and refresh the comment list."""
            inp = self.query_one("#reply-input", Input)
            content = inp.value.strip()
            if not content:
                self._cancel_reply()
                return

            target = self._reply_target
            if target is None:
                self._cancel_reply()
                return

            target_id = target["id"]
            target_author = target.get("author", "anonymous")

            def _do_reply_and_refresh() -> None:
                """Send reply, then refresh comments — all in worker thread."""
                try:
                    comment_item(self._item_type, self._item_id, content, reply_comment_id=target_id)
                    self.call_from_thread(self._on_reply_result, f"✓ Reply posted to {target_author}")
                    # Fetch updated comments in the same worker thread to avoid blocking UI
                    new_comments = list(fetch_root_comments(self._item_type, self._item_id))
                    if new_comments:
                        new_flat = _flatten_comments(new_comments)
                        self.call_from_thread(self._on_refresh_result, new_flat)
                    else:
                        self.call_from_thread(self._notify_status, "No comments found after refresh.", True)
                except Exception as exc:
                    self.call_from_thread(self._on_reply_result, f"✗ Failed to post reply: {exc}", True)

            self._cancel_reply()
            self.run_worker(_do_reply_and_refresh, thread=True)

        def _on_reply_result(self, message: str, is_error: bool = False) -> None:  # noqa: FBT001,FBT002
            """Callback from worker thread: show reply result."""
            self._notify_status(message, is_error=is_error)

        # ── Voting ──────────────────────────────────────────────────

        def _do_vote_action(self, action: str) -> None:
            """Execute a vote action (like/dislike toggle) in a worker thread."""
            if self._replying:
                return
            cid = self._get_selected_comment_id()
            if cid is None:
                return
            current = self._votes.get(cid)

            def _do() -> None:
                """Run vote API call in worker thread.  Only drives the HTTP request;
                all state updates happen on the main thread via call_from_thread."""
                new_state: str | None = None
                try:
                    if action == "like":
                        if current == "liked":
                            cancel_like_comment(cid)
                            new_state = None
                        else:
                            if current == "disliked":
                                cancel_dislike_comment(cid)
                            like_comment(cid)
                            new_state = "liked"
                    else:  # dislike
                        if current == "disliked":
                            cancel_dislike_comment(cid)
                            new_state = None
                        else:
                            if current == "liked":
                                cancel_like_comment(cid)
                            dislike_comment(cid)
                            new_state = "disliked"
                    # Schedule main-thread UI update with the final state
                    self.call_from_thread(self._update_vote_ui, cid, new_state)
                    self.call_from_thread(self._notify_status, f"✓ {'Liked' if action == 'like' else 'Disliked'}")
                except Exception as exc:
                    self.call_from_thread(self._notify_status, f"✗ Failed: {exc}", True)

            self.run_worker(_do, thread=True)

        def _update_vote_ui(self, cid: str, new_state: str | None) -> None:
            """Update the vote state and rebuild list to reflect changes."""
            self._votes[cid] = new_state
            self._rebuild_list(keep_index=True)

        # ── Refresh ─────────────────────────────────────────────────

        def _do_refresh_fetch(self) -> None:
            """Fetch comments in worker thread and update list."""
            try:
                new_comments = list(fetch_root_comments(self._item_type, self._item_id))
                if new_comments:
                    new_flat = _flatten_comments(new_comments)
                    self.call_from_thread(self._on_refresh_result, new_flat)
                else:
                    self.call_from_thread(self._notify_status, "No comments found after refresh.", True)
            except Exception as exc:
                self.call_from_thread(self._notify_status, f"✗ Refresh failed: {exc}", True)

        def _on_refresh_result(self, new_flat: list[dict[str, Any]]) -> None:
            """Apply refreshed comment data to the UI.

            Preserves the current selection position as closely as possible
            when the list changes size (e.g., after a reply is posted or
            comments are deleted remotely).
            """
            self._flat_items = new_flat
            self._rebuild_list(keep_index=True)
            # Clamp selection if the list shrunk
            if self._list.index is not None and self._list.index >= len(new_flat):
                self._list.index = max(0, len(new_flat) - 1)
            self._update_header()
            self._notify_status(f"✓ {len(new_flat)} comments loaded")

        # ── Actions (bound to keys) ─────────────────────────────────

        def action_reply_or_submit(self) -> None:
            """Enter: open reply panel or submit current reply."""
            if self._replying:
                self._submit_reply()
            else:
                self._enter_reply_mode()

        def action_cancel_reply(self) -> None:
            """Escape: cancel reply mode if active."""
            if self._replying:
                self._cancel_reply()

        def action_toggle_like(self) -> None:
            """Tab: toggle like on selected comment."""
            self._do_vote_action("like")

        def action_toggle_dislike(self) -> None:
            """Shift+Tab: toggle dislike on selected comment."""
            self._do_vote_action("dislike")

        def action_refresh(self) -> None:
            """Ctrl+R: re-fetch comments from server."""
            if self._replying:
                return
            self._notify_status("Refreshing comments...")
            self.run_worker(self._do_refresh_fetch, thread=True)

        def action_jump_root_prev(self) -> None:
            """PageUp: jump to previous root comment."""
            if self._replying:
                return
            idx = self._list.index
            if idx is None:
                return
            for i in range(idx - 1, -1, -1):
                if self._flat_items[i]["is_root"]:
                    self._list.index = i
                    return

        def action_jump_root_next(self) -> None:
            """PageDown: jump to next root comment."""
            if self._replying:
                return
            idx = self._list.index
            if idx is None:
                return
            for i in range(idx + 1, len(self._flat_items)):
                if self._flat_items[i]["is_root"]:
                    self._list.index = i
                    return

        def action_go_top(self) -> None:
            """Home: go to first comment."""
            if not self._replying and self._flat_items:
                self._list.index = 0

        def action_go_bottom(self) -> None:
            """End: go to last comment."""
            if not self._replying and self._flat_items:
                self._list.index = len(self._flat_items) - 1

        def action_quit_app(self) -> None:
            """q / Ctrl+Q: exit the TUI."""
            self.exit()

    # ── Run ─────────────────────────────────────────────────────────────

    try:
        app = CommentBrowserApp()
        app.run()
    except Exception as exc:
        error(f"TUI error: {exc}")
