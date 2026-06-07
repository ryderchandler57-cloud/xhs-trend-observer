from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.request import Request, urlopen


CN_TZ = timezone(timedelta(hours=8))

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}
SKIP_KEY_TOKENS = (
    "avatar",
    "icon",
    "emoji",
    "logo",
    "profile",
    "head",
    "user",
    "watermark",
)
IMAGE_KEY_TOKENS = ("image", "images", "img", "photo", "cover", "image_list", "image_metadata")
IMAGE_URL_TOKENS = (
    "sns-webpic",
    "sns-img",
    "xhscdn.com",
    "imageview2",
    "webp",
    "jpg",
    "jpeg",
    "png",
)


def now_iso() -> str:
    return datetime.now(CN_TZ).isoformat(timespec="seconds")


def load_json(path: str | Path, default: Any) -> Any:
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
    if isinstance(value, dict):
        for key in ("text", "content", "comment", "title", "desc", "url"):
            if value.get(key):
                return clean_text(value.get(key))
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        return " ".join(clean_text(item) for item in value)
    return re.sub(r"\s+", " ", str(value)).strip()


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
    for key in ("note_url", "url", "report_source_url", "raw_url", "canonical_url", "detail_url"):
        value = clean_text(item.get(key))
        if value:
            return value
    source = item.get("source")
    if isinstance(source, dict):
        return clean_text(source.get("url"))
    return ""


def note_id_for(item: dict[str, Any]) -> str:
    for key in ("note_id", "id", "noteId", "note_id_md5"):
        value = clean_text(item.get(key))
        if value:
            return value
    return note_id_from_url(canonical_url(item))


def title_for(item: dict[str, Any]) -> str:
    return clean_text(item.get("title") or item.get("note_title") or item.get("phenomenon"))


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


def safe_name(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9_.-]+", "_", value.strip())
    return value[:80] or stable_hash(value)


def raw_records(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, dict):
        records = raw.get("records") or raw.get("notes") or raw.get("structured_notes") or []
    elif isinstance(raw, list):
        records = raw
    else:
        records = []
    return [item for item in records if isinstance(item, dict)]


def structured_records(structured: Any) -> list[dict[str, Any]]:
    if isinstance(structured, dict):
        records = structured.get("structured_notes") or structured.get("notes") or structured.get("records") or []
    elif isinstance(structured, list):
        records = structured
    else:
        records = []
    return [item for item in records if isinstance(item, dict)]


def add_index(index: dict[str, dict[str, Any]], item: dict[str, Any], source: str) -> None:
    key = key_for(item)
    if key:
        index.setdefault(key, {})[source] = item


def build_index(raw: Any, structured: Any) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for item in raw_records(raw):
        add_index(index, item, "raw")
    for item in structured_records(structured):
        add_index(index, item, "structured")
    return index


def should_skip_key(key_path: list[str]) -> bool:
    lowered = ".".join(key_path).lower()
    return any(token in lowered for token in SKIP_KEY_TOKENS)


def looks_like_image_url(url: str) -> bool:
    if not re.match(r"^https?://", url, flags=re.I):
        return False
    lowered = url.lower()
    if any(token in lowered for token in ("avatar", "icon", "emoji", "logo", "profile")):
        return False
    parsed = urlparse(url)
    suffix = Path(parsed.path).suffix.lower()
    if suffix in IMAGE_SUFFIXES:
        return True
    return any(token in lowered for token in IMAGE_URL_TOKENS)


def collect_url_string(value: str) -> list[str]:
    urls = re.findall(r"https?://[^\s\"'<>]+", value)
    return [url.rstrip("),.;]") for url in urls if looks_like_image_url(url.rstrip("),.;]"))]


def direct_image_urls(item: dict[str, Any]) -> list[str]:
    urls: list[str] = []
    for key in ("images", "image_urls", "image_list", "image_metadata"):
        value = item.get(key)
        if not value:
            continue
        if isinstance(value, list):
            for child in value:
                if isinstance(child, str):
                    urls.extend(collect_url_string(child))
                elif isinstance(child, dict):
                    for url_key in ("url", "src", "original", "origin", "trace_url"):
                        if child.get(url_key):
                            urls.extend(collect_url_string(clean_text(child.get(url_key))))
                    for child_value in child.values():
                        if isinstance(child_value, str):
                            urls.extend(collect_url_string(child_value))
        elif isinstance(value, str):
            urls.extend(collect_url_string(value))
        elif isinstance(value, dict):
            for child_value in value.values():
                if isinstance(child_value, str):
                    urls.extend(collect_url_string(child_value))
    return urls


def recursive_image_urls(value: Any, key_path: list[str] | None = None, depth: int = 0) -> list[str]:
    if depth > 6:
        return []
    key_path = key_path or []
    if should_skip_key(key_path):
        return []
    urls: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            next_path = key_path + [str(key)]
            key_l = str(key).lower()
            if should_skip_key(next_path):
                continue
            if isinstance(child, str):
                if any(token in key_l for token in IMAGE_KEY_TOKENS + ("url", "src")):
                    urls.extend(collect_url_string(child))
            else:
                urls.extend(recursive_image_urls(child, next_path, depth + 1))
    elif isinstance(value, list):
        for child in value:
            urls.extend(recursive_image_urls(child, key_path, depth + 1))
    elif isinstance(value, str) and any(token in ".".join(key_path).lower() for token in IMAGE_KEY_TOKENS):
        urls.extend(collect_url_string(value))
    return urls


def dedupe_urls(urls: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for url in urls:
        normalized = url.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        out.append(normalized)
    return out


def has_video(item: dict[str, Any]) -> bool:
    for key in ("video", "video_url", "video_metadata", "media_stream"):
        value = item.get(key)
        if value not in (None, "", [], {}):
            return True
    text = json.dumps(item, ensure_ascii=False).lower()
    return '"video"' in text or "video_metadata" in text


def note_type_for(raw: dict[str, Any], structured: dict[str, Any], image_count: int) -> str:
    video = has_video(raw) or has_video(structured)
    if video and image_count:
        return "mixed"
    if video:
        return "video"
    if image_count:
        return "image"
    return "unknown"


def extension_for(url: str, content_type: str = "") -> str:
    suffix = Path(urlparse(url).path).suffix.lower()
    if suffix in IMAGE_SUFFIXES:
        return ".jpg" if suffix == ".jpeg" else suffix
    if content_type:
        guessed = mimetypes.guess_extension(content_type.split(";")[0].strip())
        if guessed and guessed.lower() in IMAGE_SUFFIXES:
            return ".jpg" if guessed.lower() == ".jpeg" else guessed.lower()
    lowered = url.lower()
    if "webp" in lowered:
        return ".webp"
    if "png" in lowered:
        return ".png"
    return ".jpg"


def download_image(url: str, path_without_suffix: Path, timeout: int = 25) -> tuple[Path | None, str]:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125 Safari/537.36",
        "Referer": "https://www.xiaohongshu.com/",
        "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
    }
    req = Request(url, headers=headers)
    try:
        with urlopen(req, timeout=timeout) as response:
            content_type = response.headers.get("Content-Type", "")
            data = response.read()
        if not data:
            return None, "empty response"
        suffix = extension_for(url, content_type)
        out = path_without_suffix.with_suffix(suffix)
        out.parent.mkdir(parents=True, exist_ok=True)
        if out.exists() and out.stat().st_size > 0:
            return out, ""
        out.write_bytes(data)
        return out, ""
    except Exception as exc:
        return None, str(exc)


def resolve_inputs(args: argparse.Namespace) -> argparse.Namespace:
    if args.run_dir:
        run_dir = Path(args.run_dir)
        args.raw = args.raw or str(run_dir / "raw-xhs.json")
        args.structured = args.structured or str(run_dir / "structured-notes.json")
        args.output_dir = args.output_dir or str(run_dir)
    if not args.raw or not args.structured:
        raise SystemExit("请提供 --run-dir，或同时提供 --raw 与 --structured。")
    args.output_dir = args.output_dir or str(Path(args.raw).parent)
    return args


def build_manifest(args: argparse.Namespace) -> dict[str, Any]:
    raw = load_json(args.raw, {})
    structured = load_json(args.structured, [])
    out_dir = Path(args.output_dir)
    media_dir = out_dir / "media"
    index = build_index(raw, structured)
    notes: list[dict[str, Any]] = []
    summary = {
        "total_notes": 0,
        "notes_with_images": 0,
        "downloaded_images": 0,
        "notes_no_images": 0,
        "failed_download_notes": 0,
        "partial_notes": 0,
        "skipped_video_notes": 0,
    }
    for key, parts in sorted(index.items()):
        raw_note = parts.get("raw", {}) or {}
        structured_note = parts.get("structured", {}) or {}
        note_id = note_id_for(structured_note) or note_id_for(raw_note) or key.split(":", 1)[-1]
        title = title_for(structured_note) or title_for(raw_note)
        urls = dedupe_urls(
            direct_image_urls(structured_note)
            + direct_image_urls(raw_note)
            + recursive_image_urls(structured_note)
            + recursive_image_urls(raw_note)
        )
        note_type = note_type_for(raw_note, structured_note, len(urls))
        image_paths: list[str] = []
        errors: list[str] = []
        if urls:
            note_media_dir = media_dir / safe_name(note_id or stable_hash(title, key))
            for idx, url in enumerate(urls, start=1):
                path_without_suffix = note_media_dir / f"image_{idx:03d}"
                existing = next(path_without_suffix.parent.glob(path_without_suffix.name + ".*"), None)
                if existing and existing.suffix.lower() in IMAGE_SUFFIXES and existing.stat().st_size > 0:
                    image_paths.append(str(existing))
                    continue
                downloaded, error = download_image(url, path_without_suffix)
                if downloaded:
                    image_paths.append(str(downloaded))
                else:
                    errors.append(f"{idx}: {error}")
                time.sleep(0.1)
        downloaded_count = len(image_paths)
        if urls and downloaded_count == len(urls):
            status = "downloaded"
        elif urls and downloaded_count > 0:
            status = "partial"
        elif urls:
            status = "download_failed"
        elif note_type == "video":
            status = "skipped_video"
        else:
            status = "no_images"
        summary["total_notes"] += 1
        if urls:
            summary["notes_with_images"] += 1
        if status == "no_images":
            summary["notes_no_images"] += 1
        if status == "download_failed":
            summary["failed_download_notes"] += 1
        if status == "partial":
            summary["partial_notes"] += 1
        if status == "skipped_video":
            summary["skipped_video_notes"] += 1
        summary["downloaded_images"] += downloaded_count
        notes.append(
            {
                "note_key": key,
                "note_id": note_id,
                "title": title,
                "note_type": note_type,
                "image_count": len(urls),
                "downloaded_count": downloaded_count,
                "image_urls": urls,
                "image_paths": image_paths,
                "status": status,
                "error_message": "; ".join(errors[:5]),
            }
        )
    return {
        "generated_at": now_iso(),
        "raw_path": str(Path(args.raw)),
        "structured_path": str(Path(args.structured)),
        "output_dir": str(out_dir),
        "media_dir": str(media_dir),
        "summary": summary,
        "notes": notes,
    }


def build_markdown(manifest: dict[str, Any]) -> str:
    summary = manifest["summary"]
    lines = [
        "# 图文媒体提取报告",
        "",
        f"- 生成时间：{manifest['generated_at']}",
        f"- 总笔记数：{summary['total_notes']}",
        f"- 识别到图片的笔记数：{summary['notes_with_images']}",
        f"- 成功下载图片数：{summary['downloaded_images']}",
        f"- 无图片笔记数：{summary['notes_no_images']}",
        f"- 部分下载笔记数：{summary['partial_notes']}",
        f"- 下载失败笔记数：{summary['failed_download_notes']}",
        f"- 纯视频跳过笔记数：{summary['skipped_video_notes']}",
        f"- 媒体目录：{manifest['media_dir']}",
        "",
        "## 明细",
        "",
        "| 状态 | 类型 | 图片 | 已下载 | 标题 |",
        "| --- | --- | ---: | ---: | --- |",
    ]
    for note in manifest["notes"]:
        title = (note.get("title") or note.get("note_id") or "").replace("|", " ")
        lines.append(
            f"| {note['status']} | {note['note_type']} | {note['image_count']} | {note['downloaded_count']} | {title} |"
        )
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extract and download note body images from XHS run outputs.")
    parser.add_argument("--run-dir", default="", help="Run directory containing raw-xhs.json and structured-notes.json.")
    parser.add_argument("--raw", default="", help="Path to raw-xhs.json.")
    parser.add_argument("--structured", default="", help="Path to structured-notes.json.")
    parser.add_argument("--output-dir", default="", help="Output directory for manifest/report/media.")
    return resolve_inputs(parser.parse_args())


def main() -> None:
    args = parse_args()
    manifest = build_manifest(args)
    out_dir = Path(args.output_dir)
    manifest_path = out_dir / "media-manifest.json"
    report_path = out_dir / "media-report.md"
    write_json(manifest_path, manifest)
    write_text(report_path, build_markdown(manifest))
    summary = manifest["summary"]
    print(f"notes={summary['total_notes']}")
    print(f"notes_with_images={summary['notes_with_images']}")
    print(f"downloaded_images={summary['downloaded_images']}")
    print(f"notes_no_images={summary['notes_no_images']}")
    print(f"failed_download_notes={summary['failed_download_notes']}")
    print(f"manifest={manifest_path}")
    print(f"markdown={report_path}")


if __name__ == "__main__":
    main()
