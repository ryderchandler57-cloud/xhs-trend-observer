from __future__ import annotations

from typing import Any
from urllib.parse import urlparse


REPORT_SOURCE_FIELDS = (
    "clicked_dom_href",
    "raw_dom_href",
    "source_url",
    "after_click_url",
)

NOTE_PATH_PREFIXES = (
    "/search_result/",
    "/explore/",
    "/discovery/item/",
)


def absolute_xhs_url(value: Any) -> str:
    url = str(value or "").strip()
    if not url:
        return ""
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("/"):
        return "https://www.xiaohongshu.com" + url
    return url


def note_id_from_url(url: str) -> str:
    parsed = urlparse(url)
    path = parsed.path or ""
    for prefix in NOTE_PATH_PREFIXES:
        if prefix in path:
            tail = path.split(prefix, 1)[1].strip("/")
            return tail.split("/", 1)[0]
    return ""


def is_valid_report_source_url(url: Any, note_id: str = "") -> bool:
    candidate = absolute_xhs_url(url)
    if not candidate:
        return False
    parsed = urlparse(candidate)
    if parsed.scheme not in {"http", "https"}:
        return False
    host = (parsed.netloc or "").lower()
    if not (host == "www.xiaohongshu.com" or host.endswith(".xiaohongshu.com")):
        return False
    path = parsed.path or ""
    if path.startswith(("/404", "/login")):
        return False
    extracted = note_id_from_url(candidate)
    if not extracted:
        return False
    if note_id and extracted != note_id:
        return False
    return True


def select_report_source_url(record: dict[str, Any]) -> tuple[str, str, bool]:
    note_id = str(record.get("note_id") or record.get("id") or "").strip()
    for field in REPORT_SOURCE_FIELDS:
        url = absolute_xhs_url(record.get(field))
        if is_valid_report_source_url(url, note_id):
            return url, field, True
    if record.get("report_source_url") and is_valid_report_source_url(record.get("report_source_url"), note_id):
        return absolute_xhs_url(record.get("report_source_url")), str(record.get("report_source_url_type") or "report_source_url"), True
    return "", "unavailable", False
