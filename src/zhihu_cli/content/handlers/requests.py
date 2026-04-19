from bs4 import BeautifulSoup
from curl_cffi import requests as _requests
import json
from .cache_manager import cache_manager

session = requests = _requests.Session(impersonate="chrome110")
requests.headers.update(cache_manager.load_headers())

def get_page_entities(url: str) -> dict:
    resp = session.get(url, timeout=15)
    
    if resp.status_code == 403:
        raise PermissionError(f"Access denied (403). You might be blocked: {url}")

    soup = BeautifulSoup(resp.text, 'html.parser')

    script_tag = soup.find('script', id='js-initialData')
    if not script_tag or script_tag.string is None:
        raise ValueError(f"Could not find 'js-initialData' script tag at {url}")

    initial_data = json.loads(script_tag.string)
    page_data = initial_data['initialState']['entities']
    return page_data
