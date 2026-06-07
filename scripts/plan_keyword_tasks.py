from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from storage.database import resolve_db_path  # noqa: E402


CN_TZ = timezone(timedelta(hours=8))
BROAD_TERMS = {"AI", "人工智能", "消费", "行业", "生活", "趋势", "年轻人", "真实逻辑", "信息差"}


def now_iso() -> str:
    return datetime.now(CN_TZ).isoformat(timespec="seconds")


def connect_readonly(db_path: Path) -> sqlite3.Connection:
    if not db_path.exists():
        raise FileNotFoundError(f"Database not found: {db_path}")
    uri_path = urllib.parse.quote(db_path.resolve().as_posix(), safe="/:")
    conn = sqlite3.connect(f"file:{uri_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def rows_to_dicts(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


def as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def as_float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def load_json(value: str | None, default: Any = None) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


def compact_text(value: Any, limit: int = 90) -> str:
    if value is None:
        text = ""
    else:
        text = " ".join(str(value).split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "..."


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    text = str(value).replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        try:
            return datetime.fromisoformat(text[:10])
        except ValueError:
            return None


def days_since(value: str | None) -> int | None:
    dt = parse_datetime(value)
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=CN_TZ)
    return max(0, (datetime.now(CN_TZ) - dt.astimezone(CN_TZ)).days)


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?", (table,)).fetchone()
    return row is not None


def table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    if not table_exists(conn, table):
        return set()
    return {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def query_keywords(conn: sqlite3.Connection, category: str) -> list[dict[str, Any]]:
    columns = table_columns(conn, "keywords")
    if not columns:
        return []
    select_status = "status" if "status" in columns else "'' AS status"
    where = ["enabled = 1" if "enabled" in columns else "1=1"]
    params: list[Any] = []
    if "status" in columns:
        where.append("(status IS NULL OR status = '' OR status NOT IN ('disabled', 'paused', 'archived', 'rejected'))")
    if category:
        where.append("category = ?")
        params.append(category)
    rows = conn.execute(
        f"""
        SELECT keyword_id, keyword, category, enabled, created_at, updated_at, {select_status}
        FROM keywords
        WHERE {" AND ".join(where)}
        ORDER BY keyword_id ASC
        """,
        tuple(params),
    ).fetchall()
    return rows_to_dicts(rows)


def discovered_by_keyword(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    if not table_exists(conn, "discovered_keywords"):
        return {}
    rows = conn.execute(
        """
        SELECT *
        FROM discovered_keywords
        ORDER BY last_seen_at DESC, discovered_id DESC
        """
    ).fetchall()
    out: dict[str, dict[str, Any]] = {}
    for row in rows_to_dicts(rows):
        out.setdefault(str(row.get("keyword") or ""), row)
    return out


def crawl_stats(conn: sqlite3.Connection, keyword_id: int) -> dict[str, Any]:
    if not table_exists(conn, "crawl_runs"):
        return {"last_crawled_at": "", "crawl_count": 0}
    has_map = table_exists(conn, "keyword_note_map")
    if has_map:
        row = conn.execute(
            """
            WITH run_ids AS (
              SELECT run_id FROM crawl_runs WHERE keyword_id = ?
              UNION
              SELECT run_id FROM keyword_note_map WHERE keyword_id = ?
            )
            SELECT
              COUNT(DISTINCT cr.run_id) AS crawl_count,
              MAX(COALESCE(cr.finished_at, cr.started_at)) AS last_crawled_at
            FROM run_ids r
            JOIN crawl_runs cr ON cr.run_id = r.run_id
            """,
            (keyword_id, keyword_id),
        ).fetchone()
    else:
        row = conn.execute(
            """
            SELECT
              COUNT(DISTINCT run_id) AS crawl_count,
              MAX(COALESCE(finished_at, started_at)) AS last_crawled_at
            FROM crawl_runs
            WHERE keyword_id = ?
            """,
            (keyword_id,),
        ).fetchone()
    return {
        "last_crawled_at": row["last_crawled_at"] if row else "",
        "crawl_count": as_int(row["crawl_count"] if row else 0),
    }


def observation_stats(conn: sqlite3.Connection, keyword_id: int, keyword: str, days: int) -> dict[str, Any]:
    if not table_exists(conn, "observations"):
        return {"recent_observation_count": 0, "growth_score": 0, "latest_observation_date": ""}
    row = conn.execute(
        """
        SELECT
          COUNT(*) AS recent_observation_count,
          MAX(observation_date) AS latest_observation_date,
          SUM(COALESCE(liked_growth, 0)) AS liked_growth,
          SUM(COALESCE(collected_growth, 0)) AS collected_growth,
          SUM(COALESCE(comment_growth, 0)) AS comment_growth
        FROM observations
        WHERE (keyword_id = ? OR keyword = ?)
          AND observation_date >= date('now', ?)
        """,
        (keyword_id, keyword, f"-{max(days, 1) - 1} days"),
    ).fetchone()
    liked = as_int(row["liked_growth"] if row else 0)
    collected = as_int(row["collected_growth"] if row else 0)
    comments = as_int(row["comment_growth"] if row else 0)
    return {
        "recent_observation_count": as_int(row["recent_observation_count"] if row else 0),
        "latest_observation_date": row["latest_observation_date"] if row else "",
        "liked_growth": liked,
        "collected_growth": collected,
        "comment_growth": comments,
        "growth_score": liked + collected * 2 + comments * 3,
    }


def latest_insight_for_keyword(conn: sqlite3.Connection, keyword_id: int, keyword: str, days: int) -> dict[str, Any]:
    if not table_exists(conn, "insights"):
        return {}
    row = conn.execute(
        """
        SELECT *
        FROM insights
        WHERE (keyword_id = ? OR keyword = ?)
          AND period_end >= date('now', ?)
        ORDER BY period_end DESC, insight_id DESC
        LIMIT 1
        """,
        (keyword_id, keyword, f"-{max(days, 1) - 1} days"),
    ).fetchone()
    if row:
        return dict(row)
    row = conn.execute(
        """
        SELECT *
        FROM insights
        WHERE (keyword IS NULL OR keyword = '')
          AND period_end >= date('now', ?)
        ORDER BY period_end DESC, insight_id DESC
        LIMIT 1
        """,
        (f"-{max(days, 1) - 1} days",),
    ).fetchone()
    return dict(row) if row else {}


def insight_recommends_keyword(insight: dict[str, Any], keyword: str) -> bool:
    values: list[Any] = []
    values.extend(load_json(insight.get("recommended_keywords"), []) or [])
    output = load_json(insight.get("output_json"), {}) or {}
    values.extend(output.get("recommended_tracking_keywords", []) or [])
    candidates = {str(item).strip() for item in values if str(item).strip()}
    if keyword in candidates:
        return True
    return any(keyword in item or item in keyword for item in candidates if len(item) >= 3)


def insight_summary(insight: dict[str, Any]) -> str:
    if not insight:
        return ""
    return compact_text(insight.get("one_sentence_summary") or "", 140)


def is_recent_promoted(discovered: dict[str, Any] | None, days: int) -> bool:
    if not discovered or discovered.get("status") != "promoted":
        return False
    age = days_since(discovered.get("promoted_at"))
    return age is not None and age <= max(days, 1)


def is_broad_keyword(keyword: str) -> bool:
    if keyword in BROAD_TERMS:
        return True
    parts = [part for part in keyword.split() if part]
    return len(parts) == 1 and len(keyword) <= 3 and keyword.upper() != "AI"


def score_keyword(
    keyword_row: dict[str, Any],
    discovered: dict[str, Any] | None,
    crawls: dict[str, Any],
    observations: dict[str, Any],
    insight: dict[str, Any],
    days: int,
    category_filter: str,
) -> dict[str, Any]:
    keyword = str(keyword_row.get("keyword") or "")
    score = 20
    reasons: list[str] = []
    source = "keywords"
    if discovered:
        source = f"keywords + discovered_keywords:{discovered.get('status')}"
    if is_recent_promoted(discovered, days):
        score += 35
        reasons.append("最近人工晋升，优先进入下一轮人工确认采集计划。")
    confidence = as_float(discovered.get("confidence") if discovered else 0)
    if confidence >= 0.7:
        score += 15
        reasons.append(f"discovered_keywords 置信度较高：{confidence:.2f}。")
    elif confidence >= 0.5:
        score += 10
        reasons.append(f"discovered_keywords 置信度可用：{confidence:.2f}。")
    if insight_recommends_keyword(insight, keyword):
        score += 20
        reasons.append("最近 insight 建议继续追踪。")
    if observations["growth_score"] > 0:
        score += 20
        reasons.append(
            "最近 observations 有互动增长："
            f"赞 {observations['liked_growth']} / 藏 {observations['collected_growth']} / 评 {observations['comment_growth']}。"
        )
        if observations["growth_score"] >= 10000:
            score += 10
            reasons.append("增长分较高。")
    last_age = days_since(crawls.get("last_crawled_at"))
    if not crawls.get("last_crawled_at"):
        score += 15
        reasons.append("尚无采集记录。")
    elif last_age is not None and last_age >= days:
        score += 12
        reasons.append(f"过去 {days} 天没有采集。")
    elif last_age is not None and last_age <= 2:
        score -= 12
        reasons.append("最近已经采集过，避免过密采集。")
    if crawls["crawl_count"] >= 3 and last_age is not None and last_age < days:
        score -= 18
        reasons.append("累计采集次数较多且最近窗口内已采集，建议暂缓。")
    if category_filter:
        score += 8
        reasons.append("匹配本次指定 category。")
    elif keyword_row.get("category") and any(term in str(keyword_row.get("category")) for term in ["AI", "趋势", "重点"]):
        score += 4
        reasons.append("category 带重点方向信号。")
    if is_broad_keyword(keyword):
        score -= 25
        reasons.append("关键词偏宽泛，建议先人工收窄。")
    summary = insight_summary(insight)
    if "数据不足" in summary and not discovered and observations["growth_score"] <= 0:
        score -= 8
        reasons.append("最近 insight 数据不足且缺少增长证据。")
    priority = "high" if score >= 65 else "medium" if score >= 40 else "low"
    status = "ready" if priority in {"high", "medium"} else "postpone"
    if priority == "low":
        reasons.append("综合优先级较低，建议暂缓采集。")
    suggested_limit = 30 if priority == "high" else 20 if priority == "medium" else 10
    return {
        "keyword": keyword,
        "category": keyword_row.get("category") or "",
        "priority": priority,
        "priority_score": score,
        "reason": "；".join(reasons) or "正式 keywords 中启用的关键词，等待人工确认。",
        "source": source,
        "is_recent_promoted": is_recent_promoted(discovered, days),
        "last_crawled_at": crawls.get("last_crawled_at") or "",
        "crawl_count": crawls["crawl_count"],
        "recent_observation_count": observations["recent_observation_count"],
        "recent_insight_summary": summary,
        "suggested_limit": suggested_limit,
        "status": status,
    }


def build_plan(
    conn: sqlite3.Connection,
    days: int,
    category: str,
    limit_keywords: int,
    new_only: bool,
) -> dict[str, Any]:
    discovered_map = discovered_by_keyword(conn)
    rows = query_keywords(conn, category)
    planned = []
    for row in rows:
        keyword_id = as_int(row.get("keyword_id"))
        keyword = str(row.get("keyword") or "")
        discovered = discovered_map.get(keyword)
        if new_only and not is_recent_promoted(discovered, days):
            continue
        crawls = crawl_stats(conn, keyword_id)
        observations = observation_stats(conn, keyword_id, keyword, days)
        insight = latest_insight_for_keyword(conn, keyword_id, keyword, days)
        planned.append(score_keyword(row, discovered, crawls, observations, insight, days, category))
    planned.sort(
        key=lambda item: (
            item["status"] != "ready",
            {"high": 0, "medium": 1, "low": 2}.get(item["priority"], 3),
            -item["priority_score"],
            item["keyword"],
        )
    )
    limited = planned[: max(limit_keywords, 1)]
    return {
        "generated_at": now_iso(),
        "plan_type": "manual_review",
        "days": days,
        "category": category,
        "new_only": new_only,
        "limit_keywords": limit_keywords,
        "keywords": limited,
        "excluded_count": max(0, len(planned) - len(limited)),
    }


def markdown_table(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "暂无关键词。"
    headers = [
        ("关键词", "keyword"),
        ("来源", "source"),
        ("新晋升", "is_recent_promoted"),
        ("上次采集", "last_crawled_at"),
        ("采集次数", "crawl_count"),
        ("观察数", "recent_observation_count"),
        ("优先级", "priority"),
        ("状态", "status"),
        ("原因", "reason"),
    ]
    lines = ["| " + " | ".join(label for label, _ in headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    for row in rows:
        values = []
        for _, key in headers:
            value = row.get(key, "")
            if key == "is_recent_promoted":
                value = "是" if value else "否"
            values.append(compact_text(value, 70).replace("|", "/"))
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines)


def render_markdown(plan: dict[str, Any]) -> str:
    ready = [item for item in plan["keywords"] if item["status"] == "ready"]
    postponed = [item for item in plan["keywords"] if item["status"] != "ready"]
    lines = [
        "# 关键词采集任务计划",
        "",
        f"- 生成时间：{plan['generated_at']}",
        "- 计划类型：manual_review",
        f"- 最近窗口：{plan['days']} 天",
        f"- category 过滤：{plan['category'] or '无'}",
        f"- 只看新晋升：{'是' if plan['new_only'] else '否'}",
        f"- 本轮输出关键词数：{len(plan['keywords'])}",
        f"- 因 limit 暂未列出：{plan['excluded_count']}",
        "",
        "## 本轮建议采集关键词",
        "",
        markdown_table(ready),
        "",
        "## 建议暂缓采集关键词",
        "",
        markdown_table(postponed),
        "",
        "## 最近 insight 结论",
        "",
    ]
    for item in plan["keywords"]:
        lines.append(f"- {item['keyword']}：{item.get('recent_insight_summary') or '暂无 insight'}")
    return "\n".join(lines) + "\n"


def request_draft(plan: dict[str, Any]) -> dict[str, Any]:
    ready_keywords = [item for item in plan["keywords"] if item["status"] == "ready"]
    query = " ".join(item["keyword"] for item in ready_keywords)
    return {
        "meta": {
            "source": "keyword_task_plan",
            "generated_at": plan["generated_at"],
            "plan_type": plan["plan_type"],
            "manual_review_required": True,
            "note": "This is a draft only. It is not an official request.json and does not trigger collection.",
        },
        "query": query,
        "topic": "keyword-task-plan",
        "expanded_queries": [item["keyword"] for item in ready_keywords],
        "keywords": ready_keywords,
        "suggested_limits": {
            "max_notes": max([item["suggested_limit"] for item in ready_keywords], default=20),
            "per_query": min(10, max([item["suggested_limit"] for item in ready_keywords], default=20)),
        },
    }


def write_outputs(plan: dict[str, Any], output_dir: Path, include_request_draft: bool) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    md_path = output_dir / "keyword-task-plan.md"
    json_path = output_dir / "keyword-task-plan.json"
    md_path.write_text(render_markdown(plan), encoding="utf-8-sig")
    json_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    paths = {"markdown_path": str(md_path), "json_path": str(json_path)}
    if include_request_draft:
        draft_path = output_dir / "request-draft.json"
        draft_path.write_text(json.dumps(request_draft(plan), ensure_ascii=False, indent=2), encoding="utf-8")
        paths["request_draft_path"] = str(draft_path)
    return paths


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a manual-review keyword crawl task plan.")
    parser.add_argument("--db", default="data/xhs-trend.sqlite")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--category", default="")
    parser.add_argument("--limit-keywords", type=int, default=10)
    parser.add_argument("--new-only", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="Preview only; do not write plan files.")
    parser.add_argument("--request-draft", action="store_true", help="Also generate request-draft.json.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    db_path = resolve_db_path(args.db)
    output_dir = Path(args.output_dir)
    if not output_dir.is_absolute():
        output_dir = PROJECT_ROOT / output_dir
    with connect_readonly(db_path) as conn:
        plan = build_plan(
            conn,
            days=max(args.days, 1),
            category=" ".join(args.category.split()).strip(),
            limit_keywords=max(args.limit_keywords, 1),
            new_only=args.new_only,
        )
    print(f"db={db_path}")
    print(f"plan_type={plan['plan_type']}")
    print(f"keywords={len(plan['keywords'])}")
    for item in plan["keywords"]:
        print(
            "keyword "
            f"name={item['keyword']} priority={item['priority']} status={item['status']} "
            f"promoted={item['is_recent_promoted']} last_crawled_at={item['last_crawled_at'] or 'never'}"
        )
    if args.dry_run:
        print("dry_run=true")
        print("No files written. No database writes attempted.")
        return 0
    paths = write_outputs(plan, output_dir, args.request_draft)
    print(f"markdown={paths['markdown_path']}")
    print(f"json={paths['json_path']}")
    if paths.get("request_draft_path"):
        print(f"request_draft={paths['request_draft_path']}")
    print("No collection command was started.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
