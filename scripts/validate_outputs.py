from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path


def load_json(path: str) -> dict:
    file_path = Path(path)
    if not file_path.exists():
        raise SystemExit(f"missing file: {file_path}")
    try:
        return json.loads(file_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"invalid JSON {file_path}: {exc}") from exc


def assert_keys(name: str, data: dict, keys: list[str]) -> None:
    missing = [key for key in keys if key not in data]
    if missing:
        raise SystemExit(f"{name} missing keys: {', '.join(missing)}")


def find_browser_executable() -> str | None:
    candidates = []
    program_files_x86 = os.environ.get("ProgramFiles(x86)", "")
    program_files = os.environ.get("ProgramFiles", "")
    local_app = os.environ.get("LOCALAPPDATA", "")
    if program_files_x86:
        candidates.append(Path(program_files_x86) / "Microsoft/Edge/Application/msedge.exe")
        candidates.append(Path(program_files_x86) / "Google/Chrome/Application/chrome.exe")
    if program_files:
        candidates.append(Path(program_files) / "Microsoft/Edge/Application/msedge.exe")
        candidates.append(Path(program_files) / "Google/Chrome/Application/chrome.exe")
    if local_app:
        candidates.append(Path(local_app) / "Google/Chrome/Application/chrome.exe")
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return None


def launch_chromium(pw):
    try:
        return pw.chromium.launch(headless=True)
    except Exception:
        executable = find_browser_executable()
        if not executable:
            raise
        return pw.chromium.launch(headless=True, executable_path=executable)


def browser_check(html_path: Path) -> list[str]:
    warnings: list[str] = []
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        return [f"browser check skipped: playwright import failed ({exc})"]

    try:
        with sync_playwright() as pw:
            browser = launch_chromium(pw)
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            console_errors: list[str] = []
            page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
            page.goto(html_path.resolve().as_uri(), wait_until="load")
            page.wait_for_timeout(500)
            cards = page.locator(".dash-card, [data-observation-card]").count()
            if cards == 0:
                warnings.append("browser check: no dashboard cards rendered")
            if console_errors:
                warnings.append("browser check console errors: " + " | ".join(console_errors[:3]))
            mobile = browser.new_page(viewport={"width": 390, "height": 844}, is_mobile=True)
            mobile.goto(html_path.resolve().as_uri(), wait_until="load")
            mobile.wait_for_timeout(300)
            if mobile.locator(".dash-card, [data-observation-card]").count() == 0:
                warnings.append("mobile browser check: no dashboard cards rendered")
            browser.close()
    except Exception as exc:
        warnings.append(f"browser check skipped: {exc}")
    return warnings


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate trend observer outputs.")
    parser.add_argument("--request", required=True)
    parser.add_argument("--raw", required=True)
    parser.add_argument("--normalized", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--html", required=True)
    parser.add_argument("--structured-notes", default="")
    parser.add_argument("--csv", default="")
    parser.add_argument("--xlsx", default="")
    parser.add_argument("--sqlite", default="")
    parser.add_argument("--markdown", default="")
    parser.add_argument("--quality-audit", default="")
    parser.add_argument("--ai-input", default="")
    parser.add_argument("--ai-prompt", default="")
    parser.add_argument("--ai-output-json", default="")
    parser.add_argument("--ai-output-md", default="")
    parser.add_argument("--browser-check", action="store_true")
    args = parser.parse_args()

    request = load_json(args.request)
    raw = load_json(args.raw)
    normalized = load_json(args.normalized)
    report = load_json(args.report)
    html_path = Path(args.html)
    if not html_path.exists():
        raise SystemExit(f"missing HTML: {html_path}")
    html = html_path.read_text(encoding="utf-8")

    assert_keys("request", request, ["meta", "query", "expanded_queries", "focus_topics"])
    assert_keys("raw", raw, ["meta", "records", "errors"])
    assert_keys("normalized", normalized, ["meta", "observations", "leads_to_verify", "themes"])
    assert_keys(
        "report",
        report,
        ["meta", "observations", "themes", "source", "structured_notes", "ai_summary", "high_value_comments"],
    )

    if '<script id="app-data" type="application/json">' not in html:
        raise SystemExit("HTML does not contain embedded app data script")

    match = re.search(
        r'<script id="app-data" type="application/json">(.*?)</script>',
        html,
        re.DOTALL,
    )
    if not match:
        raise SystemExit("could not extract embedded app data")
    embedded = json.loads(match.group(1))
    assert_keys("embedded HTML data", embedded, ["meta", "observations"])

    warnings = []
    structured_count = len(report.get("structured_notes", []) or [])
    if args.structured_notes:
        notes = load_json(args.structured_notes)
        if not isinstance(notes, list):
            raise SystemExit("structured notes JSON is not a list")
        structured_count = len(notes)
        required_note_keys = [
            "keyword",
            "note_id",
            "title",
            "content",
            "author",
            "publish_time",
            "like_count",
            "collect_count",
            "comment_count",
            "comments",
            "images",
            "video",
            "url",
        ]
        if notes:
            assert_keys("structured note", notes[0], required_note_keys)
    for label, path in [
        ("CSV", args.csv),
        ("Excel", args.xlsx),
        ("SQLite", args.sqlite),
        ("Markdown", args.markdown),
        ("AI prompt", args.ai_prompt),
    ]:
        if path and not Path(path).exists():
            raise SystemExit(f"missing {label}: {path}")
    if args.quality_audit:
        audit = load_json(args.quality_audit)
        assert_keys("quality audit", audit, ["status", "counts", "ratios", "checks", "recommendations"])
    if args.ai_input:
        ai_input = load_json(args.ai_input)
        assert_keys("AI input", ai_input, ["meta", "quality", "analysis_tasks", "notes", "high_value_comments"])
    if args.ai_output_json:
        ai_output = load_json(args.ai_output_json)
        new_dashboard_keys = [
            "meta",
            "top_findings",
            "high_value_comments",
            "hidden_comment_insights",
            "info_gap_rankings",
            "trend_clusters",
            "counterintuitive_findings",
            "next_search_keywords",
        ]
        legacy_keys = [
            "meta",
            "core_trends",
            "hot_topics",
            "key_viewpoints",
            "high_value_posts",
            "high_value_comments",
            "report_recommendations",
        ]
        if all(key in ai_output for key in new_dashboard_keys):
            pass
        else:
            assert_keys("AI output", ai_output, legacy_keys)
        if ai_output.get("meta", {}).get("analysis_provider") != "gemini":
            raise SystemExit("AI output was not produced by Gemini")
        if not report.get("gemini_analysis"):
            raise SystemExit("report missing gemini_analysis")
        if not report.get("meta", {}).get("ai_analyzed"):
            raise SystemExit("report meta does not mark ai_analyzed")
    if args.ai_output_md and not Path(args.ai_output_md).exists():
        raise SystemExit(f"missing AI output Markdown: {args.ai_output_md}")
    if len(raw.get("records", [])) == 0:
        warnings.append("raw data has no records")
    if len(report.get("observations", [])) == 0:
        warnings.append("report has no observations")
    if not report.get("ai_summary", {}).get("high_frequency_views"):
        warnings.append("report AI summary has no high-frequency views")
    if structured_count == 0:
        warnings.append("no structured notes exported")
    if args.browser_check:
        warnings.extend(browser_check(html_path))

    print("VALIDATION OK")
    print(f"records={len(raw.get('records', []))}")
    print(f"observations={len(report.get('observations', []))}")
    print(f"leads_to_verify={len(report.get('leads_to_verify', []))}")
    print(f"structured_notes={structured_count}")
    print(f"high_value_comments={len(report.get('high_value_comments', []) or [])}")
    if warnings:
        for warning in warnings:
            print(f"warning: {warning}")


if __name__ == "__main__":
    main()
