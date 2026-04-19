from .requests import requests
from .cache_manager import cache_manager

requests.headers.update(cache_manager.load_headers())
