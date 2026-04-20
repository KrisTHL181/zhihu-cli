from bs4 import BeautifulSoup
from curl_cffi import requests as _requests
from user_agents import parse
import json
from zhihu_cli.content.handlers.cache_manager import cache_manager

def get_browser(ua: str) -> _requests.BrowserTypeLiteral:
    family = parse(ua).browser.family
    if family in _requests.impersonate.REAL_TARGET_MAP:
        return family

    return "chrome"

headers = cache_manager.load_headers()

session = requests = _requests.Session(impersonate=get_browser(headers.get("User-Agent")))
requests.headers.update(headers)

def get_page_entities(url: str) -> dict:
    resp = session.get(url)
    
    if resp.status_code == 403:
        raise PermissionError(f"Access denied (403). You might be blocked: {url}")

    soup = BeautifulSoup(resp.text, 'html.parser')

    script_tag = soup.find('script', id='js-initialData')
    if not script_tag or script_tag.string is None:
        raise ValueError(f"Could not find 'js-initialData' script tag at {url}")

    initial_data = json.loads(script_tag.string)
    page_data = initial_data['initialState']['entities']
    return page_data
