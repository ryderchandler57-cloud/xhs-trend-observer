from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from source_urls import select_report_source_url


CN_TZ = timezone(timedelta(hours=8))


def load_json(path: str, default: Any = None) -> Any:
    if not path:
        return default
    file_path = Path(path)
    if not file_path.exists():
        return default
    return json.loads(file_path.read_text(encoding="utf-8"))


def write_json(path: str, data: dict[str, Any]) -> None:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def as_text_list(items: Any, limit: int = 8) -> list[str]:
    out: list[str] = []
    if not isinstance(items, list):
        return out
    for item in items[:limit]:
        if isinstance(item, str):
            text = item
        elif isinstance(item, dict):
            parts = []
            for key in ["title", "one_line", "summary", "comment", "why_high_value", "why_valuable", "reason", "evidence", "topic", "real_logic", "trend", "cluster"]:
                value = item.get(key)
                if isinstance(value, list):
                    value = "；".join(str(part) for part in value if part)
                if value:
                    parts.append(str(value))
            text = "：".join(parts)
        else:
            text = str(item)
        text = text.strip()
        if text:
            out.append(text)
    return out


def build_keyword_stats(raw: dict[str, Any]) -> list[dict[str, Any]]:
    records = raw.get("records", []) if isinstance(raw, dict) else []
    grouped: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        keyword = record.get("keyword") or record.get("query") or "未标注关键词"
        grouped.setdefault(keyword, []).append(record)
    rows: list[dict[str, Any]] = []
    for keyword, items in grouped.items():
        rows.append(
            {
                "keyword": keyword,
                "collected_count": len(items),
                "detail_opened_count": sum(1 for item in items if item.get("detail_opened") or item.get("detail_opened_by_click")),
                "json_parsed_count": sum(
                    1
                    for item in items
                    if item.get("parsed_from_network") or "network parser" in (item.get("extraction_method") or "")
                ),
                "comment_count": sum(len(item.get("comments", []) or []) for item in items),
                "api_payload_count": sum(int(item.get("api_payload_count") or 0) for item in items),
            }
        )
    return sorted(rows, key=lambda row: (row["collected_count"], row["json_parsed_count"]), reverse=True)


STOPWORDS = {
    "一个",
    "这个",
    "那个",
    "就是",
    "可以",
    "没有",
    "不是",
    "真的",
    "感觉",
    "因为",
    "所以",
    "自己",
    "现在",
    "小红书",
    "话题",
}


def build_high_frequency_keywords(notes: list[dict[str, Any]], observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counter: Counter[str] = Counter()
    for item in observations:
        counter.update(str(signal) for signal in item.get("signals", []) or [] if signal)
        if item.get("theme"):
            counter[item["theme"]] += 2
    for note in notes:
        blob = " ".join(
            [
                str(note.get("keyword", "")),
                str(note.get("title", "")),
                str(note.get("content", ""))[:800],
                " ".join(str(comment) for comment in (note.get("comments", []) or [])[:12]),
            ]
        )
        for token in re.findall(r"[A-Za-z][A-Za-z0-9_+-]{1,24}|[\u4e00-\u9fff]{2,8}", blob):
            if token in STOPWORDS:
                continue
            counter[token] += 1
    return [{"keyword": key, "count": count} for key, count in counter.most_common(30)]


def build_raw_posts(raw: dict[str, Any], limit: int = 120) -> list[dict[str, Any]]:
    records = raw.get("records", []) if isinstance(raw, dict) else []
    posts: list[dict[str, Any]] = []
    for record in records[:limit]:
        report_url, report_url_type, source_url_valid = select_report_source_url(record)
        posts.append(
            {
                "keyword": record.get("keyword") or record.get("query", ""),
                "note_id": record.get("note_id") or record.get("id", ""),
                "title": record.get("title", ""),
                "author": record.get("author", ""),
                "publish_time": record.get("publish_time") or record.get("created_at", ""),
                "url": report_url,
                "raw_url": record.get("url", ""),
                "report_source_url": report_url,
                "report_source_url_type": report_url_type,
                "source_url_valid": source_url_valid,
                "like_count": record.get("like_count"),
                "collect_count": record.get("collect_count"),
                "comment_count": record.get("comment_count"),
                "comments": (record.get("comments", []) or [])[:20],
                "access_status": record.get("access_status", ""),
                "detail_opened": bool(record.get("detail_opened") or record.get("detail_opened_by_click")),
                "parsed_from_network": bool(record.get("parsed_from_network")),
                "extraction_method": record.get("extraction_method", ""),
                "api_payload_count": record.get("api_payload_count", 0),
                "comment_source": record.get("comment_source", ""),
            }
        )
    return posts


def build_report_source_rows(raw: dict[str, Any], limit: int = 500) -> list[dict[str, Any]]:
    records = raw.get("records", []) if isinstance(raw, dict) else []
    rows: list[dict[str, Any]] = []
    for record in records[:limit]:
        report_url, report_url_type, source_url_valid = select_report_source_url(record)
        rows.append(
            {
                "note_id": record.get("note_id") or record.get("id", ""),
                "title": record.get("title", ""),
                "report_source_url": report_url,
                "report_source_url_type": report_url_type,
                "source_url_valid": source_url_valid,
                "raw_url": record.get("url", ""),
            }
        )
    return rows


def write_report_source_diagnostics(raw: dict[str, Any], diagnostics_dir: str = "") -> None:
    if not diagnostics_dir:
        return
    diagnostics_path = Path(diagnostics_dir) / "diagnostics.json"
    if diagnostics_path.exists():
        diagnostics = load_json(str(diagnostics_path), {}) or {}
    else:
        diagnostics = {"generated_at": datetime.now(CN_TZ).isoformat(timespec="seconds"), "events": [], "screenshots": []}
    if not isinstance(diagnostics, dict):
        diagnostics = {"generated_at": datetime.now(CN_TZ).isoformat(timespec="seconds"), "events": [], "screenshots": []}
    diagnostics["report_source_urls"] = build_report_source_rows(raw)
    diagnostics_path.parent.mkdir(parents=True, exist_ok=True)
    diagnostics_path.write_text(json.dumps(diagnostics, ensure_ascii=False, indent=2), encoding="utf-8")


def build_diagnostics_summary(raw: dict[str, Any], diagnostics_dir: str = "") -> dict[str, Any]:
    diagnostics_file = ""
    if diagnostics_dir:
        candidate = Path(diagnostics_dir) / "diagnostics.json"
        if candidate.exists():
            diagnostics_file = str(candidate)
    if not diagnostics_file:
        diagnostics_file = raw.get("meta", {}).get("diagnostics_file", "") if isinstance(raw, dict) else ""
    data = load_json(diagnostics_file, {}) if diagnostics_file else {}
    events = data.get("events", []) if isinstance(data, dict) else []
    detail_events = [
        event
        for event in events
        if isinstance(event, dict)
        and (
            event.get("detail_state")
            or event.get("network_capture_enabled") is not None
            or event.get("parsed_from_network") is not None
        )
    ]
    states = Counter(str(event.get("detail_state") or "unknown") for event in detail_events)
    return {
        "path": diagnostics_file,
        "event_count": len(events),
        "detail_event_count": len(detail_events),
        "detail_states": dict(states),
        "network_capture_events": sum(1 for event in detail_events if event.get("network_capture_enabled")),
        "network_parser_success_count": sum(1 for event in detail_events if event.get("parsed_from_network")),
        "screenshot_count": len(data.get("screenshots", []) or []) if isinstance(data, dict) else 0,
    }


def map_gemini_to_summary(ai_output: dict[str, Any]) -> dict[str, Any]:
    return {
        "high_frequency_views": as_text_list(ai_output.get("top_findings", []) or ai_output.get("core_trends", []), 8),
        "user_pain_points": as_text_list(ai_output.get("hidden_comment_insights", []) or ai_output.get("key_viewpoints", []), 8),
        "recommend_reasons": as_text_list(ai_output.get("cognitive_upgrades", []) or ai_output.get("report_recommendations", []), 8),
        "avoid_pitfalls": as_text_list(ai_output.get("risk_notes", []), 8),
        "keyword_trends": as_text_list(ai_output.get("trend_clusters", []) or ai_output.get("hot_topics", []), 12),
        "trend_tags": as_text_list(ai_output.get("trend_clusters", []) or ai_output.get("highlighted_trends", []), 12),
        "actionable_suggestions": as_text_list(ai_output.get("next_search_keywords", []) or ai_output.get("report_recommendations", []), 10),
        "content_conclusions": as_text_list(ai_output.get("info_gap_rankings", []) or ai_output.get("highlighted_trends", []), 8),
        "kol_analysis": [],
        "comment_sentiment": {"counts": {"positive": 0, "negative": 0, "neutral": 0}},
        "viral_note_teardown": [],
        "competitor_comparison": [],
    }


def build_report(
    normalized: dict[str, Any],
    raw: dict[str, Any] | None = None,
    ai_output: dict[str, Any] | None = None,
    ai_output_markdown: str = "",
    diagnostics_dir: str = "",
) -> dict[str, Any]:
    meta = normalized.get("meta", {})
    observations = normalized.get("observations", [])
    raw = raw or {}
    high_value_comments: list[dict[str, Any]] = []
    for item in observations:
        source = item.get("source", {}) or {}
        for comment in item.get("valuable_comments", []) or []:
            if comment.get("value_score", 0) < 5:
                continue
            high_value_comments.append(
                {
                    **comment,
                    "note_title": source.get("title", item.get("phenomenon", "")),
                    "note_url": source.get("url", ""),
                    "theme": item.get("theme", ""),
                }
            )
    high_value_comments.sort(key=lambda row: (row.get("value_score", 0), len(row.get("comment", ""))), reverse=True)
    high_count = sum(1 for item in observations if item.get("value_level") == "high")
    records_seen = meta.get("records_seen", 0) or 0
    detail_count = meta.get("detail_record_count", 0) or 0
    api_detail_count = meta.get("api_detail_record_count", 0) or 0
    evidence_count = meta.get("evidence_record_count", detail_count + api_detail_count) or 0
    comment_record_count = meta.get("comment_record_count", 0) or 0
    quality_warning = ""
    if not meta.get("sample_fixture"):
        if meta.get("collection_incomplete"):
            quality_warning = (
                f"采集链路不完整：详情成功 {meta.get('detail_success_count', 0)}/"
                f"{records_seen}，低于 70%。请先查看 detail-open-audit，再解读趋势。"
            )
        elif records_seen and evidence_count < max(1, int(records_seen * 0.35)):
            quality_warning = "本轮详情/评论证据比例偏低，报告可能主要来自搜索卡片，请检查小红书是否拦截详情页。"
        elif detail_count == 0 and api_detail_count:
            quality_warning = "本轮页面详情未成功打开，但接口补读拿到部分正文/评论，请优先核对来源链接。"
        elif evidence_count and comment_record_count < max(1, int(evidence_count * 0.35)):
            quality_warning = "本轮抓到评论的帖子比例偏低，评论区证据不足，请谨慎解读。"
    report = {
        "meta": {
            "title": "小红书信息差发现仪表盘",
            "project_root": str(Path(__file__).resolve().parents[1]),
            "topic": meta.get("topic", ""),
            "query": meta.get("query", ""),
            "focus_topics": meta.get("focus_topics", normalized.get("request", {}).get("focus_topics", [])),
            "sort": meta.get("sort", "general"),
            "generated_at": datetime.now(CN_TZ).isoformat(timespec="seconds"),
            "source": "xiaohongshu",
            "observation_count": len(observations),
            "high_value_count": high_count,
            "lead_count": len(normalized.get("leads_to_verify", [])),
            "warning_count": meta.get("warning_count", 0),
            "sample_fixture": meta.get("sample_fixture", False),
            "records_seen": records_seen,
            "detail_record_count": meta.get("detail_record_count", 0),
            "api_detail_record_count": meta.get("api_detail_record_count", 0),
            "evidence_record_count": meta.get("evidence_record_count", 0),
            "detail_attempted_count": meta.get("detail_attempted_count", 0),
            "detail_success_count": meta.get("detail_success_count", 0),
            "detail_success_rate": meta.get("detail_success_rate", 0),
            "collection_incomplete": meta.get("collection_incomplete", False),
            "collection_status": meta.get("collection_status", "unknown"),
            "detail_open_audit_json": meta.get("detail_open_audit_json", ""),
            "detail_open_audit_md": meta.get("detail_open_audit_md", ""),
            "comment_record_count": meta.get("comment_record_count", 0),
            "total_comment_count": meta.get("total_comment_count", 0),
            "fallback_record_count": meta.get("fallback_record_count", 0),
            "skipped_existing_count": meta.get("skipped_existing_count", 0),
            "collected_index": meta.get("collected_index", ""),
            "high_value_comment_count": len(high_value_comments),
            "quality_warning": quality_warning,
            "keyword_count": len(meta.get("queries", []) or raw.get("meta", {}).get("queries", []) or []),
            "json_parser_success_count": sum(
                1
                for item in (raw.get("records", []) or [])
                if item.get("parsed_from_network") or "network parser" in (item.get("extraction_method") or "")
            ),
            "ai_analyzed": bool(ai_output),
            "ai_status": (ai_output or {}).get("meta", {}).get("ai_status", "not_run" if not ai_output else "ok"),
            "ai_error_count": (ai_output or {}).get("meta", {}).get("error_count", 0),
            "ai_error_message": (ai_output or {}).get("meta", {}).get("last_error", ""),
            "ai_errors_path": (ai_output or {}).get("meta", {}).get("errors_path", ""),
        },
        "themes": normalized.get("themes", []),
        "theme_options": normalized.get("theme_options", []),
        "ai_summary": map_gemini_to_summary(ai_output) if ai_output else normalized.get("ai_summary", {}),
        "local_ai_summary": normalized.get("ai_summary", {}),
        "gemini_analysis": ai_output or {},
        "ai_output_markdown": ai_output_markdown,
        "keyword_stats": build_keyword_stats(raw),
        "high_frequency_keywords": build_high_frequency_keywords(normalized.get("structured_notes", []), observations),
        "raw_posts": build_raw_posts(raw),
        "diagnostics_summary": build_diagnostics_summary(raw, diagnostics_dir),
        "structured_notes": normalized.get("structured_notes", []),
        "high_value_comments": high_value_comments[:50],
        "observations": observations,
        "leads_to_verify": normalized.get("leads_to_verify", []),
        "source": {
            "methodology": normalized.get("methodology", {}),
            "request": normalized.get("request", {}),
            "raw_warnings": normalized.get("raw_warnings", []),
            "raw_errors": normalized.get("raw_errors", []),
        },
    }
    return report


HTML_TEMPLATE = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>小红书信息差发现仪表盘</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f4f6f1;
      --panel: #ffffff;
      --panel-2: #eef4ef;
      --ink: #202320;
      --muted: #626a63;
      --line: #d9ded7;
      --accent: #bd3f32;
      --teal: #08776f;
      --gold: #a66a12;
      --violet: #6f4aa8;
      --low: #747b75;
      --shadow: 0 16px 40px rgba(32, 35, 32, .08);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", Arial, sans-serif;
      background: var(--bg);
      color: var(--ink);
      line-height: 1.6;
    }
    button, input, select {
      font: inherit;
    }
    a { color: var(--teal); }
    .page {
      max-width: 1160px;
      margin: 0 auto;
      padding: 22px 16px 56px;
    }
    .top {
      display: grid;
      gap: 18px;
      align-items: end;
      padding: 20px 0 18px;
      border-bottom: 1px solid var(--line);
    }
    .kicker {
      margin: 0 0 8px;
      color: var(--accent);
      font-weight: 700;
      font-size: 14px;
    }
    h1 {
      margin: 0;
      font-size: 30px;
      line-height: 1.15;
      font-weight: 800;
    }
    .subtitle {
      max-width: 760px;
      margin: 12px 0 0;
      color: var(--muted);
      font-size: 15px;
    }
    .actions {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
    }
    .btn {
      border: 1px solid var(--line);
      background: var(--panel);
      color: var(--ink);
      min-height: 38px;
      padding: 8px 12px;
      border-radius: 8px;
      cursor: pointer;
    }
    .btn.primary {
      background: var(--ink);
      color: white;
      border-color: var(--ink);
    }
    .btn:hover { border-color: var(--teal); }
    .summary-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px;
      margin: 18px 0;
    }
    .metric {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 12px;
      min-height: 86px;
    }
    .metric strong {
      display: block;
      font-size: 24px;
      line-height: 1.2;
      color: var(--ink);
      overflow-wrap: anywhere;
    }
    .metric span {
      display: block;
      margin-top: 5px;
      color: var(--muted);
      font-size: 13px;
    }
    .notice {
      display: none;
      margin: 12px 0;
      border: 1px solid rgba(189, 63, 50, .28);
      background: #fff8f6;
      border-radius: 8px;
      padding: 10px 12px;
      color: #783026;
    }
    .insights,
    .comment-board,
    .run-overview,
    .raw-posts {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 14px;
      margin: 14px 0;
    }
    .insights h2,
    .comment-board h2,
    .run-overview h2,
    .raw-posts h2 {
      margin: 0 0 10px;
      font-size: 18px;
    }
    .stat-table,
    .raw-list {
      display: grid;
      gap: 8px;
    }
    .stat-row,
    .raw-item {
      border-top: 1px solid var(--line);
      padding-top: 9px;
    }
    .stat-row:first-child,
    .raw-item:first-child {
      border-top: 0;
      padding-top: 0;
    }
    .stat-row {
      display: grid;
      gap: 6px;
    }
    .stat-row strong,
    .raw-item strong {
      font-size: 15px;
    }
    .mini-metrics,
    .keyword-cloud {
      display: flex;
      flex-wrap: wrap;
      gap: 7px;
      color: var(--muted);
      font-size: 13px;
    }
    .keyword-pill {
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 4px 8px;
      background: #fff;
    }
    .gemini-highlight {
      border-left: 4px solid var(--accent);
      padding-left: 10px;
    }
    .markdown-box {
      white-space: pre-wrap;
      max-height: 360px;
      overflow: auto;
      padding: 10px;
      border-radius: 8px;
      background: #f8faf7;
      border: 1px solid var(--line);
      color: var(--muted);
      font-size: 13px;
    }
    .keyword-workbench {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 14px;
      margin: 14px 0;
    }
    .keyword-workbench h2 {
      margin: 0 0 10px;
      font-size: 18px;
    }
    .keyword-form {
      display: grid;
      gap: 10px;
    }
    .keyword-list {
      display: grid;
      gap: 8px;
      margin-top: 12px;
    }
    .keyword-command {
      font-family: Consolas, "SFMono-Regular", monospace;
      white-space: pre-wrap;
      background: #f0f3ee;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 9px;
      color: var(--ink);
      font-size: 13px;
    }
    .keyword-entry {
      border-top: 1px solid var(--line);
      padding-top: 8px;
    }
    .discovery-dashboard {
      display: grid;
      gap: 14px;
      margin: 16px 0;
    }
    .dashboard-head {
      display: flex;
      flex-wrap: wrap;
      align-items: end;
      justify-content: space-between;
      gap: 10px;
      padding: 14px;
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: var(--shadow);
    }
    .dashboard-head h2 {
      margin: 0;
      font-size: 22px;
      line-height: 1.2;
    }
    .dashboard-head p {
      margin: 6px 0 0;
      color: var(--muted);
      font-size: 13px;
    }
    .dash-section {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 12px;
      box-shadow: var(--shadow);
    }
    .dash-section-wrap {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 12px;
      box-shadow: var(--shadow);
    }
    .dash-section-wrap > summary {
      cursor: pointer;
      color: var(--teal);
      font-weight: 700;
    }
    .dash-section-wrap .dash-section {
      margin-top: 10px;
      border: 0;
      padding: 0;
      box-shadow: none;
    }
    .dash-section-title {
      display: flex;
      justify-content: space-between;
      gap: 10px;
      align-items: center;
      margin-bottom: 10px;
    }
    .dash-section-title h2 {
      margin: 0;
      font-size: 18px;
    }
    .dash-count {
      color: var(--muted);
      font-size: 12px;
    }
    .dash-grid {
      display: grid;
      gap: 10px;
    }
    .dash-card {
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 10px;
      background: #fff;
    }
    .dash-card h3 {
      margin: 0 0 6px;
      font-size: 15px;
      line-height: 1.35;
    }
    .dash-line {
      margin: 0 0 6px;
      color: var(--ink);
      font-size: 13px;
      display: -webkit-box;
      -webkit-line-clamp: 2;
      -webkit-box-orient: vertical;
      overflow: hidden;
    }
    .dash-meta {
      display: flex;
      flex-wrap: wrap;
      gap: 5px;
      color: var(--muted);
      font-size: 12px;
      margin-bottom: 6px;
    }
    .dash-tag {
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 2px 7px;
      background: #f8faf7;
    }
    .dash-rank {
      color: var(--accent);
      font-weight: 800;
    }
    .dash-detail {
      font-size: 13px;
      color: var(--muted);
    }
    .dash-detail summary {
      cursor: pointer;
      color: var(--teal);
    }
    .dash-detail div {
      margin-top: 6px;
      padding-top: 6px;
      border-top: 1px solid var(--line);
      white-space: pre-wrap;
    }
    .source-unavailable {
      color: #8b3b32;
    }
    .high-comment-card {
      border-left: 4px solid var(--accent);
    }
    #run-overview,
    #gemini-analysis,
    #ai-summary,
    #comment-board,
    .layout {
      display: none !important;
    }
    .raw-item details {
      margin-top: 6px;
    }
    .raw-item summary {
      cursor: pointer;
      color: var(--teal);
    }
    .insight-grid {
      display: grid;
      gap: 10px;
    }
    .insight-block {
      background: var(--panel-2);
      border-radius: 8px;
      padding: 10px;
    }
    .insight-block b {
      display: block;
      margin-bottom: 6px;
    }
    .insight-block ul {
      margin: 0;
      padding-left: 18px;
    }
    .comment-rank {
      display: grid;
      gap: 8px;
    }
    .rank-item {
      border-top: 1px solid var(--line);
      padding-top: 8px;
    }
    .rank-item:first-child {
      border-top: 0;
      padding-top: 0;
    }
    .rank-meta {
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      color: var(--muted);
      font-size: 13px;
    }
    .layout {
      display: grid;
      gap: 16px;
    }
    .controls {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 12px;
      box-shadow: var(--shadow);
    }
    .control-row {
      display: grid;
      gap: 10px;
    }
    .control-row + .control-row { margin-top: 10px; }
    .search {
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 10px 12px;
      background: #fff;
    }
    .selects {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 10px;
    }
    select {
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fff;
      padding: 10px;
    }
    .topic-chips {
      display: flex;
      flex-wrap: wrap;
      gap: 7px;
      margin-top: 12px;
      padding-top: 12px;
      border-top: 1px solid var(--line);
    }
    .chip {
      border: 1px solid var(--line);
      background: #fff;
      color: var(--ink);
      border-radius: 999px;
      padding: 6px 10px;
      font-size: 12px;
      min-height: 31px;
      cursor: pointer;
    }
    .chip.active {
      border-color: var(--teal);
      background: #effaf8;
      color: #075d57;
      font-weight: 700;
    }
    .chip.zero {
      color: var(--muted);
      background: #f8f8f6;
    }
    .theme-list {
      display: grid;
      gap: 8px;
      margin-top: 12px;
    }
    .theme-item {
      display: grid;
      gap: 5px;
      border-top: 1px solid var(--line);
      padding-top: 10px;
      cursor: pointer;
    }
    .theme-item:hover .theme-line strong { color: var(--teal); }
    .theme-line {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      font-size: 13px;
      color: var(--muted);
    }
    .bar {
      height: 8px;
      background: var(--panel-2);
      border-radius: 999px;
      overflow: hidden;
    }
    .bar > i {
      display: block;
      height: 100%;
      background: linear-gradient(90deg, var(--teal), var(--gold));
      border-radius: inherit;
    }
    .cards {
      display: grid;
      gap: 14px;
    }
    .card {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 16px;
      box-shadow: var(--shadow);
    }
    .card-head {
      display: grid;
      gap: 10px;
    }
    .badges {
      display: flex;
      gap: 6px;
      flex-wrap: wrap;
    }
    .badge {
      display: inline-flex;
      align-items: center;
      min-height: 25px;
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 3px 9px;
      font-size: 12px;
      color: var(--muted);
      background: #fff;
    }
    .badge.high { color: #8a271e; border-color: rgba(189, 63, 50, .35); background: #fff3f1; }
    .badge.medium { color: #76500e; border-color: rgba(166, 106, 18, .35); background: #fff9ed; }
    .badge.low { color: var(--low); background: #f7f7f5; }
    .badge.theme { color: #075d57; border-color: rgba(8, 119, 111, .28); background: #effaf8; }
    h2 {
      margin: 0;
      font-size: 20px;
      line-height: 1.35;
    }
    .source {
      color: var(--muted);
      font-size: 13px;
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
    }
    .seven {
      display: grid;
      gap: 10px;
      margin-top: 14px;
    }
    .field {
      border-top: 1px solid var(--line);
      padding-top: 10px;
    }
    .field b {
      display: block;
      margin-bottom: 4px;
      font-size: 13px;
      color: var(--accent);
    }
    .field p {
      margin: 0;
    }
    .signals {
      display: flex;
      gap: 6px;
      flex-wrap: wrap;
      margin-top: 12px;
    }
    .signal {
      font-size: 12px;
      color: var(--muted);
      background: var(--panel-2);
      border-radius: 999px;
      padding: 3px 8px;
    }
    .evidence {
      margin-top: 12px;
      padding: 12px;
      border-radius: 8px;
      background: #f8faf8;
      border: 1px solid var(--line);
    }
    .evidence h3 {
      margin: 0 0 8px;
      font-size: 14px;
    }
    .evidence ul {
      margin: 0;
      padding-left: 18px;
    }
    .evidence li { margin: 6px 0; }
    .empty {
      border: 1px dashed var(--line);
      background: rgba(255,255,255,.55);
      border-radius: 8px;
      padding: 28px;
      color: var(--muted);
      text-align: center;
    }
    .leads {
      margin-top: 20px;
    }
    .lead-grid {
      display: grid;
      gap: 8px;
    }
    .lead {
      background: #fff;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 10px 12px;
      color: var(--muted);
      font-size: 14px;
    }
    .file-input { display: none; }
    @media (min-width: 840px) {
      .top {
        grid-template-columns: 1fr auto;
      }
      h1 { font-size: 36px; }
      .summary-grid { grid-template-columns: repeat(8, minmax(0, 1fr)); }
      .layout {
        grid-template-columns: 310px 1fr;
        align-items: start;
      }
      .insight-grid {
        grid-template-columns: repeat(2, minmax(0, 1fr));
      }
      .dash-grid {
        grid-template-columns: repeat(2, minmax(0, 1fr));
      }
      .dash-grid.compact {
        grid-template-columns: repeat(3, minmax(0, 1fr));
      }
      .controls {
        position: sticky;
        top: 14px;
      }
    }
    @media print {
      body { background: #fff; }
      .actions, .controls, .leads { display: none; }
      .page { max-width: none; padding: 0; }
      .card { break-inside: avoid; box-shadow: none; }
      a::after { content: " (" attr(href) ")"; font-size: 11px; color: #555; }
    }
  </style>
</head>
<body>
  <div class="page">
    <header class="top">
      <div>
        <p class="kicker">趋势与认知观察助手</p>
        <h1 id="page-title">小红书信息差发现仪表盘</h1>
        <p class="subtitle" id="subtitle"></p>
      </div>
      <div class="actions">
        <button class="btn" id="import-btn" type="button">导入 JSON</button>
        <button class="btn" id="export-btn" type="button">导出 JSON</button>
        <button class="btn" id="export-md-btn" type="button">导出 Markdown</button>
        <button class="btn" id="export-html-btn" type="button">导出 HTML</button>
        <button class="btn" id="copy-btn" type="button">复制摘要</button>
        <button class="btn primary" id="print-btn" type="button">打印</button>
        <input class="file-input" id="file-input" type="file" accept="application/json,.json">
      </div>
    </header>

    <div class="notice" id="notice"></div>
    <section class="discovery-dashboard" id="discovery-dashboard"></section>
    <section class="summary-grid" id="metrics"></section>
    <section class="keyword-workbench" id="keyword-workbench">
      <h2>关键词采集与 AI 分析</h2>
      <div class="keyword-form">
        <div class="selects">
          <input class="search" id="keyword-input" type="text" placeholder="输入任意关键词">
          <input class="search" id="keyword-max-notes" type="number" min="1" value="5" aria-label="MaxNotes">
        </div>
        <textarea class="search" id="keyword-bulk" rows="3" placeholder="也可以一行一个关键词，批量追加"></textarea>
        <div class="actions">
          <button class="btn primary" id="keyword-add-btn" type="button">添加关键词</button>
          <button class="btn" id="keyword-bulk-btn" type="button">批量追加</button>
          <button class="btn" id="keyword-copy-batch-btn" type="button">复制批量命令</button>
          <button class="btn" id="keyword-download-cmd-btn" type="button">下载批处理</button>
          <button class="btn" id="keyword-clear-btn" type="button">清空</button>
        </div>
        <div class="keyword-command" id="keyword-batch-command">添加关键词后生成批量执行命令。</div>
        <div class="keyword-list" id="keyword-list"></div>
      </div>
    </section>
    <section class="run-overview" id="run-overview"></section>
    <section class="insights" id="gemini-analysis"></section>
    <section class="insights" id="ai-summary"></section>
    <section class="comment-board" id="comment-board"></section>

    <main class="layout">
      <aside class="controls">
        <div class="control-row">
          <input class="search" id="search" type="search" placeholder="搜索现象、逻辑、信号词">
        </div>
        <div class="control-row selects">
          <select id="theme-filter" aria-label="按主题筛选"></select>
          <select id="value-filter" aria-label="按价值等级筛选">
            <option value="all">全部价值等级</option>
            <option value="high">高价值</option>
            <option value="medium">中价值</option>
            <option value="low">低价值</option>
          </select>
        </div>
        <div class="topic-chips" id="topic-chips" aria-label="主题按钮"></div>
        <div class="theme-list" id="theme-list"></div>
      </aside>

      <section>
        <div class="cards" id="cards"></div>
        <section class="leads" id="leads"></section>
        <section class="raw-posts" id="raw-posts"></section>
      </section>
    </main>
  </div>

  <script id="app-data" type="application/json">__DATA__</script>
  <script>
    const state = {
      data: normalizeImported(JSON.parse(document.getElementById('app-data').textContent)),
      theme: 'all',
      value: 'all',
      search: ''
    };
    let keywordQueue = JSON.parse(localStorage.getItem('xhsTrendKeywordQueue') || '[]');

    function normalizeImported(data) {
      if (data && Array.isArray(data.observations)) {
        return data;
      }
      if (data && Array.isArray(data.records)) {
        return {
          meta: {
            title: '原始小红书数据预览',
            topic: data.meta?.request_topic || '导入的 raw-xhs.json',
            query: (data.meta?.queries || []).join(' / '),
            generated_at: data.meta?.collected_at || '',
            observation_count: data.records.length,
            high_value_count: 0,
            lead_count: 0,
            warning_count: (data.errors || []).length + (data.warnings || []).length
          },
          themes: [],
          theme_options: [],
          observations: data.records.map((record, index) => ({
            phenomenon: record.title || `原始记录 ${index + 1}`,
            surface_appearance: record.card_text || record.text || '这是原始记录，尚未经过趋势分析。',
            underlying_logic: '请先运行归一化脚本，页面才能显示背后逻辑。',
            why_recent: '原始数据没有分析字段。',
            trend_reflected: '原始数据没有分析字段。',
            possibly_overstated: '需要归一化后判断。',
            worth_deeper: '需要归一化后判断。',
            theme: '原始数据',
            signals: [],
            research_score: 0,
            value_level: 'low',
            confidence: 'low',
            source: record,
            evidence: []
          })),
          leads_to_verify: [],
          source: { raw_import: true }
        };
      }
      return {
        meta: {
          title: '请求 JSON 预览',
          topic: data?.meta?.topic || '导入的 request.json',
          query: data?.query || '',
          generated_at: data?.meta?.created_at || '',
          observation_count: 0,
          high_value_count: 0,
          lead_count: 0,
          warning_count: 0
        },
        themes: [],
        theme_options: [],
        observations: [],
        leads_to_verify: [],
        source: { request_import: data || {} }
      };
    }

    function text(value) {
      return value === null || value === undefined ? '' : String(value);
    }

    function levelLabel(level) {
      return { high: '高价值', medium: '中价值', low: '低价值' }[level] || level || '未知';
    }

    function metric(label, value) {
      return `<div class="metric"><strong>${escapeHtml(value)}</strong><span>${escapeHtml(label)}</span></div>`;
    }

    function statusLabel(status) {
      return {
        ok: '完成',
        partial: '部分完成',
        not_run: '未运行',
        missing_api_key: '未配置',
        failed: '失败',
        timeout: '超时'
      }[status] || status || '未知';
    }

    function escapeHtml(value) {
      return text(value).replace(/[&<>"']/g, char => ({
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#039;'
      }[char]));
    }

    function psQuote(value) {
      return text(value).replace(/"/g, '`"');
    }

    function keywordMaxNotes() {
      const raw = Number(document.getElementById('keyword-max-notes')?.value || 5);
      return Number.isFinite(raw) && raw > 0 ? Math.floor(raw) : 5;
    }

    function keywordSingleCommand(keyword) {
      return `.\\trend_xhs_observer.cmd -Query "${psQuote(keyword)}" -MaxNotes ${keywordMaxNotes()} -AIAnalyze`;
    }

    function keywordBatchCommand() {
      if (!keywordQueue.length) return '';
      const joined = keywordQueue.map(item => item.keyword).join(';');
      return `.\\run_keyword_batch.cmd -Keywords "${psQuote(joined)}" -MaxNotes ${keywordMaxNotes()}`;
    }

    function projectRootCdCommand() {
      const root = text(state.data.meta?.project_root).replace(/"/g, '');
      return root ? `cd /d "${root}"` : 'cd /d "%~dp0"';
    }

    function saveKeywordQueue() {
      localStorage.setItem('xhsTrendKeywordQueue', JSON.stringify(keywordQueue));
    }

    async function copyKeywordText(value) {
      if (!value) return;
      try {
        await navigator.clipboard.writeText(value);
        alert('命令已复制到剪贴板');
      } catch {
        prompt('复制这段命令：', value);
      }
    }

    function addKeywordValue(keyword) {
      const cleaned = text(keyword).trim();
      if (!cleaned) return false;
      if (!keywordQueue.some(item => item.keyword === cleaned)) {
        keywordQueue.push({ keyword: cleaned });
        saveKeywordQueue();
      }
      return true;
    }

    function addKeywordFromInput() {
      const input = document.getElementById('keyword-input');
      if (addKeywordValue(input.value)) {
        input.value = '';
        renderKeywordQueue();
      } else {
        alert('请输入关键词');
      }
    }

    function addBulkKeywords() {
      const input = document.getElementById('keyword-bulk');
      const values = text(input.value).split(/[;\r\n]+/).map(item => item.trim()).filter(Boolean);
      let added = false;
      for (const keyword of values) {
        added = addKeywordValue(keyword) || added;
      }
      input.value = '';
      renderKeywordQueue();
      if (!values.length) alert('请输入至少一个关键词');
    }

    function downloadKeywordBatchCmd() {
      const command = keywordBatchCommand();
      if (!command) {
        alert('请先添加关键词');
        return;
      }
      const content = ['@echo off', projectRootCdCommand(), command, 'pause', ''].join('\r\n');
      const blob = new Blob([content], { type: 'application/octet-stream' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = 'run_xhs_keywords.cmd';
      a.click();
      URL.revokeObjectURL(url);
    }

    function renderKeywordQueue() {
      const list = document.getElementById('keyword-list');
      const batchBox = document.getElementById('keyword-batch-command');
      if (!list || !batchBox) return;
      batchBox.textContent = keywordBatchCommand() || '添加关键词后生成批量执行命令。';
      if (!keywordQueue.length) {
        list.innerHTML = '<div class="keyword-entry"><span>还没有关键词。</span></div>';
        return;
      }
      list.innerHTML = keywordQueue.map((item, index) => {
        const command = keywordSingleCommand(item.keyword);
        return `
          <div class="keyword-entry">
            <strong>${index + 1}. ${escapeHtml(item.keyword)}</strong>
            <div class="keyword-command">${escapeHtml(command)}</div>
            <div class="actions">
              <button class="btn" type="button" data-copy-keyword-command="${index}">复制单条命令</button>
              <button class="btn" type="button" data-remove-keyword="${index}">移除</button>
            </div>
          </div>
        `;
      }).join('');
    }

    function compactText(value, limit = 96) {
      const cleaned = text(value).replace(/\s+/g, ' ').trim();
      return cleaned.length > limit ? `${cleaned.slice(0, limit - 1)}…` : cleaned;
    }

    function asList(value) {
      return Array.isArray(value) ? value.filter(Boolean) : [];
    }

    function itemText(item) {
      if (typeof item === 'string') return item;
      if (!item || typeof item !== 'object') return text(item);
      const keys = ['title', 'one_line', 'summary', 'comment', 'why_high_value', 'why_valuable', 'reason', 'evidence', 'real_logic', 'underlying_logic', 'why_now', 'trend', 'reflected_trend', 'topic', 'cluster'];
      return keys.map(key => {
        const value = item[key];
        return Array.isArray(value) ? value.join(' / ') : value;
      }).filter(Boolean).join('；');
    }

    function firstValue(item, keys, fallback = '') {
      if (!item || typeof item !== 'object') return fallback;
      for (const key of keys) {
        const value = item[key];
        if (Array.isArray(value) && value.length) return value.filter(Boolean).join(' / ');
        if (value !== undefined && value !== null && String(value).trim()) return String(value);
      }
      return fallback;
    }

    function tagHtml(tags) {
      return asList(tags).slice(0, 5).map(tag => `<span class="dash-tag">${escapeHtml(tag)}</span>`).join('');
    }

    function sourceUrlForNote(noteId) {
      const post = rawPostFor(noteId);
      return post.url || '';
    }

    function detailHtml(lines, url = '', showSourceStatus = false) {
      const body = asList(lines).filter(Boolean).map(line => `<div>${escapeHtml(line)}</div>`).join('');
      const link = url
        ? `<div><a href="${escapeHtml(url)}" target="_blank" rel="noreferrer">打开来源</a></div>`
        : showSourceStatus
          ? '<div class="source-unavailable">来源链接不可用</div>'
          : '';
      return `<details class="dash-detail"><summary>展开详情</summary>${body}${link}</details>`;
    }

    function dashboardCard(card) {
      return `
        <article class="dash-card ${card.className || ''}">
          <h3><span class="dash-rank">${escapeHtml(card.rank || '')}</span>${card.rank ? ' ' : ''}${escapeHtml(compactText(card.title || '未命名发现', 46))}</h3>
          <p class="dash-line">${escapeHtml(compactText(card.line || '暂无结论', 120))}</p>
          <div class="dash-meta">${tagHtml(card.tags || [])}</div>
          ${detailHtml(card.details || [], card.url || '', Boolean(card.showSourceStatus))}
        </article>
      `;
    }

    function dashboardSection(code, title, cards, compact = false, collapsed = false) {
      const visibleCards = cards.length ? cards : [{
        title: '暂无足够信号',
        line: '本轮样本还不够支撑这个排行榜。',
        tags: ['等待更多样本'],
        details: []
      }];
      const body = `
        <section class="dash-section">
          <div class="dash-section-title">
            <h2>${escapeHtml(code)} ${escapeHtml(title)}</h2>
            <span class="dash-count">${visibleCards.length} 条</span>
          </div>
          <div class="dash-grid ${compact ? 'compact' : ''}">${visibleCards.map(dashboardCard).join('')}</div>
        </section>`;
      if (!collapsed) return body;
      return `<details class="dash-section-wrap"><summary>${escapeHtml(code)} ${escapeHtml(title)} · ${visibleCards.length} 条</summary>${body}</details>`;
    }

    function postEngagement(post) {
      return Number(post.like_count || 0) + Number(post.collect_count || 0) * 2 + Number(post.comment_count || 0) * 3;
    }

    function rawPostFor(noteId) {
      return (state.data.raw_posts || []).find(post => post.note_id && post.note_id === noteId) || {};
    }

    function evidenceDetails(item, fields = []) {
      const details = [];
      for (const key of fields) {
        const value = item?.[key];
        if (Array.isArray(value)) {
          for (const row of value.slice(0, 4)) details.push(itemText(row));
        } else if (value) {
          details.push(value);
        }
      }
      return details.filter(Boolean);
    }

    function collectDashboardComments() {
      const rows = [];
      const seen = new Set();
      const add = (comment, score, source, details, url = '') => {
        const value = compactText(comment, 180);
        if (!value || seen.has(value)) return;
        seen.add(value);
        rows.push({ comment: value, score, source, details, url });
      };

      for (const item of asList(state.data.gemini_analysis?.high_value_comments)) {
        add(
          item.comment || itemText(item),
          Number(item.score || 90),
          item.source_title || item.topic || 'Gemini 高价值评论',
          [item.why_high_value || item.reason || itemText(item)],
          sourceUrlForNote(item.note_id)
        );
      }
      for (const item of asList(state.data.gemini_analysis?.hidden_comment_insights)) {
        add(
          item.one_line || itemText(item),
          85,
          item.title || '评论区隐藏信息',
          evidenceDetails(item, ['evidence']).concat(item.confidence ? `置信度：${item.confidence}` : ''),
          sourceUrlForNote(item.note_id)
        );
      }
      for (const item of asList(state.data.gemini_analysis?.high_score_comment_excerpts)) {
        add(item.comment || itemText(item), 80, item.topic || 'Gemini 高价值评论', [item.why_high_value || item.reason || itemText(item)]);
      }
      for (const item of asList(state.data.high_value_comments)) {
        add(item.comment, Number(item.value_score || 0) + 60, item.note_title || item.theme || '高价值评论', [item.reason, asList(item.tags).join(' / ')], item.note_url || '');
      }
      for (const obs of asList(state.data.observations)) {
        const source = obs.source || {};
        for (const item of asList(obs.valuable_comments)) {
          add(item.comment, Number(item.value_score || 0) + 40, source.title || obs.phenomenon, [item.reason, asList(item.tags).join(' / ')], source.url || '');
        }
      }
      return rows.sort((a, b) => b.score - a.score).slice(0, 8).map((item, index) => ({
        rank: `#${index + 1}`,
        title: item.source,
        line: item.comment,
        tags: [`评论价值 ${item.score}`, '独立展示'],
        details: item.details,
        url: item.url,
        className: 'high-comment-card',
        showSourceStatus: true
      }));
    }

    function buildTopFindings() {
      const aiCards = asList(state.data.gemini_analysis?.top_findings).slice(0, 5).map((item, index) => ({
        rank: `#${index + 1}`,
        title: item.title || firstValue(item, ['one_line'], '未命名发现'),
        line: firstValue(item, ['one_line', 'why_valuable'], itemText(item)),
        tags: ['AI精选', item.confidence || '待核验'],
        details: [item.why_valuable, ...evidenceDetails(item, ['evidence'])],
        url: sourceUrlForNote(item.note_id),
        showSourceStatus: true
      }));
      if (aiCards.length) return aiCards;
      return asList(state.data.observations)
        .slice()
        .sort((a, b) => Number(b.research_score || 0) - Number(a.research_score || 0))
        .slice(0, 5)
        .map((item, index) => {
          const source = item.source || {};
          return {
            rank: `#${index + 1}`,
            title: item.phenomenon || source.title,
            line: item.underlying_logic || item.trend_reflected || item.surface_appearance,
            tags: [item.theme, `价值 ${item.research_score || 0}`, item.confidence, `${source.like_count || 0}赞`],
            details: [item.surface_appearance, item.why_recent, item.possibly_overstated, item.worth_deeper],
            url: source.url || '',
            showSourceStatus: true
          };
        });
    }

    function buildInfoGapCards() {
      const cards = [];
      for (const item of asList(state.data.gemini_analysis?.info_gap_rankings)) {
        cards.push({
          title: item.title || '未命名信息差',
          line: item.real_logic || item.one_line || itemText(item),
          tags: ['信息差', item.overstated_risk ? '需核验' : 'AI'],
          details: [item.surface, item.why_now, item.trend, item.overstated_risk, item.dig_deeper, ...evidenceDetails(item, ['evidence'])],
          url: sourceUrlForNote(item.note_id),
          showSourceStatus: Boolean(item.note_id)
        });
      }
      for (const item of asList(state.data.gemini_analysis?.cognitive_upgrades)) {
        cards.push({
          title: item.title || '认知升级点',
          line: item.one_line || item.from_to || itemText(item),
          tags: ['认知升级'],
          details: [item.from_to, ...evidenceDetails(item, ['evidence'])],
          url: sourceUrlForNote(item.note_id),
          showSourceStatus: Boolean(item.note_id)
        });
      }
      for (const item of asList(state.data.gemini_analysis?.core_trends)) {
        cards.push({
          title: item.title || '未命名信息差',
          line: item.underlying_logic || item.summary,
          tags: ['Gemini', item.exaggeration_risk ? '有夸大风险' : '待核验'],
          details: [item.surface_appearance, item.why_now, item.reflected_trend, item.exaggeration_risk, asList(item.evidence).join(' / ')]
        });
      }
      for (const item of asList(state.data.observations)) {
        cards.push({
          title: item.phenomenon,
          line: item.underlying_logic,
          tags: [item.theme, `分数 ${item.research_score || 0}`],
          details: [item.surface_appearance, item.why_recent, item.trend_reflected, item.possibly_overstated],
          url: item.source?.url || ''
        });
      }
      return cards.slice(0, 8).map((item, index) => ({ ...item, rank: `#${index + 1}` }));
    }

    function buildTrendClusters() {
      const cards = asList(state.data.gemini_analysis?.trend_clusters).map((item, index) => ({
        rank: `#${index + 1}`,
        title: item.cluster || item.title || '未命名趋势簇',
        line: item.one_line || itemText(item),
        tags: asList(item.signals).concat([item.confidence || '', `样本 ${item.sample_count || '-'}`]),
        details: [`代表信号：${asList(item.signals).join(' / ') || '暂无'}`]
      }));
      for (const theme of asList(state.data.themes)) {
        cards.push({
          title: theme.theme,
          line: `样本 ${theme.count || 0} 条，均分 ${theme.avg_score || '-'}`,
          tags: asList(theme.top_signals).slice(0, 5),
          details: [`代表信号：${asList(theme.top_signals).join(' / ') || '暂无'}`]
        });
      }
      for (const topic of asList(state.data.gemini_analysis?.highlighted_trends).concat(asList(state.data.gemini_analysis?.hot_topics))) {
        cards.push({
          title: itemText(topic),
          line: 'Gemini 标记为值得关注的趋势信号。',
          tags: ['趋势标签'],
          details: [itemText(topic)]
        });
      }
      return cards.slice(0, 10).map((item, index) => ({ ...item, rank: `#${index + 1}` }));
    }

    function buildCounterintuitiveCards() {
      const cards = [];
      const counterWords = /反直觉|反常识|不是|反而|便宜|责任|不行|为什么|竟然|不信|难以|无法/;
      for (const item of asList(state.data.gemini_analysis?.counterintuitive_findings)) {
        cards.push({
          title: item.title || '反常识发现',
          line: item.one_line || item.why_counterintuitive || itemText(item),
          tags: ['反常识', item.confidence || '待核验'],
          details: [item.why_counterintuitive, ...evidenceDetails(item, ['evidence'])],
          url: sourceUrlForNote(item.note_id),
          showSourceStatus: Boolean(item.note_id)
        });
      }
      for (const item of asList(state.data.gemini_analysis?.controversies)) {
        cards.push({
          title: item.title || '争议话题',
          line: item.why_it_matters || itemText(item),
          tags: ['争议', '需核验'],
          details: asList(item.sides).concat(evidenceDetails(item, ['evidence'])),
          url: sourceUrlForNote(item.note_id),
          showSourceStatus: Boolean(item.note_id)
        });
      }
      for (const item of asList(state.data.gemini_analysis?.core_trends)) {
        const joined = [item.title, item.summary, item.underlying_logic, item.exaggeration_risk].join(' ');
        if (counterWords.test(joined)) {
          cards.push({
            title: item.title,
            line: item.underlying_logic || item.summary,
            tags: ['反常识', '趋势'],
            details: [item.surface_appearance, item.why_now, item.exaggeration_risk]
          });
        }
      }
      for (const comment of collectDashboardComments()) {
        if (counterWords.test(comment.line)) {
          cards.push({
            title: comment.title,
            line: comment.line,
            tags: ['评论反例'],
            details: comment.details,
            url: comment.url
          });
        }
      }
      return cards.slice(0, 8).map((item, index) => ({ ...item, rank: `#${index + 1}` }));
    }

    function buildRawPostCards() {
      return asList(state.data.raw_posts)
        .slice()
        .sort((a, b) => postEngagement(b) - postEngagement(a))
        .slice(0, 12)
        .map((post, index) => ({
          rank: `#${index + 1}`,
          title: post.title || '未命名帖子',
          line: asList(post.comments)[0] || post.extraction_method || '暂无正文/评论摘要',
          tags: [`赞 ${post.like_count ?? '-'}`, `藏 ${post.collect_count ?? '-'}`, `评 ${post.comment_count ?? '-'}`, post.detail_opened ? '已进详情' : '搜索卡片', post.source_url_valid ? '来源可打开' : '来源链接不可用'],
          details: [`作者：${post.author || '-'}`, `关键词：${post.keyword || '-'}`, `采集方式：${post.extraction_method || '-'}`, `来源状态：${post.source_url_valid ? post.report_source_url_type : '不可用'}`, asList(post.comments).slice(0, 5).join('\n')],
          url: post.url || '',
          showSourceStatus: true
        }));
    }

    function renderDiscoveryDashboard() {
      const box = document.getElementById('discovery-dashboard');
      const meta = state.data.meta || {};
      const comments = collectDashboardComments();
      const topFindings = buildTopFindings();
      const infoGaps = buildInfoGapCards();
      const clusters = buildTrendClusters();
      const counter = buildCounterintuitiveCards();
      const rawPosts = buildRawPostCards();
      box.innerHTML = `
        <div class="dashboard-head">
          <div>
            <h2>小红书信息差发现仪表盘</h2>
            <p>${escapeHtml(meta.query || meta.topic || '未命名关键词')} · 默认只看结论，展开再看证据</p>
          </div>
          <div class="dash-meta">
            <span class="dash-tag">发现 ${topFindings.length}</span>
            <span class="dash-tag">评论 ${comments.length}</span>
            <span class="dash-tag">原帖 ${rawPosts.length}</span>
          </div>
        </div>
        ${dashboardSection('P1', '今日最有价值发现', topFindings)}
        ${dashboardSection('P2', '高赞评论排行榜', comments)}
        ${dashboardSection('P3', '信息差排行榜', infoGaps)}
        ${dashboardSection('P4', '趋势聚类', clusters, true)}
        ${dashboardSection('P5', '反常识发现', counter)}
        ${dashboardSection('P6', '原始帖子列表', rawPosts, true, true)}
      `;
    }

    function render() {
      const data = state.data;
      const meta = data.meta || {};
      document.getElementById('page-title').textContent = '小红书信息差发现仪表盘';
      const focusText = Array.isArray(meta.focus_topics) && meta.focus_topics.length ? ` · 关注 ${meta.focus_topics.length} 个方向` : '';
      document.getElementById('subtitle').textContent = `${meta.query || meta.topic || '未提供查询'} · 样本 ${meta.records_seen || (data.raw_posts || []).length || 0}${focusText} · ${meta.generated_at || ''}`;
      const ai = data.gemini_analysis || {};
      const topFindingCount = (ai.top_findings || []).length || (data.observations || []).length;
      const commentInsightCount = (ai.high_value_comments || []).length + (ai.hidden_comment_insights || []).length + (data.high_value_comments || []).length;

      document.getElementById('metrics').innerHTML = [
        metric('发现', topFindingCount),
        metric('高价值评论', commentInsightCount),
        metric('原始帖子', (data.raw_posts || []).length),
        metric('真实进帖', meta.detail_record_count || 0),
        metric('JSON解析', meta.json_parser_success_count || 0),
        metric('评论数', meta.total_comment_count || 0),
        metric('趋势簇', (data.themes || []).length),
        metric('AI状态', statusLabel(meta.ai_status || 'not_run')),
        metric('诊断警告', meta.warning_count || 0)
      ].join('');

      const notice = document.getElementById('notice');
      const warnings = data.source?.raw_warnings || [];
      const errors = data.source?.raw_errors || [];
      if (meta.sample_fixture) {
        notice.style.display = 'block';
        notice.textContent = '当前页面来自 smoke test 样例数据，不代表实时小红书结果。运行正式采集命令后会生成真实报告。';
      } else if (meta.ai_status && !['ok', 'partial', 'not_run'].includes(meta.ai_status)) {
        notice.style.display = 'block';
        const reason = meta.ai_error_message ? `原因：${meta.ai_error_message}` : `状态：${meta.ai_status}`;
        notice.textContent = `Gemini AI 分析未完成，已降级为本地统计仪表盘。${reason}`;
      } else if (meta.ai_status === 'partial') {
        notice.style.display = 'block';
        notice.textContent = `Gemini AI 分析部分完成，页面同时保留本地统计和原始帖子。错误数：${meta.ai_error_count || 0}`;
      } else if (meta.quality_warning) {
        notice.style.display = 'block';
        notice.textContent = meta.quality_warning;
      } else if (warnings.length || errors.length) {
        notice.style.display = 'block';
        notice.textContent = `采集存在 ${warnings.length + errors.length} 条警告或错误，页面仍保留来源链接，请交叉验证。`;
      } else {
        notice.style.display = 'none';
      }

      renderDiscoveryDashboard();
      renderFilters();
      renderRunOverview();
      renderGeminiAnalysis();
      renderTopicChips();
      renderThemes();
      renderAISummary();
      renderCommentBoard();
      renderCards();
      renderLeads();
      renderRawPosts();
    }

    function themeOptions() {
      const options = state.data.theme_options || [];
      const fromThemes = (state.data.themes || []).map(item => item.theme).filter(Boolean);
      const fromCards = (state.data.observations || []).map(item => item.theme).filter(Boolean);
      return Array.from(new Set([...options, ...fromThemes, ...fromCards]));
    }

    function themeCounts() {
      const counts = {};
      for (const item of state.data.observations || []) {
        if (!item.theme) continue;
        counts[item.theme] = (counts[item.theme] || 0) + 1;
      }
      return counts;
    }

    function renderFilters() {
      const select = document.getElementById('theme-filter');
      const themes = themeOptions();
      const current = state.theme;
      select.innerHTML = ['<option value="all">全部主题</option>']
        .concat(themes.map(theme => `<option value="${escapeHtml(theme)}">${escapeHtml(theme)}</option>`))
        .join('');
      select.value = themes.includes(current) ? current : 'all';
      state.theme = select.value;
    }

    function renderTopicChips() {
      const container = document.getElementById('topic-chips');
      const counts = themeCounts();
      const themes = themeOptions();
      if (!themes.length) {
        container.innerHTML = '';
        return;
      }
      const chips = [
        `<button class="chip ${state.theme === 'all' ? 'active' : ''}" type="button" data-theme-chip="all">全部 ${(state.data.observations || []).length}</button>`
      ];
      for (const theme of themes) {
        const count = counts[theme] || 0;
        chips.push(`<button class="chip ${state.theme === theme ? 'active' : ''} ${count === 0 ? 'zero' : ''}" type="button" data-theme-chip="${escapeHtml(theme)}">${escapeHtml(theme)} ${count}</button>`);
      }
      container.innerHTML = chips.join('');
    }

    function renderThemes() {
      const container = document.getElementById('theme-list');
      const themes = state.data.themes || [];
      if (!themes.length) {
        container.innerHTML = '<div class="theme-item"><div class="theme-line"><span>暂无主题统计</span></div></div>';
        return;
      }
      const max = Math.max(...themes.map(item => item.count || 0), 1);
      container.innerHTML = themes.map(item => {
        const width = Math.max(8, Math.round((item.count || 0) / max * 100));
        const signals = (item.top_signals || []).slice(0, 4).join(' / ');
        return `
          <div class="theme-item" role="button" tabindex="0" data-theme-list="${escapeHtml(item.theme)}">
            <div class="theme-line"><strong>${escapeHtml(item.theme)}</strong><span>${item.count || 0} 条</span></div>
            <div class="bar"><i style="width:${width}%"></i></div>
            <div class="theme-line"><span>${escapeHtml(signals || '暂无信号词')}</span><span>均分 ${escapeHtml(item.avg_score || '-')}</span></div>
          </div>
        `;
      }).join('');
    }

    function analysisItemText(item) {
      if (typeof item === 'string') return item;
      if (!item || typeof item !== 'object') return text(item);
      const keys = [
        'title',
        'summary',
        'comment',
        'why_high_value',
        'concept',
        'explanation',
        'why_it_matters',
        'topic',
        'reason',
        'evidence',
        'underlying_logic',
        'why_now',
        'reflected_trend',
        'exaggeration_risk',
        'next_search_angle',
        'follow_up_keywords'
      ];
      return keys.map(key => {
        const value = item[key];
        if (Array.isArray(value)) return value.filter(Boolean).join(' / ');
        return value;
      }).filter(Boolean).join('；');
    }

    function listHtml(items) {
      const values = (items || []).map(analysisItemText).filter(Boolean).slice(0, 8);
      if (!values.length) return '<li>暂无足够证据。</li>';
      return values.map(item => `<li>${escapeHtml(item)}</li>`).join('');
    }

    function renderRunOverview() {
      const box = document.getElementById('run-overview');
      const stats = state.data.keyword_stats || [];
      const diagnostics = state.data.diagnostics_summary || {};
      const keywords = state.data.high_frequency_keywords || [];
      if (!stats.length && !keywords.length && !diagnostics.path) {
        box.style.display = 'none';
        return;
      }
      box.style.display = 'block';
      const rows = stats.map(item => `
        <div class="stat-row">
          <strong>${escapeHtml(item.keyword || '未标注关键词')}</strong>
          <div class="mini-metrics">
            <span>采集 ${escapeHtml(item.collected_count || 0)}</span>
            <span>详情 ${escapeHtml(item.detail_opened_count || 0)}</span>
            <span>JSON ${escapeHtml(item.json_parsed_count || 0)}</span>
            <span>评论 ${escapeHtml(item.comment_count || 0)}</span>
            <span>接口响应 ${escapeHtml(item.api_payload_count || 0)}</span>
          </div>
        </div>
      `).join('');
      const keywordCloud = keywords.slice(0, 24)
        .map(item => `<span class="keyword-pill">${escapeHtml(item.keyword)} ${escapeHtml(item.count || 0)}</span>`)
        .join('');
      box.innerHTML = `
        <h2>运行与采集概览</h2>
        <div class="insight-grid">
          <div class="insight-block">
            <b>关键词采集统计</b>
            <div class="stat-table">${rows || '<div class="stat-row">暂无关键词统计。</div>'}</div>
          </div>
          <div class="insight-block">
            <b>高频关键词</b>
            <div class="keyword-cloud">${keywordCloud || '<span>暂无高频词。</span>'}</div>
          </div>
          <div class="insight-block">
            <b>Diagnostics 摘要</b>
            <ul>
              <li>事件：${escapeHtml(diagnostics.event_count || 0)}</li>
              <li>详情事件：${escapeHtml(diagnostics.detail_event_count || 0)}</li>
              <li>Network 捕获：${escapeHtml(diagnostics.network_capture_events || 0)}</li>
              <li>Network Parser 成功：${escapeHtml(diagnostics.network_parser_success_count || 0)}</li>
              <li>截图：${escapeHtml(diagnostics.screenshot_count || 0)}</li>
            </ul>
          </div>
        </div>
      `;
    }

    function renderGeminiAnalysis() {
      const box = document.getElementById('gemini-analysis');
      const analysis = state.data.gemini_analysis || {};
      if (!Object.keys(analysis).length) {
        box.style.display = 'none';
        return;
      }
      box.style.display = 'block';
      const trends = (analysis.core_trends || []).slice(0, 8).map(item => `
        <div class="insight-block gemini-highlight">
          <b>${escapeHtml(item.title || item.summary || '未命名趋势')}</b>
          <p>${escapeHtml(item.summary || '')}</p>
          <ul>
            <li>真实逻辑：${escapeHtml(item.underlying_logic || '不足以判断')}</li>
            <li>为什么最近出现：${escapeHtml(item.why_now || '不足以判断')}</li>
            <li>趋势：${escapeHtml(item.reflected_trend || '不足以判断')}</li>
            <li>风险：${escapeHtml(item.exaggeration_risk || '不足以判断')}</li>
          </ul>
        </div>
      `).join('');
      const recommendations = listHtml(analysis.report_recommendations || []);
      const commentExcerpts = listHtml(analysis.high_score_comment_excerpts || analysis.high_value_comments || []);
      const concepts = listHtml(analysis.core_concepts || []);
      const extensions = listHtml(analysis.topic_extensions || []);
      const highlighted = (analysis.highlighted_trends || []).slice(0, 12)
        .map(item => `<span class="keyword-pill">${escapeHtml(analysisItemText(item))}</span>`)
        .join('');
      const md = state.data.ai_output_markdown || '';
      box.innerHTML = `
        <h2>Gemini 趋势分析</h2>
        <div class="keyword-cloud">${highlighted}</div>
        <div class="insight-grid">${trends || '<div class="insight-block">Gemini 未返回核心趋势。</div>'}</div>
        <div class="insight-grid">
          <div class="insight-block">
            <b>高分评论摘录</b>
            <ul>${commentExcerpts}</ul>
          </div>
          <div class="insight-block">
            <b>核心概念科普</b>
            <ul>${concepts}</ul>
          </div>
          <div class="insight-block">
            <b>主题延伸</b>
            <ul>${extensions}</ul>
          </div>
        </div>
        <div class="insight-block">
          <b>可直接用于报告的建议</b>
          <ul>${recommendations}</ul>
        </div>
        ${md ? `<details><summary>查看 Gemini Markdown 原文</summary><div class="markdown-box">${escapeHtml(md)}</div></details>` : ''}
      `;
    }

    function renderAISummary() {
      const box = document.getElementById('ai-summary');
      const summary = state.data.ai_summary || {};
      if (!Object.keys(summary).length) {
        box.style.display = 'none';
        return;
      }
      box.style.display = 'block';
      const blocks = [
        ['高频观点', summary.high_frequency_views],
        ['用户真实痛点', summary.user_pain_points],
        ['推荐/不推荐逻辑', summary.recommend_reasons],
        ['避坑点', summary.avoid_pitfalls],
        ['关键词趋势', summary.keyword_trends],
        ['可执行建议', summary.actionable_suggestions],
        ['内容结论', summary.content_conclusions],
      ];
      box.innerHTML = `
        <h2>AI分析摘要</h2>
        <div class="insight-grid">
          ${blocks.map(([title, items]) => `
            <div class="insight-block">
              <b>${escapeHtml(title)}</b>
              <ul>${listHtml(items)}</ul>
            </div>
          `).join('')}
          <div class="insight-block">
            <b>KOL/博主分析</b>
            <ul>${(summary.kol_analysis || []).slice(0, 6).map(item => `<li>${escapeHtml(item.author || '')}：${escapeHtml(item.note_count || 0)} 篇，点赞 ${escapeHtml(item.total_likes || 0)}</li>`).join('') || '<li>暂无足够作者信息。</li>'}</ul>
          </div>
          <div class="insight-block">
            <b>评论情绪</b>
            <ul>
              <li>正向 ${escapeHtml(summary.comment_sentiment?.counts?.positive || 0)}</li>
              <li>负向 ${escapeHtml(summary.comment_sentiment?.counts?.negative || 0)}</li>
              <li>中性 ${escapeHtml(summary.comment_sentiment?.counts?.neutral || 0)}</li>
            </ul>
          </div>
          <div class="insight-block">
            <b>爆款结构拆解</b>
            <ul>${(summary.viral_note_teardown || []).slice(0, 6).map(item => `<li>${escapeHtml(item.title || '')}：${escapeHtml((item.hooks || []).join(' / '))}</li>`).join('') || '<li>暂无足够互动样本。</li>'}</ul>
          </div>
          <div class="insight-block">
            <b>竞品/渠道对比线索</b>
            <ul>${(summary.competitor_comparison || []).slice(0, 6).map(item => `<li>${escapeHtml((item.entities || []).join(' / '))}：${escapeHtml(item.note_title || '')}</li>`).join('') || '<li>暂无明显竞品线索。</li>'}</ul>
          </div>
        </div>
      `;
    }

    function renderCommentBoard() {
      const board = document.getElementById('comment-board');
      const comments = state.data.high_value_comments || [];
      if (!comments.length) {
        board.style.display = 'none';
        return;
      }
      board.style.display = 'block';
      board.innerHTML = `
        <h2>高价值评论榜</h2>
        <div class="comment-rank">
          ${comments.slice(0, 12).map(item => `
            <div class="rank-item">
              <div class="rank-meta">
                <strong>${escapeHtml(item.value_score ?? 0)} 分</strong>
                <span>${escapeHtml((item.tags || []).join(' / ') || '补充信息')}</span>
                <span>${escapeHtml(item.theme || '')}</span>
              </div>
              <p>${escapeHtml(item.comment || '')}</p>
              <div class="rank-meta">
                <span>${escapeHtml(item.reason || '')}</span>
                ${item.note_url ? `<a href="${escapeHtml(item.note_url)}" target="_blank" rel="noreferrer">${escapeHtml(item.note_title || '打开来源')}</a>` : ''}
              </div>
            </div>
          `).join('')}
        </div>
      `;
    }

    function currentCards() {
      const q = state.search.trim().toLowerCase();
      return (state.data.observations || []).filter(item => {
        const themeOk = state.theme === 'all' || item.theme === state.theme;
        const valueOk = state.value === 'all' || item.value_level === state.value;
        const haystack = [
          item.phenomenon,
          item.surface_appearance,
          item.underlying_logic,
          item.why_recent,
          item.trend_reflected,
          item.possibly_overstated,
          item.worth_deeper,
          item.theme,
          ...(item.signals || [])
        ].join(' ').toLowerCase();
        const searchOk = !q || haystack.includes(q);
        return themeOk && valueOk && searchOk;
      });
    }

    function renderCards() {
      const cards = currentCards();
      const container = document.getElementById('cards');
      if (!cards.length) {
        container.innerHTML = '<div class="empty">当前筛选下没有观察卡片。可以换个主题、价值等级或导入新的 JSON。</div>';
        return;
      }
      container.innerHTML = cards.map(item => {
        const source = item.source || {};
        const sourceLink = source.url ? `<a href="${escapeHtml(source.url)}" target="_blank" rel="noreferrer">打开来源</a>` : '';
        const score = item.research_score ?? '-';
        const evidence = (item.evidence || []).slice(0, 5);
        const method = source.extraction_method || '';
        const sourceBadge = source.detail_opened
          ? '来源 详情页'
          : method === 'detail-api fallback'
            ? '来源 接口补读'
            : '来源 搜索卡片';
        const commentBadge = source.comment_source && source.comment_source !== 'none'
          ? `评论 ${source.comment_source}`
          : '评论 未抓到';
        return `
          <article class="card" data-observation-card>
            <div class="card-head">
              <div class="badges">
                <span class="badge ${escapeHtml(item.value_level || 'low')}">${escapeHtml(levelLabel(item.value_level))}</span>
                <span class="badge theme">${escapeHtml(item.theme || '未分类')}</span>
                <span class="badge">参考分 ${escapeHtml(score)}</span>
                <span class="badge">置信度 ${escapeHtml(item.confidence || 'unknown')}</span>
                <span class="badge">${escapeHtml(sourceBadge)}</span>
                <span class="badge">${escapeHtml(commentBadge)}</span>
              </div>
              <h2>${escapeHtml(item.phenomenon || '未命名现象')}</h2>
              <div class="source">
                <span>${escapeHtml(source.query || '')}</span>
                <span>${escapeHtml(source.author || '')}</span>
                <span>${escapeHtml(source.created_at || '')}</span>
                ${sourceLink}
              </div>
            </div>
            <div class="seven">
              ${field('1. 现象', item.phenomenon)}
              ${field('2. 表面看起来是什么', item.surface_appearance)}
              ${field('3. 背后真实逻辑', item.underlying_logic)}
              ${field('4. 为什么最近开始出现', item.why_recent)}
              ${field('5. 它反映了什么趋势', item.trend_reflected)}
              ${field('6. 是否可能被夸大', item.possibly_overstated)}
              ${field('7. 值不值得继续深挖', item.worth_deeper)}
            </div>
            ${(item.signals || []).length ? `<div class="signals">${item.signals.map(signal => `<span class="signal">${escapeHtml(signal)}</span>`).join('')}</div>` : ''}
            ${evidence.length ? `
              <div class="evidence">
                <h3>证据线索</h3>
                <ul>${evidence.map(row => `<li><b>${escapeHtml(row.type || '线索')}：</b>${escapeHtml(row.text || '')}</li>`).join('')}</ul>
              </div>
            ` : ''}
          </article>
        `;
      }).join('');
    }

    function field(label, value) {
      return `<div class="field"><b>${escapeHtml(label)}</b><p>${escapeHtml(value || '暂无')}</p></div>`;
    }

    function renderLeads() {
      const leads = state.data.leads_to_verify || [];
      const container = document.getElementById('leads');
      if (!leads.length) {
        container.innerHTML = '';
        return;
      }
      container.innerHTML = `
        <h2>待核实线索</h2>
        <div class="lead-grid">
          ${leads.slice(0, 20).map(lead => `
            <div class="lead">
              <b>${escapeHtml(lead.title || '未命名线索')}</b>
              <span> · ${escapeHtml(lead.theme || '')} · 分数 ${escapeHtml(lead.score || 0)}</span>
              ${lead.url ? ` · <a href="${escapeHtml(lead.url)}" target="_blank" rel="noreferrer">来源</a>` : ''}
            </div>
          `).join('')}
        </div>
      `;
    }

    function renderRawPosts() {
      const box = document.getElementById('raw-posts');
      const posts = state.data.raw_posts || [];
      if (!posts.length) {
        box.style.display = 'none';
        return;
      }
      box.style.display = 'block';
      box.innerHTML = `
        <h2>原始帖子列表</h2>
        <div class="raw-list">
          ${posts.slice(0, 80).map(post => {
            const comments = (post.comments || []).slice(0, 12);
            return `
              <div class="raw-item">
                <strong>${escapeHtml(post.title || '未命名帖子')}</strong>
                <div class="mini-metrics">
                  <span>${escapeHtml(post.keyword || '')}</span>
                  <span>${escapeHtml(post.author || '')}</span>
                  <span>${escapeHtml(post.publish_time || '')}</span>
                  <span>点赞 ${escapeHtml(post.like_count ?? '')}</span>
                  <span>收藏 ${escapeHtml(post.collect_count ?? '')}</span>
                  <span>评论 ${escapeHtml(post.comment_count ?? '')}</span>
                  <span>详情 ${post.detail_opened ? 'OK' : '未打开'}</span>
                  <span>JSON ${post.parsed_from_network ? 'OK' : 'fallback'}</span>
                </div>
                <div class="mini-metrics">
                  <span>${escapeHtml(post.extraction_method || '')}</span>
                  <span>${escapeHtml(post.comment_source || '')}</span>
                  ${post.url ? `<a href="${escapeHtml(post.url)}" target="_blank" rel="noreferrer">来源</a>` : ''}
                </div>
                <details>
                  <summary>展开评论 ${comments.length}</summary>
                  <ul>${comments.map(comment => `<li>${escapeHtml(comment)}</li>`).join('') || '<li>暂无评论。</li>'}</ul>
                </details>
              </div>
            `;
          }).join('')}
        </div>
      `;
    }

    function makeSummary() {
      const items = (state.data.observations || []).slice(0, 6);
      const lines = [
        `主题：${state.data.meta?.topic || ''}`,
        `观察数：${items.length}`,
        ''
      ];
      for (const item of items) {
        lines.push(`- ${item.phenomenon}`);
        lines.push(`  真实逻辑：${item.underlying_logic}`);
        lines.push(`  值得深挖：${item.worth_deeper}`);
      }
      return lines.join('\n');
    }

    function makeMarkdown() {
      const meta = state.data.meta || {};
      const summary = state.data.ai_summary || {};
      const lines = [
        `# ${meta.title || '趋势与认知观察报告'}`,
        '',
        `- 搜索关键词：${meta.query || ''}`,
        `- 主题：${meta.topic || ''}`,
        `- 关注方向：${Array.isArray(meta.focus_topics) ? meta.focus_topics.join(' / ') : ''}`,
        `- 生成时间：${meta.generated_at || ''}`,
        `- 观察数：${(state.data.observations || []).length}`,
        `- 高价值评论：${(state.data.high_value_comments || []).length}`,
        '',
        '## AI总结',
      ];
      const addList = (title, items) => {
        lines.push('', `### ${title}`);
        const values = (items || []).filter(Boolean);
        if (!values.length) {
          lines.push('- 暂无足够证据');
        } else {
          for (const item of values.slice(0, 10)) lines.push(`- ${item}`);
        }
      };
      addList('高频观点', summary.high_frequency_views);
      addList('用户真实痛点', summary.user_pain_points);
      addList('避坑点', summary.avoid_pitfalls);
      addList('可执行建议', summary.actionable_suggestions);
      lines.push('', '### KOL/博主分析');
      for (const item of (summary.kol_analysis || []).slice(0, 10)) {
        lines.push(`- ${item.author || ''}：${item.note_count || 0} 篇，点赞 ${item.total_likes || 0}`);
      }
      lines.push('', '### 评论情绪');
      const sentiment = summary.comment_sentiment?.counts || {};
      lines.push(`- 正向：${sentiment.positive || 0}`);
      lines.push(`- 负向：${sentiment.negative || 0}`);
      lines.push(`- 中性：${sentiment.neutral || 0}`);
      lines.push('', '### 爆款结构拆解');
      for (const item of (summary.viral_note_teardown || []).slice(0, 10)) {
        lines.push(`- ${item.title || ''}：${(item.hooks || []).join(' / ')}`);
      }
      lines.push('', '### 竞品/渠道对比');
      for (const item of (summary.competitor_comparison || []).slice(0, 10)) {
        lines.push(`- ${(item.entities || []).join(' / ')}：${item.note_title || ''}`);
      }
      lines.push('', '## 高价值评论');
      for (const item of (state.data.high_value_comments || []).slice(0, 20)) {
        lines.push(`- [${item.value_score ?? 0}分] ${item.comment || ''}`);
        if (item.reason) lines.push(`  - 理由：${item.reason}`);
        if (item.note_url) lines.push(`  - 来源：${item.note_url}`);
      }
      lines.push('', '## 观察卡片');
      for (const item of (state.data.observations || []).slice(0, 30)) {
        lines.push('', `### ${item.phenomenon || '未命名现象'}`);
        lines.push(`- 主题：${item.theme || ''}`);
        lines.push(`- 背后逻辑：${item.underlying_logic || ''}`);
        lines.push(`- 趋势：${item.trend_reflected || ''}`);
        if (item.source?.url) lines.push(`- 原文：${item.source.url}`);
      }
      return lines.join('\n');
    }

    document.getElementById('theme-filter').addEventListener('change', event => {
      state.theme = event.target.value;
      renderTopicChips();
      renderCards();
    });
    document.getElementById('topic-chips').addEventListener('click', event => {
      const button = event.target.closest('[data-theme-chip]');
      if (!button) return;
      state.theme = button.dataset.themeChip || 'all';
      document.getElementById('theme-filter').value = state.theme;
      renderTopicChips();
      renderCards();
    });
    document.getElementById('theme-list').addEventListener('click', event => {
      const item = event.target.closest('[data-theme-list]');
      if (!item) return;
      state.theme = item.dataset.themeList || 'all';
      document.getElementById('theme-filter').value = state.theme;
      renderTopicChips();
      renderCards();
    });
    document.getElementById('theme-list').addEventListener('keydown', event => {
      if (event.key !== 'Enter' && event.key !== ' ') return;
      const item = event.target.closest('[data-theme-list]');
      if (!item) return;
      event.preventDefault();
      state.theme = item.dataset.themeList || 'all';
      document.getElementById('theme-filter').value = state.theme;
      renderTopicChips();
      renderCards();
    });
    document.getElementById('value-filter').addEventListener('change', event => {
      state.value = event.target.value;
      renderCards();
    });
    document.getElementById('search').addEventListener('input', event => {
      state.search = event.target.value;
      renderCards();
    });
    document.getElementById('print-btn').addEventListener('click', () => window.print());
    document.getElementById('copy-btn').addEventListener('click', async () => {
      const summary = makeSummary();
      try {
        await navigator.clipboard.writeText(summary);
        document.getElementById('copy-btn').textContent = '已复制';
        setTimeout(() => document.getElementById('copy-btn').textContent = '复制摘要', 1200);
      } catch {
        alert(summary);
      }
    });
    document.getElementById('export-btn').addEventListener('click', () => {
      const blob = new Blob([JSON.stringify(state.data, null, 2)], { type: 'application/json;charset=utf-8' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = 'trend-report.json';
      a.click();
      URL.revokeObjectURL(url);
    });
    document.getElementById('export-md-btn').addEventListener('click', () => {
      const blob = new Blob([makeMarkdown()], { type: 'text/markdown;charset=utf-8' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = 'trend-report.md';
      a.click();
      URL.revokeObjectURL(url);
    });
    document.getElementById('export-html-btn').addEventListener('click', () => {
      const html = '<!doctype html>\n' + document.documentElement.outerHTML;
      const blob = new Blob([html], { type: 'text/html;charset=utf-8' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = 'trend-report.html';
      a.click();
      URL.revokeObjectURL(url);
    });
    document.getElementById('import-btn').addEventListener('click', () => {
      document.getElementById('file-input').click();
    });
    document.getElementById('file-input').addEventListener('change', async event => {
      const file = event.target.files?.[0];
      if (!file) return;
      try {
        const text = await file.text();
        state.data = normalizeImported(JSON.parse(text));
        state.theme = 'all';
        state.value = 'all';
        state.search = '';
        document.getElementById('search').value = '';
        document.getElementById('value-filter').value = 'all';
        render();
      } catch (error) {
        alert(`导入失败：${error.message}`);
      } finally {
        event.target.value = '';
      }
    });

    document.getElementById('keyword-add-btn').addEventListener('click', addKeywordFromInput);
    document.getElementById('keyword-bulk-btn').addEventListener('click', addBulkKeywords);
    document.getElementById('keyword-copy-batch-btn').addEventListener('click', () => copyKeywordText(keywordBatchCommand()));
    document.getElementById('keyword-download-cmd-btn').addEventListener('click', downloadKeywordBatchCmd);
    document.getElementById('keyword-clear-btn').addEventListener('click', () => {
      keywordQueue = [];
      saveKeywordQueue();
      renderKeywordQueue();
    });
    document.getElementById('keyword-max-notes').addEventListener('input', renderKeywordQueue);
    document.getElementById('keyword-input').addEventListener('keydown', event => {
      if (event.key === 'Enter') addKeywordFromInput();
    });
    document.getElementById('keyword-list').addEventListener('click', event => {
      const copyButton = event.target.closest('[data-copy-keyword-command]');
      const removeButton = event.target.closest('[data-remove-keyword]');
      if (copyButton) {
        const index = Number(copyButton.dataset.copyKeywordCommand);
        const keyword = keywordQueue[index]?.keyword;
        if (keyword) copyKeywordText(keywordSingleCommand(keyword));
      }
      if (removeButton) {
        const index = Number(removeButton.dataset.removeKeyword);
        keywordQueue.splice(index, 1);
        saveKeywordQueue();
        renderKeywordQueue();
      }
    });

    render();
    renderKeywordQueue();
  </script>
</body>
</html>
"""


def load_text(path: str | Path, default: str = "") -> str:
    file_path = Path(path)
    if not file_path.exists():
        return default
    return file_path.read_text(encoding="utf-8", errors="replace")


def load_sidecar_outputs(output_dir: str | Path) -> dict[str, Any]:
    out_dir = Path(output_dir)
    json_files = {
        "trend_db_summary": "trend-db-summary.json",
        "observation_report": "observation-report.json",
        "insight_report": "insight-report.json",
        "insights": "insights.json",
        "discovered_keywords": "discovered-keywords.json",
        "keyword_task_plan": "keyword-task-plan.json",
        "trend_loop_health": "trend-loop-health.json",
        "quality_audit": "quality-audit.json",
        "structured_notes_file": "structured-notes.json",
        "ai_output": "ai_output.json",
        "ai_output_summary": "ai_output_summary.json",
        "cookie_health": "cookie-health.json",
        "db_sync_report": "db-sync-report.json",
        "request_draft": "request-draft.json",
    }
    sidecars: dict[str, Any] = {"files": {}}
    for key, filename in json_files.items():
        file_path = out_dir / filename
        exists = file_path.exists()
        sidecars["files"][filename] = {"exists": exists, "path": str(file_path)}
        if exists:
            sidecars[key] = load_json(str(file_path), None)

    text_files = {
        "trend_db_report_markdown": "trend-db-report.md",
        "discovered_keywords_markdown": "discovered-keywords.md",
        "keyword_task_plan_markdown": "keyword-task-plan.md",
        "trend_loop_health_markdown": "trend-loop-health.md",
        "ai_output_markdown_file": "ai_output.md",
        "quality_audit_markdown": "quality-audit.md",
    }
    for key, filename in text_files.items():
        file_path = out_dir / filename
        exists = file_path.exists()
        sidecars["files"][filename] = {"exists": exists, "path": str(file_path)}
        if exists:
            sidecars[key] = load_text(file_path)
    return sidecars


def attach_workbench_data(report: dict[str, Any], output_dir: str | Path) -> dict[str, Any]:
    enriched = dict(report)
    enriched["workbench"] = load_sidecar_outputs(output_dir)
    return enriched


HTML_TEMPLATE = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>趋势情报工作台</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f4f6f8;
      --surface: #ffffff;
      --surface-soft: #f9fafb;
      --text: #162033;
      --muted: #667085;
      --line: #dde3ea;
      --line-soft: #edf1f5;
      --blue: #2367d1;
      --blue-soft: #e9f1ff;
      --green: #16844a;
      --green-soft: #e8f7ef;
      --yellow: #9a6700;
      --yellow-soft: #fff4d6;
      --red: #c73535;
      --red-soft: #fff0f0;
      --ink: #0f172a;
      --shadow: 0 18px 48px rgba(18, 31, 52, 0.08);
      --radius: 8px;
    }
    * { box-sizing: border-box; }
    html { scroll-behavior: smooth; }
    body {
      margin: 0;
      color: var(--text);
      background:
        linear-gradient(180deg, #eef3f8 0, #f7f8fa 360px, var(--bg) 100%);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", "PingFang SC", Arial, sans-serif;
      font-size: 15px;
      line-height: 1.65;
    }
    button, input, textarea, select {
      font: inherit;
    }
    a { color: var(--blue); text-decoration: none; }
    a:hover { text-decoration: underline; }
    .shell {
      width: min(1200px, calc(100vw - 36px));
      margin: 0 auto;
    }
    .top-nav {
      position: sticky;
      top: 0;
      z-index: 20;
      backdrop-filter: blur(16px);
      background: rgba(244, 246, 248, 0.86);
      border-bottom: 1px solid rgba(221, 227, 234, 0.8);
    }
    .nav-inner {
      width: min(1200px, calc(100vw - 36px));
      min-height: 62px;
      margin: 0 auto;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 18px;
    }
    .brand {
      display: flex;
      align-items: center;
      gap: 10px;
      min-width: max-content;
      font-weight: 800;
      letter-spacing: 0;
      color: var(--ink);
    }
    .brand-mark {
      width: 32px;
      height: 32px;
      border-radius: 8px;
      display: grid;
      place-items: center;
      color: #fff;
      background: linear-gradient(135deg, var(--blue), #0b8a5b);
      box-shadow: 0 8px 24px rgba(35, 103, 209, 0.24);
      font-size: 14px;
    }
    .nav-links {
      display: flex;
      gap: 4px;
      align-items: center;
      overflow-x: auto;
      scrollbar-width: none;
    }
    .nav-links::-webkit-scrollbar { display: none; }
    .nav-links a {
      display: inline-flex;
      align-items: center;
      min-height: 34px;
      padding: 0 11px;
      border-radius: 8px;
      color: #344054;
      font-size: 13px;
      white-space: nowrap;
    }
    .nav-links a:hover {
      background: #fff;
      text-decoration: none;
    }
    .hero {
      padding: 30px 0 22px;
    }
    .hero-panel {
      background: linear-gradient(135deg, #ffffff 0%, #f8fbff 58%, #f2f8f5 100%);
      border: 1px solid #e3e9f0;
      border-radius: 8px;
      box-shadow: var(--shadow);
      padding: clamp(22px, 4vw, 38px);
      display: grid;
      grid-template-columns: minmax(0, 1.5fr) minmax(290px, 0.7fr);
      gap: clamp(20px, 4vw, 34px);
      overflow: hidden;
      position: relative;
    }
    .hero-panel::after {
      content: "";
      position: absolute;
      inset: auto -80px -120px auto;
      width: 300px;
      height: 300px;
      border-radius: 50%;
      background: radial-gradient(circle, rgba(35,103,209,.12), rgba(35,103,209,0) 68%);
      pointer-events: none;
    }
    h1, h2, h3, p { margin-top: 0; }
    h1 {
      margin-bottom: 14px;
      color: var(--ink);
      font-size: clamp(32px, 4.6vw, 58px);
      line-height: 1.05;
      letter-spacing: 0;
    }
    .hero-statement {
      margin: 0 0 22px;
      max-width: 760px;
      color: #26364f;
      font-size: clamp(18px, 2vw, 24px);
      line-height: 1.45;
      font-weight: 700;
    }
    .meta-line {
      display: flex;
      flex-wrap: wrap;
      gap: 9px;
      margin-bottom: 18px;
    }
    .chip, .status-pill {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      min-height: 30px;
      padding: 4px 10px;
      border-radius: 999px;
      border: 1px solid var(--line);
      background: #fff;
      color: #344054;
      font-size: 13px;
      font-weight: 650;
    }
    .status-pill.green, .chip.green { color: var(--green); background: var(--green-soft); border-color: #c8ead7; }
    .status-pill.yellow, .chip.yellow { color: var(--yellow); background: var(--yellow-soft); border-color: #f5d98f; }
    .status-pill.red, .chip.red { color: var(--red); background: var(--red-soft); border-color: #f3c6c6; }
    .status-pill.blue, .chip.blue { color: var(--blue); background: var(--blue-soft); border-color: #c9dcff; }
    .hero-actions, .toolbar-actions {
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      align-items: center;
    }
    .btn {
      min-height: 36px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fff;
      color: #243047;
      padding: 7px 12px;
      cursor: pointer;
      font-size: 13px;
      font-weight: 750;
    }
    .btn:hover { border-color: #b8c6d6; box-shadow: 0 8px 22px rgba(18,31,52,.08); }
    .btn.primary {
      background: var(--blue);
      border-color: var(--blue);
      color: #fff;
    }
    .hero-side {
      display: grid;
      gap: 12px;
      align-content: start;
    }
    .score-card {
      position: relative;
      background: rgba(255,255,255,.78);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 16px;
    }
    .score-card strong {
      display: block;
      color: var(--ink);
      font-size: 34px;
      line-height: 1;
      margin-bottom: 6px;
    }
    .score-card span { color: var(--muted); font-size: 13px; }
    .score-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px;
    }
    .section {
      scroll-margin-top: 82px;
      padding: 20px 0;
    }
    .section-head {
      display: flex;
      align-items: end;
      justify-content: space-between;
      gap: 20px;
      margin: 0 0 14px;
    }
    .section-title h2 {
      margin: 0;
      color: var(--ink);
      font-size: clamp(22px, 2.4vw, 31px);
      letter-spacing: 0;
      line-height: 1.2;
    }
    .section-title p {
      margin: 7px 0 0;
      color: var(--muted);
      max-width: 760px;
    }
    .grid {
      display: grid;
      grid-template-columns: repeat(12, minmax(0, 1fr));
      gap: 14px;
    }
    .panel, .judgement-card, .metric-card, .raw-panel {
      background: var(--surface);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: 0 10px 30px rgba(18, 31, 52, 0.04);
    }
    .panel {
      padding: 18px;
    }
    .judgement-card {
      grid-column: span 6;
      padding: 18px;
      display: flex;
      flex-direction: column;
      gap: 12px;
    }
    .judgement-card h3 {
      margin: 0;
      color: var(--ink);
      font-size: 19px;
      line-height: 1.35;
    }
    .judgement-card p { margin: 0; color: #435066; }
    .card-top {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
    }
    .evidence-strip {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
    }
    .mini-stat {
      padding: 7px 9px;
      border-radius: 8px;
      background: var(--surface-soft);
      border: 1px solid var(--line-soft);
      color: #344054;
      font-size: 12px;
      font-weight: 700;
    }
    details {
      border-top: 1px solid var(--line-soft);
      padding-top: 10px;
    }
    details summary {
      cursor: pointer;
      color: var(--blue);
      font-weight: 800;
      list-style: none;
    }
    details summary::-webkit-details-marker { display: none; }
    .metric-card {
      grid-column: span 3;
      padding: 16px;
      min-height: 112px;
    }
    .metric-card strong {
      display: block;
      color: var(--ink);
      font-size: 28px;
      line-height: 1.1;
      margin-bottom: 6px;
    }
    .metric-card span { color: var(--muted); font-size: 13px; }
    .metric-card p { margin: 10px 0 0; color: #526176; font-size: 13px; }
    .span-4 { grid-column: span 4; }
    .span-5 { grid-column: span 5; }
    .span-6 { grid-column: span 6; }
    .span-7 { grid-column: span 7; }
    .span-8 { grid-column: span 8; }
    .span-12 { grid-column: span 12; }
    .list {
      display: grid;
      gap: 10px;
      margin: 0;
      padding: 0;
      list-style: none;
    }
    .list li, .row-item {
      padding: 11px 0;
      border-bottom: 1px solid var(--line-soft);
    }
    .list li:last-child, .row-item:last-child { border-bottom: 0; }
    .item-title {
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 14px;
      margin-bottom: 4px;
      color: var(--ink);
      font-weight: 800;
    }
    .item-meta {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      color: var(--muted);
      font-size: 12px;
    }
    .keyword-table {
      width: 100%;
      border-collapse: collapse;
      font-size: 13px;
    }
    .keyword-table th, .keyword-table td {
      text-align: left;
      border-bottom: 1px solid var(--line-soft);
      padding: 10px 8px;
      vertical-align: top;
    }
    .keyword-table th {
      color: #475467;
      font-size: 12px;
      background: var(--surface-soft);
    }
    .empty {
      color: var(--muted);
      background: var(--surface-soft);
      border: 1px dashed #cfd8e3;
      border-radius: 8px;
      padding: 14px;
    }
    .alert {
      border-left: 4px solid var(--yellow);
      background: var(--yellow-soft);
      color: #684700;
      padding: 12px 14px;
      border-radius: 8px;
      margin: 10px 0;
    }
    .alert.red {
      border-left-color: var(--red);
      background: var(--red-soft);
      color: #831d1d;
    }
    .alert.blue {
      border-left-color: var(--blue);
      background: var(--blue-soft);
      color: #173f83;
    }
    .tabs {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-bottom: 12px;
    }
    .tab-btn {
      min-height: 32px;
      border: 1px solid var(--line);
      background: #fff;
      border-radius: 999px;
      color: #42526b;
      padding: 4px 10px;
      cursor: pointer;
      font-size: 13px;
      font-weight: 750;
    }
    .tab-btn.active {
      background: var(--ink);
      border-color: var(--ink);
      color: #fff;
    }
    .raw-panel {
      margin-bottom: 12px;
      padding: 0;
      overflow: hidden;
    }
    .raw-panel > summary {
      padding: 15px 18px;
      color: var(--ink);
      background: #fff;
      border: 0;
    }
    .raw-body {
      padding: 0 18px 18px;
    }
    pre {
      max-height: 360px;
      overflow: auto;
      margin: 0;
      padding: 14px;
      background: #111827;
      color: #e5e7eb;
      border-radius: 8px;
      white-space: pre-wrap;
      word-break: break-word;
      font-size: 12px;
      line-height: 1.55;
    }
    .footer-space { height: 40px; }
    @media (max-width: 880px) {
      .nav-inner { align-items: flex-start; flex-direction: column; padding: 10px 0; }
      .hero-panel { grid-template-columns: 1fr; }
      .judgement-card, .metric-card, .span-4, .span-5, .span-6, .span-7, .span-8, .span-12 { grid-column: 1 / -1; }
      .section-head { align-items: start; flex-direction: column; }
      .keyword-table { display: block; overflow-x: auto; }
      .score-grid { grid-template-columns: 1fr; }
    }
    @media print {
      .top-nav, .hero-actions, .toolbar-actions, .btn { display: none !important; }
      body { background: #fff; }
      .panel, .judgement-card, .metric-card, .raw-panel, .hero-panel { box-shadow: none; }
    }
  </style>
</head>
<body>
  <nav class="top-nav">
    <div class="nav-inner">
      <div class="brand"><span class="brand-mark">趋</span><span>趋势情报工作台</span></div>
      <div class="nav-links" aria-label="页面导航">
        <a href="#overview">总览</a>
        <a href="#judgements">核心判断</a>
        <a href="#evidence">趋势证据</a>
        <a href="#ai">AI 洞察</a>
        <a href="#keywords">关键词发现</a>
        <a href="#health">健康检查</a>
        <a href="#raw">原始材料</a>
      </div>
    </div>
  </nav>

  <div class="shell">
    <header class="hero" id="overview">
      <div class="hero-panel">
        <div>
          <h1>趋势情报工作台</h1>
          <p class="hero-statement" id="hero-statement">正在读取趋势情报数据。</p>
          <div class="meta-line" id="hero-meta"></div>
          <div class="hero-actions">
            <button class="btn primary" type="button" id="copy-summary-btn">复制趋势摘要</button>
            <button class="btn" type="button" id="export-json-btn">导出 JSON</button>
            <button class="btn" type="button" id="export-md-btn">导出 Markdown</button>
            <button class="btn" type="button" id="export-html-btn">导出 HTML</button>
            <button class="btn" type="button" id="print-btn">打印</button>
          </div>
        </div>
        <aside class="hero-side">
          <div class="score-grid" id="hero-scores"></div>
          <div id="hero-alerts"></div>
        </aside>
      </div>
    </header>

    <section class="section" id="judgements">
      <div class="section-head">
        <div class="section-title">
          <h2>今日最重要的判断</h2>
          <p>先看结论，再展开证据。这里优先使用 observations 与 insights，不再把原始帖子堆在最前面。</p>
        </div>
      </div>
      <div class="grid" id="judgement-grid"></div>
    </section>

    <section class="section" id="evidence">
      <div class="section-head">
        <div class="section-title">
          <h2>趋势证据</h2>
          <p>来自长期 SQLite、快照层和观察层的可核验数据。历史不足时会明确提示，不假装已有趋势。</p>
        </div>
      </div>
      <div class="grid" id="evidence-grid"></div>
    </section>

    <section class="section" id="ai">
      <div class="section-head">
        <div class="section-title">
          <h2>AI 洞察</h2>
          <p>展示 Gemini / insights 层的结构化判断，拆分为趋势等级、新主题、痛点、推荐追踪和风险提示。</p>
        </div>
      </div>
      <div class="grid" id="ai-grid"></div>
    </section>

    <section class="section" id="keywords">
      <div class="section-head">
        <div class="section-title">
          <h2>关键词发现与下一轮计划</h2>
          <p>只展示候选与计划，不自动 promote，也不自动采集。下一步仍由人工确认。</p>
        </div>
        <div class="toolbar-actions">
          <button class="btn" type="button" id="copy-plan-btn">复制下一轮命令</button>
        </div>
      </div>
      <div class="grid" id="keyword-grid"></div>
    </section>

    <section class="section" id="health">
      <div class="section-head">
        <div class="section-title">
          <h2>系统健康与数据质量</h2>
          <p>健康评分、失败任务、登录态、样本充足度和闭环层级都会在这里集中呈现。</p>
        </div>
      </div>
      <div class="grid" id="health-grid"></div>
    </section>

    <section class="section" id="raw">
      <div class="section-head">
        <div class="section-title">
          <h2>原始证据材料</h2>
          <p>帖子、评论、原始 JSON 与 AI 原文默认折叠，仅作为结论证据使用。</p>
        </div>
      </div>
      <div id="raw-materials"></div>
    </section>
    <div class="footer-space"></div>
  </div>

  <script id="app-data" type="application/json">__DATA__</script>
  <script>
    const report = JSON.parse(document.getElementById('app-data').textContent || '{}');
    const wb = report.workbench || {};
    const trend = wb.trend_db_summary || {};
    const health = wb.trend_loop_health || {};
    const quality = wb.quality_audit || {};
    const insightReport = wb.insight_report || wb.insights || {};
    const insight = insightReport.output || insightReport || {};
    const discovered = wb.discovered_keywords || {};
    const plan = wb.keyword_task_plan || {};
    const cookie = wb.cookie_health || {};

    function escapeHtml(value) {
      return String(value ?? '').replace(/[&<>"']/g, char => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
      }[char]));
    }
    function asArray(value) {
      if (Array.isArray(value)) return value;
      if (value === undefined || value === null || value === '') return [];
      return [value];
    }
    function compact(value, limit = 120) {
      const text = itemText(value).replace(/\s+/g, ' ').trim();
      if (text.length <= limit) return text;
      return text.slice(0, limit - 1).trim() + '…';
    }
    function itemText(value) {
      if (value === undefined || value === null) return '';
      if (typeof value === 'string' || typeof value === 'number') return String(value);
      if (Array.isArray(value)) return value.map(itemText).filter(Boolean).join(' / ');
      if (typeof value === 'object') {
        const keys = ['title', 'topic', 'pain_point', 'keyword', 'summary', 'reason', 'evidence', 'one_line', 'comment', 'text', 'name'];
        for (const key of keys) {
          if (value[key]) return itemText(value[key]);
        }
        return JSON.stringify(value);
      }
      return String(value);
    }
    function num(value) {
      const number = Number(value || 0);
      return Number.isFinite(number) ? number : 0;
    }
    function fmt(value) {
      if (value === undefined || value === null || value === '') return '暂无';
      const number = Number(value);
      if (!Number.isFinite(number)) return String(value);
      return number.toLocaleString('zh-CN');
    }
    function pct(value) {
      const number = Number(value || 0);
      if (!Number.isFinite(number)) return '暂无';
      return `${Math.round(number * 100)}%`;
    }
    function chip(label, tone = '') {
      return `<span class="status-pill ${tone}">${escapeHtml(label)}</span>`;
    }
    function empty(message) {
      return `<div class="empty">${escapeHtml(message)}</div>`;
    }
    function list(items, limit = 8) {
      const values = asArray(items).filter(Boolean).slice(0, limit);
      if (!values.length) return empty('暂无数据，继续运行几轮后会出现。');
      return `<ul class="list">${values.map(item => `<li>${escapeHtml(itemText(item))}</li>`).join('')}</ul>`;
    }
    function metric(label, value, note = '') {
      return `<div class="metric-card"><strong>${escapeHtml(fmt(value))}</strong><span>${escapeHtml(label)}</span>${note ? `<p>${escapeHtml(note)}</p>` : ''}</div>`;
    }
    function panel(title, body, span = 'span-6', extra = '') {
      return `<article class="panel ${span} ${extra}"><h3>${escapeHtml(title)}</h3>${body}</article>`;
    }
    function statusTone(status) {
      const text = String(status || '').toLowerCase();
      if (['ok', 'success', 'ready', 'high', '正常'].some(key => text.includes(key))) return 'green';
      if (['needs_login', 'failed', 'error', 'risk', 'low', '异常'].some(key => text.includes(key))) return 'red';
      if (['partial', 'medium', 'warning', 'postpone', 'candidate', 'watching', '中'].some(key => text.includes(key))) return 'yellow';
      return 'blue';
    }
    function generatedAt() {
      return report.meta?.generated_at || trend.latest_run?.finished_at || health.generated_at || insightReport.generated_at || '';
    }
    function statement() {
      if (insight.one_sentence_summary || insightReport.one_sentence_summary) {
        return insight.one_sentence_summary || insightReport.one_sentence_summary;
      }
      const notes = trend.database_overview?.notes_total;
      const obs = health.tables?.observations?.count || trend.database_overview?.crawl_runs_total || report.observations?.length;
      const query = report.meta?.query || report.meta?.topic || quality.query || '当前关键词';
      if (!notes && !obs) return '暂无足够历史数据，当前页面只能展示本次采集材料，不能判断长期趋势。';
      return `当前围绕「${query}」已形成 ${fmt(notes || report.structured_notes?.length || 0)} 条笔记样本和 ${fmt(obs || 0)} 条观察记录，趋势判断仍应结合后续多轮快照验证。`;
    }
    function renderHero() {
      document.getElementById('hero-statement').textContent = statement();
      const healthScore = health.health?.score;
      const qualityStatus = quality.status || '暂无质量审计';
      const trendLevel = insight.current_trend_level || insightReport.current_trend_level || '暂无';
      const obsCount = num(health.tables?.observations?.count || insightReport.observation_count || 0);
      const dataStage = obsCount < 5 ? '试运行阶段' : '可持续观察';
      document.getElementById('hero-meta').innerHTML = [
        chip(`关键词：${report.meta?.query || quality.query || report.meta?.topic || '未标注'}`, 'blue'),
        chip(`运行时间：${generatedAt() || '暂无'}`),
        chip(`来源：${report.meta?.source || 'XHS / SQLite / AI'}`),
        chip(`趋势等级：${trendLevel}`, statusTone(trendLevel)),
        chip(`质量：${qualityStatus}`, statusTone(qualityStatus)),
        chip(dataStage, obsCount < 5 ? 'yellow' : 'green')
      ].join('');
      document.getElementById('hero-scores').innerHTML = [
        `<div class="score-card"><strong>${escapeHtml(healthScore ?? '暂无')}</strong><span>健康评分</span></div>`,
        `<div class="score-card"><strong>${escapeHtml(fmt(trend.database_overview?.notes_total || report.structured_notes?.length || 0))}</strong><span>累计笔记</span></div>`,
        `<div class="score-card"><strong>${escapeHtml(fmt(trend.database_overview?.metrics_snapshots_total || 0))}</strong><span>快照数量</span></div>`,
        `<div class="score-card"><strong>${escapeHtml(fmt(discovered.candidates?.length || health.tables?.discovered_keywords?.count || 0))}</strong><span>候选关键词</span></div>`
      ].join('');
      const alerts = [];
      if (healthScore !== undefined && healthScore < 80) alerts.push(`<div class="alert red">健康评分低于 80，当前闭环仍需修复或继续积累。</div>`);
      if (quality.status === 'needs_login') alerts.push(`<div class="alert red">质量审计提示登录态可能异常，本次采集可能受限。</div>`);
      if (obsCount < 5) alerts.push(`<div class="alert">observations 少于 5 条，仍处于试运行阶段。</div>`);
      if ((health.tables?.failures?.count || 0) > 0) alerts.push(`<div class="alert">存在失败任务：${fmt(health.tables.failures.count)} 条，建议查看 failures 与质量审计。</div>`);
      document.getElementById('hero-alerts').innerHTML = alerts.join('');
    }
    function judgementSourceItems() {
      const cards = [];
      asArray(insight.fastest_growing_topics).forEach(item => cards.push({ type: '趋势', tone: 'green', source: item }));
      asArray(insight.frequent_pain_points).forEach(item => cards.push({ type: '痛点', tone: 'yellow', source: item }));
      asArray(insight.new_topics).forEach(item => cards.push({ type: '机会', tone: 'blue', source: item }));
      asArray(insight.risk_warnings).forEach(item => cards.push({ type: '风险', tone: 'red', source: item }));
      asArray(report.gemini_analysis?.top_findings).forEach(item => cards.push({ type: 'AI 判断', tone: 'blue', source: item }));
      asArray(report.observations).slice(0, 3).forEach(item => cards.push({ type: item.trend_reflected ? '趋势' : '观察', tone: 'green', source: item }));
      return cards.slice(0, 5);
    }
    function judgementTitle(item) {
      const src = item.source || {};
      const raw = src.title || src.topic || src.pain_point || src.phenomenon || src.summary || src.reason || itemText(src);
      const text = humanizeSignalTitle(compact(raw, 58));
      if (!text || text === '未命名发现') return '来自采集内容的潜在趋势';
      return text;
    }
    function humanizeSignalTitle(text) {
      const value = String(text || '').trim();
      const map = {
        insufficient_history: '历史快照不足，趋势仍需继续验证',
        new_failures: '采集失败增加，数据质量需要复核',
        needs_login: '登录态可能异常，采集结果需要复核',
        no_observations: '观察层数据不足，暂不适合趋势判断',
        no_insights: 'AI 洞察尚未生成，当前只能查看原始证据'
      };
      if (map[value]) return map[value];
      if (/^[a-z_]+$/i.test(value) && value.includes('_')) {
        return `系统检测到 ${value.replace(/_/g, ' ')} 信号`;
      }
      return value;
    }
    function judgementExplain(item) {
      const src = item.source || {};
      const key = String(src.title || src.topic || src.pain_point || '').trim();
      if (key === 'insufficient_history') return '历史快照数量不足，当前增长结论需要继续积累多轮采集后再确认。';
      if (key === 'new_failures') return '失败任务出现增长，可能与登录态、页面结构变化或账号状态有关，需要先检查质量审计。';
      return compact(src.reason || src.evidence || src.underlying_logic || src.summary || src.trend_reflected || itemText(src), 180);
    }
    function renderJudgements() {
      const items = judgementSourceItems();
      if (!items.length) {
        document.getElementById('judgement-grid').innerHTML = empty('AI 洞察尚未生成，或当前样本不足以形成核心判断。');
        return;
      }
      const obsCount = insightReport.observation_count || health.tables?.observations?.count || 0;
      const noteCount = trend.latest_run?.total_saved || report.structured_notes?.length || 0;
      const commentCount = quality.counts?.comments || report.high_value_comments?.length || 0;
      document.getElementById('judgement-grid').innerHTML = items.map((item, index) => `
        <article class="judgement-card" data-observation-card>
          <div class="card-top">
            ${chip(item.type, item.tone)}
            ${chip(`判断 ${index + 1}`)}
          </div>
          <h3>${escapeHtml(judgementTitle(item))}</h3>
          <p>${escapeHtml(judgementExplain(item) || '当前只有有限证据，建议继续追踪后再下结论。')}</p>
          <div class="evidence-strip">
            <span class="mini-stat">观察 ${fmt(obsCount)}</span>
            <span class="mini-stat">相关帖子 ${fmt(noteCount)}</span>
            <span class="mini-stat">评论 ${fmt(commentCount)}</span>
            <span class="mini-stat">可信度 ${escapeHtml(item.source?.confidence || insight.current_trend_level || '待验证')}</span>
          </div>
          <details>
            <summary>查看证据</summary>
            ${list([item.source?.reason, item.source?.evidence, item.source?.underlying_logic, item.source?.why_recent, item.source?.trend_reflected].filter(Boolean), 6)}
          </details>
        </article>
      `).join('');
    }
    function growthRows(items, key, limit = 6) {
      const rows = asArray(items).slice(0, limit);
      if (!rows.length) return empty('历史快照不足，暂无增长排行。');
      return `<ul class="list">${rows.map(item => `
        <li>
          <div class="item-title"><span>${escapeHtml(compact(item.title || item.note_id || '未命名笔记', 58))}</span><strong>${escapeHtml(fmt(item[key]))}</strong></div>
          <div class="item-meta"><span>${escapeHtml(item.author || '')}</span><span>${escapeHtml(item.latest_snapshot_time || '')}</span>${item.note_url ? `<a href="${escapeHtml(item.note_url)}" target="_blank" rel="noreferrer">来源</a>` : ''}</div>
        </li>
      `).join('')}</ul>`;
    }
    function renderEvidence() {
      const overview = trend.database_overview || {};
      const diff = trend.run_diff || {};
      const latestMetrics = diff.latest_metrics || {};
      const delta = diff.interaction_delta || {};
      const hasHistory = diff.has_enough_history;
      const alert = hasHistory ? '' : `<div class="alert">历史快照不足，暂无法判断增长趋势。</div>`;
      document.getElementById('evidence-grid').innerHTML = [
        metric('notes 总数', overview.notes_total || health.tables?.notes?.count || report.structured_notes?.length || 0, '长期数据库累计'),
        metric('metrics_snapshots 总数', overview.metrics_snapshots_total || health.tables?.metrics_snapshots?.count || 0, '每次采集都可新增'),
        metric('observations 总数', health.tables?.observations?.count || insightReport.observation_count || 0, '观察层记录'),
        metric('insights 总数', health.tables?.insights?.count || 0, 'AI 洞察层'),
        panel('本轮新增与互动变化', `
          ${alert}
          <div class="evidence-strip">
            <span class="mini-stat">新增笔记 ${fmt(diff.added_note_count ?? trend.latest_run?.total_saved)}</span>
            <span class="mini-stat">点赞增长 ${fmt(delta.liked_count)}</span>
            <span class="mini-stat">收藏增长 ${fmt(delta.collected_count)}</span>
            <span class="mini-stat">评论增长 ${fmt(delta.comment_count)}</span>
          </div>
          <div class="evidence-strip" style="margin-top:10px">
            <span class="mini-stat">本轮点赞 ${fmt(latestMetrics.liked_count)}</span>
            <span class="mini-stat">本轮收藏 ${fmt(latestMetrics.collected_count)}</span>
            <span class="mini-stat">本轮评论 ${fmt(latestMetrics.comment_count)}</span>
          </div>
        `, 'span-6'),
        panel('高频作者', list(asArray(trend.top_authors).map(item => `${item.nickname || item.author_id}：${fmt(item.note_count)} 篇，互动 ${fmt(item.total_liked_count || 0)}`), 8), 'span-6'),
        panel('高频标签 / 关键词', list(asArray(trend.top_keywords_or_tags).map(item => `${item.keyword || item.tag || item.name || itemText(item)} ${fmt(item.count || item.note_count || '')}`), 10), 'span-4'),
        panel('点赞增长最快', growthRows(trend.fastest_liked_growth, 'liked_growth'), 'span-4'),
        panel('评论增长最快', growthRows(trend.fastest_comment_growth, 'comment_growth'), 'span-4'),
        panel('互动最高的笔记', list(asArray(trend.top_notes).map(item => `${item.title || item.note_id}：点赞 ${fmt(item.liked_count)} / 收藏 ${fmt(item.collected_count)} / 评论 ${fmt(item.comment_count)}`), 8), 'span-12')
      ].join('');
    }
    function renderAi() {
      const status = insightReport.ai_status || insight.ai_status || report.gemini_analysis?.meta?.status || '暂无';
      document.getElementById('ai-grid').innerHTML = [
        panel('趋势等级', `
          <div class="evidence-strip">${chip(`等级：${insight.current_trend_level || insightReport.current_trend_level || '暂无'}`, statusTone(insight.current_trend_level || insightReport.current_trend_level))}${chip(`AI 状态：${status}`, statusTone(status))}${chip(`模型：${insightReport.model || report.gemini_analysis?.meta?.model || '暂无'}`, 'blue')}</div>
          <p>${escapeHtml(insight.one_sentence_summary || insightReport.one_sentence_summary || 'AI 洞察尚未生成。')}</p>
        `, 'span-12'),
        panel('增长主题', list(insight.fastest_growing_topics, 6), 'span-6'),
        panel('新出现主题', list(insight.new_topics, 6), 'span-6'),
        panel('高频痛点', list(insight.frequent_pain_points, 6), 'span-6'),
        panel('推荐追踪关键词', list(insight.recommended_tracking_keywords || report.gemini_analysis?.next_search_keywords, 10), 'span-6'),
        panel('风险提示', list(insight.risk_warnings || report.gemini_analysis?.risk_notes, 8), 'span-12')
      ].join('');
    }
    function copyCommandForKeyword(item) {
      const keyword = item.keyword || '';
      const limit = item.suggested_limit || 20;
      return `.\\scripts\\run_local_pipeline.ps1 -Query "${keyword.replace(/"/g, '\\"')}" -MaxNotes ${limit} -AIAnalyze -NoOpen`;
    }
    function renderKeywords() {
      const candidates = asArray(discovered.candidates);
      const rows = candidates.length ? candidates.map(item => `
        <tr>
          <td><strong>${escapeHtml(item.keyword || '')}</strong></td>
          <td>${chip(item.status || 'candidate', statusTone(item.status))}</td>
          <td>${escapeHtml(fmt(item.confidence))}</td>
          <td>${escapeHtml(compact(item.reason || '暂无理由', 120))}</td>
          <td>${escapeHtml(item.source_type || item.source || '暂无')}</td>
          <td>${item.status === 'promoted' ? '是' : '否'}</td>
          <td><button class="btn" type="button" data-copy="${escapeHtml(item.keyword || '')}">复制关键词</button></td>
        </tr>
      `).join('') : `<tr><td colspan="7">暂无候选关键词。</td></tr>`;
      const planRows = asArray(plan.keywords).slice(0, 10);
      const planHtml = planRows.length ? `<ul class="list">${planRows.map(item => `
        <li>
          <div class="item-title"><span>${escapeHtml(item.keyword || '')}</span>${chip(item.priority || item.status || '待定', statusTone(item.priority || item.status))}</div>
          <div class="item-meta"><span>${escapeHtml(item.reason || '')}</span><span>建议数量 ${fmt(item.suggested_limit)}</span><button class="btn" type="button" data-copy-command="${escapeHtml(copyCommandForKeyword(item))}">复制命令</button></div>
        </li>
      `).join('')}</ul>` : empty('暂无任务计划，请先运行 plan_keyword_tasks.py。');
      document.getElementById('keyword-grid').innerHTML = [
        panel('候选关键词', `
          <table class="keyword-table">
            <thead><tr><th>keyword</th><th>status</th><th>confidence</th><th>reason</th><th>source</th><th>正式 keywords</th><th>操作</th></tr></thead>
            <tbody>${rows}</tbody>
          </table>
        `, 'span-12'),
        panel('下一轮任务计划', planHtml, 'span-8'),
        panel('发现层状态', `
          <div class="evidence-strip">
            <span class="mini-stat">写入 ${fmt(discovered.written_count || candidates.length)}</span>
            <span class="mini-stat">重复 ${fmt(discovered.duplicate_keywords?.length || 0)}</span>
            <span class="mini-stat">正式关键词变化：${discovered.formal_keywords_changed ? '是' : '否'}</span>
            <span class="mini-stat">Gemini：${escapeHtml(discovered.gemini_status || '暂无')}</span>
          </div>
          ${discovered.formal_keywords_changed ? '<div class="alert red">发现层不应自动修改正式关键词，请检查。</div>' : '<div class="alert blue">发现层仅生成候选，不自动采集、不自动晋升。</div>'}
        `, 'span-4')
      ].join('');
    }
    function renderHealth() {
      const score = health.health?.score;
      const tables = health.tables || {};
      const issues = asArray(health.issues);
      const completed = asArray(health.completed_layers);
      const qualityCounts = quality.counts || {};
      document.getElementById('health-grid').innerHTML = [
        metric('health_score', score ?? '暂无', score !== undefined && score < 80 ? '低于 80，需要注意' : '闭环健康检查'),
        metric('warnings', report.meta?.warning_count || qualityCounts.warnings || 0, '本次质量警告'),
        metric('failures', tables.failures?.count || 0, '失败任务累计'),
        metric('completed_layers', completed.length, '已完成层级'),
        panel('质量状态', `
          <div class="evidence-strip">
            ${chip(`quality_status：${quality.status || '暂无'}`, statusTone(quality.status))}
            ${chip(`Cookie：${cookie.status || cookie.cookie_status || '暂无'}`, statusTone(cookie.status || cookie.cookie_status))}
            ${chip(`采集可能受限：${quality.status === 'needs_login' ? '是' : '待判断'}`, quality.status === 'needs_login' ? 'red' : 'yellow')}
          </div>
          ${quality.status === 'needs_login' ? '<div class="alert red">quality_status 为 needs_login，请检查小红书登录态。</div>' : ''}
          ${(tables.observations?.count || 0) < 5 ? '<div class="alert">observations 少于 5，当前仍处于试运行阶段。</div>' : ''}
        `, 'span-6'),
        panel('闭环问题清单', issues.length ? `<ul class="list">${issues.map(item => `<li><div class="item-title"><span>${escapeHtml(item.name || '未命名问题')}</span>${chip(item.severity || 'low', statusTone(item.severity))}</div><div class="item-meta"><span>${escapeHtml(item.possible_cause || '')}</span><span>${escapeHtml(item.next_step || '')}</span></div></li>`).join('')}</ul>` : empty('暂无健康检查问题。'), 'span-6'),
        panel('核心表数量', `<div class="evidence-strip">${Object.entries(tables).map(([name, info]) => `<span class="mini-stat">${escapeHtml(name)} ${fmt(info.count)}</span>`).join('')}</div>`, 'span-12')
      ].join('');
    }
    function rawNoteTitle(note) {
      return note.title || note.note_title || note.phenomenon || note.note_id || '未命名帖子';
    }
    function renderRaw() {
      const notes = asArray(wb.structured_notes_file).length ? asArray(wb.structured_notes_file) : asArray(report.structured_notes);
      const rawPosts = asArray(report.raw_posts);
      const comments = asArray(report.high_value_comments);
      const rawJson = JSON.stringify({
        trend_report: report,
        trend_db_summary: trend,
        insight_report: insightReport,
        discovered_keywords: discovered,
        keyword_task_plan: plan,
        trend_loop_health: health
      }, null, 2);
      document.getElementById('raw-materials').innerHTML = `
        <details class="raw-panel">
          <summary>原始帖子 Top 10（其余折叠）</summary>
          <div class="raw-body">${notes.length ? `<ul class="list">${notes.slice(0, 10).map(note => `<li><div class="item-title"><span>${escapeHtml(compact(rawNoteTitle(note), 80))}</span></div><div class="item-meta"><span>${escapeHtml(note.author || note.nickname || '')}</span><span>点赞 ${fmt(note.like_count || note.liked_count)}</span><span>收藏 ${fmt(note.collect_count || note.collected_count)}</span><span>评论 ${fmt(note.comment_count)}</span>${(note.url || note.note_url) ? `<a href="${escapeHtml(note.url || note.note_url)}" target="_blank" rel="noreferrer">来源</a>` : ''}</div></li>`).join('')}</ul>` : empty('暂无 structured notes。')}</div>
        </details>
        <details class="raw-panel">
          <summary>高价值评论证据 Top 10</summary>
          <div class="raw-body">${comments.length ? `<ul class="list">${comments.slice(0, 10).map(item => `<li><div class="item-title"><span>${escapeHtml(compact(item.comment || itemText(item), 120))}</span><strong>${escapeHtml(item.value_score ?? '')}</strong></div><div class="item-meta"><span>${escapeHtml(item.reason || '')}</span>${item.note_url ? `<a href="${escapeHtml(item.note_url)}" target="_blank" rel="noreferrer">来源</a>` : ''}</div></li>`).join('')}</ul>` : empty('暂无高价值评论。')}</div>
        </details>
        <details class="raw-panel">
          <summary>原始采集帖子摘要</summary>
          <div class="raw-body">${rawPosts.length ? `<ul class="list">${rawPosts.slice(0, 10).map(post => `<li><div class="item-title"><span>${escapeHtml(compact(post.title || '未命名帖子', 80))}</span></div><div class="item-meta"><span>${escapeHtml(post.keyword || '')}</span><span>${escapeHtml(post.author || '')}</span><span>点赞 ${fmt(post.like_count)}</span><span>收藏 ${fmt(post.collect_count)}</span><span>评论 ${fmt(post.comment_count)}</span>${post.url ? `<a href="${escapeHtml(post.url)}" target="_blank" rel="noreferrer">来源</a>` : ''}</div></li>`).join('')}</ul>` : empty('暂无 raw posts。')}</div>
        </details>
        <details class="raw-panel">
          <summary>原始 AI 输出</summary>
          <div class="raw-body"><pre>${escapeHtml(wb.ai_output_markdown_file || report.ai_output_markdown || JSON.stringify(report.gemini_analysis || {}, null, 2) || 'AI 洞察尚未生成。')}</pre></div>
        </details>
        <details class="raw-panel">
          <summary>页面聚合 JSON</summary>
          <div class="raw-body"><pre>${escapeHtml(rawJson)}</pre></div>
        </details>
      `;
    }
    function makeMarkdown() {
      const lines = [
        '# 趋势情报工作台',
        '',
        `- 一句话结论：${statement()}`,
        `- 关键词：${report.meta?.query || quality.query || ''}`,
        `- 运行时间：${generatedAt()}`,
        `- 健康评分：${health.health?.score ?? '暂无'}`,
        `- 质量状态：${quality.status || '暂无'}`,
        '',
        '## 推荐追踪关键词',
        ...asArray(insight.recommended_tracking_keywords || []).slice(0, 10).map(item => `- ${itemText(item)}`),
        '',
        '## 风险提示',
        ...asArray(insight.risk_warnings || []).slice(0, 10).map(item => `- ${itemText(item)}`)
      ];
      return lines.join('\n');
    }
    function download(filename, text, type) {
      const blob = new Blob([text], { type });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = filename;
      a.click();
      URL.revokeObjectURL(url);
    }
    async function copyText(text) {
      try {
        await navigator.clipboard.writeText(text);
      } catch {
        const textarea = document.createElement('textarea');
        textarea.value = text;
        document.body.appendChild(textarea);
        textarea.select();
        document.execCommand('copy');
        textarea.remove();
      }
    }
    function planCommand() {
      const items = asArray(plan.keywords).filter(item => item.status === 'ready').slice(0, 5);
      if (!items.length) return '暂无 ready 关键词，请先查看 keyword-task-plan.json。';
      return items.map(copyCommandForKeyword).join('\n');
    }
    function bindEvents() {
      document.getElementById('print-btn').addEventListener('click', () => window.print());
      document.getElementById('export-json-btn').addEventListener('click', () => download('trend-workbench.json', JSON.stringify(report, null, 2), 'application/json;charset=utf-8'));
      document.getElementById('export-md-btn').addEventListener('click', () => download('trend-workbench.md', makeMarkdown(), 'text/markdown;charset=utf-8'));
      document.getElementById('export-html-btn').addEventListener('click', () => download('trend-workbench.html', '<!doctype html>\n' + document.documentElement.outerHTML, 'text/html;charset=utf-8'));
      document.getElementById('copy-summary-btn').addEventListener('click', async () => copyText(statement()));
      document.getElementById('copy-plan-btn').addEventListener('click', async () => copyText(planCommand()));
      document.body.addEventListener('click', async event => {
        const copy = event.target.closest('[data-copy]');
        const copyCommand = event.target.closest('[data-copy-command]');
        if (copy) await copyText(copy.dataset.copy || '');
        if (copyCommand) await copyText(copyCommand.dataset.copyCommand || '');
      });
    }
    function render() {
      renderHero();
      renderJudgements();
      renderEvidence();
      renderAi();
      renderKeywords();
      renderHealth();
      renderRaw();
      bindEvents();
    }
    render();
  </script>
</body>
</html>
"""


def render_html(report: dict[str, Any]) -> str:
    data = json.dumps(report, ensure_ascii=False)
    data = data.replace("</", "<\\/")
    return HTML_TEMPLATE.replace("__DATA__", data)


def main() -> None:
    parser = argparse.ArgumentParser(description="Render trend report JSON and single-file HTML.")
    parser.add_argument("--normalized", required=True)
    parser.add_argument("--raw", default="")
    parser.add_argument("--ai-output-json", default="")
    parser.add_argument("--ai-output-md", default="")
    parser.add_argument("--diagnostics-dir", default="")
    parser.add_argument("--report-out", required=True)
    parser.add_argument("--html-out", required=True)
    args = parser.parse_args()

    normalized = load_json(args.normalized, {})
    raw = load_json(args.raw, {}) if args.raw else {}
    ai_output = load_json(args.ai_output_json, {}) if args.ai_output_json else {}
    ai_output_markdown = ""
    if args.ai_output_md and Path(args.ai_output_md).exists():
        ai_output_markdown = Path(args.ai_output_md).read_text(encoding="utf-8")
    write_report_source_diagnostics(raw, args.diagnostics_dir)
    report = build_report(normalized, raw, ai_output, ai_output_markdown, args.diagnostics_dir)
    html_out = Path(args.html_out)
    write_json(args.report_out, report)

    html = render_html(attach_workbench_data(report, html_out.parent))
    html_out.parent.mkdir(parents=True, exist_ok=True)
    html_out.write_text(html, encoding="utf-8")
    print(f"report JSON written: {args.report_out}")
    print(f"HTML written: {args.html_out}")


if __name__ == "__main__":
    main()
