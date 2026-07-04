"""Interactive comment browser with keyboard navigation and inline reply.

Provides a prompt_toolkit-based terminal UI for browsing threaded comments
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


# ── FormattedText builders for prompt_toolkit ────────────────────────────


def _build_comment_tokens(
    item: dict[str, Any],
    is_selected: bool,
    item_index: int,
    total: int,
    vote_state: str | None = None,
) -> list[tuple[str, str]]:
    """Build prompt_toolkit FormattedText tokens for a single comment line.

    :param item: A flat-list item with ``comment``, ``depth``, ``is_root``.
    :param is_selected: Whether this comment is the current cursor target.
    :param item_index: 0-based index of this item in the flat list.
    :param total: Total number of flat items.
    :param vote_state: ``"liked"``, ``"disliked"``, or ``None``.
    :returns: List of ``(style, text)`` tuples.
    """
    c = item["comment"]
    depth = item["depth"]

    tokens: list[tuple[str, str]] = []

    # ── Row 1: selection marker + checkbox + indent + index + author + stats ──

    # Selection indicator and checkbox
    if is_selected:
        tokens.append(("class:selected", "▶"))
        tokens.append(("class:selected", " ☐ "))
    else:
        tokens.append(("", "  ☐ "))

    # Vote indicator
    if vote_state == "liked":
        tokens.append(("class:vote-liked", "♥️ "))
    elif vote_state == "disliked":
        tokens.append(("class:vote-disliked", "💔 "))
    else:
        tokens.append(("", "  "))

    # Indentation for child comments
    if depth > 0:
        indent = "  " * (depth - 1) + " ↳ "
        tokens.append(("class:dim", indent))
    elif depth == 0:
        # Root comment serial number
        serial = str(item_index + 1) if total > 1 else ""
        tokens.append(("class:index", serial + " " if serial else ""))

    # Author name
    author = c.get("author", "anonymous")
    tokens.append(("class:author" if not is_selected else "class:author-sel", author))

    # Stats: likes, time
    tokens.append(("", "  "))
    tokens.append(("class:stats", f"👍 {c.get('like_count', 0)}"))
    if c.get("dislike_count", 0):
        tokens.append(("class:stats", f"  👎 {c.get('dislike_count', 0)}"))
    created = c.get("created_time")
    if created:
        tokens.append(("class:dim", f"  {fmt_time(created)}"))

    # Reply count
    child_count = len(c.get("child_comments", []))
    if child_count:
        tokens.append(("class:dim", f"  ({child_count} replies)"))

    tokens.append(("", "\n"))

    # ── Row 2: content preview ──
    content = _truncate(c.get("content", ""), 80)

    # Build prefix for alignment
    prefix = "      "  # after select+checkbox+leading space
    if depth > 0:
        prefix += "  " * (depth - 1) + "   "  # indent + ↳

    if is_selected:
        tokens.append(("class:content-sel", prefix + content))
    else:
        tokens.append(("class:content", prefix + content))

    tokens.append(("", "\n"))

    return tokens


def _build_reply_header_tokens(target: dict[str, Any]) -> list[tuple[str, str]]:
    """Build FormattedText tokens for the reply panel header.

    :param target: The comment dict being replied to.
    """
    author = target.get("author", "anonymous")
    content = target.get("content", "").replace("\n", " ").strip()

    return [
        ("class:reply-title", "┌─ Replying to ─────────────────────\n"),
        ("", "│\n"),
        ("class:reply-author", f"│  {author}\n"),
        ("class:dim", f'│  "{content}"\n'),
        ("", "│\n"),
        ("class:reply-hint", "│  Type your reply below.\n"),
        ("class:reply-hint", "│  Ctrl+Enter to send, Esc to cancel.\n"),
        ("", "│" + "─" * 35 + "\n"),
    ]


# ── Main TUI entry point ────────────────────────────────────────────────


def run_interactive_comments(item_type: str, item_id: str) -> None:
    """Launch the interactive comment browser TUI.

    Fetches the full comment tree for *item_type*/*item_id*, then displays a
    keyboard-navigable list.  Press :kbd:`Enter` on any comment to open an
    inline reply panel on the right side of the screen.

    :param item_type: Resource type (``"answers"``, ``"articles"``, ``"pins"``, ``"questions"``).
    :param item_id: Resource ID.
    """
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

    # ── Prompt-toolkit imports (lazy to keep CLI startup fast) ──────────
    import shutil

    from prompt_toolkit import Application
    from prompt_toolkit.buffer import Buffer
    from prompt_toolkit.filters import Condition
    from prompt_toolkit.key_binding import KeyBindings, KeyPressEvent
    from prompt_toolkit.layout import HSplit, Layout, VSplit, Window
    from prompt_toolkit.layout.containers import ConditionalContainer
    from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
    from prompt_toolkit.layout.dimension import Dimension
    from prompt_toolkit.styles import Style

    # ── Mutable state ───────────────────────────────────────────────────
    state: dict[str, Any] = {
        "selected": 0,
        "scroll_top": 0,  # index of first visible comment item
        "replying": False,
        "reply_target": None,  # comment dict being replied to
        "status": "Use ↑/↓ or j/k to navigate, Enter to reply, Tab to like, q to quit.",
        "votes": {},  # comment_id -> "liked" | "disliked" | None
    }

    reply_buffer = Buffer(multiline=True)

    def _estimate_visible() -> int:
        """Return an estimate of how many comment items fit on screen."""
        term_h = shutil.get_terminal_size().lines
        # Each comment item: 2 lines (metadata + content), plus header/footer ≈4 lines
        return max(1, (term_h - 4) // 2)

    def _ensure_visible() -> None:
        """Adjust scroll_top so the selected item is in the viewport."""
        visible = _estimate_visible()
        sel = state["selected"]
        if sel < state["scroll_top"]:
            state["scroll_top"] = sel
        elif sel >= state["scroll_top"] + visible:
            state["scroll_top"] = sel - visible + 1
        # Clamp
        max_top = max(0, len(flat_items) - visible)
        if state["scroll_top"] > max_top:
            state["scroll_top"] = max_top
        if state["scroll_top"] < 0:
            state["scroll_top"] = 0

    # ── Key bindings ────────────────────────────────────────────────────
    kb = KeyBindings()

    def _move_cursor(delta: int) -> None:
        """Move the cursor by *delta* positions, clamped to valid range."""
        new = state["selected"] + delta
        if 0 <= new < len(flat_items):
            state["selected"] = new
            _ensure_visible()

    def _exit_reply_mode(app: Application) -> None:
        """Cancel reply mode and clear the reply buffer."""
        state["replying"] = False
        state["reply_target"] = None
        reply_buffer.text = ""
        app.layout.focus(comment_window)

    def _submit_reply(buf: Buffer) -> bool:
        """Accept handler for reply buffer — called on Ctrl+Enter."""
        content = buf.text.strip()
        if not content:
            state["replying"] = False
            state["reply_target"] = None
            buf.text = ""
            return True  # consumed

        target = state["reply_target"]
        if target is None:
            state["replying"] = False
            return True

        try:
            comment_item(item_type, item_id, content, reply_comment_id=target["id"])
            state["status"] = f"✓ Reply posted to {target.get('author', 'anonymous')}"
        except Exception as exc:
            state["status"] = f"✗ Failed to post reply: {exc}"

        state["replying"] = False
        state["reply_target"] = None
        buf.text = ""
        return True

    reply_buffer.accept_handler = _submit_reply

    @kb.add("up")
    @kb.add("k")
    def _(event: KeyPressEvent) -> None:
        if not state["replying"]:
            _move_cursor(-1)

    @kb.add("down")
    @kb.add("j")
    def _(event: KeyPressEvent) -> None:
        if not state["replying"]:
            _move_cursor(1)

    @kb.add("pageup")
    def _(event: KeyPressEvent) -> None:
        """Jump to previous top-level comment."""
        if state["replying"]:
            return
        for i in range(state["selected"] - 1, -1, -1):
            if flat_items[i]["is_root"]:
                state["selected"] = i
                _ensure_visible()
                return

    @kb.add("pagedown")
    def _(event: KeyPressEvent) -> None:
        """Jump to next top-level comment."""
        if state["replying"]:
            return
        for i in range(state["selected"] + 1, len(flat_items)):
            if flat_items[i]["is_root"]:
                state["selected"] = i
                _ensure_visible()
                return

    @kb.add("home")
    def _(event: KeyPressEvent) -> None:
        if not state["replying"]:
            state["selected"] = 0
            _ensure_visible()

    @kb.add("end")
    def _(event: KeyPressEvent) -> None:
        if not state["replying"]:
            state["selected"] = len(flat_items) - 1
            _ensure_visible()

    @kb.add("enter")
    def _(event: KeyPressEvent) -> None:
        if state["replying"]:
            return  # handled by BufferControl accept_handler
        # Enter reply mode for the selected comment
        item = flat_items[state["selected"]]
        state["reply_target"] = item["comment"]
        state["replying"] = True
        reply_buffer.text = ""
        state["status"] = f"Replying to {item['comment'].get('author', 'anonymous')}..."
        event.app.layout.focus(reply_input_window)

    @kb.add("tab")
    def _(event: KeyPressEvent) -> None:
        """Like the selected comment (toggle)."""
        if state["replying"]:
            return
        item = flat_items[state["selected"]]
        comment_id = item["comment"].get("id")
        if not comment_id:
            return
        votes: dict = state["votes"]
        current = votes.get(comment_id)
        try:
            if current == "liked":
                cancel_like_comment(comment_id)
                votes[comment_id] = None
                state["status"] = f"✓ Like removed from {item['comment'].get('author', 'anonymous')}"
            else:
                if current == "disliked":
                    cancel_dislike_comment(comment_id)
                like_comment(comment_id)
                votes[comment_id] = "liked"
                state["status"] = f"✓ Liked {item['comment'].get('author', 'anonymous')}"
        except Exception as exc:
            state["status"] = f"✗ Failed: {exc}"

    @kb.add("s-tab")
    def _(event: KeyPressEvent) -> None:
        """Dislike the selected comment (toggle)."""
        if state["replying"]:
            return
        item = flat_items[state["selected"]]
        comment_id = item["comment"].get("id")
        if not comment_id:
            return
        votes: dict = state["votes"]
        current = votes.get(comment_id)
        try:
            if current == "disliked":
                cancel_dislike_comment(comment_id)
                votes[comment_id] = None
                state["status"] = f"✓ Dislike removed from {item['comment'].get('author', 'anonymous')}"
            else:
                if current == "liked":
                    cancel_like_comment(comment_id)
                dislike_comment(comment_id)
                votes[comment_id] = "disliked"
                state["status"] = f"✓ Disliked {item['comment'].get('author', 'anonymous')}"
        except Exception as exc:
            state["status"] = f"✗ Failed: {exc}"

    @kb.add("escape")
    def _(event: KeyPressEvent) -> None:
        if state["replying"]:
            _exit_reply_mode(event.app)
            state["status"] = "Reply cancelled."

    @kb.add("q")
    def _(event: KeyPressEvent) -> None:
        if not state["replying"]:
            event.app.exit()

    @kb.add("c-c")
    def _(event: KeyPressEvent) -> None:
        event.app.exit()

    # ── Dynamic content builders ────────────────────────────────────────

    def _get_comment_text() -> list[tuple[str, str]]:
        """Build the full comment list display (virtual scrolling)."""
        tokens: list[tuple[str, str]] = []
        total = len(flat_items)
        visible = _estimate_visible()
        top = state["scroll_top"]
        # Re-estimate now that terminal size may have changed
        end = min(top + visible, total)

        # Header
        tokens.append(("class:title", f"── Comments ({total} items) [{top + 1}-{end}/{total}] ──\n"))
        tokens.append(("class:header-sub", f"   {item_type} / {item_id}\n\n"))

        # Overflow indicator at top
        if top > 0:
            tokens.append(("class:dim", f"  ... {top} more above ...\n\n"))

        # Visible comment lines
        votes: dict = state["votes"]
        for i in range(top, end):
            is_sel = i == state["selected"]
            comment_id = flat_items[i]["comment"].get("id")
            vote_state = votes.get(comment_id) if comment_id else None
            tokens.extend(_build_comment_tokens(flat_items[i], is_sel, i, total, vote_state))

        # Overflow indicator at bottom
        if end < total:
            tokens.append(("", "\n"))
            tokens.append(("class:dim", f"  ... {total - end} more below ...\n"))

        # Footer help
        tokens.append(("", "\n"))
        tokens.append(
            (
                "class:help",
                " ↑↓/jk: Navigate  │  PgUp/PgDn: Jump root  │  Enter: Reply  │  Tab: Like  │  Shift+Tab: Dislike  │  q: Quit\n",
            )
        )
        tokens.append(("class:dim", f" Item {state['selected'] + 1} of {total}\n"))

        return tokens

    def _get_reply_text() -> list[tuple[str, str]]:
        """Build the reply panel (excluding the text buffer)."""
        target = state["reply_target"]
        if target is None:
            return []
        return _build_reply_header_tokens(target)

    def _get_status_text() -> list[tuple[str, str]]:
        status = state.get("status", "")
        if "✓" in status:
            return [("class:status-ok", status)]
        elif "✗" in status:
            return [("class:status-err", status)]
        return [("class:dim", status)]

    # ── Conditional filter ──────────────────────────────────────────────

    @Condition
    def is_replying() -> bool:
        return state["replying"]

    # ── Controls & Windows ──────────────────────────────────────────────

    comment_control = FormattedTextControl(text=_get_comment_text, focusable=True)
    comment_window = Window(content=comment_control, wrap_lines=True, always_hide_cursor=True)

    reply_header_control = FormattedTextControl(text=_get_reply_text, focusable=False)
    reply_header_window = Window(content=reply_header_control, wrap_lines=True, dont_extend_height=True)

    reply_input_control = BufferControl(buffer=reply_buffer, focusable=True)
    reply_input_window = Window(content=reply_input_control, height=3)

    reply_panel = HSplit(
        [reply_header_window, reply_input_window],
        width=Dimension.exact(42),
        style="class:reply-panel",
    )

    status_control = FormattedTextControl(text=_get_status_text, focusable=False)
    status_window = Window(content=status_control, height=1, style="class:status-bar")

    # ── Layout tree ─────────────────────────────────────────────────────

    root_container = HSplit(
        [
            VSplit(
                [
                    comment_window,
                    ConditionalContainer(content=reply_panel, filter=is_replying),
                ],
            ),
            status_window,
        ]
    )

    layout = Layout(root_container, focused_element=comment_window)

    # ── Styles ──────────────────────────────────────────────────────────

    # Catppuccin Mocha palette: https://github.com/catppuccin/catppuccin
    style = Style(
        [
            # ── Comment list ────────────────────────────────────────
            ("title", "bold #cba6f7"),  # Mauve
            ("header-sub", "#6c7086"),  # Overlay0
            ("author", "bold #a6e3a1"),  # Green
            ("author-sel", "bold #a6e3a1 bg:#313244"),  # Green on Surface0
            ("index", "bold #fab387"),  # Peach
            ("stats", "#89b4fa"),  # Blue
            ("dim", "#7f849c"),  # Overlay1
            ("help", "italic #6c7086"),  # Overlay0
            ("content", "#cdd6f4"),  # Text
            ("content-sel", "#cdd6f4 bg:#313244"),  # Text on Surface0
            ("selected", "bold #b4befe bg:#313244"),  # Lavender on Surface0
            # ── Reply panel ─────────────────────────────────────────
            ("reply-title", "bold #cba6f7"),  # Mauve
            ("reply-author", "bold #a6e3a1"),  # Green
            ("reply-hint", "italic #6c7086"),  # Overlay0
            ("reply-panel", "bg:#181825"),  # Mantle
            # ── Status bar ──────────────────────────────────────────
            ("status-bar", "bg:#313244"),  # Surface0
            ("status-ok", "bold #a6e3a1"),  # Green
            ("status-err", "bold #f38ba8"),  # Red
            ("vote-liked", "bold #f38ba8"),  # Red (like)
            ("vote-disliked", "bold #585b70"),  # Dim (dislike)
        ]
    )

    # ── Run ─────────────────────────────────────────────────────────────

    try:
        app = Application(
            layout=layout,
            key_bindings=kb,
            style=style,
            full_screen=True,
            mouse_support=False,
        )
        app.run()
    except Exception as exc:
        error(f"TUI error: {exc}")
