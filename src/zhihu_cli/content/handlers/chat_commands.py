from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass
class ChatCommand:
    """A registered interactive chat slash-command.

    :ivar name: Command name without the leading ``/`` (e.g. ``"unsend"``).
    :ivar help_text: One-line description shown in ``/help`` output.
    :ivar handler: Callable ``(app, args: str) -> bool``.
        Receives the :class:`~textual.app.App` instance and the argument
        string (everything after the command name, stripped).  Returns
        ``True`` when the command executed successfully, ``False`` when
        arguments are invalid (the framework will display usage info).
    """

    name: str
    help_text: str
    handler: Callable[[Any, str], bool]


class ChatCommandRegistry:
    """Registry of slash-commands for the interactive chat session.

    Commands are matched by **unique prefix**: if the user types ``/un``
    and only ``unsend`` matches, it is auto-completed.  Multiple matches
    are reported back as ambiguous so the caller can list candidates.
    """

    def __init__(self) -> None:
        self._commands: dict[str, ChatCommand] = {}

    def register(self, name: str, help_text: str, handler: Callable[[Any, str], bool]) -> ChatCommand:
        """Register a single command.

        :param name: Command name (without ``/``).
        :param help_text: One-line help description.
        :param handler: ``(app, args: str) -> bool`` callable.
        :returns: The created :class:`ChatCommand` instance.
        :raises ValueError: If *name* is already registered.
        """
        if name in self._commands:
            raise ValueError(f"Command '{name}' is already registered")
        cmd = ChatCommand(name=name, help_text=help_text, handler=handler)
        self._commands[name] = cmd
        return cmd

    def match(self, text: str) -> tuple[str | None, ChatCommand | None, list[str]]:
        """Match *text* against registered commands.

        *text* is the user's input after stripping the leading ``/``,
        split into the command token and the rest.  For example, for
        ``"/unsend abc123"`` the *text* would be ``"unsend abc123"``.

        :param text: User input without the leading ``/``.
        :returns: A 3-tuple ``(matched_name, command, candidates)``:

            * If *text* is empty: ``(None, None, [])``
            * If an **exact** command name matches: ``(name, command, [])``
            * If a **unique prefix** matches: ``(matched_name, command, [matched_name])``
            * If **multiple** prefix matches: ``(None, None, [list of matching names])``
            * If **no** match: ``(None, None, [])``
        """
        text = text.strip()
        if not text:
            return None, None, []

        # Split into command token and the rest
        parts = text.split(maxsplit=1)
        token = parts[0]

        # Exact match
        if token in self._commands:
            return token, self._commands[token], []

        # Prefix match
        candidates = [name for name in self._commands if name.startswith(token)]
        if len(candidates) == 1:
            return candidates[0], self._commands[candidates[0]], [candidates[0]]
        elif len(candidates) > 1:
            return None, None, candidates

        return None, None, []

    def list_all(self) -> dict[str, ChatCommand]:
        """Return all registered commands (name → ChatCommand)."""
        return dict(self._commands)
