from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


CN_TZ = timezone(timedelta(hours=8))


REQUIRED_FIELDS = [
    "note_id",
    "title",
    "author",
    "publish_time",
    "liked_count",
    "collected_count",
    "comment_count",
    "note_url",
    "raw_json",
]


def now_iso() -> str:
    return datetime.now(CN_TZ).isoformat(timespec="seconds")


def load_json(path: str | Path | None, default: Any = None) -> Any:
    if not path:
        return default
    file_path = Path(path)
    if not file_path.exists():
        return default
    return json.loads(file_path.read_text(encoding="utf-8"))


def write_json(path: str | Path, data: Any) -> None:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def write_text(path: str | Path, text: str) -> None:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        value = " ".join(clean_text(item) for item in value)
    elif isinstance(value, dict):
        for key in ("text", "content", "comment", "title", "desc", "description"):
            if value.get(key):
                return clean_text(value.get(key))
        value = json.dumps(value, ensure_ascii=False)
    return re.sub(r"\s+", " ", str(value)).strip()


def text_len(value: Any) -> int:
    return len(clean_text(value))


def first_value(*values: Any) -> Any:
    for value in values:
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        if isinstance(value, list) and not value:
            continue
        return value
    return ""


def as_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    text = str(value).strip().lower().replace(",", "")
    if not text:
        return None
    multiplier = 1
    if text.endswith("w") or text.endswith("万"):
        multiplier = 10000
        text = text[:-1]
    elif text.endswith("k") or text.endswith("千"):
        multiplier = 1000
        text = text[:-1]
    text = re.sub(r"[^0-9.]+", "", text)
    if not text:
        return None
    try:
        return int(float(text) * multiplier)
    except ValueError:
        return None


def stable_hash(*parts: Any, length: int = 16) -> str:
    text = "|".join(clean_text(part) for part in parts)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:length]


def note_id_from_url(url: str) -> str:
    parsed = urlparse(url or "")
    match = re.search(r"/(?:explore|search_result|discovery/item)/([^/?#]+)", parsed.path)
    if match:
        return match.group(1)
    parts = [part for part in parsed.path.split("/") if part]
    return parts[-1] if parts else ""


def canonical_url(item: dict[str, Any]) -> str:
    return clean_text(
        first_value(
            item.get("note_url"),
            item.get("url"),
            item.get("report_source_url"),
            item.get("raw_url"),
            item.get("detail_url"),
            item.get("source", {}).get("url") if isinstance(item.get("source"), dict) else "",
        )
    )


def note_id_for(item: dict[str, Any]) -> str:
    if not isinstance(item, dict):
        return ""
    url = canonical_url(item)
    return clean_text(
        first_value(
            item.get("note_id"),
            item.get("id"),
            item.get("noteId"),
            item.get("note_id_md5"),
            note_id_from_url(url),
        )
    )


def title_for(item: dict[str, Any]) -> str:
    return clean_text(first_value(item.get("title"), item.get("note_title"), item.get("phenomenon")))


def key_for(item: dict[str, Any]) -> str:
    note_id = note_id_for(item)
    if note_id:
        return f"id:{note_id}"
    url_id = note_id_from_url(canonical_url(item))
    if url_id:
        return f"id:{url_id}"
    title = title_for(item)
    if title:
        return f"title:{stable_hash(title, length=20)}"
    return f"unknown:{stable_hash(json.dumps(item, ensure_ascii=False, sort_keys=True), length=20)}"


def add_index(index: dict[str, dict[str, Any]], item: dict[str, Any], source: str) -> None:
    if not isinstance(item, dict):
        return
    key = key_for(item)
    index.setdefault(key, {})[source] = item


def index_notes(
    raw: dict[str, Any],
    structured: Any,
    ai_input: dict[str, Any],
    normalized: dict[str, Any],
    media_text: dict[str, Any] | None = None,
    effective_notes: dict[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for record in raw.get("records", []) if isinstance(raw, dict) else []:
        add_index(index, record, "raw")
    for note in structured if isinstance(structured, list) else []:
        add_index(index, note, "structured")
    for note in ai_input.get("notes", []) if isinstance(ai_input, dict) else []:
        add_index(index, note, "ai_input")
    for bucket in ("observations", "leads_to_verify", "dropped_noise"):
        for item in normalized.get(bucket, []) if isinstance(normalized, dict) else []:
            source = item.get("source") if isinstance(item, dict) else None
            if isinstance(source, dict):
                add_index(index, source, f"normalized_{bucket}")
            elif isinstance(item, dict):
                add_index(index, item, f"normalized_{bucket}")
    for note in (media_text or {}).get("notes", []) if isinstance(media_text, dict) else []:
        add_index(index, note, "media_text")
    for note in (effective_notes or {}).get("notes", []) if isinstance(effective_notes, dict) else []:
        add_index(index, note, "effective")
    return index


def best_note(parts: dict[str, dict[str, Any]]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for source in ("raw", "structured", "ai_input", "normalized_observations", "normalized_leads_to_verify", "normalized_dropped_noise", "effective"):
        item = parts.get(source)
        if not item:
            continue
        for key, value in item.items():
            if value in (None, "", [], {}):
                continue
            merged.setdefault(key, value)
    for source in ("structured", "raw", "ai_input"):
        item = parts.get(source)
        if not item:
            continue
        for key in ("title", "content", "text", "desc", "author", "publish_time", "created_at", "like_count", "collect_count", "comment_count", "comments"):
            if item.get(key) not in (None, "", [], {}):
                merged[key] = item.get(key)
    return merged


def comment_items(*values: Any) -> list[Any]:
    out: list[Any] = []
    for value in values:
        if not value:
            continue
        if isinstance(value, list):
            out.extend(value)
        else:
            out.append(value)
    return out


def comment_body(comment: Any) -> str:
    if isinstance(comment, dict):
        return clean_text(first_value(comment.get("content"), comment.get("text"), comment.get("comment"), comment.get("body")))
    return clean_text(comment)


def comment_like_count(comment: Any) -> int | None:
    if not isinstance(comment, dict):
        return None
    return as_int(first_value(comment.get("like_count"), comment.get("liked_count"), comment.get("likes")))


def recursive_media_scan(value: Any, depth: int = 0) -> tuple[int, bool]:
    if depth > 4:
        return 0, False
    image_count = 0
    has_video = False
    if isinstance(value, dict):
        for key, child in value.items():
            key_l = str(key).lower()
            if "avatar" in key_l or "icon" in key_l:
                continue
            if any(token in key_l for token in ("video", "media_stream")) and child not in (None, "", [], {}):
                has_video = True
            if any(token in key_l for token in ("image", "images", "img", "photo", "cover", "image_list")) and child not in (None, "", [], {}):
                if isinstance(child, list):
                    image_count += len(child)
                else:
                    image_count += 1
            child_images, child_video = recursive_media_scan(child, depth + 1)
            image_count += child_images
            has_video = has_video or child_video
    elif isinstance(value, list):
        for child in value:
            child_images, child_video = recursive_media_scan(child, depth + 1)
            image_count += child_images
            has_video = has_video or child_video
    return image_count, has_video


def media_profile(raw: dict[str, Any], structured: dict[str, Any], merged: dict[str, Any], content_length: int) -> dict[str, Any]:
    image_count = 0
    has_video = False
    for item in (raw, structured, merged):
        images, video = recursive_media_scan(item)
        image_count = max(image_count, images)
        has_video = has_video or video
    direct_images = first_value(
        raw.get("image_list") if raw else [],
        raw.get("images") if raw else [],
        raw.get("image_urls") if raw else [],
        structured.get("images") if structured else [],
        merged.get("images"),
    )
    if isinstance(direct_images, list):
        image_count = max(image_count, len(direct_images))
    if first_value(raw.get("video") if raw else "", structured.get("video") if structured else "", merged.get("video")):
        has_video = True
    if has_video and image_count > 0:
        note_type = "mixed"
    elif has_video:
        note_type = "video"
    elif image_count > 0:
        note_type = "image"
    else:
        note_type = "unknown"
    return {
        "note_type": note_type,
        "is_image_note": note_type in {"image", "mixed"},
        "is_video_note": note_type in {"video", "mixed"},
        "image_count": image_count,
        "has_image_list": image_count > 0,
        "has_video_field": has_video,
        "image_heavy_without_text": image_count >= 3 and content_length < 100,
        "content_missing_with_many_images": image_count >= 3 and content_length == 0,
    }


def field_value(field: str, merged: dict[str, Any], raw: dict[str, Any], structured: dict[str, Any], raw_present: bool) -> Any:
    if field == "note_id":
        return note_id_for(merged) or note_id_for(raw) or note_id_for(structured)
    if field == "liked_count":
        return first_value(merged.get("liked_count"), merged.get("like_count"), raw.get("liked_count"), raw.get("like_count"), structured.get("liked_count"), structured.get("like_count"))
    if field == "collected_count":
        return first_value(merged.get("collected_count"), merged.get("collect_count"), raw.get("collected_count"), raw.get("collect_count"), structured.get("collected_count"), structured.get("collect_count"))
    if field == "note_url":
        return canonical_url(merged) or canonical_url(raw) or canonical_url(structured)
    if field == "raw_json":
        return raw_present or bool(merged.get("raw_json") or structured.get("raw_json"))
    if field == "publish_time":
        return first_value(merged.get("publish_time"), merged.get("created_at"), raw.get("publish_time"), raw.get("created_at"), structured.get("publish_time"), structured.get("created_at"))
    return first_value(merged.get(field), raw.get(field), structured.get(field))


def evaluate_note(key: str, parts: dict[str, dict[str, Any]], high_value_by_key: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    raw = parts.get("raw", {}) or {}
    structured = parts.get("structured", {}) or {}
    ai_note = parts.get("ai_input", {}) or {}
    media_note = parts.get("media_text", {}) or {}
    effective_note = parts.get("effective", {}) or {}
    merged = best_note(parts)
    raw_present = bool(raw)

    title = title_for(merged) or title_for(raw) or title_for(structured)
    content = clean_text(first_value(merged.get("content"), merged.get("text"), raw.get("content"), raw.get("text"), structured.get("content"), structured.get("text")))
    desc = clean_text(first_value(merged.get("desc"), merged.get("description"), raw.get("desc"), raw.get("description"), structured.get("desc"), structured.get("description")))
    title_length = len(title)
    content_length = len(content)
    desc_length = len(desc)
    body_length = max(content_length, desc_length)
    only_title = title_length > 0 and body_length == 0
    media_ocr_text = clean_text(first_value(effective_note.get("media_ocr_text"), media_note.get("ocr_text")))
    media_ocr_text_length = len(media_ocr_text)
    effective_comment_text = clean_text(effective_note.get("comment_text_summary"))
    effective_text = clean_text(effective_note.get("effective_text"))
    effective_text_length = len(effective_text)
    effective_text_sources = effective_note.get("text_sources") if isinstance(effective_note.get("text_sources"), list) else []
    effective_text_source_count = len([source for source in effective_text_sources if source])

    comments = comment_items(merged.get("comments"), raw.get("comments"), structured.get("comments"), ai_note.get("comments"))
    dedup_comments = []
    seen_comments = set()
    for comment in comments:
        body = comment_body(comment)
        if not body or body in seen_comments:
            continue
        seen_comments.add(body)
        dedup_comments.append(comment)
    actual_comment_count = len(dedup_comments)
    comment_text_total = sum(len(comment_body(comment)) for comment in dedup_comments)
    effective_comment_text_length = max(comment_text_total, len(effective_comment_text))
    high_liked_comment_count = sum(1 for comment in dedup_comments if (comment_like_count(comment) or 0) >= 10)

    page_comment_count = as_int(first_value(merged.get("comment_count"), raw.get("comment_count"), structured.get("comment_count"))) or 0
    valuable_comments = comment_items(merged.get("valuable_comments"), raw.get("valuable_comments"), structured.get("valuable_comments"), ai_note.get("valuable_comments"))
    unique_valuable_comments: list[Any] = []
    seen_valuable = set()
    for item in valuable_comments:
        body = comment_body(item)
        marker = body or json.dumps(item, ensure_ascii=False, sort_keys=True) if isinstance(item, dict) else clean_text(item)
        if not marker or marker in seen_valuable:
            continue
        seen_valuable.add(marker)
        unique_valuable_comments.append(item)
    matched_high_value = high_value_by_key.get(key, [])
    high_value_comment_count = max(len(unique_valuable_comments), len(matched_high_value), high_liked_comment_count)
    comments_empty = actual_comment_count == 0
    likely_partial_comments = bool(page_comment_count >= 20 and actual_comment_count <= 5)
    if page_comment_count > 0 and actual_comment_count > 0 and actual_comment_count / max(page_comment_count, 1) < 0.25 and page_comment_count - actual_comment_count >= 20:
        likely_partial_comments = True

    media = media_profile(raw, structured, merged, body_length)
    image_note_needs_ocr_before = media["is_image_note"] and body_length < 100
    image_note_needs_ocr_after = media["is_image_note"] and body_length < 100 and media_ocr_text_length < 80
    field_status = {}
    missing_fields = []
    for field in REQUIRED_FIELDS:
        value = field_value(field, merged, raw, structured, raw_present)
        present = bool(value)
        field_status[field] = present
        if not present:
            missing_fields.append(field)

    score = 0
    reasons: list[str] = []
    penalties: list[str] = []
    if title_length >= 6:
        score += 8
        reasons.append("标题可用")
    if body_length >= 200:
        score += 35
        reasons.append("正文很充足")
    elif body_length >= 100:
        score += 28
        reasons.append("正文超过100字")
    elif body_length >= 50:
        score += 16
        reasons.append("正文超过50字")
    elif body_length > 0:
        score += 6
        reasons.append("有少量正文")
    if actual_comment_count >= 8 and comment_text_total >= 300:
        score += 20
        reasons.append("评论较充足")
    elif actual_comment_count >= 3 and comment_text_total >= 120:
        score += 14
        reasons.append("评论可辅助分析")
    elif actual_comment_count > 0:
        score += 6
        reasons.append("有少量评论")
    if high_value_comment_count:
        score += min(10, 4 + high_value_comment_count * 2)
        reasons.append("存在高质量评论")
    metric_fields = ["liked_count", "collected_count", "comment_count"]
    score += sum(3 for field in metric_fields if field_status.get(field))
    if field_status.get("publish_time"):
        score += 5
    if field_status.get("note_url"):
        score += 5
    if field_status.get("raw_json"):
        score += 4
    if only_title:
        score -= 25
        penalties.append("只有标题没有正文")
    if body_length < 50:
        score -= 8
        penalties.append("正文少于50字")
    elif body_length < 100:
        score -= 4
        penalties.append("正文少于100字")
    if media["image_heavy_without_text"] or image_note_needs_ocr_before:
        score -= 18
        penalties.append("图文为主但缺少 OCR/正文")
    if comments_empty:
        score -= 10
        penalties.append("评论为空")
    elif likely_partial_comments:
        score -= 6
        penalties.append("评论疑似只采到前几条")
    if not field_status.get("note_url"):
        score -= 8
        penalties.append("缺少 note_url")
    score -= min(12, len(missing_fields) * 2)
    score = max(0, min(100, score))

    score_before = score
    penalties_before = list(penalties)
    reasons_before = list(reasons)

    final_text_length = max(effective_text_length, body_length, media_ocr_text_length, effective_comment_text_length)
    if media_ocr_text_length >= 200:
        score += 32
        reasons.append("OCR 图片文本很充足")
    elif media_ocr_text_length >= 100:
        score += 24
        reasons.append("OCR 图片文本超过100字")
    elif media_ocr_text_length >= 50:
        score += 12
        reasons.append("OCR 图片文本可辅助分析")
    if body_length < 100 and effective_comment_text_length >= 300:
        score += 18
        reasons.append("评论文本可补足正文")
    elif body_length < 100 and effective_comment_text_length >= 120:
        score += 10
        reasons.append("评论文本可辅助分析")
    if effective_text_source_count >= 3:
        score += 8
        reasons.append("DOM/OCR/评论三类文本来源齐全")
    elif effective_text_source_count >= 2:
        score += 5
        reasons.append("多来源文本可交叉分析")
    if (media["image_heavy_without_text"] or image_note_needs_ocr_before) and media_ocr_text_length >= 100:
        score += 14
        reasons.append("图文笔记已由 OCR 补足")
    if final_text_length >= 100 and "正文少于50字" in penalties:
        penalties.append("原始 DOM 正文短，但 effective_text 已补足")
    score = max(0, min(100, score))

    tags_before: list[str] = []
    if body_length >= 100:
        tags_before.append("usable_text_rich")
    if body_length < 100 and (actual_comment_count >= 3 or high_value_comment_count > 0) and comment_text_total >= 120:
        tags_before.append("comment_driven")
    if image_note_needs_ocr_before:
        tags_before.append("image_heavy_need_ocr")
    if body_length < 50 and actual_comment_count < 3 and not media["image_heavy_without_text"]:
        tags_before.append("metadata_only")
    if score_before < 30 or (only_title and actual_comment_count == 0):
        tags_before.append("low_value_noise")
    if not tags_before:
        tags_before.append("metadata_only" if score_before < 50 else "usable_text_rich")

    tags: list[str] = []
    if final_text_length >= 100:
        tags.append("usable_text_rich")
    if body_length < 100 and media_ocr_text_length >= 100:
        tags.append("ocr_enriched")
    if body_length < 100 and (actual_comment_count >= 3 or high_value_comment_count > 0) and effective_comment_text_length >= 120:
        tags.append("comment_driven")
    if image_note_needs_ocr_after:
        tags.append("image_heavy_need_ocr")
    if final_text_length < 50 and actual_comment_count < 3 and media_ocr_text_length < 50:
        tags.append("metadata_only")
    if score < 30 or (only_title and actual_comment_count == 0 and final_text_length < 50):
        tags.append("low_value_noise")
    if not tags:
        tags.append("metadata_only" if score < 50 else "usable_text_rich")

    priority = ["low_value_noise", "image_heavy_need_ocr", "usable_text_rich", "ocr_enriched", "comment_driven", "metadata_only"]
    primary_layer = next((tag for tag in priority if tag in tags), tags[0])

    raw_text_len = text_len(first_value(raw.get("content"), raw.get("text"), raw.get("desc"), raw.get("description")))
    structured_text_len = text_len(first_value(structured.get("content"), structured.get("text"), structured.get("desc"), structured.get("description")))
    ai_input_text_len = text_len(first_value(ai_note.get("content"), ai_note.get("text"), ai_note.get("desc"), ai_note.get("description")))

    return {
        "note_key": key,
        "note_id": field_value("note_id", merged, raw, structured, raw_present) or "",
        "title": title,
        "author": clean_text(first_value(merged.get("author"), raw.get("author"), structured.get("author"))),
        "publish_time": field_value("publish_time", merged, raw, structured, raw_present) or "",
        "note_url": field_value("note_url", merged, raw, structured, raw_present) or "",
        "keyword": clean_text(first_value(merged.get("keyword"), merged.get("query"), raw.get("keyword"), raw.get("query"), structured.get("keyword"))),
        "extraction_method": clean_text(first_value(merged.get("extraction_method"), raw.get("extraction_method"), structured.get("extraction_method"))),
        "access_status": clean_text(first_value(merged.get("access_status"), raw.get("access_status"), structured.get("access_status"))),
        "text_quality": {
            "title_length": title_length,
            "content_length": content_length,
            "desc_length": desc_length,
            "body_length": body_length,
            "only_title_no_body": only_title,
            "body_less_than_50": body_length < 50,
            "body_less_than_100": body_length < 100,
            "raw_text_length": raw_text_len,
            "structured_text_length": structured_text_len,
            "ai_input_text_length": ai_input_text_len,
            "dom_text_length": body_length,
            "media_ocr_text_length": media_ocr_text_length,
            "comment_text_length": effective_comment_text_length,
            "effective_text_length": final_text_length,
            "effective_text_source_count": effective_text_source_count,
        },
        "effective_text_quality": {
            "media_ocr_text_length": media_ocr_text_length,
            "comment_text_length": effective_comment_text_length,
            "effective_text_length": final_text_length,
            "effective_text_sources": effective_text_sources,
            "effective_text_source_count": effective_text_source_count,
            "improved_by_ocr": body_length < 100 and media_ocr_text_length >= 100,
            "improved_by_comments": "comments" in effective_text_sources and effective_comment_text_length >= 80 and final_text_length > max(body_length, media_ocr_text_length),
        },
        "media_quality": media,
        "comment_quality": {
            "page_comment_count": page_comment_count,
            "actual_collected_comment_count": actual_comment_count,
            "high_liked_comment_count": high_liked_comment_count,
            "high_value_comment_count": high_value_comment_count,
            "comment_text_total_length": comment_text_total,
            "comments_empty": comments_empty,
            "likely_partial_comments": likely_partial_comments,
        },
        "field_completeness": {
            "fields": field_status,
            "missing_fields": missing_fields,
            "complete_count": len(REQUIRED_FIELDS) - len(missing_fields),
            "complete_ratio": round((len(REQUIRED_FIELDS) - len(missing_fields)) / len(REQUIRED_FIELDS), 3),
        },
        "ai_usable_score": score,
        "ai_usable_score_before": score_before,
        "ai_usable_score_after": score,
        "quality_tags_before": tags_before,
        "quality_tags": tags,
        "primary_layer": primary_layer,
        "score_reasons": reasons,
        "score_penalties": penalties,
        "score_reasons_before": reasons_before,
        "score_penalties_before": penalties_before,
        "source_presence": {name: bool(parts.get(name)) for name in ("raw", "structured", "ai_input", "normalized_observations", "normalized_leads_to_verify", "normalized_dropped_noise", "media_text", "effective")},
    }


def high_value_comment_index(ai_input: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = defaultdict(list)
    if not isinstance(ai_input, dict):
        return out
    for item in ai_input.get("high_value_comments", []) or []:
        if not isinstance(item, dict):
            continue
        pseudo = {
            "title": item.get("note_title", ""),
            "url": item.get("note_url", ""),
            "note_url": item.get("note_url", ""),
        }
        out[key_for(pseudo)].append(item)
    return out


def summarize(note_reports: list[dict[str, Any]], quality_audit: dict[str, Any] | None) -> dict[str, Any]:
    total = len(note_reports)
    counter = Counter(note["primary_layer"] for note in note_reports)
    tag_counter = Counter(tag for note in note_reports for tag in note.get("quality_tags", []))
    tag_counter_before = Counter(tag for note in note_reports for tag in note.get("quality_tags_before", []))
    comment_insufficient = sum(1 for note in note_reports if note["comment_quality"]["comments_empty"] or note["comment_quality"]["likely_partial_comments"])
    high_quality_comments = sum(note["comment_quality"]["high_value_comment_count"] for note in note_reports)
    avg_score = round(sum(note["ai_usable_score"] for note in note_reports) / total, 1) if total else 0.0
    avg_score_before = round(sum(note.get("ai_usable_score_before", note["ai_usable_score"]) for note in note_reports) / total, 1) if total else 0.0
    avg_score_after = round(sum(note.get("ai_usable_score_after", note["ai_usable_score"]) for note in note_reports) / total, 1) if total else 0.0
    ocr_available_note_count = sum(1 for note in note_reports if note.get("effective_text_quality", {}).get("media_ocr_text_length", 0) > 0)
    ocr_text_total_length = sum(note.get("effective_text_quality", {}).get("media_ocr_text_length", 0) for note in note_reports)
    notes_improved_by_ocr = sum(1 for note in note_reports if note.get("effective_text_quality", {}).get("improved_by_ocr"))
    notes_improved_by_comments = sum(1 for note in note_reports if note.get("effective_text_quality", {}).get("improved_by_comments"))
    summary = {
        "total_notes": total,
        "average_ai_usable_score": avg_score,
        "average_ai_usable_score_before": avg_score_before,
        "average_ai_usable_score_after": avg_score_after,
        "directly_usable_notes": tag_counter.get("usable_text_rich", 0),
        "comment_driven_notes": tag_counter.get("comment_driven", 0),
        "ocr_enriched_notes": tag_counter.get("ocr_enriched", 0),
        "image_heavy_need_ocr_notes": tag_counter.get("image_heavy_need_ocr", 0),
        "metadata_only_notes": tag_counter.get("metadata_only", 0),
        "low_value_noise_notes": tag_counter.get("low_value_noise", 0),
        "metadata_only_before": tag_counter_before.get("metadata_only", 0),
        "metadata_only_after": tag_counter.get("metadata_only", 0),
        "low_value_noise_before": tag_counter_before.get("low_value_noise", 0),
        "low_value_noise_after": tag_counter.get("low_value_noise", 0),
        "ocr_available_note_count": ocr_available_note_count,
        "ocr_text_total_length": ocr_text_total_length,
        "notes_improved_by_ocr": notes_improved_by_ocr,
        "notes_improved_by_comments": notes_improved_by_comments,
        "comment_insufficient_notes": comment_insufficient,
        "high_quality_comment_count": high_quality_comments,
        "layer_counts": dict(counter),
        "tag_counts": dict(tag_counter),
        "tag_counts_before": dict(tag_counter_before),
        "quality_audit_status": (quality_audit or {}).get("status", "") if isinstance(quality_audit, dict) else "",
    }
    return summary


def assess_ai_input(note_reports: list[dict[str, Any]], ai_input: dict[str, Any]) -> dict[str, Any]:
    ai_notes = ai_input.get("notes", []) if isinstance(ai_input, dict) else []
    report_by_key = {note["note_key"]: note for note in note_reports}
    ai_keys = [key_for(note) for note in ai_notes if isinstance(note, dict)]
    ai_reports = [report_by_key[key] for key in ai_keys if key in report_by_key]
    bad_layers = {"metadata_only", "low_value_noise", "image_heavy_need_ocr"}
    low_quality = [note for note in ai_reports if note["primary_layer"] in bad_layers or note["ai_usable_score"] < 45]
    keep = [note for note in ai_reports if note["primary_layer"] in {"usable_text_rich", "comment_driven"} and note["ai_usable_score"] >= 55]
    image_heavy_ratio = 0.0
    if ai_reports:
        image_heavy_ratio = round(sum(1 for note in ai_reports if note["primary_layer"] == "image_heavy_need_ocr") / len(ai_reports), 3)
    return {
        "ai_input_note_count": len(ai_notes),
        "matched_ai_input_note_count": len(ai_reports),
        "low_quality_note_count": len(low_quality),
        "low_quality_ratio": round(len(low_quality) / len(ai_reports), 3) if ai_reports else 0.0,
        "image_heavy_ratio": image_heavy_ratio,
        "recommended_exclude": [
            {
                "note_id": note["note_id"],
                "title": note["title"],
                "score": note["ai_usable_score"],
                "primary_layer": note["primary_layer"],
                "reason": "；".join(note["score_penalties"][:4]) or "AI 可用性评分偏低",
            }
            for note in sorted(low_quality, key=lambda item: item["ai_usable_score"])[:30]
        ],
        "recommended_keep": [
            {
                "note_id": note["note_id"],
                "title": note["title"],
                "score": note["ai_usable_score"],
                "primary_layer": note["primary_layer"],
                "reason": "；".join(note["score_reasons"][:4]),
            }
            for note in sorted(keep, key=lambda item: item["ai_usable_score"], reverse=True)[:30]
        ],
        "ocr_or_keyword_warning": image_heavy_ratio >= 0.35,
    }


def diagnose_stages(note_reports: list[dict[str, Any]], ai_assessment: dict[str, Any]) -> dict[str, Any]:
    total = len(note_reports)
    if not total:
        return {
            "collection": {"risk": "high", "evidence": "没有可诊断的笔记。"},
            "normalization": {"risk": "unknown", "evidence": "无样本。"},
            "ai_input": {"risk": "unknown", "evidence": "无样本。"},
            "page_display": {"risk": "not_checked", "evidence": "本脚本不读取 Dashboard，只诊断数据输入质量。"},
        }
    title_only = sum(1 for note in note_reports if note["text_quality"]["only_title_no_body"])
    short_body = sum(1 for note in note_reports if note["text_quality"]["body_less_than_50"])
    comment_empty = sum(1 for note in note_reports if note["comment_quality"]["comments_empty"])
    fallback = sum(1 for note in note_reports if "fallback" in note.get("extraction_method", ""))
    raw_to_structured_loss = sum(
        1
        for note in note_reports
        if note["text_quality"]["raw_text_length"] >= 100 and note["text_quality"]["structured_text_length"] < 50
    )
    low_ai = ai_assessment.get("low_quality_ratio", 0.0)
    collection_risk = "high" if (short_body / total >= 0.45 or comment_empty / total >= 0.5) else "medium" if (fallback or title_only) else "low"
    normalization_risk = "high" if raw_to_structured_loss / total >= 0.25 else "medium" if raw_to_structured_loss else "low"
    ai_risk = "high" if low_ai >= 0.4 else "medium" if low_ai >= 0.2 else "low"
    return {
        "collection": {
            "risk": collection_risk,
            "evidence": f"正文少于50字 {short_body}/{total}，评论为空 {comment_empty}/{total}，fallback 记录 {fallback}/{total}。",
        },
        "normalization": {
            "risk": normalization_risk,
            "evidence": f"raw 正文>=100但 structured 正文<50 的疑似标准化损失 {raw_to_structured_loss}/{total}。",
        },
        "ai_input": {
            "risk": ai_risk,
            "evidence": f"当前 ai_input 中低质量 note 占比 {low_ai:.1%}。",
        },
        "page_display": {
            "risk": "not_checked",
            "evidence": "本脚本不读取 Dashboard。若这里显示数据质量偏低，页面空泛通常不是展示层根因。",
        },
    }


def build_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    ai = report["ai_input_assessment"]
    lines = [
        "# 采集质量诊断报告",
        "",
        f"- 生成时间：{report['generated_at']}",
        f"- 总笔记数：{summary['total_notes']}",
        f"- 平均 AI 可用性评分：{summary['average_ai_usable_score']}",
        f"- 平均 AI 可用性评分（改造前）：{summary['average_ai_usable_score_before']}",
        f"- 平均 AI 可用性评分（改造后）：{summary['average_ai_usable_score_after']}",
        f"- 可直接分析笔记数：{summary['directly_usable_notes']}",
        f"- 评论驱动型笔记数：{summary['comment_driven_notes']}",
        f"- OCR 增强笔记数：{summary['ocr_enriched_notes']}",
        f"- 需要 OCR 的图文笔记数：{summary['image_heavy_need_ocr_notes']}",
        f"- 只适合热度统计笔记数：{summary['metadata_only_notes']}",
        f"- 低价值噪声笔记数：{summary['low_value_noise_notes']}",
        f"- metadata_only 改造前/后：{summary['metadata_only_before']} / {summary['metadata_only_after']}",
        f"- low_value_noise 改造前/后：{summary['low_value_noise_before']} / {summary['low_value_noise_after']}",
        f"- OCR 可用笔记数：{summary['ocr_available_note_count']}",
        f"- OCR 文本总长度：{summary['ocr_text_total_length']}",
        f"- OCR 改善笔记数：{summary['notes_improved_by_ocr']}",
        f"- 评论改善笔记数：{summary['notes_improved_by_comments']}",
        f"- 评论不足笔记数：{summary['comment_insufficient_notes']}",
        f"- 高质量评论数量：{summary['high_quality_comment_count']}",
        "",
        "## 问题定位",
        "",
        "| 阶段 | 风险 | 证据 |",
        "| --- | --- | --- |",
    ]
    for stage, item in report["stage_diagnosis"].items():
        lines.append(f"| {stage} | {item['risk']} | {item['evidence']} |")

    lines.extend(
        [
            "",
            "## AI 输入诊断",
            "",
            f"- ai_input note 数：{ai['ai_input_note_count']}",
            f"- 成功匹配 note 数：{ai['matched_ai_input_note_count']}",
            f"- 低质量 note 数：{ai['low_quality_note_count']}",
            f"- 低质量占比：{ai['low_quality_ratio']:.1%}",
        ]
    )
    if ai.get("ocr_or_keyword_warning"):
        lines.append("- 提示：当前结果图文占比过高，需要 OCR 或更换关键词。")

    lines.extend(["", "## 最适合进入 Gemini 的 Top 20", "", "| 分数 | 分层 | 标题 | 原因 |", "| --- | --- | --- | --- |"])
    for note in report["top_gemini_candidates"][:20]:
        reason = "；".join(note.get("score_reasons", [])[:3])
        lines.append(f"| {note['ai_usable_score']} | {note['primary_layer']} | {note['title']} | {reason} |")

    lines.extend(["", "## 不建议进入 Gemini 的低质量样本", "", "| 分数 | 分层 | 标题 | 扣分原因 |", "| --- | --- | --- | --- |"])
    for note in report["low_quality_samples"][:30]:
        reason = "；".join(note.get("score_penalties", [])[:4])
        lines.append(f"| {note['ai_usable_score']} | {note['primary_layer']} | {note['title']} | {reason} |")

    lines.extend(["", "## 建议剔除的 ai_input notes", "", "| 分数 | 分层 | 标题 | 原因 |", "| --- | --- | --- | --- |"])
    for note in ai["recommended_exclude"][:30]:
        lines.append(f"| {note['score']} | {note['primary_layer']} | {note['title']} | {note['reason']} |")

    lines.extend(["", "## 建议优先保留的 ai_input notes", "", "| 分数 | 分层 | 标题 | 原因 |", "| --- | --- | --- | --- |"])
    for note in ai["recommended_keep"][:30]:
        lines.append(f"| {note['score']} | {note['primary_layer']} | {note['title']} | {note['reason']} |")

    lines.extend(["", "## 每篇笔记质量明细", ""])
    for note in report["notes"]:
        tq = note["text_quality"]
        cq = note["comment_quality"]
        lines.extend(
            [
                f"### {note['title'] or note['note_id'] or note['note_key']}",
                "",
                f"- 分数：{note['ai_usable_score']} / 100",
                f"- 分数前后：{note.get('ai_usable_score_before', note['ai_usable_score'])} -> {note.get('ai_usable_score_after', note['ai_usable_score'])}",
                f"- 分层：{note['primary_layer']}（{', '.join(note['quality_tags'])}）",
                f"- 正文：title {tq['title_length']} 字，DOM body {tq['body_length']} 字，effective {tq.get('effective_text_length', tq['body_length'])} 字，只有标题：{tq['only_title_no_body']}",
                f"- 文本来源：OCR {tq.get('media_ocr_text_length', 0)} 字，评论 {tq.get('comment_text_length', 0)} 字，来源数 {tq.get('effective_text_source_count', 0)}",
                f"- 媒体：{note['media_quality']['note_type']}，图片数 {note['media_quality']['image_count']}，图文缺 OCR：{note['media_quality']['image_heavy_without_text']}",
                f"- 评论：页面显示 {cq['page_comment_count']}，实际采集 {cq['actual_collected_comment_count']}，高质量 {cq['high_value_comment_count']}，疑似不全：{cq['likely_partial_comments']}",
                f"- 缺失字段：{', '.join(note['field_completeness']['missing_fields']) or '无'}",
                f"- URL：{note['note_url'] or '缺失'}",
                "",
            ]
        )
    return "\n".join(lines)


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    raw = load_json(args.raw, {}) or {}
    normalized = load_json(args.normalized, {}) or {}
    structured = load_json(args.structured_notes, []) or []
    quality_audit = load_json(args.quality_audit, {}) if args.quality_audit else {}
    ai_input = load_json(args.ai_input, {}) if args.ai_input else {}
    media_text = load_json(args.media_text, {}) if args.media_text else {}
    effective_notes = load_json(args.effective_notes, {}) if args.effective_notes else {}
    index = index_notes(raw, structured, ai_input, normalized, media_text, effective_notes)
    high_value_by_key = high_value_comment_index(ai_input)
    notes = [evaluate_note(key, parts, high_value_by_key) for key, parts in sorted(index.items())]
    notes.sort(key=lambda item: (item["ai_usable_score"], item["text_quality"]["body_length"], item["comment_quality"]["actual_collected_comment_count"]), reverse=True)
    summary = summarize(notes, quality_audit)
    ai_assessment = assess_ai_input(notes, ai_input)
    top_candidates = [
        note
        for note in notes
        if note["primary_layer"] in {"usable_text_rich", "comment_driven"} and note["ai_usable_score"] >= 55
    ][:20]
    low_quality = [
        note
        for note in sorted(notes, key=lambda item: item["ai_usable_score"])
        if note["primary_layer"] in {"metadata_only", "low_value_noise", "image_heavy_need_ocr"} or note["ai_usable_score"] < 45
    ][:40]
    report = {
        "generated_at": now_iso(),
        "inputs": {
            "raw": str(Path(args.raw)) if args.raw else "",
            "normalized": str(Path(args.normalized)) if args.normalized else "",
            "structured_notes": str(Path(args.structured_notes)) if args.structured_notes else "",
            "quality_audit": str(Path(args.quality_audit)) if args.quality_audit else "",
            "ai_input": str(Path(args.ai_input)) if args.ai_input else "",
            "media_text": str(Path(args.media_text)) if args.media_text else "",
            "effective_notes": str(Path(args.effective_notes)) if args.effective_notes else "",
        },
        "summary": summary,
        "stage_diagnosis": {},
        "ai_input_assessment": ai_assessment,
        "top_gemini_candidates": top_candidates,
        "low_quality_samples": low_quality,
        "notes": notes,
        "source_quality_audit": {
            "status": quality_audit.get("status", "") if isinstance(quality_audit, dict) else "",
            "counts": quality_audit.get("counts", {}) if isinstance(quality_audit, dict) else {},
            "ratios": quality_audit.get("ratios", {}) if isinstance(quality_audit, dict) else {},
        },
    }
    report["stage_diagnosis"] = diagnose_stages(notes, ai_assessment)
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit per-note XHS extraction quality before AI analysis.")
    parser.add_argument("--run-dir", default="", help="Run directory containing raw/normalized/structured outputs.")
    parser.add_argument("--raw", default="", help="Path to raw-xhs.json.")
    parser.add_argument("--normalized", default="", help="Path to normalized-research.json.")
    parser.add_argument("--structured-notes", default="", help="Path to structured-notes.json.")
    parser.add_argument("--quality-audit", default="", help="Optional quality-audit.json.")
    parser.add_argument("--ai-input", default="", help="Optional ai_input.json / ai-input.json.")
    parser.add_argument("--media-text", default="", help="Optional media-text.json.")
    parser.add_argument("--effective-notes", default="", help="Optional effective-notes.json.")
    parser.add_argument("--output-dir", default="", help="Directory for extraction-quality-report outputs.")
    parser.add_argument("--out-json", default="", help="Explicit JSON output path.")
    parser.add_argument("--out-md", default="", help="Explicit Markdown output path.")
    args = parser.parse_args()
    if args.run_dir:
        run_dir = Path(args.run_dir)
        args.raw = args.raw or str(run_dir / "raw-xhs.json")
        args.normalized = args.normalized or str(run_dir / "normalized-research.json")
        args.structured_notes = args.structured_notes or str(run_dir / "structured-notes.json")
        args.quality_audit = args.quality_audit or str(run_dir / "quality-audit.json")
        ai_input = run_dir / "ai-input.json"
        if not ai_input.exists():
            ai_input = run_dir / "ai_input.json"
        args.ai_input = args.ai_input or str(ai_input)
        args.media_text = args.media_text or str(run_dir / "media-text.json")
        args.effective_notes = args.effective_notes or str(run_dir / "effective-notes.json")
        args.output_dir = args.output_dir or str(run_dir)
    missing = []
    for name in ("raw", "normalized", "structured_notes"):
        value = getattr(args, name)
        if not value:
            missing.append("--" + name.replace("_", "-"))
        elif not Path(value).exists():
            missing.append(f"{name} not found: {value}")
    if missing:
        raise SystemExit("请提供 --run-dir，或提供必要输入：" + "; ".join(missing))
    return args


def output_paths(args: argparse.Namespace) -> tuple[Path, Path]:
    if args.out_json and args.out_md:
        return Path(args.out_json), Path(args.out_md)
    if args.output_dir:
        out_dir = Path(args.output_dir)
    else:
        out_dir = Path(args.raw).parent
    return (
        Path(args.out_json) if args.out_json else out_dir / "extraction-quality-report.json",
        Path(args.out_md) if args.out_md else out_dir / "extraction-quality-report.md",
    )


def main() -> None:
    args = parse_args()
    report = build_report(args)
    out_json, out_md = output_paths(args)
    write_json(out_json, report)
    write_text(out_md, build_markdown(report))
    summary = report["summary"]
    ai = report["ai_input_assessment"]
    print(f"notes={summary['total_notes']}")
    print(f"average_ai_usable_score={summary['average_ai_usable_score']}")
    print(f"average_ai_usable_score_before={summary['average_ai_usable_score_before']}")
    print(f"average_ai_usable_score_after={summary['average_ai_usable_score_after']}")
    print(f"usable_text_rich={summary['directly_usable_notes']}")
    print(f"comment_driven={summary['comment_driven_notes']}")
    print(f"ocr_enriched={summary['ocr_enriched_notes']}")
    print(f"image_heavy_need_ocr={summary['image_heavy_need_ocr_notes']}")
    print(f"metadata_only_before={summary['metadata_only_before']}")
    print(f"metadata_only_after={summary['metadata_only_after']}")
    print(f"metadata_only={summary['metadata_only_notes']}")
    print(f"low_value_noise={summary['low_value_noise_notes']}")
    print(f"ocr_available_note_count={summary['ocr_available_note_count']}")
    print(f"ocr_text_total_length={summary['ocr_text_total_length']}")
    print(f"notes_improved_by_ocr={summary['notes_improved_by_ocr']}")
    print(f"notes_improved_by_comments={summary['notes_improved_by_comments']}")
    print(f"ai_input_low_quality={ai['low_quality_note_count']}/{ai['matched_ai_input_note_count']}")
    print(f"markdown={out_md}")
    print(f"json={out_json}")


if __name__ == "__main__":
    main()
