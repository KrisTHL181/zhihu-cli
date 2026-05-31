#!/usr/bin/env python3
"""
Shell autocompletion setup for zhihu CLI.

Detects the current OS and shell, then prints the appropriate ``eval`` command
to enable Click-based shell completion.  Pipe the output into your shell::

    eval "$(python autocomp.py)"

Or add the printed command directly to your shell's rc file (``.zshrc``,
``.bashrc``, or ``$PROFILE`` on PowerShell).
"""

from __future__ import annotations

import os
import platform
import shutil
import sys


def detect_shell() -> str | None:
    """Best-effort detection of the active shell.

    Returns one of ``"zsh"``, ``"bash"``, ``"powershell"``, or *None*.
    """
    system = platform.system()

    # ── Windows ──────────────────────────────────────────────────────
    if system == "Windows":
        # PSModulePath is the most reliable PowerShell-only env var.
        if "PSModulePath" in os.environ:
            return "powershell"
        # Fallback: check COMSPEC or SHELL for pwsh mentions.
        shell = os.environ.get("SHELL", "") or os.environ.get("COMSPEC", "")
        if "powershell" in shell.lower() or "pwsh" in shell.lower():
            return "powershell"
        return None

    # ── Linux / macOS ────────────────────────────────────────────────
    if system in ("Linux", "Darwin"):
        # $SHELL is the login shell — accurate enough for the vast
        # majority of users.
        shell_path = os.environ.get("SHELL", "")
        basename = os.path.basename(shell_path)
        if basename in ("zsh", "bash"):
            return basename
        # Some distros symlink /bin/sh → bash; be tolerant.
        if basename == "sh":
            if os.environ.get("BASH_VERSION"):
                return "bash"
            if os.environ.get("ZSH_VERSION"):
                return "zsh"
        return None

    return None


def get_completion_command() -> str:
    """Return the eval-ready autocompletion command for this OS/shell."""
    if shutil.which("zhihu") is None:
        sys.exit(
            "zhihu command not found. "
            + f"Please install it first: `pip install -e {os.path.abspath(os.path.dirname(__file__))}`"
        )

    shell = detect_shell()
    system = platform.system()

    if shell is None:
        sys.exit(
            f"Unsupported environment: OS={system}, "
            f"SHELL={os.environ.get('SHELL', '?')}. "
            f"Only Linux (bash/zsh) and Windows (PowerShell) are supported."
        )

    if shell == "zsh":
        return 'eval "$(_ZHIHU_COMPLETE=zsh_source zhihu)"'
    if shell == "bash":
        return 'eval "$(_ZHIHU_COMPLETE=bash_source zhihu)"'
    if shell == "powershell":
        return "& $env:_ZHIHU_COMPLETE=powershell_source zhihu" + " | Out-String | Invoke-Expression"

    sys.exit(f"Unsupported shell: {shell}")


def main() -> None:
    print(get_completion_command())


if __name__ == "__main__":
    main()
