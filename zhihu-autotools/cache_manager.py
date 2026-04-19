import functools
import json
import threading
import time
from pathlib import Path


class CacheManager:
    _instance = None
    _lock = threading.Lock()          # 为单例模式提供的线程锁

    def __new__(cls, *args, **kwargs):
        # Thread‑safe singleton creation
        if not cls._instance:
            with cls._lock:
                if not cls._instance:
                    cls._instance = super(CacheManager, cls).__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self, cache_dir=".cache"):
        if self._initialized:
            return

        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(exist_ok=True)
        self.header_file = self.cache_dir / "headers.json"
        self.content_dir = self.cache_dir / "questions"
        self.content_dir.mkdir(exist_ok=True)

        # Instance lock for all file operations
        self._file_lock = threading.Lock()

        self._initialized = True

    @staticmethod
    def make_thread_safe(fn=None, *, lock_attr='_file_lock'):
        """
        使得一个函数是线程安全的
        """
        def decorator(func):
            @functools.wraps(func)
            def wrapper(self, *args, **kwargs):
                lock = getattr(self, lock_attr)
                with lock:
                    return func(self, *args, **kwargs)
            return wrapper

        if fn is None:
            return decorator
        return decorator(fn)

    def _atomic_write(self, path, data, mode='w'):
        """原子写入文件的辅助方法"""
        tmp_path = path.with_suffix(".tmp")
        if mode == 'w':
            tmp_path.write_text(data, encoding='utf-8')
        else:  # binary mode
            tmp_path.write_bytes(data)
        tmp_path.replace(path)

    @make_thread_safe
    def save_headers(self, headers):
        """保存 headers.json"""
        self._atomic_write(self.header_file, json.dumps(headers, indent=2))

    @make_thread_safe
    def load_headers(self):
        """读取 headers.json"""
        if self.header_file.exists():
            try:
                return json.loads(self.header_file.read_text())
            except json.JSONDecodeError:
                return None
        return None

    @make_thread_safe
    def get_cached_question(self, q_id):
        """
        获取一个问题的缓存，如果超过一天则认为缓存失效。
        """
        cache_path = self.content_dir / f"{q_id}.json"
        if cache_path.exists():
            if time.time() - cache_path.stat().st_mtime < 86400:
                return json.loads(cache_path.read_text())
        return None

    @make_thread_safe
    def save_question(self, q_id, data):
        """存储一个问题数据。"""
        cache_path = self.content_dir / f"{q_id}.json"
        self._atomic_write(cache_path, json.dumps(data, indent=2))

cache_manager = CacheManager()

if __name__ == "__main__":
    import sys
    import re

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

    # 1. 提取 Headers (兼容 -H 和 --header)
    headers = {}
    header_matches = re.findall(r"(?:-H|--header)\s+['\"]([^'\"]+)['\"]", curl_text)
    
    for h in header_matches:
        if ":" in h:
            k, v = h.split(":", 1)
            # 过滤掉一些可能引起干扰的 header
            if k.strip().lower() not in ['accept-encoding', 'content-length']:
                headers[k.strip()] = v.strip()

    # 2. 校验并保存
    if headers:
        # 确保关键字段存在
        if 'cookie' not in [k.lower() for k in headers.keys()]:
            print("⚠️  Warning: No Cookie found in headers. Some requests might fail.")

        cache_manager.save_headers(headers)
        print(f"✅ Success! {len(headers)} headers saved to {cache_manager.header_file}")
    else:
        print("❌ Error: Could not parse any headers from the input.")
