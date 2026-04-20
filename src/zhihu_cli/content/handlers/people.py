from .requests import session

def follow(user_id: str) -> dict:
    resp = session.post(f"https://www.zhihu.com/api/v4/members/{user_id}/followers")

    data = resp.json()
    if resp.status_code == 403 and "error" in data.keys():
        raise PermissionError(f"Failed to follow: {data['error']['message']}")

    return data

def unfollow(user_id: str) -> dict:
    resp = session.delete(f"https://www.zhihu.com/api/v4/members/{user_id}/followers")
    return resp.json()

def block(user_id: str) -> None:
    session.post(f"https://www.zhihu.com/api/v4/members/{user_id}/actions/block")

def unblock(user_id: str) -> None:
    session.delete(f"https://www.zhihu.com/api/v4/members/{user_id}/actions/block")

