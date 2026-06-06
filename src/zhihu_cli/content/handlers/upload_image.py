"""Upload images to Zhihu's image hosting service.

Handles the full flow: register → OSS upload → poll → return image info.
"""

import base64
import email.utils
import hashlib
import hmac
import mimetypes
import re
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from zhihu_cli.content.handlers.requests import session
from zhihu_cli.content.utils.wait import wait

ZHIHU_IMAGE_API: str = "https://api.zhihu.com/images"
ZHIHU_OSS_UPLOAD_URL: str = "https://zhihu-pics-upload.zhimg.com"
DEFAULT_TIMEOUT: int = 15


def upload_image(file_path: str, source: str = "article") -> dict:
    """Upload an image to Zhihu and return image info.

    Handles the full flow: register → OSS upload → poll → return info.

    Args:
        file_path: Path to the image file.
        source: Upload context ('article', 'pin', etc.).

    Returns:
        Dict with keys: src, original_src, watermark, watermark_src.
    """
    path = Path(file_path)
    if not path.is_file():
        raise FileNotFoundError(f"Image file not found: {file_path}")

    image_data = path.read_bytes()
    md5_hex = hashlib.md5(image_data).hexdigest()

    # Register image with Zhihu
    resp = session.post(
        ZHIHU_IMAGE_API,
        json={"image_hash": md5_hex, "source": source},
        timeout=DEFAULT_TIMEOUT,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Image registration failed ({resp.status_code}): {resp.text[:200]}")
    data = resp.json()
    upload_file = data["upload_file"]
    image_id = upload_file["image_id"]
    state = upload_file["state"]

    if state == 2:
        obj_key = upload_file["object_key"]
        content_type = _guess_mime(path)
        _upload_to_oss(obj_key, image_data, data["upload_token"], content_type)
    elif state != 1:
        raise RuntimeError(f"Unexpected image state: {state}")

    image_info = _poll_image(str(image_id))

    try:
        from PIL import Image

        with Image.open(path) as img:
            image_info["width"], image_info["height"] = img.size
    except Exception:
        image_info.setdefault("width", 0)
        image_info.setdefault("height", 0)

    return image_info


def _guess_mime(path: Path) -> str:
    """Guess the MIME type of an image file, falling back to image/jpeg."""
    mime, _ = mimetypes.guess_type(str(path))
    if mime and mime.startswith("image/"):
        return mime
    return "image/jpeg"


def _upload_to_oss(obj_key: str, data: bytes, token: dict, content_type: str) -> None:
    """Upload image data to Alibaba Cloud OSS."""
    date = email.utils.formatdate(usegmt=True)
    security_token = token["access_token"]
    access_id = token["access_id"]
    access_key = token["access_key"]

    string_to_sign = f"PUT\n\n{content_type}\n{date}\nx-oss-security-token:{security_token}\n/zhihu-pics/{obj_key}"
    signature = base64.b64encode(hmac.new(access_key.encode(), string_to_sign.encode(), hashlib.sha1).digest()).decode()

    headers = {
        "Content-Type": content_type,
        "Date": date,
        "x-oss-security-token": security_token,
        "Authorization": f"OSS {access_id}:{signature}",
    }
    resp = session.put(
        f"{ZHIHU_OSS_UPLOAD_URL}/{obj_key}",
        data=data,
        headers=headers,
        timeout=DEFAULT_TIMEOUT,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"OSS upload failed ({resp.status_code}): {resp.text[:200]}")


def _poll_image(image_id: str, max_attempts: int = 15, interval: float = 2.0) -> dict:
    """Poll image status until processing completes."""
    for _ in range(max_attempts):
        resp = session.get(
            f"{ZHIHU_IMAGE_API}/{image_id}",
            timeout=DEFAULT_TIMEOUT,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"Image poll failed ({resp.status_code}): {resp.text[:200]}")
        data = resp.json()
        if data.get("status") == "success":
            return {
                "src": data["src"],
                "original_src": data["original_src"],
                "watermark": data.get("watermark", "watermark"),
                "watermark_src": data.get("watermark_src", ""),
            }
        wait(interval, forced_to_wait=True)
    raise RuntimeError("Image processing timed out")


def to_visible_url(src: str) -> str:
    """Convert a pic-private.zhihu.com URL to a visible pic1.zhimg.com URL.

    Extracts the image ID (e.g. ``v2-a019cc18bc079641a6e9dff3dcf471cb``)
    and rewrites it to the public ``pic1.zhimg.com`` domain with a
    ``.jpg`` suffix, preserving the ``source`` query param when
    available.
    """
    match = re.search(r"/(v\d+-[a-f0-9]+)", src)
    if not match:
        return src

    image_id = match.group(1)

    parsed = urlparse(src)
    params = parse_qs(parsed.query)
    source = params.get("source", [None])[0]

    url = f"https://pic1.zhimg.com/{image_id}.jpg"
    if source:
        url += f"?source={source}"
    return url
