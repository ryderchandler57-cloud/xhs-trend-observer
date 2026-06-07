from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from storage.database import get_connection, init_database, transaction  # noqa: E402
from storage.repositories import (  # noqa: E402
    insert_analysis_result,
    insert_comment,
    insert_metrics_snapshot,
    link_keyword_note,
    record_failure,
    stable_hash,
    upsert_author,
    upsert_crawl_run,
    upsert_keyword,
    upsert_note,
)


CN_TZ = timezone(timedelta(hours=8))


def now_iso() -> str:
    return datetime.now(CN_TZ).isoformat(timespec="seconds")


def load_json(path: str | Path | None, default: Any = None) -> Any:
    if not path:
        return default
    file_path = Path(path)
    if not file_path.exists():
        return default
    try:
        return json.loads(file_path.read_text(encoding="utf-8"))
    except Exception:
        return default


def json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


def clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def first_value(*values: Any) -> Any:
    for value in values:
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        return value
    return ""


def to_int(value: Any) -> int | None:
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


def note_id_from_url(url: str) -> str:
    parsed = urlparse(url or "")
    match = re.search(r"^/(?:explore|discovery/item|search_result)/([^/?#]+)", parsed.path)
    if match:
        return match.group(1)
    parts = [part for part in parsed.path.split("/") if part]
    return parts[-1] if parts else ""


def note_id_for(record: dict[str, Any]) -> str:
    return clean_text(
        first_value(
            record.get("note_id"),
            record.get("id"),
            note_id_from_url(record.get("url", "")),
            note_id_from_url(record.get("raw_url", "")),
            note_id_from_url(record.get("report_source_url", "")),
        )
    )


def normalize_url(record: dict[str, Any]) -> str:
    return clean_text(
        first_value(
            record.get("report_source_url"),
            record.get("url"),
            record.get("raw_url"),
            record.get("canonical_url"),
            record.get("detail_url"),
        )
    )


def records_by_note(raw: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for record in raw.get("records", []) if isinstance(raw, dict) else []:
        if not isinstance(record, dict):
            continue
        note_id = note_id_for(record)
        if note_id and note_id not in out:
            out[note_id] = record
    return out


def report_notes_by_note(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for note in report.get("structured_notes", []) if isinstance(report, dict) else []:
        if not isinstance(note, dict):
            continue
        note_id = note_id_for(note)
        if note_id:
            out[note_id] = note
    return out


def merged_notes(raw: dict[str, Any], report: dict[str, Any]) -> list[dict[str, Any]]:
    raw_records = records_by_note(raw)
    structured = report_notes_by_note(report)
    note_ids = list(dict.fromkeys([*raw_records.keys(), *structured.keys()]))
    notes: list[dict[str, Any]] = []
    for note_id in note_ids:
        raw_record = raw_records.get(note_id, {})
        note = structured.get(note_id, {})
        merged = {**raw_record, **{k: v for k, v in note.items() if v not in (None, "", [])}}
        merged["_raw_record"] = raw_record
        merged["_structured_note"] = note
        merged["note_id"] = note_id
        notes.append(merged)
    return notes


def root_keyword(request: dict[str, Any], raw: dict[str, Any], report: dict[str, Any]) -> str:
    return clean_text(
        first_value(
            report.get("meta", {}).get("query") if isinstance(report, dict) else "",
            raw.get("meta", {}).get("request_topic") if isinstance(raw, dict) else "",
            request.get("query") if isinstance(request, dict) else "",
            request.get("meta", {}).get("topic") if isinstance(request, dict) else "",
            "unknown",
        )
    )


def run_started_at(request: dict[str, Any], raw: dict[str, Any]) -> str:
    return clean_text(
        first_value(
            request.get("meta", {}).get("created_at") if isinstance(request, dict) else "",
            raw.get("meta", {}).get("collected_at") if isinstance(raw, dict) else "",
            now_iso(),
        )
    )


def run_finished_at(raw: dict[str, Any], report: dict[str, Any]) -> str:
    return clean_text(
        first_value(
            report.get("meta", {}).get("generated_at") if isinstance(report, dict) else "",
            raw.get("meta", {}).get("collected_at") if isinstance(raw, dict) else "",
            now_iso(),
        )
    )


def infer_status(raw: dict[str, Any], report: dict[str, Any]) -> str:
    record_count = len(raw.get("records", []) or []) if isinstance(raw, dict) else 0
    error_count = len(raw.get("errors", []) or []) if isinstance(raw, dict) else 0
    warning_count = int(report.get("meta", {}).get("warning_count") or 0) if isinstance(report, dict) else 0
    if record_count == 0 and error_count:
        return "failed"
    if error_count or warning_count:
        return "partial"
    return "success"


def run_key_for(out_dir: str, request: dict[str, Any], raw: dict[str, Any], report: dict[str, Any]) -> str:
    basis = [
        out_dir,
        root_keyword(request, raw, report),
        raw.get("meta", {}).get("collected_at") if isinstance(raw, dict) else "",
        report.get("meta", {}).get("generated_at") if isinstance(report, dict) else "",
    ]
    return f"run:{stable_hash(*basis, length=32)}"


def note_comments(note: dict[str, Any]) -> list[Any]:
    comments = []
    raw_record = note.get("_raw_record") if isinstance(note.get("_raw_record"), dict) else {}
    structured_note = note.get("_structured_note") if isinstance(note.get("_structured_note"), dict) else {}
    for source in [raw_record.get("comments"), structured_note.get("comments"), note.get("comments")]:
        if isinstance(source, list):
            comments.extend(source)
    seen: set[str] = set()
    out: list[Any] = []
    for item in comments:
        key = clean_text(item.get("content") if isinstance(item, dict) else item)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def failure_rows(raw: dict[str, Any], notes: list[dict[str, Any]]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for item in raw.get("errors", []) if isinstance(raw, dict) else []:
        if isinstance(item, dict):
            rows.append(
                {
                    "stage": "search",
                    "target": clean_text(item.get("query") or item.get("target") or ""),
                    "error_type": clean_text(item.get("type") or "raw_error"),
                    "error_message": clean_text(item.get("error") or item.get("message") or json_text(item)),
                }
            )
        else:
            rows.append({"stage": "search", "target": "", "error_type": "raw_error", "error_message": clean_text(item)})
    for note in notes:
        errors = note.get("errors") or []
        access_status = clean_text(note.get("access_status"))
        if not errors and access_status in ("", "ok"):
            continue
        target = clean_text(first_value(note.get("note_id"), note.get("url"), note.get("title")))
        if errors:
            for error in errors:
                rows.append(
                    {
                        "stage": "detail" if note.get("detail_attempted") else "search",
                        "target": target,
                        "error_type": access_status or "record_error",
                        "error_message": clean_text(error),
                    }
                )
        elif access_status and access_status != "ok":
            rows.append(
                {
                    "stage": "detail" if note.get("detail_attempted") else "search",
                    "target": target,
                    "error_type": access_status,
                    "error_message": f"access_status={access_status}",
                }
            )
    return rows


def sync_run(
    db_path: str,
    request_path: str,
    raw_path: str,
    report_path: str,
    ai_input_path: str = "",
    ai_output_path: str = "",
    quality_audit_path: str = "",
    out_dir: str = "",
) -> dict[str, Any]:
    request = load_json(request_path, {}) or {}
    raw = load_json(raw_path, {}) or {}
    report = load_json(report_path, {}) or {}
    ai_input = load_json(ai_input_path, {}) or {}
    ai_output = load_json(ai_output_path, {}) or {}
    quality_audit = load_json(quality_audit_path, {}) or {}

    init_database(db_path)
    notes = merged_notes(raw, report)
    root = root_keyword(request, raw, report)
    started_at = run_started_at(request, raw)
    finished_at = run_finished_at(raw, report)
    snapshot_time = clean_text(first_value(raw.get("meta", {}).get("collected_at"), finished_at, now_iso()))
    run_key = run_key_for(out_dir, request, raw, report)
    status = infer_status(raw, report)
    error_rows = failure_rows(raw, notes)

    synced_notes = 0
    synced_comments = 0
    synced_snapshots = 0
    keyword_count = 0

    with get_connection(db_path) as conn:
        with transaction(conn):
            root_keyword_id = upsert_keyword(conn, root, category=clean_text(request.get("meta", {}).get("topic")))
            run_id = upsert_crawl_run(
                conn,
                run_key=run_key,
                keyword_id=root_keyword_id,
                started_at=started_at,
                finished_at=finished_at,
                status=status,
                total_found=len(raw.get("records", []) or notes),
                total_saved=len(notes),
                error_count=len(error_rows),
                notes=clean_text(quality_audit.get("status") or report.get("meta", {}).get("quality_warning") or ""),
                out_dir=str(out_dir or Path(report_path).resolve().parent),
                raw_path=str(raw_path),
                report_path=str(report_path),
                ai_output_path=str(ai_output_path or ""),
            )

            for rank, note in enumerate(notes, start=1):
                note_id = clean_text(note.get("note_id"))
                if not note_id:
                    continue
                author_id = upsert_author(
                    conn,
                    "xiaohongshu",
                    nickname=clean_text(note.get("author")),
                    profile_url=clean_text(note.get("home_url") or note.get("profile_url")),
                    avatar_url=clean_text(note.get("avatar_url") or note.get("avatar")),
                    timestamp=snapshot_time,
                )
                upsert_note(
                    conn,
                    note_id=note_id,
                    platform="xiaohongshu",
                    author_id=author_id,
                    title=clean_text(note.get("title")),
                    content=clean_text(first_value(note.get("content"), note.get("text"), note.get("desc"), note.get("card_text"))),
                    note_url=normalize_url(note),
                    publish_time=clean_text(first_value(note.get("publish_time"), note.get("created_at"))),
                    seen_at=snapshot_time,
                    raw_json=note.get("_raw_record") or note,
                )
                synced_notes += 1

                link_keyword_note(conn, root_keyword_id, note_id, run_id, rank_position=rank, search_time=snapshot_time)
                keyword_count += 1
                keyword = clean_text(first_value(note.get("keyword"), note.get("query"), root))
                if keyword and keyword != root:
                    keyword_id = upsert_keyword(conn, keyword, category=root)
                    keyword_count += 1
                    link_keyword_note(conn, keyword_id, note_id, run_id, rank_position=rank, search_time=snapshot_time)
                snapshot_id = insert_metrics_snapshot(
                    conn,
                    note_id=note_id,
                    run_id=run_id,
                    liked_count=to_int(note.get("like_count") or note.get("liked_count")),
                    collected_count=to_int(note.get("collect_count") or note.get("collected_count")),
                    comment_count=to_int(note.get("comment_count")),
                    share_count=to_int(note.get("share_count")),
                    snapshot_time=snapshot_time,
                )
                if snapshot_id is not None:
                    synced_snapshots += 1

                for comment in note_comments(note):
                    if isinstance(comment, dict):
                        content = clean_text(first_value(comment.get("content"), comment.get("comment"), comment.get("text")))
                        comment_author_id = upsert_author(
                            conn,
                            "xiaohongshu",
                            nickname=clean_text(comment.get("author") or comment.get("user_name") or comment.get("nickname")),
                            timestamp=snapshot_time,
                        )
                        inserted = insert_comment(
                            conn,
                            note_id=note_id,
                            content=content,
                            author_id=comment_author_id,
                            comment_id=clean_text(comment.get("comment_id") or comment.get("id")),
                            like_count=to_int(comment.get("like_count")),
                            publish_time=clean_text(comment.get("publish_time")),
                            raw_json=comment,
                            created_at=snapshot_time,
                        )
                    else:
                        inserted = insert_comment(conn, note_id=note_id, content=clean_text(comment), created_at=snapshot_time)
                    if inserted:
                        synced_comments += 1

            for failure in error_rows:
                record_failure(
                    conn,
                    run_id=run_id,
                    stage=failure["stage"],
                    target=failure["target"],
                    error_type=failure["error_type"],
                    error_message=failure["error_message"],
                    created_at=snapshot_time,
                )

            if ai_output:
                meta = ai_output.get("meta", {}) if isinstance(ai_output, dict) else {}
                insert_analysis_result(
                    conn,
                    run_id=run_id,
                    keyword_id=root_keyword_id,
                    analysis_type="gemini_summary",
                    input_summary=json_text(ai_input.get("meta", {}) if isinstance(ai_input, dict) else {}),
                    output_text=json_text({k: v for k, v in ai_output.items() if k != "meta"})[:200000],
                    raw_json=ai_output,
                    model_name=clean_text(meta.get("model")),
                    created_at=clean_text(meta.get("generated_at")) or snapshot_time,
                    analysis_key=f"analysis:{run_key}:gemini_summary",
                )

    return {
        "db_path": str(Path(db_path).resolve()),
        "run_key": run_key,
        "root_keyword": root,
        "notes": synced_notes,
        "keywords_seen": keyword_count,
        "comments": synced_comments,
        "snapshots": synced_snapshots,
        "failures": len(error_rows),
        "analysis_saved": bool(ai_output),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync one XHS run JSON bundle into the long-term SQLite database.")
    parser.add_argument("--db", required=True)
    parser.add_argument("--request", required=True)
    parser.add_argument("--raw", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--ai-input", default="")
    parser.add_argument("--ai-output", default="")
    parser.add_argument("--quality-audit", default="")
    parser.add_argument("--out-dir", default="")
    parser.add_argument("--json-report", default="")
    args = parser.parse_args()

    report = sync_run(
        db_path=args.db,
        request_path=args.request,
        raw_path=args.raw,
        report_path=args.report,
        ai_input_path=args.ai_input,
        ai_output_path=args.ai_output,
        quality_audit_path=args.quality_audit,
        out_dir=args.out_dir,
    )
    if args.json_report:
        out = Path(args.json_report)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        "db_sync "
        f"notes={report['notes']} comments={report['comments']} "
        f"snapshots={report['snapshots']} failures={report['failures']} "
        f"db={report['db_path']}"
    )


if __name__ == "__main__":
    main()
