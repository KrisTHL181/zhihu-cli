from zhihu_cli.content.handlers.requests import session


def collect(item_type: str, item_id) -> dict:
    resp = session.post(f"https://www.zhihu.com/api/v4/collections/contents/{item_type}/{item_id}")
    return resp.json()


def add_to_collection(item_type: str, item_id, collection_id) -> dict:
    resp = session.post(
        f"https://www.zhihu.com/api/v4/collections/{collection_id}/contents?content_id={item_id}&content_type={item_type}"
    )
    return resp.json()


def delete_to_collection(item_type: str, item_id, collection_id) -> dict:
    resp = session.delete(
        f"https://www.zhihu.com/api/v4/collections/{collection_id}/contents?content_id={item_id}&content_type={item_type}"
    )
    return resp.json()


def create_collection(title: str, description: str, is_public: bool) -> dict:
    resp = session.post(
        "https://www.zhihu.com/api/v4/collections?include=updated_time%2Canswer_count%2Cfollower_count",
        json={"title": title, "description": description, "is_public": is_public},
    )
    return resp.json()


def delete_collection(collection_id) -> dict:
    resp = session.delete(f"https://www.zhihu.com/api/v4/collections/{collection_id}")
    return resp.json()
