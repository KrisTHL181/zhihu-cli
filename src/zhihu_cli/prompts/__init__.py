"""LLM system prompts loaded from text files.

Each prompt is a module-level string loaded from a ``.txt`` file in this directory.
Import them directly::

    from zhihu_cli.prompts import AGORA_VOTE_SYSTEM_PROMPT

To modify a prompt, edit the corresponding ``.txt`` file — no code changes needed.
"""

from __future__ import annotations

from pathlib import Path

_PROMPTS_DIR = Path(__file__).resolve().parent


def _load_prompt(filename: str) -> str:
    """Read a prompt from a text file, stripping trailing whitespace.

    :param filename: Name of the ``.txt`` file in this directory.
    :returns: Prompt string.
    """
    path = _PROMPTS_DIR / filename
    if not path.exists():
        raise FileNotFoundError(f"Prompt file not found: {path}")
    return path.read_text(encoding="utf-8").rstrip()


AGORA_VOTE_SYSTEM_PROMPT: str = _load_prompt("agora_vote.txt")
"""System prompt for the Zhihu Agora (community moderation) AI voting feature."""

SERIES_NAMING_SYSTEM_PROMPT: str = _load_prompt("series_naming.txt")
"""System prompt for naming a series of papers by a citizen scientist."""

COMMIT_MESSAGE_SYSTEM_PROMPT: str = _load_prompt("commit_message.txt")
"""System prompt for generating git commit messages for archived papers."""


__all__ = [
    "AGORA_VOTE_SYSTEM_PROMPT",
    "SERIES_NAMING_SYSTEM_PROMPT",
    "COMMIT_MESSAGE_SYSTEM_PROMPT",
]
