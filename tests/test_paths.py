"""Tests for :mod:`zhihu_cli.paths` and the ``ZHIHU_CLI_HOME`` override."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from zhihu_cli.paths import HOME_ENV, zhihu_home


def test_default_home(monkeypatch, tmp_path: Path) -> None:
    """Without the override the directory is ``$HOME/.zhihu-cli``."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv(HOME_ENV, raising=False)

    assert zhihu_home() == tmp_path / ".zhihu-cli"


def test_override(monkeypatch, tmp_path: Path) -> None:
    """A non-empty override is returned verbatim."""
    custom = tmp_path / "custom-home"
    monkeypatch.setenv(HOME_ENV, str(custom))

    assert zhihu_home() == custom


def test_override_expands_user(monkeypatch) -> None:
    """A leading ``~`` in the override is expanded to the real home."""
    monkeypatch.setenv(HOME_ENV, "~/zhihu-test")

    result = zhihu_home()

    assert result == Path.home() / "zhihu-test"
    assert str(result).startswith(str(Path.home()))


def test_empty_override_falls_back(monkeypatch, tmp_path: Path) -> None:
    """An empty override means "unset" and falls back to the default."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv(HOME_ENV, "")

    assert zhihu_home() == tmp_path / ".zhihu-cli"


def test_env_override_applies_at_import(tmp_path: Path) -> None:
    """Module-level constants freeze the override when they are imported."""
    code = (
        "import json\n"
        "from zhihu_cli.content.handlers.cache_manager import cache_manager\n"
        "from zhihu_cli.daemon.transport import STATE_FILE\n"
        "print(json.dumps({'cache': str(cache_manager.cache_dir), 'state': str(STATE_FILE)}))\n"
    )
    env = os.environ.copy()
    env[HOME_ENV] = str(tmp_path)

    completed = subprocess.run(
        [sys.executable, "-c", code],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    resolved = json.loads(completed.stdout)

    assert Path(resolved["cache"]).is_relative_to(tmp_path)
    assert Path(resolved["state"]).is_relative_to(tmp_path)


def test_no_hardcoded_home() -> None:
    """No module except :mod:`zhihu_cli.paths` hardcodes the directory name."""
    src_root = Path(__file__).resolve().parents[1] / "src" / "zhihu_cli"
    literal = '".zhihu-cli"'
    offenders = []

    for path in sorted(src_root.rglob("*.py")):
        if path.name == "paths.py":
            # The defining module is the one place allowed to name the default.
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if literal in line and "zhihu_home" not in line:
                offenders.append(f"{path.relative_to(src_root.parent.parent)}:{lineno}")

    assert not offenders, "hardcoded ~/.zhihu-cli paths found:\n" + "\n".join(offenders)
