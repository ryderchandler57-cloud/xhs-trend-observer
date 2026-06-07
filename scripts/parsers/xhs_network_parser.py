from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


CN_TZ = timezone(timedelta(hours=8))


def note_id_from_url(url: str) -> str:
    parsed = urlparse(url or "")
    match = re.search(r"^/(?:explore|discovery/item|search_result)/([^/?#]+)", parsed.path)
    if match:
        return match.group(1)
    return ""


def compact_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def normalize_count(value: Any) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    text = str(value).strip().lower()
    if not text:
        return None
    multiplier = 1
    if text.endswith("万") or text.endswith("w"):
        multiplier = 10000
        text = text[:-1]
    elif text.endswith("千") or text.endswith("k"):
        multiplier = 1000
        text = text[:-1]
    text = re.sub(r"[^0-9.]+", "", text)
    if not text:
        return None
    try:
        return int(float(text) * multiplier)
    except ValueError:
        return None


def normalize_time(value: Any) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, str):
        return compact_text(value)
    if isinstance(value, (int, float)) and value > 0:
        stamp = float(value)
        if stamp > 10_000_000_000:
            stamp = stamp / 1000
        if 1_000_000_000 <= stamp <= 4_000_000_000:
            return datetime.fromtimestamp(stamp, CN_TZ).isoformat(timespec="seconds")
    return str(value)


def dedupe(items: list[Any], limit: int = 80) -> list[Any]:
    seen: set[str] = set()
    out: list[Any] = []
    for item in items:
        key = json.dumps(item, ensure_ascii=False, sort_keys=True) if isinstance(item, dict) else str(item)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(item)
        if len(out) >= limit:
            break
    return out


def iter_dicts(obj: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            found.append(value)
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(obj)
    return found


def unwrap_payload(raw: dict[str, Any]) -> Any:
    if "data" in raw and ("url" in raw or "captured_at" in raw or "status" in raw):
        return raw.get("data")
    return raw


def file_matches_note(raw: dict[str, Any], payload: Any, note_id: str) -> bool:
    if not note_id:
        return True
    if note_id_from_url(str(raw.get("url") or "")) == note_id:
        return True
    text = json.dumps(payload, ensure_ascii=False)
    return note_id in text


def pick_first_text(items: list[Any]) -> str:
    for item in items:
        text = compact_text(item)
        if text:
            return text
    return ""


def maybe_note_card(obj: dict[str, Any], note_id: str) -> bool:
    keys = set(obj.keys())
    obj_id = str(obj.get("note_id") or obj.get("id") or obj.get("noteId") or "")
    if note_id and obj_id and obj_id != note_id:
        return False
    if keys & {"note_card", "interact_info", "image_list", "video", "desc", "display_title"}:
        return True
    return bool((keys & {"title", "desc"}) and (keys & {"user", "user_info", "userInfo"}))


def extract_author(obj: dict[str, Any]) -> str:
    for key in ["user_info", "user", "userInfo", "author"]:
        user = obj.get(key)
        if isinstance(user, dict):
            name = user.get("nickname") or user.get("name") or user.get("nick_name")
            if isinstance(name, str) and name.strip():
                return compact_text(name)
    return ""


def extract_counts(card: dict[str, Any]) -> dict[str, int | None]:
    scopes = [card]
    for key in ["interact_info", "interactInfo", "note_interact_info"]:
        if isinstance(card.get(key), dict):
            scopes.insert(0, card[key])

    def find(keys: list[str]) -> int | None:
        for scope in scopes:
            for key in keys:
                value = scope.get(key)
                count = normalize_count(value)
                if count is not None:
                    return count
        return None

    return {
        "like_count": find(["liked_count", "like_count", "likes", "likedCount"]),
        "collect_count": find(["collected_count", "collect_count", "collects", "collectedCount"]),
        "comment_count": find(["comment_count", "comments_count", "commentCount"]),
    }


def extract_images(card: dict[str, Any]) -> tuple[list[str], list[dict[str, Any]]]:
    metadata: list[dict[str, Any]] = []
    urls: list[str] = []

    def add_url(raw: Any, meta: dict[str, Any] | None = None) -> None:
        if not isinstance(raw, str) or not raw.startswith("http"):
            return
        urls.append(raw)
        item = dict(meta or {})
        item["url"] = raw
        metadata.append(item)

    for key in ["image_list", "images_list", "images", "imageList"]:
        value = card.get(key)
        if isinstance(value, list):
            for image in value:
                if isinstance(image, dict):
                    meta = {
                        "id": image.get("image_id") or image.get("id") or "",
                        "width": image.get("width"),
                        "height": image.get("height"),
                        "trace_id": image.get("trace_id") or "",
                    }
                    for url_key in ["url_default", "url_pre", "url", "src", "original"]:
                        add_url(image.get(url_key), meta)
                    info_list = image.get("info_list") or image.get("infoList")
                    if isinstance(info_list, list):
                        for info in info_list:
                            if isinstance(info, dict):
                                add_url(info.get("url"), {**meta, "type": info.get("image_scene") or info.get("type") or ""})
                elif isinstance(image, str):
                    add_url(image, {})

    for obj in iter_dicts(card):
        for key, value in obj.items():
            key_l = str(key).lower()
            if any(token in key_l for token in ["image", "img", "cover", "url_default", "url_pre"]):
                add_url(value, {"source_key": key})

    return dedupe(urls, 30), dedupe(metadata, 30)


def extract_video(card: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    videos: list[str] = []
    metadata: dict[str, Any] = {}

    def add_url(value: Any) -> None:
        if isinstance(value, str) and value.startswith("http"):
            videos.append(value)

    for key in ["video", "video_info", "videoInfo"]:
        value = card.get(key)
        if isinstance(value, dict):
            metadata.update({k: v for k, v in value.items() if k in {"id", "duration", "width", "height"}})
            for obj in iter_dicts(value):
                for k, v in obj.items():
                    if any(token in str(k).lower() for token in ["url", "master", "h264", "h265", "stream"]):
                        add_url(v)
    return (dedupe(videos, 1)[0] if videos else ""), metadata


def extract_comments(payloads: list[Any], max_comments: int) -> tuple[list[str], list[dict[str, Any]]]:
    comments: list[str] = []
    items: list[dict[str, Any]] = []
    for payload in payloads:
        for obj in iter_dicts(payload):
            content = obj.get("content") or obj.get("text") or obj.get("comment")
            if not isinstance(content, str) or len(compact_text(content)) < 2:
                continue
            looks_like_comment = (
                "comment_id" in obj
                or "sub_comment" in json.dumps(obj, ensure_ascii=False)[:600]
                or "user_info" in obj and ("like_count" in obj or "time" in obj or "ip_location" in obj)
            )
            if not looks_like_comment:
                continue
            text = compact_text(content)
            comments.append(text)
            item = {
                "content": text,
                "author": extract_author(obj),
                "publish_time": normalize_time(obj.get("create_time") or obj.get("time") or obj.get("date")),
                "like_count": normalize_count(obj.get("like_count") or obj.get("likeCount")),
                "ip_location": obj.get("ip_location") or obj.get("ipLocation") or "",
            }
            items.append(item)
    return dedupe(comments, max_comments), dedupe(items, max_comments)


def parse_network_raw_dir(directory: str | Path, note_id: str = "", max_comments: int = 30) -> dict[str, Any]:
    raw_dir = Path(directory)
    errors: list[str] = []
    source_files: list[str] = []
    wrappers: list[dict[str, Any]] = []
    payloads: list[Any] = []

    if not raw_dir.exists():
        return {"success": False, "parser_errors": [f"network_raw directory does not exist: {raw_dir}"]}

    for path in sorted(raw_dir.glob("*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            errors.append(f"{path.name}: {exc}")
            continue
        payload = unwrap_payload(raw)
        if not file_matches_note(raw if isinstance(raw, dict) else {}, payload, note_id):
            continue
        source_files.append(str(path))
        if isinstance(raw, dict):
            wrappers.append(raw)
        payloads.append(payload)

    note_cards: list[dict[str, Any]] = []
    for payload in payloads:
        for obj in iter_dicts(payload):
            card = obj.get("note_card") if isinstance(obj.get("note_card"), dict) else obj
            if isinstance(card, dict) and maybe_note_card(card, note_id):
                note_cards.append(card)

    title_candidates: list[str] = []
    desc_candidates: list[str] = []
    authors: list[str] = []
    publish_times: list[str] = []
    counts: dict[str, int | None] = {"like_count": None, "collect_count": None, "comment_count": None}
    images: list[str] = []
    image_metadata: list[dict[str, Any]] = []
    video = ""
    video_metadata: dict[str, Any] = {}

    for card in note_cards:
        for key in ["title", "display_title", "displayTitle"]:
            if isinstance(card.get(key), str):
                title_candidates.append(card[key])
        for key in ["desc", "description", "note_desc", "noteDesc"]:
            if isinstance(card.get(key), str):
                desc_candidates.append(card[key])
        author = extract_author(card)
        if author:
            authors.append(author)
        for key in ["time", "last_update_time", "create_time", "publish_time", "date"]:
            time_text = normalize_time(card.get(key))
            if time_text:
                publish_times.append(time_text)
        found_counts = extract_counts(card)
        for key, value in found_counts.items():
            if counts[key] is None and value is not None:
                counts[key] = value
        image_urls, image_meta = extract_images(card)
        images.extend(image_urls)
        image_metadata.extend(image_meta)
        if not video:
            video, video_metadata = extract_video(card)

    comments, comment_items = extract_comments(payloads, max_comments)
    matched_api_responses = [
        {
            "url": item.get("url", ""),
            "status": item.get("status"),
            "method": item.get("method", ""),
            "file": item.get("file", ""),
        }
        for item in wrappers
        if isinstance(item, dict)
    ]

    result = {
        "success": bool(note_cards or comments),
        "note_id": note_id or pick_first_text([card.get("note_id") or card.get("id") for card in note_cards]),
        "title": pick_first_text(title_candidates),
        "desc": "\n".join(dedupe([compact_text(item) for item in desc_candidates if compact_text(item)], 8)),
        "text": "\n".join(dedupe([compact_text(item) for item in desc_candidates if compact_text(item)], 8)),
        "author": pick_first_text(authors),
        "publish_time": pick_first_text(publish_times),
        "like_count": counts["like_count"],
        "collect_count": counts["collect_count"],
        "comment_count": counts["comment_count"],
        "comments": comments,
        "comment_items": comment_items,
        "images": dedupe(images, 30),
        "image_metadata": dedupe(image_metadata, 30),
        "video": video,
        "video_metadata": video_metadata,
        "source_files": source_files,
        "matched_api_responses": matched_api_responses,
        "parser_errors": errors,
    }
    if not result["success"] and not errors:
        result["parser_errors"] = ["no matching note/detail/feed/comment JSON parsed from network_raw"]
    return result


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("network_raw_dir")
    parser.add_argument("--note-id", default="")
    parser.add_argument("--max-comments", type=int, default=30)
    args = parser.parse_args()
    print(json.dumps(parse_network_raw_dir(args.network_raw_dir, args.note_id, args.max_comments), ensure_ascii=False, indent=2))
