from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CN_TZ = timezone(timedelta(hours=8))


def now_iso() -> str:
    return datetime.now(CN_TZ).isoformat(timespec="seconds")


def resolve_project_path(path: str | Path) -> Path:
    file_path = Path(path)
    if not file_path.is_absolute():
        file_path = PROJECT_ROOT / file_path
    return file_path


def connect_readonly(db_path: Path, errors: list[dict[str, str]]) -> sqlite3.Connection | None:
    if not db_path.exists():
        errors.append({"query": "connect", "error": f"数据库不存在: {db_path}"})
        return None
    uri_path = quote(db_path.resolve().as_posix(), safe="/:")
    try:
        conn = sqlite3.connect(f"file:{uri_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.Error as exc:
        errors.append({"query": "connect", "error": str(exc)})
        return None


def rows_to_dicts(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


def fetch_rows(
    conn: sqlite3.Connection | None,
    query_name: str,
    sql: str,
    params: tuple[Any, ...],
    errors: list[dict[str, str]],
) -> list[dict[str, Any]]:
    if conn is None:
        return []
    try:
        return rows_to_dicts(conn.execute(sql, params).fetchall())
    except sqlite3.Error as exc:
        errors.append({"query": query_name, "error": str(exc)})
        return []


def fetch_one(
    conn: sqlite3.Connection | None,
    query_name: str,
    sql: str,
    params: tuple[Any, ...],
    errors: list[dict[str, str]],
) -> dict[str, Any]:
    rows = fetch_rows(conn, query_name, sql, params, errors)
    return rows[0] if rows else {}


def as_int(value: Any) -> int:
    if value is None or value == "":
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def clip_text(value: Any, limit: int = 56) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "..."


def json_loads(value: str | None) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return None


def scoped_notes_cte(keyword: str | None) -> tuple[str, tuple[Any, ...]]:
    if keyword:
        return (
            """
            scoped_notes AS (
              SELECT DISTINCT n.note_id, n.author_id
              FROM notes n
              JOIN keyword_note_map knm ON knm.note_id = n.note_id
              JOIN keywords k ON k.keyword_id = knm.keyword_id
              WHERE k.keyword = ?
            )
            """,
            (keyword,),
        )
    return (
        """
        scoped_notes AS (
          SELECT DISTINCT n.note_id, n.author_id
          FROM notes n
        )
        """,
        (),
    )


def query_database_overview(
    conn: sqlite3.Connection | None,
    keyword: str | None,
    days: int,
    errors: list[dict[str, str]],
) -> dict[str, Any]:
    overview = fetch_one(
        conn,
        "database_overview",
        """
        SELECT
          (SELECT COUNT(*) FROM notes) AS notes_total,
          (SELECT COUNT(*) FROM authors) AS authors_total,
          (SELECT COUNT(*) FROM comments) AS comments_total,
          (SELECT COUNT(*) FROM metrics_snapshots) AS metrics_snapshots_total,
          (SELECT COUNT(*) FROM crawl_runs) AS crawl_runs_total,
          (SELECT COALESCE(finished_at, started_at)
             FROM crawl_runs
             ORDER BY COALESCE(finished_at, started_at) DESC, run_id DESC
             LIMIT 1) AS latest_crawl_time
        """,
        (),
        errors,
    )
    overview = {
        "notes_total": as_int(overview.get("notes_total")),
        "authors_total": as_int(overview.get("authors_total")),
        "comments_total": as_int(overview.get("comments_total")),
        "metrics_snapshots_total": as_int(overview.get("metrics_snapshots_total")),
        "crawl_runs_total": as_int(overview.get("crawl_runs_total")),
        "latest_crawl_time": overview.get("latest_crawl_time") or "",
        "scope_keyword": keyword or "",
        "recent_window_days": days,
    }
    if keyword:
        scoped = fetch_one(
            conn,
            "database_overview_scoped",
            """
            SELECT COUNT(DISTINCT knm.note_id) AS scoped_notes_total
            FROM keyword_note_map knm
            JOIN keywords k ON k.keyword_id = knm.keyword_id
            WHERE k.keyword = ?
            """,
            (keyword,),
            errors,
        )
        overview["scoped_notes_total"] = as_int(scoped.get("scoped_notes_total"))
        recent = fetch_one(
            conn,
            "database_overview_recent_scoped",
            """
            SELECT COUNT(DISTINCT n.note_id) AS recent_new_notes_total
            FROM notes n
            JOIN keyword_note_map knm ON knm.note_id = n.note_id
            JOIN keywords k ON k.keyword_id = knm.keyword_id
            WHERE k.keyword = ?
              AND n.first_seen_at >= datetime('now', ?)
            """,
            (keyword, f"-{days} days"),
            errors,
        )
    else:
        recent = fetch_one(
            conn,
            "database_overview_recent",
            """
            SELECT COUNT(DISTINCT note_id) AS recent_new_notes_total
            FROM notes
            WHERE first_seen_at >= datetime('now', ?)
            """,
            (f"-{days} days",),
            errors,
        )
    overview["recent_new_notes_total"] = as_int(recent.get("recent_new_notes_total"))
    return overview


def query_latest_runs(
    conn: sqlite3.Connection | None,
    keyword: str | None,
    limit: int,
    errors: list[dict[str, str]],
) -> list[dict[str, Any]]:
    if keyword:
        return fetch_rows(
            conn,
            "latest_runs_keyword",
            """
            SELECT DISTINCT cr.*
            FROM crawl_runs cr
            JOIN keyword_note_map knm ON knm.run_id = cr.run_id
            JOIN keywords k ON k.keyword_id = knm.keyword_id
            WHERE k.keyword = ?
            ORDER BY COALESCE(cr.finished_at, cr.started_at) DESC, cr.run_id DESC
            LIMIT ?
            """,
            (keyword, limit),
            errors,
        )
    return fetch_rows(
        conn,
        "latest_runs",
        """
        SELECT *
        FROM crawl_runs
        ORDER BY COALESCE(finished_at, started_at) DESC, run_id DESC
        LIMIT ?
        """,
        (limit,),
        errors,
    )


def query_run_note_ids(
    conn: sqlite3.Connection | None,
    run_id: int,
    keyword: str | None,
    errors: list[dict[str, str]],
) -> set[str]:
    if keyword:
        rows = fetch_rows(
            conn,
            "run_note_ids_keyword",
            """
            SELECT DISTINCT knm.note_id
            FROM keyword_note_map knm
            JOIN keywords k ON k.keyword_id = knm.keyword_id
            WHERE knm.run_id = ? AND k.keyword = ?
            """,
            (run_id, keyword),
            errors,
        )
    else:
        rows = fetch_rows(
            conn,
            "run_note_ids",
            """
            SELECT DISTINCT note_id
            FROM keyword_note_map
            WHERE run_id = ?
            """,
            (run_id,),
            errors,
        )
    return {str(row["note_id"]) for row in rows if row.get("note_id")}


def query_run_metrics(
    conn: sqlite3.Connection | None,
    run_id: int,
    keyword: str | None,
    errors: list[dict[str, str]],
) -> dict[str, int]:
    if keyword:
        row = fetch_one(
            conn,
            "run_metrics_keyword",
            """
            SELECT
              SUM(COALESCE(ms.liked_count, 0)) AS liked_count,
              SUM(COALESCE(ms.collected_count, 0)) AS collected_count,
              SUM(COALESCE(ms.comment_count, 0)) AS comment_count,
              SUM(COALESCE(ms.share_count, 0)) AS share_count
            FROM metrics_snapshots ms
            WHERE ms.run_id = ?
              AND EXISTS (
                SELECT 1
                FROM keyword_note_map knm
                JOIN keywords k ON k.keyword_id = knm.keyword_id
                WHERE knm.run_id = ms.run_id
                  AND knm.note_id = ms.note_id
                  AND k.keyword = ?
              )
            """,
            (run_id, keyword),
            errors,
        )
    else:
        row = fetch_one(
            conn,
            "run_metrics",
            """
            SELECT
              SUM(COALESCE(liked_count, 0)) AS liked_count,
              SUM(COALESCE(collected_count, 0)) AS collected_count,
              SUM(COALESCE(comment_count, 0)) AS comment_count,
              SUM(COALESCE(share_count, 0)) AS share_count
            FROM metrics_snapshots
            WHERE run_id = ?
            """,
            (run_id,),
            errors,
        )
    return {
        "liked_count": as_int(row.get("liked_count")),
        "collected_count": as_int(row.get("collected_count")),
        "comment_count": as_int(row.get("comment_count")),
        "share_count": as_int(row.get("share_count")),
    }


def query_failure_count(
    conn: sqlite3.Connection | None,
    run_id: int,
    errors: list[dict[str, str]],
) -> int:
    row = fetch_one(
        conn,
        "failure_count_for_run",
        "SELECT COUNT(*) AS failure_count FROM failures WHERE run_id = ?",
        (run_id,),
        errors,
    )
    return as_int(row.get("failure_count"))


def query_run_diff(
    conn: sqlite3.Connection | None,
    keyword: str | None,
    errors: list[dict[str, str]],
) -> dict[str, Any]:
    runs = query_latest_runs(conn, keyword, 2, errors)
    if not runs:
        return {
            "status": "暂无数据",
            "has_enough_history": False,
            "message": "暂无采集记录，无法计算最近两次采集差异。",
        }
    latest = runs[0]
    if len(runs) < 2:
        return {
            "status": "history_insufficient",
            "has_enough_history": False,
            "latest_run_id": latest.get("run_id"),
            "message": "历史快照不足，无法计算增长。",
        }

    latest_run_id = as_int(latest.get("run_id"))
    previous = runs[1]
    previous_run_id = as_int(previous.get("run_id"))
    latest_ids = query_run_note_ids(conn, latest_run_id, keyword, errors)
    previous_ids = query_run_note_ids(conn, previous_run_id, keyword, errors)
    latest_metrics = query_run_metrics(conn, latest_run_id, keyword, errors)
    previous_metrics = query_run_metrics(conn, previous_run_id, keyword, errors)
    interaction_delta = {
        key: latest_metrics[key] - previous_metrics.get(key, 0)
        for key in ("liked_count", "collected_count", "comment_count", "share_count")
    }
    interaction_delta["interaction_score"] = (
        interaction_delta["liked_count"]
        + interaction_delta["collected_count"] * 2
        + interaction_delta["comment_count"] * 3
        + interaction_delta["share_count"] * 2
    )
    latest_failures = query_failure_count(conn, latest_run_id, errors)
    previous_failures = query_failure_count(conn, previous_run_id, errors)
    return {
        "status": "ok",
        "has_enough_history": True,
        "latest_run_id": latest_run_id,
        "previous_run_id": previous_run_id,
        "latest_crawl_time": latest.get("finished_at") or latest.get("started_at") or "",
        "previous_crawl_time": previous.get("finished_at") or previous.get("started_at") or "",
        "added_note_count": len(latest_ids - previous_ids),
        "removed_note_count": len(previous_ids - latest_ids),
        "repeated_note_count": len(latest_ids & previous_ids),
        "latest_metrics": latest_metrics,
        "previous_metrics": previous_metrics,
        "interaction_delta": interaction_delta,
        "latest_failure_count": latest_failures,
        "previous_failure_count": previous_failures,
        "failure_count_delta": latest_failures - previous_failures,
    }


def query_top_notes(
    conn: sqlite3.Connection | None,
    keyword: str | None,
    limit: int,
    errors: list[dict[str, str]],
) -> list[dict[str, Any]]:
    cte, params = scoped_notes_cte(keyword)
    sql = f"""
    WITH {cte},
    latest AS (
      SELECT *
      FROM (
        SELECT
          ms.*,
          ROW_NUMBER() OVER (
            PARTITION BY ms.note_id
            ORDER BY ms.snapshot_time DESC, ms.snapshot_id DESC
          ) AS rn
        FROM metrics_snapshots ms
        JOIN scoped_notes s ON s.note_id = ms.note_id
      )
      WHERE rn = 1
    )
    SELECT
      n.note_id,
      n.title,
      n.note_url,
      n.publish_time,
      a.nickname AS author,
      latest.liked_count,
      latest.collected_count,
      latest.comment_count,
      latest.share_count,
      COALESCE(latest.liked_count, 0)
        + COALESCE(latest.collected_count, 0) * 2
        + COALESCE(latest.comment_count, 0) * 3
        + COALESCE(latest.share_count, 0) * 2 AS interaction_score,
      latest.snapshot_time
    FROM scoped_notes s
    JOIN notes n ON n.note_id = s.note_id
    LEFT JOIN authors a ON a.author_id = n.author_id
    LEFT JOIN latest ON latest.note_id = n.note_id
    ORDER BY interaction_score DESC, latest.snapshot_time DESC, n.note_id
    LIMIT ?
    """
    return fetch_rows(conn, "top_notes", sql, params + (limit,), errors)


def query_growth(
    conn: sqlite3.Connection | None,
    keyword: str | None,
    metric_column: str,
    growth_key: str,
    limit: int,
    errors: list[dict[str, str]],
) -> list[dict[str, Any]]:
    allowed_columns = {
        "liked_count",
        "collected_count",
        "comment_count",
        "share_count",
    }
    if metric_column not in allowed_columns:
        errors.append({"query": "growth", "error": f"不支持的指标字段: {metric_column}"})
        return []
    cte, params = scoped_notes_cte(keyword)
    sql = f"""
    WITH {cte},
    ranked AS (
      SELECT
        ms.*,
        ROW_NUMBER() OVER (
          PARTITION BY ms.note_id
          ORDER BY ms.snapshot_time DESC, ms.snapshot_id DESC
        ) AS rn
      FROM metrics_snapshots ms
      JOIN scoped_notes s ON s.note_id = ms.note_id
    ),
    latest AS (SELECT * FROM ranked WHERE rn = 1),
    previous AS (SELECT * FROM ranked WHERE rn = 2)
    SELECT
      n.note_id,
      n.title,
      n.note_url,
      a.nickname AS author,
      latest.snapshot_time AS latest_snapshot_time,
      previous.snapshot_time AS previous_snapshot_time,
      COALESCE(previous.{metric_column}, 0) AS previous_value,
      COALESCE(latest.{metric_column}, 0) AS latest_value,
      COALESCE(latest.{metric_column}, 0) - COALESCE(previous.{metric_column}, 0) AS {growth_key}
    FROM latest
    JOIN previous ON previous.note_id = latest.note_id
    JOIN notes n ON n.note_id = latest.note_id
    LEFT JOIN authors a ON a.author_id = n.author_id
    ORDER BY {growth_key} DESC, latest.snapshot_time DESC, n.note_id
    LIMIT ?
    """
    return fetch_rows(conn, f"growth_{metric_column}", sql, params + (limit,), errors)


def query_top_authors(
    conn: sqlite3.Connection | None,
    keyword: str | None,
    limit: int,
    errors: list[dict[str, str]],
) -> list[dict[str, Any]]:
    cte, params = scoped_notes_cte(keyword)
    sql = f"""
    WITH {cte},
    latest AS (
      SELECT *
      FROM (
        SELECT
          ms.*,
          ROW_NUMBER() OVER (
            PARTITION BY ms.note_id
            ORDER BY ms.snapshot_time DESC, ms.snapshot_id DESC
          ) AS rn
        FROM metrics_snapshots ms
        JOIN scoped_notes s ON s.note_id = ms.note_id
      )
      WHERE rn = 1
    )
    SELECT
      COALESCE(a.author_id, 'unknown') AS author_id,
      COALESCE(NULLIF(a.nickname, ''), 'unknown') AS nickname,
      a.profile_url,
      COUNT(DISTINCT s.note_id) AS note_count,
      SUM(COALESCE(latest.liked_count, 0)) AS total_liked_count,
      SUM(COALESCE(latest.collected_count, 0)) AS total_collected_count,
      SUM(COALESCE(latest.comment_count, 0)) AS total_comment_count
    FROM scoped_notes s
    JOIN notes n ON n.note_id = s.note_id
    LEFT JOIN authors a ON a.author_id = n.author_id
    LEFT JOIN latest ON latest.note_id = s.note_id
    GROUP BY COALESCE(a.author_id, 'unknown'), COALESCE(NULLIF(a.nickname, ''), 'unknown'), a.profile_url
    ORDER BY note_count DESC, total_liked_count DESC, nickname
    LIMIT ?
    """
    return fetch_rows(conn, "top_authors", sql, params + (limit,), errors)


def extract_terms_from_value(value: Any) -> list[str]:
    terms: list[str] = []
    if value is None:
        return terms
    if isinstance(value, str):
        clean = " ".join(value.replace("#", " ").split()).strip()
        if clean and len(clean) <= 80:
            terms.append(clean)
        return terms
    if isinstance(value, list):
        for item in value:
            terms.extend(extract_terms_from_value(item))
        return terms
    if isinstance(value, dict):
        for key in ("name", "tag", "keyword", "query", "title", "text"):
            if key in value:
                terms.extend(extract_terms_from_value(value.get(key)))
        return terms
    return terms


def query_raw_note_terms(
    conn: sqlite3.Connection | None,
    keyword: str | None,
    errors: list[dict[str, str]],
) -> list[dict[str, Any]]:
    cte, params = scoped_notes_cte(keyword)
    sql = f"""
    WITH {cte}
    SELECT n.note_id, n.raw_json
    FROM scoped_notes s
    JOIN notes n ON n.note_id = s.note_id
    """
    return fetch_rows(conn, "raw_note_terms", sql, params, errors)


def query_mapped_keywords(
    conn: sqlite3.Connection | None,
    keyword: str | None,
    limit: int,
    errors: list[dict[str, str]],
) -> list[dict[str, Any]]:
    if keyword:
        rows = fetch_rows(
            conn,
            "mapped_keywords_scoped",
            """
            SELECT k2.keyword, COUNT(DISTINCT knm2.note_id) AS note_count
            FROM keyword_note_map knm
            JOIN keywords k ON k.keyword_id = knm.keyword_id
            JOIN keyword_note_map knm2 ON knm2.note_id = knm.note_id
            JOIN keywords k2 ON k2.keyword_id = knm2.keyword_id
            WHERE k.keyword = ?
            GROUP BY k2.keyword
            ORDER BY note_count DESC, k2.keyword
            LIMIT ?
            """,
            (keyword, limit),
            errors,
        )
    else:
        rows = fetch_rows(
            conn,
            "mapped_keywords",
            """
            SELECT k.keyword, COUNT(DISTINCT knm.note_id) AS note_count
            FROM keyword_note_map knm
            JOIN keywords k ON k.keyword_id = knm.keyword_id
            GROUP BY k.keyword
            ORDER BY note_count DESC, k.keyword
            LIMIT ?
            """,
            (limit,),
            errors,
        )
    return [
        {"term": row.get("keyword"), "source": "keyword_note_map", "count": as_int(row.get("note_count"))}
        for row in rows
        if row.get("keyword")
    ]


def query_top_keywords_or_tags(
    conn: sqlite3.Connection | None,
    keyword: str | None,
    limit: int,
    errors: list[dict[str, str]],
) -> list[dict[str, Any]]:
    counter: Counter[tuple[str, str]] = Counter()
    for mapped in query_mapped_keywords(conn, keyword, limit * 2, errors):
        counter[(str(mapped["term"]), str(mapped["source"]))] += as_int(mapped["count"])

    raw_fields = (
        "tag_list",
        "tags",
        "keyword",
        "keywords",
        "query",
        "search_keyword",
        "topic",
        "topics",
        "hashtags",
        "hash_tags",
    )
    for row in query_raw_note_terms(conn, keyword, errors):
        raw = json_loads(row.get("raw_json"))
        if not isinstance(raw, dict):
            continue
        for field in raw_fields:
            for term in extract_terms_from_value(raw.get(field)):
                source = "raw_json." + field
                counter[(term, source)] += 1

    ranked = [
        {"term": term, "source": source, "count": count}
        for (term, source), count in counter.most_common(limit)
    ]
    return ranked


def query_failures_summary(
    conn: sqlite3.Connection | None,
    keyword: str | None,
    errors: list[dict[str, str]],
) -> dict[str, Any]:
    if keyword:
        run_filter = """
        WHERE EXISTS (
          SELECT 1
          FROM keyword_note_map knm
          JOIN keywords k ON k.keyword_id = knm.keyword_id
          WHERE knm.run_id = failures.run_id AND k.keyword = ?
        )
        """
        params = (keyword,)
    else:
        run_filter = ""
        params = ()

    total = fetch_one(
        conn,
        "failures_total",
        f"""
        SELECT
          COUNT(*) AS total_failures,
          SUM(CASE WHEN resolved = 0 THEN 1 ELSE 0 END) AS unresolved_failures
        FROM failures
        {run_filter}
        """,
        params,
        errors,
    )
    by_stage = fetch_rows(
        conn,
        "failures_by_stage",
        f"""
        SELECT stage, COUNT(*) AS failure_count
        FROM failures
        {run_filter}
        GROUP BY stage
        ORDER BY failure_count DESC, stage
        LIMIT 20
        """,
        params,
        errors,
    )
    by_type = fetch_rows(
        conn,
        "failures_by_type",
        f"""
        SELECT error_type, COUNT(*) AS failure_count
        FROM failures
        {run_filter}
        GROUP BY error_type
        ORDER BY failure_count DESC, error_type
        LIMIT 20
        """,
        params,
        errors,
    )
    recent = fetch_rows(
        conn,
        "failures_recent",
        f"""
        SELECT failure_id, run_id, stage, target, error_type, error_message, retry_count, created_at, resolved
        FROM failures
        {run_filter}
        ORDER BY created_at DESC, failure_id DESC
        LIMIT 10
        """,
        params,
        errors,
    )
    return {
        "total_failures": as_int(total.get("total_failures")),
        "unresolved_failures": as_int(total.get("unresolved_failures")),
        "by_stage": by_stage,
        "by_type": by_type,
        "recent_failures": recent,
    }


def build_rule_based_insights(summary: dict[str, Any]) -> list[str]:
    insights: list[str] = []
    overview = summary.get("database_overview", {})
    run_diff = summary.get("run_diff", {})
    top_growth = [
        row for row in summary.get("fastest_liked_growth", []) if as_int(row.get("liked_growth")) > 0
    ]
    top_collect_growth = [
        row for row in summary.get("fastest_collected_growth", []) if as_int(row.get("collected_growth")) > 0
    ]
    top_comment_growth = [
        row for row in summary.get("fastest_comment_growth", []) if as_int(row.get("comment_growth")) > 0
    ]

    if overview.get("crawl_runs_total", 0) < 2 or not run_diff.get("has_enough_history"):
        insights.append("历史快照不足，暂时只能做内容盘点，无法可靠判断增长趋势。")
    else:
        insights.append("已有至少两次采集记录，可以开始计算笔记互动增长和采集差异。")

    if top_growth:
        names = "、".join(clip_text(row.get("title"), 20) for row in top_growth[:3])
        insights.append(f"点赞增长值得关注的笔记：{names}。")
    else:
        insights.append("最近两次快照中暂未发现点赞数正增长的笔记。")

    if top_collect_growth:
        names = "、".join(clip_text(row.get("title"), 20) for row in top_collect_growth[:3])
        insights.append(f"收藏增长较快的笔记可作为深度需求信号：{names}。")

    if top_comment_growth:
        names = "、".join(clip_text(row.get("title"), 20) for row in top_comment_growth[:3])
        insights.append(f"评论增长较快的笔记可能正在形成讨论：{names}。")

    top_terms = summary.get("top_keywords_or_tags", [])
    if top_terms:
        terms = "、".join(str(item.get("term")) for item in top_terms[:5])
        insights.append(f"建议继续追踪的高频关键词/标签：{terms}。")
    else:
        insights.append("暂未从关键词映射或 raw_json 中提取到稳定的高频关键词/标签。")

    top_authors = summary.get("top_authors", [])
    if top_authors:
        authors = "、".join(str(item.get("nickname") or item.get("author_id")) for item in top_authors[:5])
        insights.append(f"值得关注的高频作者：{authors}。")

    failures = summary.get("failures_summary", {})
    if failures.get("total_failures", 0):
        insights.append(
            f"失败队列中共有 {failures.get('total_failures')} 条记录，"
            f"其中未解决 {failures.get('unresolved_failures')} 条，需要持续观察失败阶段和账号状态。"
        )
    else:
        insights.append("当前没有记录到失败任务。")

    if overview.get("metrics_snapshots_total", 0) < max(overview.get("notes_total", 0) * 2, 2):
        insights.append("快照密度仍偏低，趋势判断需要继续积累多轮采集数据。")
    else:
        insights.append("快照数量已经覆盖多轮观测，可作为后续 Gemini 历史分析的输入底座。")
    return insights


def build_summary(
    conn: sqlite3.Connection | None,
    db_path: Path,
    keyword: str | None,
    days: int,
    top_n: int,
    errors: list[dict[str, str]],
) -> dict[str, Any]:
    latest_runs = query_latest_runs(conn, keyword, 1, errors)
    latest_run = latest_runs[0] if latest_runs else {}
    summary = {
        "database_overview": query_database_overview(conn, keyword, days, errors),
        "latest_run": latest_run,
        "run_diff": query_run_diff(conn, keyword, errors),
        "top_notes": query_top_notes(conn, keyword, top_n, errors),
        "fastest_liked_growth": query_growth(conn, keyword, "liked_count", "liked_growth", top_n, errors),
        "fastest_collected_growth": query_growth(
            conn, keyword, "collected_count", "collected_growth", top_n, errors
        ),
        "fastest_comment_growth": query_growth(conn, keyword, "comment_count", "comment_growth", top_n, errors),
        "top_authors": query_top_authors(conn, keyword, top_n, errors),
        "top_keywords_or_tags": query_top_keywords_or_tags(conn, keyword, top_n, errors),
        "failures_summary": query_failures_summary(conn, keyword, errors),
        "rule_based_insights": [],
    }
    summary["rule_based_insights"] = build_rule_based_insights(summary)
    summary["_meta"] = {
        "generated_at": now_iso(),
        "db_path": str(db_path),
        "keyword": keyword or "",
        "days": days,
        "top_n": top_n,
        "query_errors": errors,
    }
    return summary


def markdown_table(rows: list[dict[str, Any]], columns: list[tuple[str, str]], empty_text: str = "暂无数据") -> str:
    if not rows:
        return empty_text
    header = "| " + " | ".join(label for label, _ in columns) + " |"
    separator = "| " + " | ".join("---" for _ in columns) + " |"
    body = []
    for row in rows:
        values = []
        for _, key in columns:
            value = row.get(key, "")
            values.append(clip_text(value, 72).replace("|", "/"))
        body.append("| " + " | ".join(values) + " |")
    return "\n".join([header, separator] + body)


def render_report(summary: dict[str, Any]) -> str:
    overview = summary["database_overview"]
    latest_run = summary["latest_run"]
    run_diff = summary["run_diff"]
    failures = summary["failures_summary"]
    meta = summary["_meta"]
    scope = meta.get("keyword") or "全库"
    top_n = meta.get("top_n") or 20

    lines = [
        "# SQLite 趋势数据库报告",
        "",
        f"- 生成时间：{meta.get('generated_at')}",
        f"- 数据库：{meta.get('db_path')}",
        f"- 分析范围：{scope}",
        f"- 最近窗口：{meta.get('days')} 天",
        "",
        "## 1. 数据库概览",
        "",
        f"- notes 总数：{overview.get('notes_total', 0)}",
        f"- authors 总数：{overview.get('authors_total', 0)}",
        f"- comments 总数：{overview.get('comments_total', 0)}",
        f"- metrics_snapshots 总数：{overview.get('metrics_snapshots_total', 0)}",
        f"- crawl_runs 总数：{overview.get('crawl_runs_total', 0)}",
        f"- 最近一次采集时间：{overview.get('latest_crawl_time') or '暂无数据'}",
        f"- 最近 {overview.get('recent_window_days', 7)} 天新增 notes：{overview.get('recent_new_notes_total', 0)}",
    ]
    if overview.get("scope_keyword"):
        lines.append(f"- 当前关键词命中的 notes 数：{overview.get('scoped_notes_total', 0)}")

    lines.extend(
        [
            "",
            "## 2. 最近两次采集差异",
            "",
        ]
    )
    if not run_diff.get("has_enough_history"):
        lines.append(run_diff.get("message") or "历史快照不足，无法计算增长。")
    else:
        delta = run_diff.get("interaction_delta", {})
        lines.extend(
            [
                f"- 最新 run_id：{run_diff.get('latest_run_id')}（{run_diff.get('latest_crawl_time')}）",
                f"- 上一次 run_id：{run_diff.get('previous_run_id')}（{run_diff.get('previous_crawl_time')}）",
                f"- 新增笔记数：{run_diff.get('added_note_count', 0)}",
                f"- 消失笔记数：{run_diff.get('removed_note_count', 0)}",
                f"- 重复笔记数：{run_diff.get('repeated_note_count', 0)}",
                f"- 点赞变化：{delta.get('liked_count', 0)}",
                f"- 收藏变化：{delta.get('collected_count', 0)}",
                f"- 评论变化：{delta.get('comment_count', 0)}",
                f"- 分享变化：{delta.get('share_count', 0)}",
                f"- 综合互动分变化：{delta.get('interaction_score', 0)}",
                f"- 失败数变化：{run_diff.get('failure_count_delta', 0)}",
            ]
        )

    lines.extend(
        [
            "",
            f"## 3. 互动量 Top{top_n}",
            "",
            markdown_table(
                summary["top_notes"],
                [
                    ("note_id", "note_id"),
                    ("标题", "title"),
                    ("作者", "author"),
                    ("点赞", "liked_count"),
                    ("收藏", "collected_count"),
                    ("评论", "comment_count"),
                    ("综合分", "interaction_score"),
                ],
            ),
            "",
            f"## 4. 点赞增长最快 Top{top_n}",
            "",
            markdown_table(
                summary["fastest_liked_growth"],
                [
                    ("note_id", "note_id"),
                    ("标题", "title"),
                    ("作者", "author"),
                    ("上次", "previous_value"),
                    ("最新", "latest_value"),
                    ("增长", "liked_growth"),
                ],
            ),
            "",
            f"## 5. 收藏增长最快 Top{top_n}",
            "",
            markdown_table(
                summary["fastest_collected_growth"],
                [
                    ("note_id", "note_id"),
                    ("标题", "title"),
                    ("作者", "author"),
                    ("上次", "previous_value"),
                    ("最新", "latest_value"),
                    ("增长", "collected_growth"),
                ],
            ),
            "",
            f"## 6. 评论增长最快 Top{top_n}",
            "",
            markdown_table(
                summary["fastest_comment_growth"],
                [
                    ("note_id", "note_id"),
                    ("标题", "title"),
                    ("作者", "author"),
                    ("上次", "previous_value"),
                    ("最新", "latest_value"),
                    ("增长", "comment_growth"),
                ],
            ),
            "",
            f"## 7. 高频作者 Top{top_n}",
            "",
            markdown_table(
                summary["top_authors"],
                [
                    ("作者", "nickname"),
                    ("note 数", "note_count"),
                    ("总点赞", "total_liked_count"),
                    ("总收藏", "total_collected_count"),
                    ("总评论", "total_comment_count"),
                ],
            ),
            "",
            "## 8. 高频关键词/标签",
            "",
            markdown_table(
                summary["top_keywords_or_tags"],
                [
                    ("词/标签", "term"),
                    ("来源", "source"),
                    ("次数", "count"),
                ],
            ),
            "",
            "## 9. 失败任务摘要",
            "",
            f"- 失败总数：{failures.get('total_failures', 0)}",
            f"- 未解决失败数：{failures.get('unresolved_failures', 0)}",
            "",
            "### 失败阶段",
            "",
            markdown_table(failures.get("by_stage", []), [("阶段", "stage"), ("次数", "failure_count")]),
            "",
            "### 失败类型",
            "",
            markdown_table(failures.get("by_type", []), [("类型", "error_type"), ("次数", "failure_count")]),
            "",
            "### 最近失败目标",
            "",
            markdown_table(
                failures.get("recent_failures", []),
                [
                    ("run_id", "run_id"),
                    ("阶段", "stage"),
                    ("目标", "target"),
                    ("类型", "error_type"),
                    ("时间", "created_at"),
                ],
            ),
            "",
            "## 10. 趋势判断摘要",
            "",
        ]
    )
    for insight in summary["rule_based_insights"]:
        lines.append(f"- {insight}")

    if meta.get("query_errors"):
        lines.extend(["", "## 查询异常", ""])
        for item in meta["query_errors"]:
            lines.append(f"- {item.get('query')}: {item.get('error')}")

    if latest_run:
        lines.extend(
            [
                "",
                "## 最近一次采集记录",
                "",
                f"- run_id：{latest_run.get('run_id')}",
                f"- status：{latest_run.get('status')}",
                f"- total_found：{latest_run.get('total_found')}",
                f"- total_saved：{latest_run.get('total_saved')}",
                f"- error_count：{latest_run.get('error_count')}",
            ]
        )
    return "\n".join(lines) + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a read-only trend report from the XHS SQLite database.")
    parser.add_argument("--db", default="data/xhs-trend.sqlite", help="SQLite database path.")
    parser.add_argument("--output-dir", required=True, help="Directory for trend-db-report.md and trend-db-summary.json.")
    parser.add_argument("--keyword", default="", help="Optional keyword scope.")
    parser.add_argument("--days", type=int, default=7, help="Recent window in days. Default: 7.")
    parser.add_argument("--top-n", type=int, default=20, help="Top list size. Default: 20.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    db_path = resolve_project_path(args.db)
    output_dir = resolve_project_path(args.output_dir)
    keyword = " ".join(args.keyword.split()).strip() or None
    days = max(args.days, 1)
    top_n = max(args.top_n, 1)
    errors: list[dict[str, str]] = []

    conn = connect_readonly(db_path, errors)
    try:
        summary = build_summary(conn, db_path, keyword, days, top_n, errors)
    finally:
        if conn is not None:
            conn.close()

    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / "trend-db-report.md"
    summary_path = output_dir / "trend-db-summary.json"
    report_path.write_text(render_report(summary), encoding="utf-8-sig")
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"trend_report={report_path}")
    print(f"trend_summary={summary_path}")
    if errors:
        print(f"query_errors={len(errors)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
