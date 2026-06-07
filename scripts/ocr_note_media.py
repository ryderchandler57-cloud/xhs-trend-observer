from __future__ import annotations

import argparse
import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


CN_TZ = timezone(timedelta(hours=8))
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".avif", ".gif", ".bmp"}
OCR_SUPPORTED_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".pdf"}
CONVERTIBLE_SUFFIXES = {".webp", ".avif"}
PROTECTED_NAMES = {
    "media-manifest.json",
    "media-text.json",
    "media-text.md",
    "media-cleanup-report.json",
    "media-cleanup-report.md",
    "raw-xhs.json",
    "structured-notes.json",
    "normalized-research.json",
    "report.html",
    "index.html",
}


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


def import_paddleocr() -> tuple[Any | None, str]:
    try:
        os.environ.setdefault("DISABLE_MODEL_SOURCE_CHECK", "True")
        from paddleocr import PaddleOCR  # type: ignore

        return PaddleOCR, ""
    except Exception as exc:
        return None, str(exc)


def create_ocr_engine(PaddleOCR: Any) -> tuple[Any | None, str]:
    attempts = (
        {
            "lang": "ch",
            "text_detection_model_name": "PP-OCRv5_mobile_det",
            "text_recognition_model_name": "PP-OCRv5_mobile_rec",
            "use_doc_orientation_classify": False,
            "use_doc_unwarping": False,
            "use_textline_orientation": False,
            "text_det_limit_side_len": 960,
        },
        {"use_angle_cls": True, "lang": "ch"},
        {"lang": "ch"},
        {},
    )
    for kwargs in attempts:
        try:
            return PaddleOCR(**kwargs), ""
        except TypeError:
            continue
        except Exception as exc:
            return None, str(exc)
    try:
        return PaddleOCR(), ""
    except Exception as exc:
        return None, str(exc)


def extract_text_recursive(value: Any) -> list[str]:
    texts: list[str] = []
    if value is None:
        return texts
    if isinstance(value, str):
        if value.strip():
            texts.append(value)
        return texts
    if isinstance(value, dict):
        for key in ("text", "rec_texts", "ocr_text", "label"):
            child = value.get(key)
            if isinstance(child, str):
                texts.append(child)
            elif isinstance(child, list):
                texts.extend(extract_text_recursive(child))
        for child in value.values():
            if isinstance(child, (dict, list, tuple)):
                texts.extend(extract_text_recursive(child))
        return texts
    if isinstance(value, tuple):
        return extract_text_recursive(list(value))
    if isinstance(value, list):
        if len(value) == 2 and isinstance(value[1], tuple) and value[1] and isinstance(value[1][0], str):
            texts.append(value[1][0])
            return texts
        if len(value) == 2 and isinstance(value[1], list) and value[1] and isinstance(value[1][0], str):
            texts.append(value[1][0])
            return texts
        for child in value:
            texts.extend(extract_text_recursive(child))
    return texts


def clean_ocr_text(texts: list[str]) -> str:
    out: list[str] = []
    seen: set[str] = set()
    for text in texts:
        for line in str(text).splitlines():
            line = re.sub(r"\s+", " ", line).strip()
            line = line.strip("|·•-_=~`")
            if not line:
                continue
            if not re.search(r"[\u4e00-\u9fffA-Za-z0-9￥$%]", line):
                continue
            if len(line) == 1 and not re.search(r"[\u4e00-\u9fffA-Za-z0-9]", line):
                continue
            if line in seen:
                continue
            seen.add(line)
            out.append(line)
    return "\n".join(out)


def run_ocr(engine: Any, image_path: Path) -> tuple[str, str]:
    try:
        if hasattr(engine, "ocr"):
            try:
                result = engine.ocr(str(image_path), cls=True)
            except TypeError:
                result = engine.ocr(str(image_path))
        elif hasattr(engine, "predict"):
            result = engine.predict(str(image_path))
        else:
            return "", "PaddleOCR engine has no ocr/predict method"
        return clean_ocr_text(extract_text_recursive(result)), ""
    except Exception as exc:
        return "", str(exc)


def safe_path_name(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9_.-]+", "_", str(value).strip())
    return value[:100] or "unknown"


def resolve_image_path(path_value: str, manifest_dir: Path) -> Path:
    path = Path(path_value)
    if path.is_absolute():
        return path
    if path.exists():
        return path
    return manifest_dir / path


def convert_for_ocr(image_path: Path, temp_dir: Path, index: int) -> tuple[Path | None, dict[str, Any]]:
    original_ext = image_path.suffix.lower()
    record = {
        "original_image_path": str(image_path),
        "ocr_input_path": "",
        "original_ext": original_ext,
        "converted_for_ocr": False,
        "conversion_status": "not_needed" if original_ext in OCR_SUPPORTED_SUFFIXES else "pending",
        "ocr_status": "pending",
        "error_message": "",
    }
    if original_ext in OCR_SUPPORTED_SUFFIXES:
        record["ocr_input_path"] = str(image_path)
        return image_path, record
    if original_ext not in CONVERTIBLE_SUFFIXES:
        record["conversion_status"] = "unsupported_format"
        record["error_message"] = f"unsupported image suffix for OCR: {original_ext}"
        record["ocr_status"] = "conversion_failed"
        return None, record
    try:
        from PIL import Image

        temp_dir.mkdir(parents=True, exist_ok=True)
        out_path = temp_dir / f"{image_path.stem}_{index:03d}.png"
        with Image.open(image_path) as image:
            image.convert("RGB").save(out_path, format="PNG")
        record.update(
            {
                "ocr_input_path": str(out_path),
                "converted_for_ocr": True,
                "conversion_status": "converted",
            }
        )
        return out_path, record
    except Exception as exc:
        record["conversion_status"] = "conversion_failed"
        record["ocr_status"] = "conversion_failed"
        record["error_message"] = str(exc)
        return None, record


def folder_stats(media_dir: Path) -> tuple[int, float]:
    if not media_dir.exists():
        return 0, 0.0
    files = [path for path in media_dir.rglob("*") if path.is_file()]
    total = sum(path.stat().st_size for path in files)
    return len(files), round(total / (1024 * 1024), 3)


def is_under(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def cleanup_media(
    *,
    media_dir: Path,
    temp_dir: Path,
    successful_images: set[Path],
    failed_images: set[Path],
    converted_temp_files: set[Path],
    conversion_failed_count: int,
    dry_run: bool,
    keep_failed: bool,
    max_media_mb: float,
    cleanup_enabled: bool,
    cleanup_status: str,
) -> dict[str, Any]:
    files_before, size_before = folder_stats(media_dir)
    temp_files_before, temp_size_before = folder_stats(temp_dir)
    warnings: list[str] = []
    deleted = 0
    deleted_bytes = 0
    temp_deleted = 0
    temp_deleted_bytes = 0
    kept_failed_files: list[str] = []
    if cleanup_enabled:
        targets = list(successful_images)
        if size_before > max_media_mb:
            warnings.append(f"media folder exceeds --max-media-mb ({size_before} MB > {max_media_mb} MB); cleaning successful OCR images first")
        for path in sorted(targets):
            if not path.exists():
                continue
            if path.name in PROTECTED_NAMES or path.suffix.lower() not in IMAGE_SUFFIXES:
                warnings.append(f"skip protected/non-image file: {path}")
                continue
            if not is_under(path, media_dir):
                warnings.append(f"skip path outside media_dir: {path}")
                continue
            if keep_failed and path in failed_images:
                kept_failed_files.append(str(path))
                continue
            size = path.stat().st_size
            if not dry_run:
                path.unlink()
            deleted += 1
            deleted_bytes += size
    for path in sorted(failed_images):
        if path.exists():
            kept_failed_files.append(str(path))
    for path in sorted(converted_temp_files):
        if not path.exists():
            continue
        if path.name in PROTECTED_NAMES or path.suffix.lower() != ".png":
            warnings.append(f"skip protected/non-temp file: {path}")
            continue
        if not is_under(path, temp_dir):
            warnings.append(f"skip temp path outside media_ocr_tmp: {path}")
            continue
        size = path.stat().st_size
        if not dry_run:
            path.unlink()
        temp_deleted += 1
        temp_deleted_bytes += size
    if not dry_run and temp_dir.exists():
        for directory in sorted([p for p in temp_dir.rglob("*") if p.is_dir()], reverse=True):
            try:
                directory.rmdir()
            except OSError:
                pass
        try:
            temp_dir.rmdir()
        except OSError:
            pass
    files_after, size_after = folder_stats(media_dir)
    temp_files_after, temp_size_after = folder_stats(temp_dir)
    return {
        "media_dir": str(media_dir),
        "temp_dir": str(temp_dir),
        "files_before": files_before,
        "size_before_mb": size_before,
        "files_deleted": deleted,
        "size_deleted_mb": round(deleted_bytes / (1024 * 1024), 3),
        "files_after": files_after,
        "size_after_mb": size_after,
        "temp_files_before": temp_files_before,
        "temp_size_before_mb": temp_size_before,
        "converted_temp_files": len(converted_temp_files),
        "temp_files_deleted": temp_deleted,
        "temp_size_deleted_mb": round(temp_deleted_bytes / (1024 * 1024), 3),
        "temp_files_after": temp_files_after,
        "temp_size_after_mb": temp_size_after,
        "conversion_failed_count": conversion_failed_count,
        "kept_failed_files": kept_failed_files,
        "cleanup_status": "dry_run" if dry_run else "completed" if cleanup_enabled else cleanup_status,
        "warnings": warnings,
    }


def build_media_text(args: argparse.Namespace) -> tuple[dict[str, Any], dict[str, Any]]:
    manifest_path = Path(args.manifest)
    manifest = load_json(manifest_path, {})
    manifest_dir = manifest_path.parent
    media_dir = Path(manifest.get("media_dir") or manifest_dir / "media")
    temp_root = manifest_dir / "media_ocr_tmp"
    PaddleOCR, import_error = import_paddleocr()
    engine = None
    engine_error = ""
    paddle_missing = PaddleOCR is None
    if PaddleOCR:
        engine, engine_error = create_ocr_engine(PaddleOCR)
        if engine is None:
            paddle_missing = True
    notes: list[dict[str, Any]] = []
    successful_images: set[Path] = set()
    failed_images: set[Path] = set()
    total_ocr_length = 0
    ok_notes = 0
    partial_notes = 0
    failed_notes = 0
    missing_notes = 0
    no_image_notes = 0
    converted_temp_files: set[Path] = set()
    conversion_failed_count = 0
    for note in manifest.get("notes", []) or []:
        image_paths = [resolve_image_path(path, manifest_dir) for path in note.get("image_paths", []) or []]
        image_paths = [path for path in image_paths if path.exists()]
        note_id = str(note.get("note_id", "") or "unknown")
        note_temp_dir = temp_root / safe_path_name(note_id)
        if not image_paths:
            status = "no_images" if not note.get("image_count") else "ocr_failed"
            if status == "no_images":
                no_image_notes += 1
            else:
                failed_notes += 1
            notes.append(
                {
                    "note_id": note.get("note_id", ""),
                    "title": note.get("title", ""),
                    "image_count": note.get("downloaded_count", 0),
                    "ocr_text": "",
                    "ocr_text_length": 0,
                    "ocr_status": status,
                    "error_message": "no downloaded images" if status == "ocr_failed" else "",
                    "image_paths_processed": [],
                    "images": [],
                }
            )
            continue
        if paddle_missing:
            missing_notes += 1
            image_records = []
            for image_path in image_paths:
                image_records.append(
                    {
                        "original_image_path": str(image_path),
                        "ocr_input_path": "",
                        "original_ext": image_path.suffix.lower(),
                        "converted_for_ocr": False,
                        "conversion_status": "skipped_paddleocr_missing",
                        "ocr_status": "paddleocr_missing",
                        "error_message": "PaddleOCR 未安装，已跳过 OCR。请安装 PaddleOCR 后重试。",
                    }
                )
            notes.append(
                {
                    "note_id": note.get("note_id", ""),
                    "title": note.get("title", ""),
                    "image_count": len(image_paths),
                    "ocr_text": "",
                    "ocr_text_length": 0,
                    "ocr_status": "paddleocr_missing",
                    "error_message": "PaddleOCR 未安装，已跳过 OCR。请安装 PaddleOCR 后重试。",
                    "image_paths_processed": [str(path) for path in image_paths],
                    "images": image_records,
                }
            )
            failed_images.update(image_paths)
            continue
        image_texts: list[str] = []
        image_errors: list[str] = []
        processed: list[str] = []
        image_records: list[dict[str, Any]] = []
        for image_index, image_path in enumerate(image_paths, start=1):
            ocr_input_path, image_record = convert_for_ocr(image_path, note_temp_dir, image_index)
            if image_record.get("converted_for_ocr") and ocr_input_path:
                converted_temp_files.add(ocr_input_path)
            if not ocr_input_path:
                conversion_failed_count += 1
                image_errors.append(f"{image_path.name}: {image_record['error_message'] or image_record['conversion_status']}")
                failed_images.add(image_path)
                image_records.append(image_record)
                continue
            text, error = run_ocr(engine, ocr_input_path)
            processed.append(str(ocr_input_path))
            if text:
                image_texts.append(text)
                successful_images.add(image_path)
                image_record["ocr_status"] = "ok"
                image_record["ocr_text_length"] = len(text)
            else:
                image_errors.append(f"{image_path.name}: {error or 'empty OCR text'}")
                failed_images.add(image_path)
                image_record["ocr_status"] = "ocr_failed"
                image_record["ocr_text_length"] = 0
                if image_record.get("error_message"):
                    image_record["error_message"] = f"{image_record['error_message']}; {error or 'empty OCR text'}"
                else:
                    image_record["error_message"] = error or "empty OCR text"
            image_records.append(image_record)
        ocr_text = clean_ocr_text(image_texts)
        text_len = len(ocr_text)
        total_ocr_length += text_len
        if image_texts and not image_errors:
            status = "ok"
            ok_notes += 1
        elif image_texts:
            status = "partial"
            partial_notes += 1
        else:
            status = "ocr_failed"
            failed_notes += 1
        notes.append(
            {
                "note_id": note.get("note_id", ""),
                "title": note.get("title", ""),
                "image_count": len(image_paths),
                "ocr_text": ocr_text,
                "ocr_text_length": text_len,
                "ocr_status": status,
                "error_message": "; ".join(image_errors[:5]),
                "image_paths_processed": processed,
                "images": image_records,
            }
        )
    cleanup_enabled = bool(args.cleanup_media) and not args.keep_media
    cleanup_status = "skipped_paddleocr_missing" if paddle_missing else "skipped_keep_media" if args.keep_media else "skipped_disabled"
    cleanup = cleanup_media(
        media_dir=media_dir,
        temp_dir=temp_root,
        successful_images=successful_images,
        failed_images=failed_images,
        converted_temp_files=converted_temp_files,
        conversion_failed_count=conversion_failed_count,
        dry_run=args.dry_run,
        keep_failed=args.keep_failed_media,
        max_media_mb=args.max_media_mb,
        cleanup_enabled=cleanup_enabled and not paddle_missing,
        cleanup_status=cleanup_status,
    )
    report = {
        "generated_at": now_iso(),
        "manifest_path": str(manifest_path),
        "media_dir": str(media_dir),
        "temp_dir": str(temp_root),
        "paddleocr_available": not paddle_missing,
        "paddleocr_error": import_error or engine_error,
        "summary": {
            "total_notes": len(notes),
            "ocr_success_note_count": ok_notes,
            "ocr_partial_note_count": partial_notes,
            "ocr_failed_note_count": failed_notes,
            "paddleocr_missing_note_count": missing_notes,
            "no_image_note_count": no_image_notes,
            "ocr_text_total_length": total_ocr_length,
            "converted_temp_file_count": len(converted_temp_files),
            "conversion_failed_count": conversion_failed_count,
        },
        "notes": notes,
    }
    return report, cleanup


def build_media_text_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# 图文 OCR 文本报告",
        "",
        f"- 生成时间：{report['generated_at']}",
        f"- PaddleOCR 可用：{report['paddleocr_available']}",
        f"- OCR 成功笔记数：{summary['ocr_success_note_count']}",
        f"- OCR 部分成功笔记数：{summary['ocr_partial_note_count']}",
        f"- OCR 总文本长度：{summary['ocr_text_total_length']}",
        f"- PaddleOCR 缺失笔记数：{summary['paddleocr_missing_note_count']}",
        f"- 临时转换文件数：{summary.get('converted_temp_file_count', 0)}",
        f"- 转换失败图片数：{summary.get('conversion_failed_count', 0)}",
    ]
    if not report["paddleocr_available"]:
        lines.append("- 提示：PaddleOCR 未安装，已跳过 OCR。请安装 PaddleOCR 后重试。")
    lines.extend(["", "## 明细", "", "| 状态 | 图片 | OCR字数 | 标题 |", "| --- | ---: | ---: | --- |"])
    for note in report["notes"]:
        title = (note.get("title") or note.get("note_id") or "").replace("|", " ")
        lines.append(f"| {note['ocr_status']} | {note['image_count']} | {note['ocr_text_length']} | {title} |")
    samples = [note for note in report["notes"] if note.get("ocr_text")]
    if samples:
        lines.extend(["", "## OCR 文本样例", ""])
        for note in samples[:10]:
            lines.append(f"### {note.get('title') or note.get('note_id')}")
            lines.append("")
            lines.append(note["ocr_text"][:1200])
            lines.append("")
    return "\n".join(lines)


def build_cleanup_markdown(cleanup: dict[str, Any]) -> str:
    lines = [
        "# 媒体缓存清理报告",
        "",
        f"- 媒体目录：{cleanup['media_dir']}",
        f"- 清理状态：{cleanup['cleanup_status']}",
        f"- 清理前文件数：{cleanup['files_before']}",
        f"- 清理前大小 MB：{cleanup['size_before_mb']}",
        f"- 删除文件数：{cleanup['files_deleted']}",
        f"- 删除大小 MB：{cleanup['size_deleted_mb']}",
        f"- 清理后文件数：{cleanup['files_after']}",
        f"- 清理后大小 MB：{cleanup['size_after_mb']}",
        f"- 保留失败文件数：{len(cleanup['kept_failed_files'])}",
        f"- 临时转换文件数：{cleanup.get('converted_temp_files', 0)}",
        f"- 临时文件删除数：{cleanup.get('temp_files_deleted', 0)}",
        f"- 转换失败图片数：{cleanup.get('conversion_failed_count', 0)}",
    ]
    if cleanup.get("warnings"):
        lines.extend(["", "## Warnings", ""])
        lines.extend(f"- {warning}" for warning in cleanup["warnings"])
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="OCR downloaded XHS note images with optional safe cleanup.")
    parser.add_argument("--manifest", required=True, help="Path to media-manifest.json.")
    parser.add_argument("--output-dir", default="", help="Output directory. Defaults to manifest parent.")
    parser.add_argument("--keep-media", action="store_true", help="Do not delete media image cache after OCR.")
    parser.add_argument("--cleanup-media", action=argparse.BooleanOptionalAction, default=True, help="Clean image cache after successful OCR.")
    parser.add_argument("--keep-failed-media", action=argparse.BooleanOptionalAction, default=True, help="Keep failed images for debugging.")
    parser.add_argument("--max-media-mb", type=float, default=500.0, help="Media cache size hint for cleanup.")
    parser.add_argument("--dry-run", action="store_true", help="Preview cleanup without deleting images.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    out_dir = Path(args.output_dir) if args.output_dir else Path(args.manifest).parent
    report, cleanup = build_media_text(args)
    media_text_json = out_dir / "media-text.json"
    media_text_md = out_dir / "media-text.md"
    cleanup_json = out_dir / "media-cleanup-report.json"
    cleanup_md = out_dir / "media-cleanup-report.md"
    write_json(media_text_json, report)
    write_text(media_text_md, build_media_text_markdown(report))
    write_json(cleanup_json, cleanup)
    write_text(cleanup_md, build_cleanup_markdown(cleanup))
    summary = report["summary"]
    print(f"paddleocr_available={report['paddleocr_available']}")
    if not report["paddleocr_available"]:
        print("PaddleOCR 未安装，已跳过 OCR。请安装 PaddleOCR 后重试。")
    print(f"ocr_success_note_count={summary['ocr_success_note_count']}")
    print(f"ocr_text_total_length={summary['ocr_text_total_length']}")
    print(f"converted_temp_file_count={summary.get('converted_temp_file_count', 0)}")
    print(f"conversion_failed_count={summary.get('conversion_failed_count', 0)}")
    print(f"cleanup_files_deleted={cleanup['files_deleted']}")
    print(f"cleanup_size_deleted_mb={cleanup['size_deleted_mb']}")
    print(f"temp_files_deleted={cleanup.get('temp_files_deleted', 0)}")
    print(f"remaining_media_size_mb={cleanup['size_after_mb']}")
    print(f"media_text_json={media_text_json}")
    print(f"media_text_md={media_text_md}")
    print(f"cleanup_json={cleanup_json}")
    print(f"cleanup_md={cleanup_md}")


if __name__ == "__main__":
    main()
