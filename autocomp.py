#!/usr/bin/env python3
"""
Shell autocompletion setup for the CLI tool.

Detects the current OS and shell, then prints the appropriate ``eval`` command
to enable Click-based shell completion.  Pipe the output into your shell::

    eval "$(python autocomp.py)"

Or add the printed command directly to your shell's rc file (``.zshrc``,
``.bashrc``, ``config.fish``, or ``$PROFILE`` on PowerShell).

You can also bypass autodetection by passing an explicit shell name::

    python autocomp.py --shell fish
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import sys

COMMAND_NAME = "zhihu"

# Shells that Click natively supports for completion.
_SUPPORTED_SHELLS = frozenset({"bash", "zsh", "fish", "powershell"})


def detect_shell() -> str | None:
    """Best-effort detection of the active shell.

    Returns one of ``"zsh"``, ``"bash"``, ``"fish"``, ``"powershell"``, or *None*.
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
        if basename in ("zsh", "bash", "fish"):
            return basename
        # Some distros symlink /bin/sh → bash; be tolerant.
        if basename == "sh":
            if os.environ.get("BASH_VERSION"):
                return "bash"
            if os.environ.get("ZSH_VERSION"):
                return "zsh"
        # Fish sets FISH_VERSION even when $SHELL doesn't point to it
        # (e.g. launched as a subshell from another shell).
        if os.environ.get("FISH_VERSION"):
            return "fish"
        return None

    return None


def _detect_with_fallback(override: str | None) -> str:
    """Resolve the shell name, preferring *override* when given.

    :param override: Explicit shell name from ``--shell``, or *None*.
    :returns: A shell name in ``_SUPPORTED_SHELLS``.
    :raises SystemExit: When detection fails or the shell is unsupported.
    """
    if override is not None:
        override = override.lower()
        if override not in _SUPPORTED_SHELLS:
            supported = ", ".join(sorted(_SUPPORTED_SHELLS))
            sys.exit(f"Unsupported shell: {override!r}. Supported shells: {supported}.")
        return override

    shell = detect_shell()
    if shell is not None:
        return shell

    system = platform.system()
    shell_env = os.environ.get("SHELL", "?")
    supported = ", ".join(sorted(_SUPPORTED_SHELLS))
    msg = (
        f"Unsupported environment: OS={system}, SHELL={shell_env}. "
        f"Supported shells: {supported}. Use --shell to pick one explicitly."
    )
    sys.exit(msg)


def get_completion_command(shell: str) -> str:
    """Return the eval-ready autocompletion command for *shell*.

    :param shell: One of ``"bash"``, ``"zsh"``, ``"fish"``, ``"powershell"``.
    """
    if shutil.which(COMMAND_NAME) is None:
        install_path = os.path.abspath(os.path.dirname(__file__))
        sys.exit(f"{COMMAND_NAME} command not found. Please install it first: `pip install -e {install_path}`")

    env_var = f"_{COMMAND_NAME.upper()}_COMPLETE"

    if shell == "zsh":
        return f'eval "$({env_var}=zsh_source {COMMAND_NAME})"'
    if shell == "bash":
        return f'eval "$({env_var}=bash_source {COMMAND_NAME})"'
    if shell == "fish":
        return f"{env_var}=fish_source {COMMAND_NAME} | source"
    if shell == "powershell":
        return f"& $env:{env_var}=powershell_source {COMMAND_NAME} | Out-String | Invoke-Expression"

    sys.exit(f"Unsupported shell: {shell}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=f"Print the eval-ready shell completion command for {COMMAND_NAME}.",
    )
    _ = parser.add_argument(
        "-s",
        "--shell",
        choices=sorted(_SUPPORTED_SHELLS),
        default=None,
        help="Shell to generate completion for (auto-detected when omitted).",
    )
    args = parser.parse_args()

    shell = _detect_with_fallback(args.shell)
    print(get_completion_command(shell))


if __name__ == "__main__":
    main()
