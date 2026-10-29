import functools
import json
import math
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from zhihu_cli.paths import zhihu_home


class CacheManager:
    _instance: "CacheManager | None" = None
    _lock: threading.Lock = threading.Lock()

    def __new__(cls, *args: Any, **kwargs: Any) -> "CacheManager":
        if not cls._instance:
            with cls._lock:
                if not cls._instance:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self, cache_dir: str | Path | None = None) -> None:
        if self._initialized:
            return

        if cache_dir is None:
            self.cache_dir = zhihu_home() / "cache"
        else:
            self.cache_dir = Path(cache_dir).resolve()

        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.header_file = self.cache_dir / "headers.json"
        self.content_dir = self.cache_dir / "questions"
        self.content_dir.mkdir(exist_ok=True)

        self.profiles_dir = self.cache_dir / "profiles"
        self.profiles_dir.mkdir(exist_ok=True)
        self.active_profile_file = self.cache_dir / "active_profile"
        self.config_file = self.cache_dir / "config.json"

        self._migrate_old_cache()

        self._file_lock = threading.RLock()
        self._initialized = True

    @staticmethod
    def make_thread_safe(fn: Callable | None = None, *, lock_attr: str = "_file_lock") -> Callable:
        def decorator(func: Callable) -> Callable:
            @functools.wraps(func)
            def wrapper(self: "CacheManager", *args: Any, **kwargs: Any) -> Any:
                lock = getattr(self, lock_attr)
                with lock:
                    return func(self, *args, **kwargs)

            return wrapper

        if fn is None:
            return decorator
        return decorator(fn)

    def _atomic_write(self, path: Path, data: str | bytes, mode: str = "w") -> None:
        tmp_path = path.with_suffix(".tmp")
        if mode == "w":
            tmp_path.write_text(data, encoding="utf-8")
        else:
            tmp_path.write_bytes(data)
        tmp_path.replace(path)

    # ── profile management ──────────────────────────────────────────────

    def _resolve_profile_path(self, name: str) -> Path:
        return self.profiles_dir / f"{name}.json"

    def _migrate_old_cache(self) -> None:
        """Migrate cache files from old location (handlers/.cache/) to new (~/.zhihu-cli/cache/)."""
        old_cache = Path(__file__).parent.resolve() / ".cache"
        if not old_cache.exists() or old_cache == self.cache_dir:
            return

        import shutil

        migrated = False
        # profiles
        old_profiles = old_cache / "profiles"
        if old_profiles.is_dir():
            for f in old_profiles.iterdir():
                dst = self.profiles_dir / f.name
                if not dst.exists():
                    shutil.copy2(f, dst)
                    migrated = True

        # active_profile
        old_active = old_cache / "active_profile"
        if old_active.exists() and not self.active_profile_file.exists():
            shutil.copy2(old_active, self.active_profile_file)
            migrated = True

        # questions
        old_questions = old_cache / "questions"
        if old_questions.is_dir():
            for f in old_questions.iterdir():
                dst = self.content_dir / f.name
                if not dst.exists():
                    shutil.copy2(f, dst)
                    migrated = True

        # legacy headers.json
        old_headers = old_cache / "headers.json"
        if old_headers.exists():
            default_path = self._resolve_profile_path("default")
            if not default_path.exists():
                shutil.copy2(old_headers, default_path)
                migrated = True

        if migrated:
            # Leave old cache in place as backup; user can delete manually.
            pass

    def _migrate_legacy_headers(self) -> str | None:
        """If old headers.json has data and no profiles exist, migrate to 'default' profile."""
        if not self.header_file.exists():
            return None
        try:
            data = json.loads(self.header_file.read_text())
        except json.JSONDecodeError:
            return None
        if not data:
            return None
        default_path = self._resolve_profile_path("default")
        self._atomic_write(default_path, json.dumps(data, indent=2))
        self._atomic_write(self.active_profile_file, "default")
        self.header_file.unlink()
        return "default"

    @make_thread_safe
    def get_active_profile(self) -> str | None:
        if self.active_profile_file.exists():
            name = self.active_profile_file.read_text().strip()
            if name and self._resolve_profile_path(name).exists():
                return name
        return None

    @make_thread_safe
    def list_profiles(self) -> list[str]:
        return sorted(p.stem for p in self.profiles_dir.glob("*.json"))

    @make_thread_safe
    def switch_profile(self, name: str) -> None:
        path = self._resolve_profile_path(name)
        if not path.exists():
            raise ValueError(f"Profile '{name}' does not exist")
        self._atomic_write(self.active_profile_file, name)

    @make_thread_safe
    def delete_profile(self, name: str) -> None:
        """Delete a profile, clearing the active pointer if it named this one.

        :param name: The profile to delete. Missing profiles are a no-op.
        """
        # Read the active pointer BEFORE unlinking: get_active_profile() verifies
        # the profile file exists, so calling it after the unlink would report
        # None and leave the pointer dangling at a profile that is now gone.
        active = self.get_active_profile()
        path = self._resolve_profile_path(name)
        if path.exists():
            path.unlink()
        if active is not None and self._resolve_profile_path(active) == path:
            if self.active_profile_file.exists():
                self.active_profile_file.unlink()

    # ── header persistence ──────────────────────────────────────────────

    @make_thread_safe
    def save_headers(self, headers: dict[str, str], profile_name: str | None = None) -> None:
        if profile_name:
            path = self._resolve_profile_path(profile_name)
            self._atomic_write(path, json.dumps(headers, indent=2))
            self._atomic_write(self.active_profile_file, profile_name)
            return

        active = self.get_active_profile()
        if active:
            path = self._resolve_profile_path(active)
            self._atomic_write(path, json.dumps(headers, indent=2))
            return

        migrated = self._migrate_legacy_headers()
        if migrated:
            path = self._resolve_profile_path(migrated)
            self._atomic_write(path, json.dumps(headers, indent=2))
            return

        path = self._resolve_profile_path("default")
        self._atomic_write(path, json.dumps(headers, indent=2))
        self._atomic_write(self.active_profile_file, "default")

    @make_thread_safe
    def load_headers(self, profile_name: str | None = None) -> dict[str, str]:
        if profile_name:
            path = self._resolve_profile_path(profile_name)
        else:
            active = self.get_active_profile()
            if active:
                path = self._resolve_profile_path(active)
            else:
                path = self.header_file

        if path.exists():
            return json.loads(path.read_text())
        return {}

    # ── config management ───────────────────────────────────────────────

    def get_config(self) -> dict[str, Any]:
        if self.config_file.exists():
            return json.loads(self.config_file.read_text())
        return {}

    def get_start_date(self) -> str:
        config = self.get_config()
        return config.get("default_start_date", "2026-01-16")

    def set_start_date(self, date_str: str) -> None:
        config = self.get_config()
        config["default_start_date"] = date_str
        self._atomic_write(self.config_file, json.dumps(config, indent=2))

    def get_qps(self) -> float:
        """Return the persisted global request rate limit (``0.0`` = unlimited).

        Stored under ``rate_limit.qps``.  The nested object leaves room for
        future limiter knobs (burst, per-host overrides) without another
        top-level key.  Anything malformed — a missing section, a non-numeric
        string, NaN/inf, or a negative number — is reported as ``0.0`` (no
        limiting) so a hand-edited config cannot make requests hang.
        """
        config = self.get_config()
        section = config.get("rate_limit", {})
        if not isinstance(section, dict):
            return 0.0
        try:
            qps = float(section.get("qps", 0.0))
        except (TypeError, ValueError):
            return 0.0
        if not math.isfinite(qps) or qps < 0:
            return 0.0
        return qps

    def set_qps(self, qps: float) -> None:
        """Persist the global request rate limit (``0.0`` = unlimited).

        Clamps negative and non-finite values to ``0.0``, matching
        :func:`zhihu_cli.content.utils.rate_limit._coerce_qps`: the CLI accepts
        any float, and a value the limiter would reject must not be stored as
        something that reads differently next time.
        """
        try:
            value = float(qps)
        except (TypeError, ValueError):
            value = 0.0
        if not math.isfinite(value) or value < 0:
            value = 0.0
        config = self.get_config()
        section = config.get("rate_limit")
        if not isinstance(section, dict):
            section = {}
        section["qps"] = value
        config["rate_limit"] = section
        self._atomic_write(self.config_file, json.dumps(config, indent=2))

    def get_smoothing(self) -> str:
        """Return the configured smoothing method — ``"ema"`` (default) or ``"ma"``."""
        config = self.get_config()
        return config.get("smoothing", "ema")

    def set_smoothing(self, method: str) -> None:
        """Set the smoothing method to ``"ma"`` or ``"ema"``."""
        if method not in ("ma", "ema"):
            raise ValueError(f"Invalid smoothing method: {method!r}. Use 'ma' or 'ema'.")
        config = self.get_config()
        config["smoothing"] = method
        self._atomic_write(self.config_file, json.dumps(config, indent=2))

    def get_app_za(self) -> str:
        """Return the configured ``x-app-za`` header value (default ``OS=Android``).

        This is the minimal non-leaking value.  Users who need to pass
        Zhihu's mobile-app checks may need to provide a fuller device
        fingerprint.
        """
        config = self.get_config()
        return config.get("app_za", "OS=Android")

    def set_app_za(self, value: str) -> None:
        """Set the ``x-app-za`` header value."""
        config = self.get_config()
        config["app_za"] = value
        self._atomic_write(self.config_file, json.dumps(config, indent=2))

    def get_app_version(self) -> str:
        """Return the configured ``x-app-version`` header value (default ``10.95.0``)."""
        config = self.get_config()
        return config.get("app_version", "10.95.0")

    def set_app_version(self, version: str) -> None:
        """Set the ``x-app-version`` header value."""
        config = self.get_config()
        config["app_version"] = version
        self._atomic_write(self.config_file, json.dumps(config, indent=2))

    def get_plot_dpi(self) -> int:
        """Return the configured plot DPI (default 300)."""
        config = self.get_config()
        return int(config.get("plot_dpi", 300))

    def set_plot_dpi(self, dpi: int) -> None:
        """Set the DPI for saved plot images."""
        if dpi < 72 or dpi > 1200:
            raise ValueError(f"DPI must be between 72 and 1200, got {dpi}")
        config = self.get_config()
        config["plot_dpi"] = dpi
        self._atomic_write(self.config_file, json.dumps(config, indent=2))

    def get_suppress_incomplete_warning(self) -> bool:
        """Return whether incomplete-pagination warnings are suppressed.

        Stored as a plain bool under ``suppress_incomplete_warning``.  Anything
        other than an explicit JSON ``true`` reads as ``False``, so a malformed
        or hand-edited value keeps the warning visible.
        """
        return self.get_config().get("suppress_incomplete_warning", False) is True

    def set_suppress_incomplete_warning(self, enabled: bool) -> None:
        """Persist whether incomplete-pagination warnings are suppressed."""
        config = self.get_config()
        config["suppress_incomplete_warning"] = bool(enabled)
        self._atomic_write(self.config_file, json.dumps(config, indent=2))

    # ── question cache ──────────────────────────────────────────────────

    @make_thread_safe
    def get_cached_question(self, q_id: str) -> dict[str, Any]:
        cache_path = self.content_dir / f"{q_id}.json"
        if cache_path.exists():
            if time.time() - cache_path.stat().st_mtime < 86400:
                return json.loads(cache_path.read_text())
        return {}

    @make_thread_safe
    def save_question(self, q_id: str, data: dict[str, Any]) -> None:
        cache_path = self.content_dir / f"{q_id}.json"
        self._atomic_write(cache_path, json.dumps(data, indent=2))


cache_manager = CacheManager()

if __name__ == "__main__":
    import re
    import sys

    print("=== Zhihu Headers Refresh Tool ===")
    print("Please paste the cURL command (from Browser DevTools):")
    print("Tip: Press Ctrl+D (Unix) or Ctrl+Z then Enter (Windows) to save.\n")

    try:
        curl_text = sys.stdin.read()
    except EOFError:
        curl_text = ""

    if not curl_text.strip():
        print("❌ Error: No input detected.")
        sys.exit(1)

    headers = {}
    header_matches = re.findall(r"(?:-H|--header)\s+['\"]([^'\"]+)['\"]", curl_text)

    for h in header_matches:
        if ":" in h:
            k, v = h.split(":", 1)
            if k.strip().lower() not in ["accept-encoding", "content-length"]:
                headers[k.strip()] = v.strip()

    if headers:
        if "cookie" not in [k.lower() for k in headers.keys()]:
            print("⚠️  Warning: No Cookie found in headers. Some requests might fail.")

        cache_manager.save_headers(headers)
        profile = cache_manager.get_active_profile() or "default"
        print(f"✅ Success! {len(headers)} headers saved to profile '{profile}'")
    else:
        print("❌ Error: Could not parse any headers from the input.")
