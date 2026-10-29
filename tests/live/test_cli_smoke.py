"""Live CLI smoke test over a subprocess.

Runs the real console entry point (``python -m zhihu_cli.main``) and checks the
contract ``--json`` promises to scripts: exit code 0, clean JSON on stdout, no
ANSI escapes mixed in.  It uses ``browse hot``, the one command confirmed to
work anonymously, and never asserts on stderr — warnings and progress messages
are allowed there and are not part of the contract.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

pytestmark = pytest.mark.live


def test_browse_hot_json() -> None:
    """``browse hot --json`` prints a parseable, escape-free JSON array."""
    env = dict(os.environ)
    env["ZHIHU_CAPTCHA_GATE"] = "0"

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "zhihu_cli.main",
            "browse",
            "hot",
            "--json",
            "--max",
            "3",
        ],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )

    assert completed.returncode == 0, f"CLI exited {completed.returncode}; stderr tail:\n{completed.stderr[-2000:]}"
    assert "\x1b[" not in completed.stdout, "ANSI escape leaked into --json stdout"

    payload = json.loads(completed.stdout)
    assert isinstance(payload, list)
    assert payload, "browse hot --json returned an empty list"

    first = payload[0]
    assert isinstance(first, dict)
    assert first.get("title", "").strip(), "hot item has no title"
    assert first.get("url") or first.get("target_id"), "hot item has no url or id"
