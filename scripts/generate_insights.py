from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import socket
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from storage.database import get_connection, init_database, resolve_db_path, transaction  # noqa: E402


CN_TZ = timezone(timedelta(hours=8))
DEFAULT_MODEL = "gemini-flash-lite-latest"
INSIGHT_LIST_KEYS = (
    "fastest_growing_topics",
    "new_topics",
    "frequent_pain_points",
    "recommended_tracking_keywords",
    "risk_warnings",
)


def now_iso() -> str:
    return datetime.now(CN_TZ).isoformat(timespec="seconds")


def today_cn() -> date:
    return datetime.now(CN_TZ).date()


def rows_to_dicts(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


def as_int(value: Any) -> int:
    if value is None or value == "":
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def dump_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def load_json_text(value: str | None, default: Any = None) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


def load_env_file(path: str) -> None:
    if not path:
        return
    env_path = Path(path)
    if not env_path.is_absolute():
        env_path = PROJECT_ROOT / env_path
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().lstrip("\ufeff")
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def stable_hash(*parts: Any, length: int = 32) -> str:
    text = "|".join(str(part or "") for part in parts)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:length]


def compact_text(value: Any, limit: int = 800) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def keyword_id_for(conn: sqlite3.Connection, keyword: str) -> int | None:
    if not keyword:
        return None
    row = conn.execute("SELECT keyword_id FROM keywords WHERE keyword = ?", (keyword,)).fetchone()
    return int(row["keyword_id"]) if row else None


def period_bounds(days: int) -> tuple[str, str]:
    end = today_cn()
    start = end - timedelta(days=max(days, 1) - 1)
    return start.isoformat(), end.isoformat()


def query_observations(
    conn: sqlite3.Connection,
    start_date: str,
    end_date: str,
    keyword: str,
) -> list[dict[str, Any]]:
    if keyword:
        rows = conn.execute(
            """
            SELECT *
            FROM observations
            WHERE keyword = ?
              AND observation_date BETWEEN ? AND ?
            ORDER BY observation_date ASC, observation_id ASC
            """,
            (keyword, start_date, end_date),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT *
            FROM observations
            WHERE observation_date BETWEEN ? AND ?
            ORDER BY observation_date ASC, keyword_id ASC, observation_id ASC
            """,
            (start_date, end_date),
        ).fetchall()
    return rows_to_dicts(rows)


def parse_observation(row: dict[str, Any]) -> dict[str, Any]:
    summary = load_json_text(row.get("summary_json"), {}) or {}
    top_authors_change = load_json_text(row.get("top_authors_change"), {}) or {}
    top_tags_change = load_json_text(row.get("top_tags_change"), {}) or {}
    anomalies = load_json_text(row.get("anomalies_json"), []) or []
    return {
        "observation_id": row.get("observation_id"),
        "run_id": row.get("run_id"),
        "keyword_id": row.get("keyword_id"),
        "date": row.get("observation_date"),
        "keyword": row.get("keyword"),
        "new_note_count": as_int(row.get("new_note_count")),
        "new_author_count": as_int(row.get("new_author_count")),
        "liked_growth": as_int(row.get("liked_growth")),
        "collected_growth": as_int(row.get("collected_growth")),
        "comment_growth": as_int(row.get("comment_growth")),
        "top_authors_change": top_authors_change,
        "top_tags_change": top_tags_change,
        "anomalies": anomalies,
        "summary": {
            "current_note_count": summary.get("current_note_count"),
            "previous_note_count": summary.get("previous_note_count"),
            "current_metrics": summary.get("current_metrics", {}),
            "previous_metrics": summary.get("previous_metrics", {}),
            "growth": summary.get("growth", {}),
            "current_failure_count": summary.get("current_failure_count"),
            "previous_failure_count": summary.get("previous_failure_count"),
            "failure_growth": summary.get("failure_growth"),
        },
    }


def collect_new_terms(observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counter: Counter[str] = Counter()
    for observation in observations:
        changes = observation.get("top_tags_change") or {}
        for item in changes.get("new_entries", []) or []:
            term = str(item.get("term") or "").strip()
            if term:
                counter[term] += 2
        for item in changes.get("current_top", []) or []:
            term = str(item.get("term") or "").strip()
            if term:
                counter[term] += as_int(item.get("count")) or 1
    return [{"topic": term, "score": count} for term, count in counter.most_common(20)]


def aggregate_observations(
    observations: list[dict[str, Any]],
    start_date: str,
    end_date: str,
    keyword: str,
) -> dict[str, Any]:
    keyword_growth: Counter[str] = Counter()
    anomaly_counter: Counter[str] = Counter()
    author_counter: Counter[str] = Counter()
    total_new_notes = 0
    total_new_authors = 0
    total_liked_growth = 0
    total_collected_growth = 0
    total_comment_growth = 0

    for observation in observations:
        name = str(observation.get("keyword") or "")
        score = (
            observation["liked_growth"]
            + observation["collected_growth"] * 2
            + observation["comment_growth"] * 3
        )
        keyword_growth[name] += score
        total_new_notes += observation["new_note_count"]
        total_new_authors += observation["new_author_count"]
        total_liked_growth += observation["liked_growth"]
        total_collected_growth += observation["collected_growth"]
        total_comment_growth += observation["comment_growth"]
        for anomaly in observation.get("anomalies") or []:
            anomaly_type = str(anomaly.get("type") or "unknown")
            anomaly_counter[anomaly_type] += 1
        changes = observation.get("top_authors_change") or {}
        for item in changes.get("current_top", []) or []:
            nickname = str(item.get("nickname") or item.get("author_id") or "").strip()
            if nickname:
                author_counter[nickname] += as_int(item.get("note_count")) or 1

    return {
        "period_start": start_date,
        "period_end": end_date,
        "keyword_scope": keyword or "全库",
        "observation_count": len(observations),
        "total_new_notes": total_new_notes,
        "total_new_authors": total_new_authors,
        "total_liked_growth": total_liked_growth,
        "total_collected_growth": total_collected_growth,
        "total_comment_growth": total_comment_growth,
        "keyword_growth_rank": [
            {"keyword": term, "growth_score": score}
            for term, score in keyword_growth.most_common(20)
        ],
        "new_topic_candidates": collect_new_terms(observations),
        "top_authors": [
            {"author": author, "score": count}
            for author, count in author_counter.most_common(20)
        ],
        "anomaly_counts": [
            {"type": item, "count": count}
            for item, count in anomaly_counter.most_common(20)
        ],
    }


def insight_input_payload(
    observations: list[dict[str, Any]],
    start_date: str,
    end_date: str,
    keyword: str,
    days: int,
) -> dict[str, Any]:
    parsed = [parse_observation(row) for row in observations]
    aggregate = aggregate_observations(parsed, start_date, end_date, keyword)
    return {
        "meta": {
            "source": "observations",
            "period_start": start_date,
            "period_end": end_date,
            "days": days,
            "keyword": keyword,
            "observation_count": len(parsed),
            "generated_at": now_iso(),
        },
        "aggregate": aggregate,
        "observations": parsed,
    }


def build_prompt(payload: dict[str, Any]) -> str:
    return f"""你是一个“小红书趋势观察层”的 AI 分析器。
你只能读取下面的 observations 汇总，不允许假设你看过原始 notes、评论全文或详情页。
请根据最近 7 天 observation 记录，输出严格 JSON，不要 Markdown，不要代码块。

需要输出的 JSON 结构：
{{
  "current_trend_level": "数据不足/低/中/高/爆发",
  "fastest_growing_topics": [
    {{"topic": "主题", "reason": "为什么增长最快", "evidence": "来自 observations 的证据", "score": 0}}
  ],
  "new_topics": [
    {{"topic": "新出现主题", "evidence": "来自标签变化或关键词变化的证据"}}
  ],
  "frequent_pain_points": [
    {{"pain_point": "高频痛点", "evidence": "来自异常、作者变化、标签变化或增长结构的证据"}}
  ],
  "recommended_tracking_keywords": ["建议继续追踪关键词"],
  "risk_warnings": ["风险提示"],
  "one_sentence_summary": "一句话总结"
}}

判断规则：
- 如果 observation_count < 2 或增长数据全为 0，要明确说数据不足或趋势弱。
- 增长主题优先看 liked_growth、collected_growth、comment_growth 的组合，而不是单次热度。
- 新主题优先来自 top_tags_change.new_entries 和 top_tags_change.current_top。
- 高频痛点可以来自 anomalies、失败增长、评论增长异常、收藏增长异常、作者变化。
- 风险提示必须指出样本不足、同日重复采集、账号/采集异常、数据偏差等可能问题。
- 不要编造 observations 中没有的具体帖子内容。

observations 输入：
{json.dumps(payload, ensure_ascii=False, indent=2)}
"""


def first_json_object(text: str) -> str:
    start = text.find("{")
    if start < 0:
        raise ValueError("Gemini response did not contain a JSON object")
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    raise ValueError("Gemini response JSON object was incomplete")


def extract_json(text: str) -> dict[str, Any]:
    clean = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", clean, re.DOTALL | re.IGNORECASE)
    if fence:
        clean = fence.group(1).strip()
    try:
        data = json.loads(clean)
    except json.JSONDecodeError:
        data = json.loads(first_json_object(clean))
    if not isinstance(data, dict):
        raise ValueError("Gemini response JSON must be an object")
    return data


def response_text(response: dict[str, Any]) -> str:
    candidates = response.get("candidates") or []
    if not candidates:
        raise ValueError("Gemini response has no candidates")
    parts = candidates[0].get("content", {}).get("parts") or []
    text = "\n".join(part.get("text", "") for part in parts if isinstance(part, dict)).strip()
    if not text:
        raise ValueError("Gemini response candidate has no text")
    return text


def is_timeout_error(exc: BaseException) -> bool:
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return True
    if isinstance(exc, urllib.error.URLError):
        reason = getattr(exc, "reason", None)
        return isinstance(reason, (TimeoutError, socket.timeout)) or "timed out" in str(reason).lower()
    return "timed out" in str(exc).lower()


def call_gemini_once(api_key: str, model: str, prompt: str, timeout: int) -> dict[str, Any]:
    model_name = model.removeprefix("models/")
    encoded_model = urllib.parse.quote(model_name, safe="")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{encoded_model}:generateContent?key={urllib.parse.quote(api_key)}"
    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.2,
            "topP": 0.9,
            "responseMimeType": "application/json",
        },
    }
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Gemini API HTTP {exc.code}: {body[:800]}") from exc
    except urllib.error.URLError as exc:
        if is_timeout_error(exc):
            raise TimeoutError(f"Gemini read timed out after {timeout}s") from exc
        raise RuntimeError(f"Gemini API failed: {exc}") from exc


def call_gemini_with_retry(
    api_key: str,
    model: str,
    prompt: str,
    timeout: int,
    retries: int,
    retry_wait_seconds: int,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    errors: list[dict[str, Any]] = []
    for attempt in range(retries + 1):
        try:
            return call_gemini_once(api_key, model, prompt, timeout), errors
        except Exception as exc:  # noqa: BLE001 - Gemini errors become stored insight metadata.
            errors.append(
                {
                    "attempt": attempt + 1,
                    "ai_status": "timeout" if is_timeout_error(exc) else "failed",
                    "message": str(exc),
                    "timestamp": now_iso(),
                }
            )
            if attempt < retries:
                time.sleep(retry_wait_seconds)
    return None, errors


def ensure_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if value in (None, ""):
        return []
    return [value]


def normalize_insight_output(data: dict[str, Any], payload: dict[str, Any], ai_status: str) -> dict[str, Any]:
    aggregate = payload.get("aggregate", {})
    normalized = {
        "current_trend_level": compact_text(data.get("current_trend_level") or "数据不足", 20),
        "fastest_growing_topics": ensure_list(data.get("fastest_growing_topics")),
        "new_topics": ensure_list(data.get("new_topics")),
        "frequent_pain_points": ensure_list(data.get("frequent_pain_points")),
        "recommended_tracking_keywords": ensure_list(data.get("recommended_tracking_keywords")),
        "risk_warnings": ensure_list(data.get("risk_warnings")),
        "one_sentence_summary": compact_text(data.get("one_sentence_summary") or "", 240),
        "ai_status": ai_status,
    }
    if not normalized["one_sentence_summary"]:
        normalized["one_sentence_summary"] = (
            f"{aggregate.get('keyword_scope', '全库')} 最近窗口内有 "
            f"{aggregate.get('observation_count', 0)} 条观察，新增帖子 "
            f"{aggregate.get('total_new_notes', 0)}，点赞增长 "
            f"{aggregate.get('total_liked_growth', 0)}。"
        )
    return normalized


def local_fallback_insight(payload: dict[str, Any], status: str, errors: list[dict[str, Any]]) -> dict[str, Any]:
    aggregate = payload.get("aggregate", {})
    observation_count = as_int(aggregate.get("observation_count"))
    total_growth_score = (
        as_int(aggregate.get("total_liked_growth"))
        + as_int(aggregate.get("total_collected_growth")) * 2
        + as_int(aggregate.get("total_comment_growth")) * 3
    )
    if observation_count < 2:
        level = "数据不足"
    elif total_growth_score <= 0:
        level = "低"
    elif total_growth_score < 10000:
        level = "中"
    elif total_growth_score < 50000:
        level = "高"
    else:
        level = "爆发"

    fastest = [
        {
            "topic": item.get("keyword"),
            "reason": "按 observations 的点赞、收藏、评论增长加权排序。",
            "evidence": f"growth_score={item.get('growth_score')}",
            "score": item.get("growth_score", 0),
        }
        for item in aggregate.get("keyword_growth_rank", [])[:5]
    ]
    new_topics = [
        {"topic": item.get("topic"), "evidence": f"observation 标签变化候选，score={item.get('score')}"}
        for item in aggregate.get("new_topic_candidates", [])[:8]
    ]
    pain_points = [
        {"pain_point": item.get("type"), "evidence": f"异常出现 {item.get('count')} 次"}
        for item in aggregate.get("anomaly_counts", [])[:8]
    ]
    risk_warnings = []
    if observation_count < 2:
        risk_warnings.append("最近 7 天 observation 数不足，趋势判断只能作为弱信号。")
    if total_growth_score == 0:
        risk_warnings.append("最近窗口互动增长为 0，可能是重复 smoke 数据或真实增长不足。")
    if errors:
        risk_warnings.append("Gemini 未成功完成，本条 insight 使用本地规则兜底。")
    return normalize_insight_output(
        {
            "current_trend_level": level,
            "fastest_growing_topics": fastest,
            "new_topics": new_topics,
            "frequent_pain_points": pain_points,
            "recommended_tracking_keywords": [
                item.get("topic") for item in new_topics[:8] if item.get("topic")
            ],
            "risk_warnings": risk_warnings,
            "one_sentence_summary": (
                f"{aggregate.get('keyword_scope', '全库')} 最近 {payload.get('meta', {}).get('days', 7)} 天"
                f"累计 {observation_count} 条 observation，趋势等级为 {level}。"
            ),
        },
        payload,
        status,
    )


def build_insight_key(
    keyword: str,
    start_date: str,
    end_date: str,
    model: str,
    observations: list[dict[str, Any]],
) -> str:
    observation_ids = ",".join(str(row.get("observation_id")) for row in observations)
    return f"insight:{stable_hash(keyword, start_date, end_date, model, observation_ids)}"


def upsert_insight(
    conn: sqlite3.Connection,
    insight_key: str,
    keyword_id: int | None,
    keyword: str,
    start_date: str,
    end_date: str,
    observation_count: int,
    model: str,
    payload: dict[str, Any],
    output: dict[str, Any],
) -> int:
    ts = now_iso()
    conn.execute(
        """
        INSERT INTO insights(
          insight_key, keyword_id, keyword, period_start, period_end,
          observation_count, current_trend_level, fastest_growing_topics,
          new_topics, frequent_pain_points, recommended_keywords,
          risk_warnings, one_sentence_summary, input_json, output_json,
          model_name, ai_status, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(insight_key) DO UPDATE SET
          keyword_id = excluded.keyword_id,
          keyword = excluded.keyword,
          period_start = excluded.period_start,
          period_end = excluded.period_end,
          observation_count = excluded.observation_count,
          current_trend_level = excluded.current_trend_level,
          fastest_growing_topics = excluded.fastest_growing_topics,
          new_topics = excluded.new_topics,
          frequent_pain_points = excluded.frequent_pain_points,
          recommended_keywords = excluded.recommended_keywords,
          risk_warnings = excluded.risk_warnings,
          one_sentence_summary = excluded.one_sentence_summary,
          input_json = excluded.input_json,
          output_json = excluded.output_json,
          model_name = excluded.model_name,
          ai_status = excluded.ai_status,
          updated_at = excluded.updated_at
        """,
        (
            insight_key,
            keyword_id,
            keyword,
            start_date,
            end_date,
            observation_count,
            output["current_trend_level"],
            dump_json(output["fastest_growing_topics"]),
            dump_json(output["new_topics"]),
            dump_json(output["frequent_pain_points"]),
            dump_json(output["recommended_tracking_keywords"]),
            dump_json(output["risk_warnings"]),
            output["one_sentence_summary"],
            dump_json(payload),
            dump_json(output),
            model,
            output["ai_status"],
            ts,
            ts,
        ),
    )
    row = conn.execute("SELECT insight_id FROM insights WHERE insight_key = ?", (insight_key,)).fetchone()
    return int(row["insight_id"])


def generate_insight(
    db_path: str | Path,
    keyword: str,
    days: int,
    model: str,
    env_file: str,
    timeout: int,
    retries: int,
    retry_wait_seconds: int,
    local_only: bool,
) -> dict[str, Any]:
    db = init_database(db_path)
    start_date, end_date = period_bounds(days)
    load_env_file(env_file)
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or os.environ.get("GOOGLE_AI_API_KEY")

    with get_connection(db) as conn:
        observations = query_observations(conn, start_date, end_date, keyword)
        payload = insight_input_payload(observations, start_date, end_date, keyword, days)
        key = build_insight_key(keyword, start_date, end_date, model, observations)
        output: dict[str, Any]
        errors: list[dict[str, Any]] = []
        raw_response: dict[str, Any] | None = None

        if not observations:
            output = local_fallback_insight(payload, "no_observations", [])
        elif local_only:
            output = local_fallback_insight(payload, "local_only", [])
        elif not api_key:
            errors = [
                {
                    "attempt": 0,
                    "ai_status": "missing_api_key",
                    "message": "Missing Gemini API key.",
                    "timestamp": now_iso(),
                }
            ]
            output = local_fallback_insight(payload, "missing_api_key", errors)
        else:
            raw_response, errors = call_gemini_with_retry(api_key, model, build_prompt(payload), timeout, retries, retry_wait_seconds)
            if raw_response is None:
                status = errors[-1]["ai_status"] if errors else "failed"
                output = local_fallback_insight(payload, status, errors)
            else:
                try:
                    output = normalize_insight_output(extract_json(response_text(raw_response)), payload, "ok")
                except Exception as exc:  # noqa: BLE001
                    errors.append(
                        {
                            "attempt": retries + 1,
                            "ai_status": "parse_failed",
                            "message": str(exc),
                            "timestamp": now_iso(),
                        }
                    )
                    output = local_fallback_insight(payload, "parse_failed", errors)

        if errors:
            output["gemini_errors"] = errors
        if raw_response is not None:
            output["gemini_response_meta"] = {
                "candidate_count": len(raw_response.get("candidates") or []),
                "usageMetadata": raw_response.get("usageMetadata", {}),
            }

        keyword_id = keyword_id_for(conn, keyword) if keyword else None
        with transaction(conn):
            insight_id = upsert_insight(
                conn,
                insight_key=key,
                keyword_id=keyword_id,
                keyword=keyword,
                start_date=start_date,
                end_date=end_date,
                observation_count=len(observations),
                model=model,
                payload=payload,
                output=output,
            )

    return {
        "db_path": str(resolve_db_path(db)),
        "insight_id": insight_id,
        "insight_key": key,
        "keyword": keyword,
        "period_start": start_date,
        "period_end": end_date,
        "observation_count": len(observations),
        "model": model,
        "ai_status": output.get("ai_status"),
        "current_trend_level": output.get("current_trend_level"),
        "one_sentence_summary": output.get("one_sentence_summary"),
        "output": output,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate AI insights from recent observation records.")
    parser.add_argument("--db", default="data/xhs-trend.sqlite")
    parser.add_argument("--keyword", default="", help="Optional keyword scope. Empty means all observations.")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--model", default=os.environ.get("GEMINI_MODEL", DEFAULT_MODEL))
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--retries", type=int, default=1)
    parser.add_argument("--retry-wait-seconds", type=int, default=5)
    parser.add_argument("--local-only", action="store_true", help="Skip Gemini and write a rule-based fallback insight.")
    parser.add_argument("--json-report", default="", help="Optional JSON report path.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = generate_insight(
        db_path=args.db,
        keyword=" ".join(args.keyword.split()).strip(),
        days=max(args.days, 1),
        model=args.model,
        env_file=args.env_file,
        timeout=args.timeout,
        retries=max(args.retries, 0),
        retry_wait_seconds=max(args.retry_wait_seconds, 0),
        local_only=args.local_only,
    )
    if args.json_report:
        out = Path(args.json_report)
        if not out.is_absolute():
            out = PROJECT_ROOT / out
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        "insight "
        f"id={report['insight_id']} status={report['ai_status']} "
        f"level={report['current_trend_level']} observations={report['observation_count']} "
        f"db={report['db_path']}"
    )
    print(report["one_sentence_summary"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
