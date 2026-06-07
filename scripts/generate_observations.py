from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from storage.database import get_connection, init_database, resolve_db_path, transaction  # noqa: E402


CN_TZ = timezone(timedelta(hours=8))


def now_iso() -> str:
    return datetime.now(CN_TZ).isoformat(timespec="seconds")


def rows_to_dicts(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def as_int(value: Any) -> int:
    if value is None or value == "":
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def dump_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def load_json(value: str | None) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return None


def clean_term(value: Any) -> str:
    text = re.sub(r"\s+", " ", str(value or "").replace("#", " ")).strip()
    if len(text) > 80:
        return ""
    return text


def extract_terms(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        term = clean_term(value)
        return [term] if term else []
    if isinstance(value, list):
        terms: list[str] = []
        for item in value:
            terms.extend(extract_terms(item))
        return terms
    if isinstance(value, dict):
        terms: list[str] = []
        for key in ("name", "tag", "keyword", "query", "title", "text"):
            if key in value:
                terms.extend(extract_terms(value.get(key)))
        return terms
    return []


def run_time_value(run: dict[str, Any]) -> str:
    return str(run.get("finished_at") or run.get("started_at") or run.get("created_at") or "")


def observation_date_for(run: dict[str, Any]) -> str:
    value = run_time_value(run)
    return value[:10] if len(value) >= 10 else now_iso()[:10]


def latest_run_id(conn: sqlite3.Connection) -> int | None:
    row = conn.execute(
        """
        SELECT run_id
        FROM crawl_runs
        ORDER BY COALESCE(finished_at, started_at) DESC, run_id DESC
        LIMIT 1
        """
    ).fetchone()
    return int(row["run_id"]) if row else None


def target_run_ids(conn: sqlite3.Connection, run_id: int | None, all_runs: bool) -> list[int]:
    if all_runs:
        rows = conn.execute(
            """
            SELECT run_id
            FROM crawl_runs
            ORDER BY COALESCE(finished_at, started_at) ASC, run_id ASC
            """
        ).fetchall()
        return [int(row["run_id"]) for row in rows]
    if run_id is not None:
        return [run_id]
    latest = latest_run_id(conn)
    return [latest] if latest is not None else []


def get_run(conn: sqlite3.Connection, run_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM crawl_runs WHERE run_id = ?", (run_id,)).fetchone()
    return row_to_dict(row)


def keyword_id_for(conn: sqlite3.Connection, keyword: str) -> int | None:
    row = conn.execute("SELECT keyword_id FROM keywords WHERE keyword = ?", (keyword,)).fetchone()
    return int(row["keyword_id"]) if row else None


def target_keywords_for_run(
    conn: sqlite3.Connection,
    run: dict[str, Any],
    keyword: str,
    all_keywords: bool,
) -> list[dict[str, Any]]:
    if keyword:
        kid = keyword_id_for(conn, keyword)
        if kid is None:
            return []
        return [{"keyword_id": kid, "keyword": keyword}]
    if all_keywords:
        rows = conn.execute(
            """
            SELECT DISTINCT k.keyword_id, k.keyword
            FROM keyword_note_map knm
            JOIN keywords k ON k.keyword_id = knm.keyword_id
            WHERE knm.run_id = ?
            ORDER BY k.keyword_id
            """,
            (run["run_id"],),
        ).fetchall()
        return rows_to_dicts(rows)
    if run.get("keyword_id") is not None:
        row = conn.execute("SELECT keyword_id, keyword FROM keywords WHERE keyword_id = ?", (run["keyword_id"],)).fetchone()
        if row:
            return [dict(row)]
    rows = conn.execute(
        """
        SELECT DISTINCT k.keyword_id, k.keyword
        FROM keyword_note_map knm
        JOIN keywords k ON k.keyword_id = knm.keyword_id
        WHERE knm.run_id = ?
        ORDER BY k.keyword_id
        LIMIT 1
        """,
        (run["run_id"],),
    ).fetchall()
    return rows_to_dicts(rows)


def previous_run_for_keyword(conn: sqlite3.Connection, run: dict[str, Any], keyword_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT DISTINCT cr.*
        FROM crawl_runs cr
        JOIN keyword_note_map knm ON knm.run_id = cr.run_id
        WHERE knm.keyword_id = ?
          AND (
            COALESCE(cr.finished_at, cr.started_at) < COALESCE(?, ?)
            OR (
              COALESCE(cr.finished_at, cr.started_at) = COALESCE(?, ?)
              AND cr.run_id < ?
            )
          )
        ORDER BY COALESCE(cr.finished_at, cr.started_at) DESC, cr.run_id DESC
        LIMIT 1
        """,
        (
            keyword_id,
            run.get("finished_at"),
            run.get("started_at"),
            run.get("finished_at"),
            run.get("started_at"),
            run["run_id"],
        ),
    ).fetchone()
    return row_to_dict(row)


def note_ids_for_run_keyword(conn: sqlite3.Connection, run_id: int, keyword_id: int) -> set[str]:
    rows = conn.execute(
        """
        SELECT DISTINCT note_id
        FROM keyword_note_map
        WHERE run_id = ? AND keyword_id = ?
        """,
        (run_id, keyword_id),
    ).fetchall()
    return {str(row["note_id"]) for row in rows}


def author_ids_for_notes(conn: sqlite3.Connection, note_ids: set[str]) -> set[str]:
    if not note_ids:
        return set()
    placeholders = ",".join("?" for _ in note_ids)
    rows = conn.execute(
        f"""
        SELECT DISTINCT author_id
        FROM notes
        WHERE note_id IN ({placeholders})
          AND author_id IS NOT NULL
          AND author_id <> ''
        """,
        tuple(note_ids),
    ).fetchall()
    return {str(row["author_id"]) for row in rows}


def aggregate_metrics(conn: sqlite3.Connection, run_id: int, keyword_id: int) -> dict[str, int]:
    row = conn.execute(
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
            WHERE knm.run_id = ms.run_id
              AND knm.note_id = ms.note_id
              AND knm.keyword_id = ?
          )
        """,
        (run_id, keyword_id),
    ).fetchone()
    return {
        "liked_count": as_int(row["liked_count"] if row else 0),
        "collected_count": as_int(row["collected_count"] if row else 0),
        "comment_count": as_int(row["comment_count"] if row else 0),
        "share_count": as_int(row["share_count"] if row else 0),
    }


def top_authors_for_run(conn: sqlite3.Connection, run_id: int, keyword_id: int, limit: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT
          COALESCE(a.author_id, 'unknown') AS author_id,
          COALESCE(NULLIF(a.nickname, ''), 'unknown') AS nickname,
          COUNT(DISTINCT n.note_id) AS note_count,
          SUM(COALESCE(ms.liked_count, 0)) AS liked_count,
          SUM(COALESCE(ms.collected_count, 0)) AS collected_count,
          SUM(COALESCE(ms.comment_count, 0)) AS comment_count
        FROM keyword_note_map knm
        JOIN notes n ON n.note_id = knm.note_id
        LEFT JOIN authors a ON a.author_id = n.author_id
        LEFT JOIN metrics_snapshots ms ON ms.run_id = knm.run_id AND ms.note_id = knm.note_id
        WHERE knm.run_id = ? AND knm.keyword_id = ?
        GROUP BY COALESCE(a.author_id, 'unknown'), COALESCE(NULLIF(a.nickname, ''), 'unknown')
        ORDER BY note_count DESC, liked_count DESC, nickname
        LIMIT ?
        """,
        (run_id, keyword_id, limit),
    ).fetchall()
    return rows_to_dicts(rows)


def compare_ranked_lists(
    current: list[dict[str, Any]],
    previous: list[dict[str, Any]],
    id_key: str,
    label_key: str,
) -> dict[str, Any]:
    current_ids = [str(item.get(id_key) or "") for item in current]
    previous_ids = [str(item.get(id_key) or "") for item in previous]
    current_set = {item for item in current_ids if item}
    previous_set = {item for item in previous_ids if item}
    previous_rank = {item_id: index + 1 for index, item_id in enumerate(previous_ids) if item_id}
    current_rank = {item_id: index + 1 for index, item_id in enumerate(current_ids) if item_id}
    labels = {
        str(item.get(id_key) or ""): str(item.get(label_key) or item.get(id_key) or "")
        for item in [*current, *previous]
    }
    rank_changes = []
    for item_id in sorted(current_set & previous_set, key=lambda value: current_rank.get(value, 9999)):
        delta = previous_rank[item_id] - current_rank[item_id]
        if delta:
            rank_changes.append(
                {
                    id_key: item_id,
                    label_key: labels.get(item_id, item_id),
                    "previous_rank": previous_rank[item_id],
                    "current_rank": current_rank[item_id],
                    "rank_delta": delta,
                }
            )
    return {
        "current_top": current,
        "previous_top": previous,
        "new_entries": [
            {id_key: item_id, label_key: labels.get(item_id, item_id)}
            for item_id in current_ids
            if item_id and item_id not in previous_set
        ],
        "dropped_entries": [
            {id_key: item_id, label_key: labels.get(item_id, item_id)}
            for item_id in previous_ids
            if item_id and item_id not in current_set
        ],
        "rank_changes": rank_changes,
    }


def tag_counter_for_run(conn: sqlite3.Connection, run_id: int, keyword_id: int) -> Counter[str]:
    rows = conn.execute(
        """
        SELECT n.raw_json, k.keyword
        FROM keyword_note_map knm
        JOIN notes n ON n.note_id = knm.note_id
        JOIN keywords k ON k.keyword_id = knm.keyword_id
        WHERE knm.run_id = ? AND knm.keyword_id = ?
        """,
        (run_id, keyword_id),
    ).fetchall()
    fields = (
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
    counter: Counter[str] = Counter()
    for row in rows:
        if row["keyword"]:
            counter[clean_term(row["keyword"])] += 1
        raw = load_json(row["raw_json"])
        if not isinstance(raw, dict):
            continue
        for field in fields:
            for term in extract_terms(raw.get(field)):
                counter[term] += 1
    return counter


def top_tags_for_run(conn: sqlite3.Connection, run_id: int, keyword_id: int, limit: int) -> list[dict[str, Any]]:
    counter = tag_counter_for_run(conn, run_id, keyword_id)
    return [{"term": term, "count": count} for term, count in counter.most_common(limit)]


def compare_tags(current: list[dict[str, Any]], previous: list[dict[str, Any]]) -> dict[str, Any]:
    return compare_ranked_lists(current, previous, "term", "term")


def run_failure_count(conn: sqlite3.Connection, run_id: int) -> int:
    row = conn.execute("SELECT COUNT(*) AS count FROM failures WHERE run_id = ?", (run_id,)).fetchone()
    return as_int(row["count"] if row else 0)


def build_anomalies(
    current_run: dict[str, Any],
    previous_run: dict[str, Any] | None,
    current_note_count: int,
    previous_note_count: int,
    current_metrics: dict[str, int],
    previous_metrics: dict[str, int],
    growth: dict[str, int],
    current_failures: int,
    previous_failures: int,
) -> list[dict[str, Any]]:
    anomalies: list[dict[str, Any]] = []
    if previous_run is None:
        anomalies.append(
            {
                "type": "insufficient_history",
                "severity": "info",
                "message": "No previous run for this keyword; observation is a baseline.",
            }
        )
    if current_note_count == 0:
        anomalies.append(
            {
                "type": "empty_current_run",
                "severity": "warning",
                "message": "Current run has no notes for this keyword.",
            }
        )
    if previous_note_count and current_note_count < max(1, int(previous_note_count * 0.5)):
        anomalies.append(
            {
                "type": "note_count_drop",
                "severity": "warning",
                "previous": previous_note_count,
                "current": current_note_count,
            }
        )
    if previous_failures and current_failures > previous_failures:
        anomalies.append(
            {
                "type": "failure_count_increase",
                "severity": "warning",
                "previous": previous_failures,
                "current": current_failures,
            }
        )
    elif current_failures and not previous_failures:
        anomalies.append(
            {
                "type": "new_failures",
                "severity": "warning",
                "current": current_failures,
            }
        )

    for metric_name, current_value in current_metrics.items():
        previous_value = previous_metrics.get(metric_name, 0)
        delta = growth.get(metric_name, 0)
        if previous_value <= 0:
            continue
        ratio = delta / previous_value
        if ratio >= 0.5:
            anomalies.append(
                {
                    "type": f"{metric_name}_surge",
                    "severity": "info",
                    "previous": previous_value,
                    "current": current_value,
                    "delta": delta,
                    "ratio": round(ratio, 4),
                }
            )
        elif ratio <= -0.5:
            anomalies.append(
                {
                    "type": f"{metric_name}_drop",
                    "severity": "warning",
                    "previous": previous_value,
                    "current": current_value,
                    "delta": delta,
                    "ratio": round(ratio, 4),
                }
            )

    if current_run.get("status") not in ("success", "partial"):
        anomalies.append(
            {
                "type": "run_status_abnormal",
                "severity": "warning",
                "status": current_run.get("status"),
            }
        )
    return anomalies


def build_observation(
    conn: sqlite3.Connection,
    run: dict[str, Any],
    keyword_row: dict[str, Any],
    top_n: int,
) -> dict[str, Any]:
    run_id = int(run["run_id"])
    keyword_id = int(keyword_row["keyword_id"])
    keyword = str(keyword_row["keyword"])
    previous_run = previous_run_for_keyword(conn, run, keyword_id)

    current_note_ids = note_ids_for_run_keyword(conn, run_id, keyword_id)
    previous_note_ids = (
        note_ids_for_run_keyword(conn, int(previous_run["run_id"]), keyword_id) if previous_run else set()
    )
    current_authors = author_ids_for_notes(conn, current_note_ids)
    previous_authors = author_ids_for_notes(conn, previous_note_ids)
    new_note_count = len(current_note_ids - previous_note_ids) if previous_run else len(current_note_ids)
    new_author_count = len(current_authors - previous_authors) if previous_run else len(current_authors)

    current_metrics = aggregate_metrics(conn, run_id, keyword_id)
    previous_metrics = aggregate_metrics(conn, int(previous_run["run_id"]), keyword_id) if previous_run else {
        "liked_count": 0,
        "collected_count": 0,
        "comment_count": 0,
        "share_count": 0,
    }
    growth = {
        key: current_metrics[key] - previous_metrics.get(key, 0)
        for key in ("liked_count", "collected_count", "comment_count", "share_count")
    }
    current_authors_top = top_authors_for_run(conn, run_id, keyword_id, top_n)
    previous_authors_top = (
        top_authors_for_run(conn, int(previous_run["run_id"]), keyword_id, top_n) if previous_run else []
    )
    top_authors_change = compare_ranked_lists(current_authors_top, previous_authors_top, "author_id", "nickname")

    current_tags_top = top_tags_for_run(conn, run_id, keyword_id, top_n)
    previous_tags_top = top_tags_for_run(conn, int(previous_run["run_id"]), keyword_id, top_n) if previous_run else []
    top_tags_change = compare_tags(current_tags_top, previous_tags_top)

    current_failures = run_failure_count(conn, run_id)
    previous_failures = run_failure_count(conn, int(previous_run["run_id"])) if previous_run else 0
    anomalies = build_anomalies(
        run,
        previous_run,
        len(current_note_ids),
        len(previous_note_ids),
        current_metrics,
        previous_metrics,
        growth,
        current_failures,
        previous_failures,
    )
    summary = {
        "run_id": run_id,
        "previous_run_id": previous_run["run_id"] if previous_run else None,
        "keyword_id": keyword_id,
        "keyword": keyword,
        "observation_date": observation_date_for(run),
        "current_note_count": len(current_note_ids),
        "previous_note_count": len(previous_note_ids),
        "new_note_count": new_note_count,
        "new_author_count": new_author_count,
        "current_metrics": current_metrics,
        "previous_metrics": previous_metrics,
        "growth": growth,
        "current_failure_count": current_failures,
        "previous_failure_count": previous_failures,
        "failure_growth": current_failures - previous_failures,
        "top_authors_change": top_authors_change,
        "top_tags_change": top_tags_change,
        "anomalies": anomalies,
        "generated_at": now_iso(),
    }
    return {
        "run_id": run_id,
        "keyword_id": keyword_id,
        "observation_date": summary["observation_date"],
        "keyword": keyword,
        "new_note_count": new_note_count,
        "new_author_count": new_author_count,
        "liked_growth": growth["liked_count"],
        "collected_growth": growth["collected_count"],
        "comment_growth": growth["comment_count"],
        "top_authors_change": top_authors_change,
        "top_tags_change": top_tags_change,
        "anomalies": anomalies,
        "summary": summary,
    }


def upsert_observation(conn: sqlite3.Connection, observation: dict[str, Any]) -> int:
    ts = now_iso()
    conn.execute(
        """
        INSERT INTO observations(
          run_id, keyword_id, observation_date, keyword,
          new_note_count, new_author_count, liked_growth, collected_growth,
          comment_growth, top_authors_change, top_tags_change, anomalies_json,
          summary_json, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(run_id, keyword_id) DO UPDATE SET
          observation_date = excluded.observation_date,
          keyword = excluded.keyword,
          new_note_count = excluded.new_note_count,
          new_author_count = excluded.new_author_count,
          liked_growth = excluded.liked_growth,
          collected_growth = excluded.collected_growth,
          comment_growth = excluded.comment_growth,
          top_authors_change = excluded.top_authors_change,
          top_tags_change = excluded.top_tags_change,
          anomalies_json = excluded.anomalies_json,
          summary_json = excluded.summary_json,
          updated_at = excluded.updated_at
        """,
        (
            observation["run_id"],
            observation["keyword_id"],
            observation["observation_date"],
            observation["keyword"],
            observation["new_note_count"],
            observation["new_author_count"],
            observation["liked_growth"],
            observation["collected_growth"],
            observation["comment_growth"],
            dump_json(observation["top_authors_change"]),
            dump_json(observation["top_tags_change"]),
            dump_json(observation["anomalies"]),
            dump_json(observation["summary"]),
            ts,
            ts,
        ),
    )
    row = conn.execute(
        """
        SELECT observation_id
        FROM observations
        WHERE run_id = ? AND keyword_id = ?
        """,
        (observation["run_id"], observation["keyword_id"]),
    ).fetchone()
    return int(row["observation_id"])


def generate_observations(
    db_path: str | Path | None,
    run_id: int | None = None,
    keyword: str = "",
    all_runs: bool = False,
    all_keywords: bool = False,
    top_n: int = 20,
) -> dict[str, Any]:
    db = init_database(db_path)
    generated: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    with get_connection(db) as conn:
        runs = target_run_ids(conn, run_id, all_runs)
        with transaction(conn):
            for target_run_id in runs:
                run = get_run(conn, target_run_id)
                if not run:
                    skipped.append({"run_id": target_run_id, "reason": "run_not_found"})
                    continue
                keyword_rows = target_keywords_for_run(conn, run, keyword, all_keywords)
                if not keyword_rows:
                    skipped.append({"run_id": target_run_id, "reason": "keyword_not_found_or_not_mapped"})
                    continue
                for keyword_row in keyword_rows:
                    observation = build_observation(conn, run, keyword_row, top_n)
                    observation_id = upsert_observation(conn, observation)
                    generated.append(
                        {
                            "observation_id": observation_id,
                            "run_id": observation["run_id"],
                            "keyword_id": observation["keyword_id"],
                            "keyword": observation["keyword"],
                            "observation_date": observation["observation_date"],
                            "new_note_count": observation["new_note_count"],
                            "new_author_count": observation["new_author_count"],
                            "liked_growth": observation["liked_growth"],
                            "collected_growth": observation["collected_growth"],
                            "comment_growth": observation["comment_growth"],
                            "anomaly_count": len(observation["anomalies"]),
                        }
                    )
    return {
        "db_path": str(resolve_db_path(db)),
        "generated_count": len(generated),
        "skipped_count": len(skipped),
        "generated": generated,
        "skipped": skipped,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate keyword observations from crawl runs and metric snapshots.")
    parser.add_argument("--db", default="data/xhs-trend.sqlite")
    parser.add_argument("--run-id", type=int, default=None, help="Run id to summarize. Defaults to latest run.")
    parser.add_argument("--keyword", default="", help="Optional keyword scope.")
    parser.add_argument("--all-runs", action="store_true", help="Generate observations for every crawl run.")
    parser.add_argument("--all-keywords", action="store_true", help="Generate one observation for every keyword in each run.")
    parser.add_argument("--top-n", type=int, default=20)
    parser.add_argument("--json-report", default="", help="Optional path for a JSON execution report.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = generate_observations(
        db_path=args.db,
        run_id=args.run_id,
        keyword=args.keyword,
        all_runs=args.all_runs,
        all_keywords=args.all_keywords,
        top_n=max(args.top_n, 1),
    )
    if args.json_report:
        out = Path(args.json_report)
        if not out.is_absolute():
            out = PROJECT_ROOT / out
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        "observations "
        f"generated={report['generated_count']} skipped={report['skipped_count']} "
        f"db={report['db_path']}"
    )
    for item in report["generated"]:
        print(
            "observation "
            f"id={item['observation_id']} run_id={item['run_id']} keyword={item['keyword']} "
            f"new_notes={item['new_note_count']} liked_growth={item['liked_growth']} "
            f"collected_growth={item['collected_growth']} comment_growth={item['comment_growth']} "
            f"anomalies={item['anomaly_count']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
