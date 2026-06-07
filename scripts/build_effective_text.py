from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


CN_TZ = timezone(timedelta(hours=8))


def now_iso() -> str:
    return datetime.now(CN_TZ).isoformat(timespec="seconds")


def load_json(path: str | Path | None, default: Any) -> Any:
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
        return "\n".join(clean_text(item) for item in value if clean_text(item))
    if isinstance(value, dict):
        for key in ("content", "text", "comment", "body", "desc", "title"):
            if value.get(key):
                return clean_text(value.get(key))
        return json.dumps(value, ensure_ascii=False)
    return re.sub(r"[ \t\r\f\v]+", " ", str(value)).strip()


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
    return ""


def note_id_for(item: dict[str, Any]) -> str:
    for key in ("note_id", "id", "noteId"):
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
    title = title_for(item)
    if title:
        return f"title:{title}"
    return ""


def structured_records(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        records = data
    elif isinstance(data, dict):
        records = data.get("structured_notes") or data.get("notes") or data.get("records") or []
    else:
        records = []
    return [item for item in records if isinstance(item, dict)]


def media_text_index(media_text: Any) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for item in media_text.get("notes", []) if isinstance(media_text, dict) else []:
        if not isinstance(item, dict):
            continue
        key = key_for(item)
        if key:
            out[key] = item
    return out


def comment_text(note: dict[str, Any], max_comments: int = 12) -> str:
    comments = []
    for key in ("valuable_comments", "comments"):
        value = note.get(key)
        if isinstance(value, list):
            comments.extend(value)
    out: list[str] = []
    seen: set[str] = set()
    for item in comments:
        text = clean_text(item)
        if not text or text in seen:
            continue
        if len(text) < 4:
            continue
        if "行吟信息科技" in text or "沪ICP备" in text:
            continue
        seen.add(text)
        out.append(text)
        if len(out) >= max_comments:
            break
    return "\n".join(out)


def join_blocks(blocks: list[tuple[str, str]]) -> tuple[str, list[str]]:
    out: list[str] = []
    sources: list[str] = []
    seen: set[str] = set()
    for label, text in blocks:
        text = clean_text(text)
        if not text:
            continue
        if text in seen:
            continue
        seen.add(text)
        sources.append(label)
        out.append(text)
    return "\n\n".join(out), sources


def effective_layer(dom_len: int, ocr_len: int, comment_len: int, effective_len: int) -> str:
    if effective_len < 50:
        return "metadata_only"
    if dom_len >= 100 and effective_len >= 100:
        return "dom_text_rich"
    if ocr_len >= 100 and ocr_len >= dom_len:
        return "ocr_enriched"
    if comment_len >= 120 and dom_len < 100:
        return "comment_driven"
    return "mixed_text"


def build_effective(args: argparse.Namespace) -> dict[str, Any]:
    structured = structured_records(load_json(args.structured_notes, []))
    media_text = load_json(args.media_text, {})
    media_by_key = media_text_index(media_text)
    notes: list[dict[str, Any]] = []
    total_dom = 0
    total_effective = 0
    improved_by_ocr = 0
    improved_by_comments = 0
    notes_with_ocr = 0
    for note in structured:
        key = key_for(note)
        media = media_by_key.get(key, {})
        title = title_for(note)
        dom_text = clean_text(note.get("content") or note.get("text") or note.get("desc"))
        ocr_text = clean_text(media.get("ocr_text"))
        comments = comment_text(note)
        dom_len = len(dom_text)
        ocr_len = len(ocr_text)
        comment_len = len(comments)
        if dom_len >= 100:
            blocks = [("dom", dom_text), ("ocr", ocr_text), ("comments", comments)]
        elif ocr_len >= max(80, dom_len):
            blocks = [("ocr", ocr_text), ("dom", dom_text), ("comments", comments)]
        elif comment_len >= 120:
            blocks = [("dom", dom_text), ("comments", comments), ("ocr", ocr_text)]
        else:
            blocks = [("dom", dom_text), ("ocr", ocr_text), ("comments", comments)]
        effective_text, sources = join_blocks(blocks)
        effective_len = len(effective_text)
        total_dom += dom_len
        total_effective += effective_len
        if ocr_len >= 50 and effective_len > dom_len:
            improved_by_ocr += 1
        if comment_len >= 80 and effective_len > max(dom_len, ocr_len):
            improved_by_comments += 1
        if ocr_len > 0:
            notes_with_ocr += 1
        notes.append(
            {
                "note_id": note_id_for(note),
                "title": title,
                "keyword": clean_text(note.get("keyword") or note.get("query")),
                "note_url": canonical_url(note),
                "dom_text": dom_text,
                "media_ocr_text": ocr_text,
                "comment_text_summary": comments,
                "effective_text": effective_text,
                "dom_text_length": dom_len,
                "media_ocr_text_length": ocr_len,
                "comment_text_length": comment_len,
                "effective_text_length": effective_len,
                "text_sources": sources,
                "effective_layer": effective_layer(dom_len, ocr_len, comment_len, effective_len),
                "ocr_status": media.get("ocr_status", "missing_media_text"),
            }
        )
    total = len(notes)
    return {
        "generated_at": now_iso(),
        "inputs": {
            "structured_notes": str(Path(args.structured_notes)) if args.structured_notes else "",
            "media_text": str(Path(args.media_text)) if args.media_text else "",
            "quality_audit": str(Path(args.quality_audit)) if args.quality_audit else "",
        },
        "summary": {
            "total_notes": total,
            "notes_with_ocr": notes_with_ocr,
            "notes_improved_by_ocr": improved_by_ocr,
            "notes_improved_by_comments": improved_by_comments,
            "average_dom_text_length": round(total_dom / total, 1) if total else 0,
            "average_effective_text_length": round(total_effective / total, 1) if total else 0,
            "effective_text_total_length": total_effective,
        },
        "notes": notes,
    }


def build_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# Effective Text 合并报告",
        "",
        f"- 生成时间：{report['generated_at']}",
        f"- 总笔记数：{summary['total_notes']}",
        f"- 有 OCR 文本笔记数：{summary['notes_with_ocr']}",
        f"- OCR 改善笔记数：{summary['notes_improved_by_ocr']}",
        f"- 评论改善笔记数：{summary['notes_improved_by_comments']}",
        f"- 平均 DOM 文本长度：{summary['average_dom_text_length']}",
        f"- 平均 effective_text 长度：{summary['average_effective_text_length']}",
        "",
        "## 明细",
        "",
        "| 层级 | DOM | OCR | 评论 | Effective | 来源 | 标题 |",
        "| --- | ---: | ---: | ---: | ---: | --- | --- |",
    ]
    for note in report["notes"]:
        title = (note.get("title") or note.get("note_id") or "").replace("|", " ")
        lines.append(
            f"| {note['effective_layer']} | {note['dom_text_length']} | {note['media_ocr_text_length']} | "
            f"{note['comment_text_length']} | {note['effective_text_length']} | {','.join(note['text_sources'])} | {title} |"
        )
    samples = [note for note in report["notes"] if note.get("effective_text")]
    if samples:
        lines.extend(["", "## effective_text 样例", ""])
        for note in samples[:10]:
            lines.append(f"### {note.get('title') or note.get('note_id')}")
            lines.append("")
            lines.append(note["effective_text"][:1200])
            lines.append("")
    return "\n".join(lines)


def resolve_inputs(args: argparse.Namespace) -> argparse.Namespace:
    if args.run_dir:
        run_dir = Path(args.run_dir)
        args.structured_notes = args.structured_notes or str(run_dir / "structured-notes.json")
        args.media_text = args.media_text or str(run_dir / "media-text.json")
        args.quality_audit = args.quality_audit or str(run_dir / "quality-audit.json")
        args.output_dir = args.output_dir or str(run_dir)
    if not args.structured_notes:
        raise SystemExit("请提供 --run-dir 或 --structured-notes。")
    args.output_dir = args.output_dir or str(Path(args.structured_notes).parent)
    return args


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build effective note text from DOM text, OCR text and valuable comments.")
    parser.add_argument("--run-dir", default="", help="Run directory containing structured-notes.json and media-text.json.")
    parser.add_argument("--structured-notes", default="", help="Path to structured-notes.json.")
    parser.add_argument("--media-text", default="", help="Path to media-text.json.")
    parser.add_argument("--quality-audit", default="", help="Optional quality-audit.json.")
    parser.add_argument("--output-dir", default="", help="Output directory for effective-notes outputs.")
    return resolve_inputs(parser.parse_args())


def main() -> None:
    args = parse_args()
    report = build_effective(args)
    out_dir = Path(args.output_dir)
    json_path = out_dir / "effective-notes.json"
    md_path = out_dir / "effective-notes.md"
    write_json(json_path, report)
    write_text(md_path, build_markdown(report))
    summary = report["summary"]
    print(f"notes={summary['total_notes']}")
    print(f"notes_with_ocr={summary['notes_with_ocr']}")
    print(f"notes_improved_by_ocr={summary['notes_improved_by_ocr']}")
    print(f"notes_improved_by_comments={summary['notes_improved_by_comments']}")
    print(f"average_dom_text_length={summary['average_dom_text_length']}")
    print(f"average_effective_text_length={summary['average_effective_text_length']}")
    print(f"json={json_path}")
    print(f"markdown={md_path}")


if __name__ == "__main__":
    main()
