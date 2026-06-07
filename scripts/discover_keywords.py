from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.generate_insights import (  # noqa: E402
    DEFAULT_MODEL,
    call_gemini_with_retry,
    extract_json,
    load_env_file,
    response_text,
)
from storage.database import get_connection, init_database, resolve_db_path, transaction  # noqa: E402


CN_TZ = timezone(timedelta(hours=8))
SOURCE_WEIGHTS = {
    "insight_recommended_keyword": 0.48,
    "insight_fastest_growing_topic": 0.38,
    "insight_new_topic": 0.34,
    "insight_frequent_pain_point": 0.2,
    "observation_new_tag": 0.28,
    "observation_current_tag": 0.16,
    "observation_summary_keyword": 0.12,
    "observation_anomaly": 0.05,
    "analysis_result_next_keyword": 0.32,
    "analysis_result_cluster": 0.22,
    "trend_summary_keyword_or_tag": 0.18,
}
BROAD_TERMS = {
    "AI",
    "人工智能",
    "趋势",
    "热门",
    "小红书",
    "年轻人",
    "消费",
    "行业",
    "生活",
    "真实",
    "逻辑",
    "信息差",
}
NOISE_TERMS = {
    "unknown",
    "none",
    "null",
    "暂无",
    "暂无数据",
    "数据不足",
    "样本不足",
    "new_failures",
    "insufficient_history",
}


@dataclass
class Candidate:
    keyword: str
    sources: list[dict[str, Any]] = field(default_factory=list)
    confidence: float = 0.0
    reason_parts: list[str] = field(default_factory=list)
    penalties: list[str] = field(default_factory=list)
    status: str = "candidate"
    gemini: dict[str, Any] = field(default_factory=dict)

    def add_source(self, source_type: str, source_id: str, reason: str, weight: float, raw: Any = None) -> None:
        self.sources.append(
            {
                "source_type": source_type,
                "source_id": source_id,
                "reason": reason,
                "weight": weight,
                "raw": raw,
            }
        )
        if reason:
            self.reason_parts.append(reason)

    @property
    def primary_source(self) -> dict[str, Any]:
        if not self.sources:
            return {"source_type": "unknown", "source_id": ""}
        return sorted(self.sources, key=lambda item: item.get("weight", 0), reverse=True)[0]

    @property
    def reason(self) -> str:
        parts = []
        seen = set()
        for reason in self.reason_parts:
            clean = compact_text(reason, 80)
            if clean and clean not in seen:
                seen.add(clean)
                parts.append(clean)
        if self.gemini.get("reason"):
            parts.insert(0, compact_text(self.gemini["reason"], 100))
        if self.penalties:
            parts.append("保守项：" + "；".join(self.penalties[:3]))
        return "；".join(parts[:5]) or "来自观察层/洞察层的候选关键词。"


def now_iso() -> str:
    return datetime.now(CN_TZ).isoformat(timespec="seconds")


def today_string() -> str:
    return datetime.now(CN_TZ).date().isoformat()


def compact_text(value: Any, limit: int = 120) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def normalize_keyword(value: Any) -> str:
    text = str(value or "").strip()
    text = re.sub(r"[#｜|/、，,；;：:]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = text.strip("「」『』【】[]()（）<>《》\"'")
    if len(text) > 40:
        return ""
    return text


def split_possible_terms(value: Any) -> list[str]:
    text = str(value or "").strip()
    if not text:
        return []
    if len(text) <= 16 and not re.search(r"[\s,，、/|｜]+", text):
        return [text]
    pieces = re.split(r"[,，、/|｜]+", text)
    terms = []
    for piece in pieces:
        clean = normalize_keyword(piece)
        if clean:
            terms.append(clean)
    clean_full = normalize_keyword(text)
    if clean_full and 3 <= len(clean_full) <= 28:
        terms.insert(0, clean_full)
    return list(dict.fromkeys(terms))


def load_json_text(value: str | None, default: Any = None) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


def read_json_file(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def rows_to_dicts(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


def as_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def add_candidate(
    candidates: dict[str, Candidate],
    keyword: Any,
    source_type: str,
    source_id: str,
    reason: str,
    raw: Any = None,
) -> None:
    for term in split_possible_terms(keyword):
        if not is_candidate_shape(term):
            continue
        candidate = candidates.setdefault(term, Candidate(keyword=term))
        candidate.add_source(source_type, source_id, reason, SOURCE_WEIGHTS.get(source_type, 0.1), raw)


def is_candidate_shape(keyword: str) -> bool:
    if not keyword:
        return False
    lowered = keyword.lower()
    if lowered in NOISE_TERMS:
        return False
    if len(keyword) < 2 or len(keyword) > 40:
        return False
    if re.fullmatch(r"[\d\W_]+", keyword):
        return False
    return True


def query_recent_observations(conn: sqlite3.Connection, days: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT *
        FROM observations
        WHERE observation_date >= date('now', ?)
        ORDER BY observation_date DESC, observation_id DESC
        """,
        (f"-{max(days, 1) - 1} days",),
    ).fetchall()
    return rows_to_dicts(rows)


def query_recent_insights(conn: sqlite3.Connection, days: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT *
        FROM insights
        WHERE period_end >= date('now', ?)
        ORDER BY period_end DESC, insight_id DESC
        """,
        (f"-{max(days, 1) - 1} days",),
    ).fetchall()
    return rows_to_dicts(rows)


def query_recent_analysis_results(conn: sqlite3.Connection, days: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT *
        FROM analysis_results
        WHERE created_at >= datetime('now', ?)
        ORDER BY created_at DESC, analysis_id DESC
        LIMIT 20
        """,
        (f"-{max(days, 1)} days",),
    ).fetchall()
    return rows_to_dicts(rows)


def existing_keywords(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT keyword FROM keywords").fetchall()
    return {str(row["keyword"]).strip() for row in rows if row["keyword"]}


def extract_from_insights(candidates: dict[str, Candidate], insights: list[dict[str, Any]]) -> None:
    for row in insights:
        insight_id = str(row.get("insight_id") or "")
        for item in load_json_text(row.get("fastest_growing_topics"), []) or []:
            if isinstance(item, dict):
                add_candidate(
                    candidates,
                    item.get("topic"),
                    "insight_fastest_growing_topic",
                    insight_id,
                    compact_text(item.get("reason") or item.get("evidence") or "增长主题"),
                    item,
                )
        for item in load_json_text(row.get("new_topics"), []) or []:
            if isinstance(item, dict):
                add_candidate(
                    candidates,
                    item.get("topic"),
                    "insight_new_topic",
                    insight_id,
                    compact_text(item.get("evidence") or "新出现主题"),
                    item,
                )
        for item in load_json_text(row.get("frequent_pain_points"), []) or []:
            if isinstance(item, dict):
                add_candidate(
                    candidates,
                    item.get("pain_point"),
                    "insight_frequent_pain_point",
                    insight_id,
                    compact_text(item.get("evidence") or "高频痛点"),
                    item,
                )
        for item in load_json_text(row.get("recommended_keywords"), []) or []:
            add_candidate(
                candidates,
                item,
                "insight_recommended_keyword",
                insight_id,
                "Gemini insight 推荐继续追踪。",
                item,
            )
        output = load_json_text(row.get("output_json"), {}) or {}
        for key, item_key, source_type in [
            ("fastest_growing_topics", "topic", "insight_fastest_growing_topic"),
            ("new_topics", "topic", "insight_new_topic"),
            ("frequent_pain_points", "pain_point", "insight_frequent_pain_point"),
        ]:
            for item in output.get(key, []) or []:
                if isinstance(item, dict):
                    add_candidate(
                        candidates,
                        item.get(item_key),
                        source_type,
                        insight_id,
                        compact_text(item.get("reason") or item.get("evidence") or key),
                        item,
                    )
        for item in output.get("recommended_tracking_keywords", []) or []:
            add_candidate(candidates, item, "insight_recommended_keyword", insight_id, "Gemini output 推荐关键词。", item)


def extract_from_observations(candidates: dict[str, Candidate], observations: list[dict[str, Any]]) -> None:
    for row in observations:
        observation_id = str(row.get("observation_id") or "")
        tags = load_json_text(row.get("top_tags_change"), {}) or {}
        for item in tags.get("new_entries", []) or []:
            if isinstance(item, dict):
                add_candidate(
                    candidates,
                    item.get("term"),
                    "observation_new_tag",
                    observation_id,
                    "观察层标签变化中新出现。",
                    item,
                )
        for item in tags.get("current_top", []) or []:
            if isinstance(item, dict):
                count = item.get("count", 0)
                add_candidate(
                    candidates,
                    item.get("term"),
                    "observation_current_tag",
                    observation_id,
                    f"观察层高频标签，出现 {count} 次。",
                    item,
                )
        summary = load_json_text(row.get("summary_json"), {}) or {}
        add_candidate(
            candidates,
            summary.get("keyword"),
            "observation_summary_keyword",
            observation_id,
            "观察摘要中的关键词范围。",
            {"keyword": summary.get("keyword")},
        )
        nested_tags = summary.get("top_tags_change") or {}
        for item in nested_tags.get("new_entries", []) or []:
            if isinstance(item, dict):
                add_candidate(candidates, item.get("term"), "observation_new_tag", observation_id, "summary_json 新标签。", item)
        for anomaly in load_json_text(row.get("anomalies_json"), []) or []:
            if isinstance(anomaly, dict):
                add_candidate(
                    candidates,
                    anomaly.get("type"),
                    "observation_anomaly",
                    observation_id,
                    "观察层异常类型，仅作为风险型候选。",
                    anomaly,
                )


def extract_from_analysis_results(candidates: dict[str, Candidate], rows: list[dict[str, Any]]) -> None:
    for row in rows:
        source_id = str(row.get("analysis_id") or "")
        payloads = [
            load_json_text(row.get("raw_json"), {}) or {},
            load_json_text(row.get("output_text"), {}) or {},
        ]
        for payload in payloads:
            if not isinstance(payload, dict):
                continue
            for item in payload.get("next_search_keywords", []) or []:
                add_candidate(candidates, item, "analysis_result_next_keyword", source_id, "analysis_results 推荐搜索词。", item)
            for item in payload.get("trend_clusters", []) or []:
                if isinstance(item, dict):
                    add_candidate(
                        candidates,
                        item.get("cluster"),
                        "analysis_result_cluster",
                        source_id,
                        compact_text(item.get("one_line") or "analysis_results 趋势聚类。"),
                        item,
                    )


def extract_from_trend_summary(candidates: dict[str, Candidate], summary: dict[str, Any], source_id: str) -> None:
    if not isinstance(summary, dict):
        return
    for item in summary.get("top_keywords_or_tags", []) or []:
        if isinstance(item, dict):
            add_candidate(
                candidates,
                item.get("term"),
                "trend_summary_keyword_or_tag",
                source_id,
                f"trend-db-summary 高频词，出现 {item.get('count', '')} 次。",
                item,
            )


def broadness_penalty(keyword: str) -> tuple[float, list[str]]:
    penalties: list[str] = []
    penalty = 0.0
    tokens = [part for part in re.split(r"\s+", keyword) if part]
    if keyword in BROAD_TERMS:
        penalty += 0.35
        penalties.append("词过于宽泛")
    if len(keyword) <= 3 and keyword.upper() not in {"AI"}:
        penalty += 0.18
        penalties.append("词太短")
    if len(tokens) >= 8:
        penalty += 0.22
        penalties.append("词组过长，可能更像主题句")
    if len(tokens) == 1 and keyword in {"AI", "消费", "行业", "生活", "趋势"}:
        penalty += 0.4
        penalties.append("单词泛化严重")
    return penalty, penalties


def score_candidates(candidates: dict[str, Candidate], existing: set[str]) -> tuple[list[Candidate], list[dict[str, Any]]]:
    skipped_existing: list[dict[str, Any]] = []
    scored: list[Candidate] = []
    for candidate in candidates.values():
        if candidate.keyword in existing:
            skipped_existing.append({"keyword": candidate.keyword, "reason": "already_in_keywords"})
            continue
        total_weight = sum(as_float(source.get("weight")) for source in candidate.sources)
        source_types = {source.get("source_type") for source in candidate.sources}
        occurrence_bonus = min(0.22, max(0, len(candidate.sources) - 1) * 0.045)
        diversity_bonus = min(0.16, max(0, len(source_types) - 1) * 0.04)
        value_bonus = 0.0
        if "insight_recommended_keyword" in source_types:
            value_bonus += 0.16
        if "insight_fastest_growing_topic" in source_types:
            value_bonus += 0.12
        if "insight_frequent_pain_point" in source_types:
            value_bonus += 0.08
        if "observation_new_tag" in source_types:
            value_bonus += 0.08
        penalty, penalties = broadness_penalty(candidate.keyword)
        candidate.penalties.extend(penalties)
        confidence = min(0.98, max(0.05, 0.18 + total_weight * 0.33 + occurrence_bonus + diversity_bonus + value_bonus - penalty))
        candidate.confidence = round(confidence, 4)
        if candidate.confidence >= 0.72:
            candidate.status = "watching"
        elif candidate.confidence >= 0.42:
            candidate.status = "candidate"
        else:
            candidate.status = "rejected"
        scored.append(candidate)
    scored.sort(key=lambda item: (item.status != "watching", -item.confidence, item.keyword))
    return scored, skipped_existing


def build_gemini_prompt(candidates: list[Candidate]) -> str:
    payload = [
        {
            "keyword": item.keyword,
            "confidence": item.confidence,
            "status": item.status,
            "sources": [
                {
                    "source_type": source.get("source_type"),
                    "reason": source.get("reason"),
                }
                for source in item.sources[:6]
            ],
            "reason": item.reason,
        }
        for item in candidates[:40]
    ]
    return f"""你是“小红书趋势关键词发现层”的保守审核器。
你只能基于下面的候选关键词和来源摘要做判断，不允许编造新的采集结果。
目标是发现“值得人工审核后继续追踪”的候选关键词，而不是自动扩张采集范围。

请输出严格 JSON：
{{
  "decisions": [
    {{
      "keyword": "候选关键词",
      "status": "candidate/watching/rejected",
      "confidence": 0.0,
      "reason": "为什么值得或不值得继续追踪",
      "tracking_value": "继续追踪价值",
      "risk": "过宽泛/重复/证据不足等风险"
    }}
  ]
}}

判断要求：
- 已有分数只是参考，你可以保守下调。
- 太宽泛的词，如“AI”“消费”“年轻人”，除非有明确组合语境，否则应 rejected 或低 confidence。
- 更偏好具体、可搜索、可连续追踪的短语。
- 只处理输入里已有 keyword，不要新增列表外的词。

候选输入：
{json.dumps(payload, ensure_ascii=False, indent=2)}
"""


def apply_gemini_decisions(candidates: list[Candidate], decisions: dict[str, Any]) -> None:
    by_keyword = {item.keyword: item for item in candidates}
    for item in decisions.get("decisions", []) or []:
        if not isinstance(item, dict):
            continue
        keyword = normalize_keyword(item.get("keyword"))
        candidate = by_keyword.get(keyword)
        if not candidate:
            continue
        status = str(item.get("status") or "").strip()
        if status not in {"candidate", "watching", "rejected"}:
            status = candidate.status
        confidence = as_float(item.get("confidence"))
        if confidence <= 0:
            confidence = candidate.confidence
        candidate.status = status
        candidate.confidence = round(min(0.98, max(0.05, confidence)), 4)
        candidate.gemini = {
            "reason": compact_text(item.get("reason"), 160),
            "tracking_value": compact_text(item.get("tracking_value"), 160),
            "risk": compact_text(item.get("risk"), 160),
        }


def upsert_candidate(conn: sqlite3.Connection, candidate: Candidate) -> tuple[int, bool]:
    ts = now_iso()
    primary = candidate.primary_source
    raw = {
        "sources": candidate.sources,
        "penalties": candidate.penalties,
        "gemini": candidate.gemini,
        "status": candidate.status,
    }
    before = conn.execute("SELECT discovered_id FROM discovered_keywords WHERE keyword = ?", (candidate.keyword,)).fetchone()
    conn.execute(
        """
        INSERT INTO discovered_keywords(
          keyword, source_type, source_id, reason, confidence, status,
          first_seen_at, last_seen_at, promoted_at, raw_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)
        ON CONFLICT(keyword) DO UPDATE SET
          source_type = excluded.source_type,
          source_id = excluded.source_id,
          reason = excluded.reason,
          confidence = MAX(discovered_keywords.confidence, excluded.confidence),
          status = CASE
            WHEN discovered_keywords.status = 'promoted' THEN discovered_keywords.status
            ELSE excluded.status
          END,
          last_seen_at = excluded.last_seen_at,
          raw_json = excluded.raw_json
        """,
        (
            candidate.keyword,
            primary.get("source_type") or "unknown",
            str(primary.get("source_id") or ""),
            candidate.reason,
            candidate.confidence,
            candidate.status,
            ts,
            ts,
            json.dumps(raw, ensure_ascii=False, sort_keys=True),
        ),
    )
    row = conn.execute("SELECT discovered_id FROM discovered_keywords WHERE keyword = ?", (candidate.keyword,)).fetchone()
    return int(row["discovered_id"]), before is None


def markdown_table(candidates: list[dict[str, Any]]) -> str:
    if not candidates:
        return "暂无"
    lines = ["| 关键词 | 状态 | 置信度 | 理由 |", "| --- | --- | --- | --- |"]
    for item in candidates:
        lines.append(
            "| "
            + " | ".join(
                [
                    str(item.get("keyword", "")).replace("|", "/"),
                    str(item.get("status", "")),
                    f"{as_float(item.get('confidence')):.2f}",
                    compact_text(item.get("reason"), 80).replace("|", "/"),
                ]
            )
            + " |"
        )
    return "\n".join(lines)


def render_markdown(report: dict[str, Any]) -> str:
    rows = report["candidates"]
    new_rows = [item for item in rows if item.get("is_new")]
    watching = [item for item in rows if item.get("status") == "watching"]
    rejected = [item for item in rows if item.get("status") == "rejected"]
    candidate = [item for item in rows if item.get("status") == "candidate"]
    return "\n".join(
        [
            "# 关键词发现报告",
            "",
            f"- 生成时间：{report['generated_at']}",
            f"- 数据库：{report['db_path']}",
            f"- 最近窗口：{report['days']} 天",
            f"- 写入 discovered_keywords：{report['written_count']}",
            f"- 跳过正式 keywords 已存在：{len(report['skipped_existing_keywords'])}",
            "",
            "## 新发现关键词",
            "",
            markdown_table(new_rows),
            "",
            "## 推荐追踪关键词",
            "",
            markdown_table(watching),
            "",
            "## 候选关键词",
            "",
            markdown_table(candidate),
            "",
            "## 暂不建议追踪关键词",
            "",
            markdown_table(rejected),
        ]
    ) + "\n"


def write_outputs(report: dict[str, Any], output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    md_path = output_dir / "discovered-keywords.md"
    json_path = output_dir / "discovered-keywords.json"
    md_path.write_text(render_markdown(report), encoding="utf-8-sig")
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return md_path, json_path


def duplicate_keywords(candidates: list[dict[str, Any]]) -> list[str]:
    seen = set()
    duplicates = set()
    for item in candidates:
        keyword = item.get("keyword")
        if keyword in seen:
            duplicates.add(keyword)
        seen.add(keyword)
    return sorted(duplicates)


def discover_keywords(
    db_path: str | Path,
    days: int,
    output_dir: str | Path,
    env_file: str,
    model: str,
    timeout: int,
    retries: int,
    retry_wait_seconds: int,
    trend_summary: str,
    local_only: bool,
) -> dict[str, Any]:
    db = init_database(db_path)
    out_dir = Path(output_dir)
    if not out_dir.is_absolute():
        out_dir = PROJECT_ROOT / out_dir
    candidates: dict[str, Candidate] = {}
    gemini_status = "not_run"
    gemini_errors: list[dict[str, Any]] = []

    with get_connection(db) as conn:
        formal_keyword_count_before = conn.execute("SELECT COUNT(*) AS count FROM keywords").fetchone()["count"]
        observations = query_recent_observations(conn, days)
        insights = query_recent_insights(conn, days)
        analysis_rows = query_recent_analysis_results(conn, days)
        formal_keywords = existing_keywords(conn)

        extract_from_insights(candidates, insights)
        extract_from_observations(candidates, observations)
        extract_from_analysis_results(candidates, analysis_rows)

        summary_path = Path(trend_summary) if trend_summary else out_dir / "trend-db-summary.json"
        if not summary_path.is_absolute():
            summary_path = PROJECT_ROOT / summary_path
        if summary_path.exists():
            extract_from_trend_summary(candidates, read_json_file(summary_path) or {}, str(summary_path))

        scored, skipped_existing = score_candidates(candidates, formal_keywords)
        if scored and not local_only:
            load_env_file(env_file)
            api_key = (
                os.environ.get("GEMINI_API_KEY")
                or os.environ.get("GOOGLE_API_KEY")
                or os.environ.get("GOOGLE_AI_API_KEY")
            )
            if api_key:
                response, gemini_errors = call_gemini_with_retry(
                    api_key,
                    model,
                    build_gemini_prompt(scored),
                    timeout,
                    retries,
                    retry_wait_seconds,
                )
                if response is None:
                    gemini_status = gemini_errors[-1].get("ai_status", "failed") if gemini_errors else "failed"
                else:
                    try:
                        apply_gemini_decisions(scored, extract_json(response_text(response)))
                        gemini_status = "ok"
                    except Exception as exc:  # noqa: BLE001
                        gemini_status = "parse_failed"
                        gemini_errors.append({"message": str(exc), "ai_status": "parse_failed", "timestamp": now_iso()})
            else:
                gemini_status = "missing_api_key"
        elif local_only:
            gemini_status = "local_only"

        written: list[dict[str, Any]] = []
        with transaction(conn):
            for candidate in scored:
                discovered_id, is_new = upsert_candidate(conn, candidate)
                written.append(
                    {
                        "discovered_id": discovered_id,
                        "keyword": candidate.keyword,
                        "status": candidate.status,
                        "confidence": candidate.confidence,
                        "reason": candidate.reason,
                        "source_type": candidate.primary_source.get("source_type"),
                        "source_id": candidate.primary_source.get("source_id"),
                        "is_new": is_new,
                    }
                )
        formal_keyword_count_after = conn.execute("SELECT COUNT(*) AS count FROM keywords").fetchone()["count"]

    report = {
        "generated_at": now_iso(),
        "db_path": str(resolve_db_path(db)),
        "days": days,
        "source_counts": {
            "observations": len(observations),
            "insights": len(insights),
            "analysis_results": len(analysis_rows),
            "trend_summary_loaded": bool(summary_path.exists()),
        },
        "gemini_status": gemini_status,
        "gemini_errors": gemini_errors,
        "written_count": len(written),
        "candidates": sorted(written, key=lambda item: (-as_float(item["confidence"]), item["keyword"])),
        "skipped_existing_keywords": skipped_existing,
        "duplicate_keywords": duplicate_keywords(written),
        "formal_keyword_count_before": formal_keyword_count_before,
        "formal_keyword_count_after": formal_keyword_count_after,
        "formal_keywords_changed": formal_keyword_count_before != formal_keyword_count_after,
    }
    md_path, json_path = write_outputs(report, out_dir)
    report["markdown_path"] = str(md_path)
    report["json_path"] = str(json_path)
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Discover candidate tracking keywords from observations and insights.")
    parser.add_argument("--db", default="data/xhs-trend.sqlite")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--retries", type=int, default=1)
    parser.add_argument("--retry-wait-seconds", type=int, default=5)
    parser.add_argument("--trend-summary", default="")
    parser.add_argument("--local-only", action="store_true", help="Skip Gemini and use rule-only scoring.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = discover_keywords(
        db_path=args.db,
        days=max(args.days, 1),
        output_dir=args.output_dir,
        env_file=args.env_file,
        model=args.model,
        timeout=args.timeout,
        retries=max(args.retries, 0),
        retry_wait_seconds=max(args.retry_wait_seconds, 0),
        trend_summary=args.trend_summary,
        local_only=args.local_only,
    )
    print(
        "keyword_discovery "
        f"written={report['written_count']} gemini_status={report['gemini_status']} "
        f"formal_keywords_changed={report['formal_keywords_changed']}"
    )
    print(f"markdown={report['markdown_path']}")
    print(f"json={report['json_path']}")
    for item in report["candidates"][:20]:
        print(
            f"candidate keyword={item['keyword']} status={item['status']} "
            f"confidence={item['confidence']:.2f}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
