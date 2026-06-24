import functools
import json
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any


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
            self.cache_dir = Path.home() / ".zhihu-cli" / "cache"
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
        path = self._resolve_profile_path(name)
        if path.exists():
            path.unlink()
        active = self.get_active_profile()
        if active == name:
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
            try:
                return json.loads(path.read_text())
            except json.JSONDecodeError:
                return {}
        return {}

    # ── config management ───────────────────────────────────────────────

    def get_config(self) -> dict[str, Any]:
        if self.config_file.exists():
            try:
                return json.loads(self.config_file.read_text())
            except json.JSONDecodeError:
                pass
        return {}

    def get_start_date(self) -> str:
        config = self.get_config()
        return config.get("default_start_date", "2026-01-16")

    def set_start_date(self, date_str: str) -> None:
        config = self.get_config()
        config["default_start_date"] = date_str
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
