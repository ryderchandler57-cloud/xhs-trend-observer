from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


CN_TZ = timezone(timedelta(hours=8))


def load_json(path: str, default: Any = None) -> Any:
    file_path = Path(path)
    if not path or not file_path.exists():
        return default
    return json.loads(file_path.read_text(encoding="utf-8"))


def dump_json(path: str, data: Any) -> None:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def ratio(part: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return round(part / total, 3)


def status_row(
    check_id: str,
    label: str,
    passed: bool,
    severity: str,
    detail: str,
    value: Any = None,
) -> dict[str, Any]:
    return {
        "id": check_id,
        "label": label,
        "status": "pass" if passed else "fail",
        "severity": severity,
        "detail": detail,
        "value": value,
    }


def cookie_status(cookie_health: dict[str, Any] | None, sample_fixture: bool) -> dict[str, Any]:
    if sample_fixture:
        return status_row(
            "cookie_health",
            "Cookie health check",
            True,
            "info",
            "Smoke or existing-data mode does not need a live XHS cookie.",
            "sample_fixture",
        )
    if not cookie_health:
        return status_row(
            "cookie_health",
            "Cookie health check",
            False,
            "warning",
            "No cookie-health.json was found. Use -Diagnose or run a live collection to verify login state.",
            "missing",
        )
    health = cookie_health.get("health", {}) or {}
    ok = bool(health.get("ok"))
    return status_row(
        "cookie_health",
        "Cookie health check",
        ok,
        "critical",
        health.get("message") or health.get("status") or "Cookie health result was recorded.",
        health.get("status", "unknown"),
    )


def determine_overall(checks: list[dict[str, Any]], access_breakdown: dict[str, int]) -> str:
    failed_critical = [item for item in checks if item["status"] == "fail" and item["severity"] == "critical"]
    failed_warning = [item for item in checks if item["status"] == "fail" and item["severity"] == "warning"]
    if access_breakdown.get("cookie_invalid_or_login_required", 0):
        return "needs_login"
    if access_breakdown.get("verification_or_rate_limited", 0):
        return "blocked_by_xhs_verification"
    if failed_critical:
        return "failed"
    if failed_warning:
        return "partial"
    return "ok"


def markdown_report(audit: dict[str, Any]) -> str:
    lines = [
        "# XHS Collection Quality Audit",
        "",
        f"- Status: {audit['status']}",
        f"- Generated at: {audit['generated_at']}",
        f"- Records: {audit['counts']['records']}",
        f"- Evidence records: {audit['counts']['evidence_records']}",
        f"- Records with comments: {audit['counts']['records_with_comments']}",
        f"- Comments captured: {audit['counts']['comments']}",
        f"- High value comments: {audit['counts']['high_value_comments']}",
        "",
        "## Checks",
    ]
    for check in audit["checks"]:
        marker = "PASS" if check["status"] == "pass" else "FAIL"
        lines.append(f"- {marker} [{check['severity']}] {check['label']}: {check['detail']}")
    lines.extend(["", "## Access Status Breakdown"])
    for key, count in audit["access_status_breakdown"].items():
        lines.append(f"- {key}: {count}")
    if audit.get("problem_samples"):
        lines.extend(["", "## Problem Samples"])
        for item in audit["problem_samples"]:
            lines.append(f"- {item.get('access_status', 'unknown')} | {item.get('extraction_method', 'unknown')} | {item.get('title', '')}")
            if item.get("url"):
                lines.append(f"  URL: {item['url']}")
            if item.get("errors"):
                lines.append(f"  Errors: {' | '.join(item['errors'][:3])}")
    if audit["recommendations"]:
        lines.extend(["", "## Recommendations"])
        lines.extend(f"- {item}" for item in audit["recommendations"])
    return "\n".join(lines)


def problem_samples(records: list[dict[str, Any]], notes: list[dict[str, Any]], limit: int = 12) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for record in records:
        note_id = str(record.get("note_id") or "")
        if note_id:
            by_id[note_id] = record
    samples: list[dict[str, Any]] = []
    for note in notes:
        status = str(note.get("access_status") or "unknown")
        method = str(note.get("extraction_method") or "unknown")
        strong_evidence_method = method == "fixture" or method.startswith("detail-page") or method == "detail-api fallback"
        failure_status = status in {
            "cookie_invalid_or_login_required",
            "verification_or_rate_limited",
            "route_404",
            "note_unavailable_or_app_only",
            "timeout",
        }
        is_problem = failure_status or method == "search-card fallback" or (status == "unknown" and not strong_evidence_method)
        if not is_problem:
            continue
        raw = by_id.get(str(note.get("note_id") or ""), {})
        samples.append(
            {
                "note_id": note.get("note_id", ""),
                "title": note.get("title", ""),
                "url": note.get("url", ""),
                "access_status": status,
                "extraction_method": method,
                "detail_url": raw.get("detail_url", ""),
                "query": raw.get("query") or note.get("keyword", ""),
                "errors": raw.get("errors", []) or [],
            }
        )
        if len(samples) >= limit:
            break
    return samples


def audit(args: argparse.Namespace) -> dict[str, Any]:
    request = load_json(args.request, {}) or {}
    raw = load_json(args.raw, {}) or {}
    report = load_json(args.report, {}) or {}
    cookie_health = load_json(args.cookie_health, None)
    html_exists = bool(args.html and Path(args.html).exists())
    structured_notes = load_json(args.structured_notes, None)
    if structured_notes is None:
        structured_notes = report.get("structured_notes", []) or []

    records = raw.get("records", []) or []
    observations = report.get("observations", []) or []
    meta = report.get("meta", {}) or {}
    sample_fixture = bool(meta.get("sample_fixture") or raw.get("meta", {}).get("sample_fixture"))

    access_breakdown = Counter()
    extraction_breakdown = Counter()
    for note in structured_notes:
        access_breakdown[str(note.get("access_status") or "unknown")] += 1
        extraction_breakdown[str(note.get("extraction_method") or "unknown")] += 1
    if not access_breakdown:
        for record in records:
            access_breakdown[str(record.get("access_status") or "unknown")] += 1
            extraction_breakdown[str(record.get("extraction_method") or "unknown")] += 1

    detail_records = int(meta.get("detail_record_count") or 0)
    api_detail_records = int(meta.get("api_detail_record_count") or 0)
    evidence_records = int(meta.get("evidence_record_count") or 0)
    records_with_comments = int(meta.get("comment_record_count") or 0)
    comments = int(meta.get("total_comment_count") or 0)
    high_value_comments = len(report.get("high_value_comments", []) or [])
    fallback_records = int(meta.get("fallback_record_count") or 0)

    if not evidence_records:
        evidence_records = sum(
            1
            for note in structured_notes
            if note.get("content") or note.get("comments") or note.get("extraction_method") == "detail-api fallback"
        )
    if not records_with_comments:
        records_with_comments = sum(1 for note in structured_notes if note.get("comments"))
    if not comments:
        comments = sum(len(note.get("comments", []) or []) for note in structured_notes)
    if not high_value_comments:
        high_value_comments = sum(len(note.get("valuable_comments", []) or []) for note in structured_notes)

    record_count = len(records)
    note_count = len(structured_notes)
    constraints = request.get("constraints", {}) or {}
    expected = int(
        request.get("meta", {}).get("max_notes")
        or request.get("max_notes")
        or constraints.get("max_notes")
        or args.expected_min_records
        or 0
    )
    min_records = max(1, min(args.expected_min_records, expected or args.expected_min_records))

    checks = [
        cookie_status(cookie_health, sample_fixture),
        status_row(
            "raw_records",
            "Raw records captured",
            record_count >= min_records,
            "critical",
            f"Captured {record_count} raw records; expected at least {min_records}.",
            record_count,
        ),
        status_row(
            "structured_notes",
            "Structured note export",
            note_count == record_count and note_count > 0,
            "critical",
            f"Structured notes={note_count}, raw records={record_count}.",
            note_count,
        ),
        status_row(
            "detail_or_api_evidence",
            "Detail or API evidence",
            evidence_records > 0 and ratio(evidence_records, record_count) >= args.min_evidence_ratio,
            "warning",
            f"Evidence ratio={ratio(evidence_records, record_count)}; threshold={args.min_evidence_ratio}.",
            evidence_records,
        ),
        status_row(
            "comments",
            "Comment extraction",
            comments >= args.min_comments and records_with_comments > 0,
            "warning",
            f"Comments={comments}, records with comments={records_with_comments}.",
            comments,
        ),
        status_row(
            "valuable_comments",
            "High-value comment scoring",
            high_value_comments >= args.min_high_value_comments,
            "warning",
            f"High-value comments={high_value_comments}; threshold={args.min_high_value_comments}.",
            high_value_comments,
        ),
        status_row(
            "ai_summary",
            "AI summary sections",
            bool(report.get("ai_summary")) and bool((report.get("ai_summary") or {}).get("high_frequency_views")),
            "warning",
            "The report contains generated analysis seed sections.",
            sorted((report.get("ai_summary") or {}).keys()),
        ),
        status_row(
            "html",
            "HTML report",
            html_exists,
            "critical",
            f"HTML report exists: {args.html}",
            args.html,
        ),
    ]

    recommendations: list[str] = []
    if access_breakdown.get("cookie_invalid_or_login_required"):
        recommendations.append("Cookie appears invalid. Run -CookieOnly -RefreshCookie, log in, then retry collection.")
    if access_breakdown.get("verification_or_rate_limited"):
        recommendations.append("XHS verification or rate limiting was detected. Stop automation and complete manual verification before retrying.")
    if ratio(evidence_records, record_count) < args.min_evidence_ratio:
        recommendations.append("Most records lack detail/comment evidence. Retry with -Diagnose and inspect diagnostics/final_url for 404 or login routes.")
    if comments < args.min_comments:
        recommendations.append("Comment evidence is thin. Increase -MaxComments, reduce -MaxNotes, or run another keyword batch.")
    if fallback_records:
        recommendations.append("Some records came from search-card fallback only. Treat them as leads, not proven observations.")
    if not recommendations:
        recommendations.append("Collection quality is good enough for a first-pass trend report; verify important claims manually before publishing.")

    audit_doc = {
        "status": determine_overall(checks, dict(access_breakdown)),
        "generated_at": datetime.now(CN_TZ).isoformat(timespec="seconds"),
        "query": request.get("query") or meta.get("query", ""),
        "topic": meta.get("topic", ""),
        "sort": meta.get("sort", raw.get("meta", {}).get("sort", "")),
        "sample_fixture": sample_fixture,
        "counts": {
            "records": record_count,
            "structured_notes": note_count,
            "observations": len(observations),
            "detail_records": detail_records,
            "api_detail_records": api_detail_records,
            "evidence_records": evidence_records,
            "fallback_records": fallback_records,
            "records_with_comments": records_with_comments,
            "comments": comments,
            "high_value_comments": high_value_comments,
            "warnings": int(meta.get("warning_count") or len(raw.get("warnings", [])) + len(raw.get("errors", []))),
            "skipped_existing": int(meta.get("skipped_existing_count") or 0),
        },
        "ratios": {
            "detail_ratio": ratio(detail_records, record_count),
            "api_detail_ratio": ratio(api_detail_records, record_count),
            "evidence_ratio": ratio(evidence_records, record_count),
            "comment_record_ratio": ratio(records_with_comments, record_count),
            "high_value_comment_ratio": ratio(high_value_comments, max(comments, 1)),
        },
        "access_status_breakdown": dict(access_breakdown),
        "extraction_method_breakdown": dict(extraction_breakdown),
        "problem_samples": problem_samples(records, structured_notes),
        "checks": checks,
        "recommendations": recommendations,
        "paths": {
            "request": args.request,
            "raw": args.raw,
            "report": args.report,
            "structured_notes": args.structured_notes,
            "html": args.html,
            "cookie_health": args.cookie_health,
        },
    }
    return audit_doc


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit XHS collection quality.")
    parser.add_argument("--request", required=True)
    parser.add_argument("--raw", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--structured-notes", required=True)
    parser.add_argument("--html", required=True)
    parser.add_argument("--cookie-health", default="")
    parser.add_argument("--out", required=True)
    parser.add_argument("--markdown", default="")
    parser.add_argument("--expected-min-records", type=int, default=3)
    parser.add_argument("--min-evidence-ratio", type=float, default=0.35)
    parser.add_argument("--min-comments", type=int, default=1)
    parser.add_argument("--min-high-value-comments", type=int, default=1)
    args = parser.parse_args()

    audit_doc = audit(args)
    dump_json(args.out, audit_doc)
    if args.markdown:
        Path(args.markdown).parent.mkdir(parents=True, exist_ok=True)
        Path(args.markdown).write_text(markdown_report(audit_doc), encoding="utf-8")
    print(f"quality audit written: {args.out}")
    print(f"quality_status={audit_doc['status']}")
    print(f"evidence_ratio={audit_doc['ratios']['evidence_ratio']}")
    print(f"comments={audit_doc['counts']['comments']}")
    print(f"high_value_comments={audit_doc['counts']['high_value_comments']}")


if __name__ == "__main__":
    main()
