from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any


CN_TZ = timezone(timedelta(hours=8))


def now_iso() -> str:
    return datetime.now(CN_TZ).isoformat(timespec="seconds")


def stable_hash(*parts: Any, length: int = 24) -> str:
    text = "|".join(str(part or "") for part in parts)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:length]


def dump_json(value: Any) -> str:
    if value is None:
        return ""
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def rows_to_dicts(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


def upsert_keyword(
    conn: sqlite3.Connection,
    keyword: str,
    category: str = "",
    enabled: bool = True,
    timestamp: str | None = None,
) -> int:
    clean = " ".join(str(keyword or "").split()).strip()
    if not clean:
        clean = "unknown"
    ts = timestamp or now_iso()
    conn.execute(
        """
        INSERT INTO keywords(keyword, category, enabled, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(keyword) DO UPDATE SET
          category = COALESCE(NULLIF(excluded.category, ''), keywords.category),
          enabled = excluded.enabled,
          updated_at = excluded.updated_at
        """,
        (clean, category or "", 1 if enabled else 0, ts, ts),
    )
    row = conn.execute("SELECT keyword_id FROM keywords WHERE keyword = ?", (clean,)).fetchone()
    return int(row["keyword_id"])


def upsert_author(
    conn: sqlite3.Connection,
    platform: str,
    nickname: str = "",
    profile_url: str = "",
    avatar_url: str = "",
    author_id: str = "",
    timestamp: str | None = None,
) -> str | None:
    nickname = str(nickname or "").strip()
    profile_url = str(profile_url or "").strip()
    avatar_url = str(avatar_url or "").strip()
    if not any([author_id, nickname, profile_url, avatar_url]):
        return None
    platform = platform or "xiaohongshu"
    stable_id = author_id or f"{platform}:author:{stable_hash(profile_url or nickname or avatar_url)}"
    ts = timestamp or now_iso()
    conn.execute(
        """
        INSERT INTO authors(author_id, platform, nickname, profile_url, avatar_url, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(author_id) DO UPDATE SET
          nickname = COALESCE(NULLIF(excluded.nickname, ''), authors.nickname),
          profile_url = COALESCE(NULLIF(excluded.profile_url, ''), authors.profile_url),
          avatar_url = COALESCE(NULLIF(excluded.avatar_url, ''), authors.avatar_url),
          updated_at = excluded.updated_at
        """,
        (stable_id, platform, nickname, profile_url, avatar_url, ts, ts),
    )
    return stable_id


def upsert_crawl_run(
    conn: sqlite3.Connection,
    run_key: str,
    keyword_id: int | None,
    started_at: str,
    finished_at: str | None = None,
    status: str = "success",
    total_found: int = 0,
    total_saved: int = 0,
    error_count: int = 0,
    notes: str = "",
    out_dir: str = "",
    raw_path: str = "",
    report_path: str = "",
    ai_output_path: str = "",
    timestamp: str | None = None,
) -> int:
    ts = timestamp or now_iso()
    key = run_key or stable_hash(keyword_id, started_at, out_dir)
    conn.execute(
        """
        INSERT INTO crawl_runs(
          run_key, keyword_id, started_at, finished_at, status, total_found,
          total_saved, error_count, notes, out_dir, raw_path, report_path,
          ai_output_path, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(run_key) DO UPDATE SET
          keyword_id = excluded.keyword_id,
          started_at = excluded.started_at,
          finished_at = excluded.finished_at,
          status = excluded.status,
          total_found = excluded.total_found,
          total_saved = excluded.total_saved,
          error_count = excluded.error_count,
          notes = excluded.notes,
          out_dir = excluded.out_dir,
          raw_path = excluded.raw_path,
          report_path = excluded.report_path,
          ai_output_path = COALESCE(NULLIF(excluded.ai_output_path, ''), crawl_runs.ai_output_path),
          updated_at = excluded.updated_at
        """,
        (
            key,
            keyword_id,
            started_at,
            finished_at,
            status,
            total_found,
            total_saved,
            error_count,
            notes,
            out_dir,
            raw_path,
            report_path,
            ai_output_path,
            ts,
            ts,
        ),
    )
    row = conn.execute("SELECT run_id FROM crawl_runs WHERE run_key = ?", (key,)).fetchone()
    return int(row["run_id"])


def upsert_note(
    conn: sqlite3.Connection,
    note_id: str,
    platform: str = "xiaohongshu",
    author_id: str | None = None,
    title: str = "",
    content: str = "",
    note_url: str = "",
    publish_time: str = "",
    seen_at: str | None = None,
    raw_json: Any = None,
) -> str:
    if not note_id:
        raise ValueError("note_id is required")
    ts = seen_at or now_iso()
    conn.execute(
        """
        INSERT INTO notes(
          note_id, platform, author_id, title, content, note_url, publish_time,
          first_seen_at, last_seen_at, raw_json, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(note_id) DO UPDATE SET
          platform = excluded.platform,
          author_id = COALESCE(excluded.author_id, notes.author_id),
          title = COALESCE(NULLIF(excluded.title, ''), notes.title),
          content = COALESCE(NULLIF(excluded.content, ''), notes.content),
          note_url = COALESCE(NULLIF(excluded.note_url, ''), notes.note_url),
          publish_time = COALESCE(NULLIF(excluded.publish_time, ''), notes.publish_time),
          last_seen_at = excluded.last_seen_at,
          raw_json = COALESCE(NULLIF(excluded.raw_json, ''), notes.raw_json),
          updated_at = excluded.updated_at
        """,
        (
            note_id,
            platform,
            author_id,
            title or "",
            content or "",
            note_url or "",
            publish_time or "",
            ts,
            ts,
            dump_json(raw_json),
            ts,
            ts,
        ),
    )
    return note_id


def insert_comment(
    conn: sqlite3.Connection,
    note_id: str,
    content: str,
    author_id: str | None = None,
    comment_id: str = "",
    like_count: int | None = None,
    publish_time: str = "",
    raw_json: Any = None,
    created_at: str | None = None,
) -> str | None:
    clean = str(content or "").strip()
    if not note_id or not clean:
        return None
    cid = comment_id or f"comment:{stable_hash(note_id, author_id or '', publish_time, clean, length=32)}"
    ts = created_at or now_iso()
    conn.execute(
        """
        INSERT INTO comments(comment_id, note_id, author_id, content, like_count, publish_time, raw_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(comment_id) DO UPDATE SET
          author_id = COALESCE(excluded.author_id, comments.author_id),
          content = excluded.content,
          like_count = COALESCE(excluded.like_count, comments.like_count),
          publish_time = COALESCE(NULLIF(excluded.publish_time, ''), comments.publish_time),
          raw_json = COALESCE(NULLIF(excluded.raw_json, ''), comments.raw_json)
        """,
        (cid, note_id, author_id, clean, like_count, publish_time or "", dump_json(raw_json), ts),
    )
    return cid


def link_keyword_note(
    conn: sqlite3.Connection,
    keyword_id: int,
    note_id: str,
    run_id: int,
    rank_position: int | None = None,
    search_time: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO keyword_note_map(keyword_id, note_id, run_id, rank_position, search_time)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(keyword_id, note_id, run_id) DO UPDATE SET
          rank_position = COALESCE(excluded.rank_position, keyword_note_map.rank_position),
          search_time = excluded.search_time
        """,
        (keyword_id, note_id, run_id, rank_position, search_time or now_iso()),
    )


def insert_metrics_snapshot(
    conn: sqlite3.Connection,
    note_id: str,
    run_id: int,
    liked_count: int | None = None,
    collected_count: int | None = None,
    comment_count: int | None = None,
    share_count: int | None = None,
    snapshot_time: str | None = None,
) -> int | None:
    ts = snapshot_time or now_iso()
    conn.execute(
        """
        INSERT OR IGNORE INTO metrics_snapshots(
          note_id, run_id, liked_count, collected_count, comment_count, share_count,
          snapshot_time, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (note_id, run_id, liked_count, collected_count, comment_count, share_count, ts, now_iso()),
    )
    row = conn.execute(
        """
        SELECT snapshot_id FROM metrics_snapshots
        WHERE note_id = ? AND run_id = ? AND snapshot_time = ?
        """,
        (note_id, run_id, ts),
    ).fetchone()
    return int(row["snapshot_id"]) if row else None


def insert_analysis_result(
    conn: sqlite3.Connection,
    run_id: int | None,
    keyword_id: int | None,
    analysis_type: str,
    input_summary: str = "",
    output_text: str = "",
    model_name: str = "",
    raw_json: Any = None,
    created_at: str | None = None,
    analysis_key: str = "",
) -> int:
    ts = created_at or now_iso()
    key = analysis_key or f"analysis:{stable_hash(run_id, keyword_id, analysis_type, model_name, ts)}"
    conn.execute(
        """
        INSERT INTO analysis_results(
          analysis_key, run_id, keyword_id, analysis_type, input_summary,
          output_text, raw_json, model_name, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(analysis_key) DO UPDATE SET
          input_summary = excluded.input_summary,
          output_text = excluded.output_text,
          raw_json = excluded.raw_json,
          model_name = excluded.model_name
        """,
        (key, run_id, keyword_id, analysis_type, input_summary, output_text, dump_json(raw_json), model_name, ts),
    )
    row = conn.execute("SELECT analysis_id FROM analysis_results WHERE analysis_key = ?", (key,)).fetchone()
    return int(row["analysis_id"])


def record_failure(
    conn: sqlite3.Connection,
    run_id: int | None,
    stage: str,
    target: str = "",
    error_type: str = "",
    error_message: str = "",
    retry_count: int = 0,
    created_at: str | None = None,
    resolved: bool = False,
    failure_key: str = "",
) -> int:
    ts = created_at or now_iso()
    key = failure_key or f"failure:{stable_hash(run_id, stage, target, error_type, error_message, length=32)}"
    conn.execute(
        """
        INSERT INTO failures(
          failure_key, run_id, stage, target, error_type, error_message,
          retry_count, created_at, resolved
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(failure_key) DO UPDATE SET
          retry_count = MAX(failures.retry_count, excluded.retry_count),
          resolved = excluded.resolved
        """,
        (key, run_id, stage or "unknown", target, error_type, error_message, retry_count, ts, 1 if resolved else 0),
    )
    row = conn.execute("SELECT failure_id FROM failures WHERE failure_key = ?", (key,)).fetchone()
    return int(row["failure_id"])


def query_recent_runs_for_keyword(conn: sqlite3.Connection, keyword: str, limit: int = 5) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT cr.*
        FROM crawl_runs cr
        JOIN keywords k ON k.keyword_id = cr.keyword_id
        WHERE k.keyword = ?
        ORDER BY COALESCE(cr.finished_at, cr.started_at) DESC
        LIMIT ?
        """,
        (keyword, limit),
    ).fetchall()
    return rows_to_dicts(rows)


def query_note_metrics(conn: sqlite3.Connection, note_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT *
        FROM metrics_snapshots
        WHERE note_id = ?
        ORDER BY snapshot_time ASC, snapshot_id ASC
        """,
        (note_id,),
    ).fetchall()
    return rows_to_dicts(rows)


def query_keyword_new_notes_last_days(conn: sqlite3.Connection, keyword: str, days: int = 7) -> dict[str, Any]:
    since = (datetime.now(CN_TZ) - timedelta(days=days)).isoformat(timespec="seconds")
    row = conn.execute(
        """
        SELECT COUNT(DISTINCT n.note_id) AS new_note_count
        FROM notes n
        JOIN keyword_note_map knm ON knm.note_id = n.note_id
        JOIN keywords k ON k.keyword_id = knm.keyword_id
        WHERE k.keyword = ? AND n.first_seen_at >= ?
        """,
        (keyword, since),
    ).fetchone()
    return {"keyword": keyword, "days": days, "new_note_count": int(row["new_note_count"] or 0)}


def query_keyword_top_notes_by_interaction(conn: sqlite3.Connection, keyword: str, limit: int = 20) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        WITH latest AS (
          SELECT ms.*
          FROM metrics_snapshots ms
          JOIN (
            SELECT note_id, MAX(snapshot_time) AS snapshot_time
            FROM metrics_snapshots
            GROUP BY note_id
          ) pick ON pick.note_id = ms.note_id AND pick.snapshot_time = ms.snapshot_time
        )
        SELECT
          n.note_id, n.title, n.note_url, n.publish_time, a.nickname AS author,
          latest.liked_count, latest.collected_count, latest.comment_count, latest.share_count,
          COALESCE(latest.liked_count, 0)
            + COALESCE(latest.collected_count, 0) * 2
            + COALESCE(latest.comment_count, 0) * 3
            + COALESCE(latest.share_count, 0) * 2 AS interaction_score,
          latest.snapshot_time
        FROM keyword_note_map knm
        JOIN keywords k ON k.keyword_id = knm.keyword_id
        JOIN notes n ON n.note_id = knm.note_id
        LEFT JOIN authors a ON a.author_id = n.author_id
        LEFT JOIN latest ON latest.note_id = n.note_id
        WHERE k.keyword = ?
        GROUP BY n.note_id
        ORDER BY interaction_score DESC, latest.snapshot_time DESC
        LIMIT ?
        """,
        (keyword, limit),
    ).fetchall()
    return rows_to_dicts(rows)


def query_keyword_fastest_growing_notes(conn: sqlite3.Connection, keyword: str, limit: int = 20) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        WITH scoped AS (
          SELECT DISTINCT n.note_id, n.title, n.note_url, a.nickname AS author
          FROM keyword_note_map knm
          JOIN keywords k ON k.keyword_id = knm.keyword_id
          JOIN notes n ON n.note_id = knm.note_id
          LEFT JOIN authors a ON a.author_id = n.author_id
          WHERE k.keyword = ?
        ),
        ranked AS (
          SELECT
            ms.*,
            ROW_NUMBER() OVER (PARTITION BY ms.note_id ORDER BY ms.snapshot_time ASC, ms.snapshot_id ASC) AS rn_first,
            ROW_NUMBER() OVER (PARTITION BY ms.note_id ORDER BY ms.snapshot_time DESC, ms.snapshot_id DESC) AS rn_last
          FROM metrics_snapshots ms
          JOIN scoped s ON s.note_id = ms.note_id
        ),
        firsts AS (SELECT * FROM ranked WHERE rn_first = 1),
        lasts AS (SELECT * FROM ranked WHERE rn_last = 1)
        SELECT
          s.note_id, s.title, s.note_url, s.author,
          lasts.snapshot_time AS latest_snapshot_time,
          COALESCE(lasts.liked_count, 0) - COALESCE(firsts.liked_count, 0) AS liked_growth,
          COALESCE(lasts.collected_count, 0) - COALESCE(firsts.collected_count, 0) AS collected_growth,
          COALESCE(lasts.comment_count, 0) - COALESCE(firsts.comment_count, 0) AS comment_growth,
          COALESCE(lasts.share_count, 0) - COALESCE(firsts.share_count, 0) AS share_growth,
          (COALESCE(lasts.liked_count, 0) - COALESCE(firsts.liked_count, 0))
            + (COALESCE(lasts.collected_count, 0) - COALESCE(firsts.collected_count, 0)) * 2
            + (COALESCE(lasts.comment_count, 0) - COALESCE(firsts.comment_count, 0)) * 3
            + (COALESCE(lasts.share_count, 0) - COALESCE(firsts.share_count, 0)) * 2 AS growth_score
        FROM scoped s
        JOIN firsts ON firsts.note_id = s.note_id
        JOIN lasts ON lasts.note_id = s.note_id
        ORDER BY growth_score DESC, latest_snapshot_time DESC
        LIMIT ?
        """,
        (keyword, limit),
    ).fetchall()
    return rows_to_dicts(rows)


def query_keyword_top_authors(conn: sqlite3.Connection, keyword: str, limit: int = 20) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT
          a.author_id, COALESCE(a.nickname, 'unknown') AS nickname,
          COUNT(DISTINCT n.note_id) AS note_count,
          SUM(COALESCE(ms.liked_count, 0)) AS total_liked_count,
          SUM(COALESCE(ms.comment_count, 0)) AS total_comment_count
        FROM keyword_note_map knm
        JOIN keywords k ON k.keyword_id = knm.keyword_id
        JOIN notes n ON n.note_id = knm.note_id
        LEFT JOIN authors a ON a.author_id = n.author_id
        LEFT JOIN metrics_snapshots ms ON ms.note_id = n.note_id AND ms.run_id = knm.run_id
        WHERE k.keyword = ?
        GROUP BY a.author_id, a.nickname
        ORDER BY note_count DESC, total_liked_count DESC
        LIMIT ?
        """,
        (keyword, limit),
    ).fetchall()
    return rows_to_dicts(rows)


def query_keyword_fastest_comment_growth(conn: sqlite3.Connection, keyword: str, limit: int = 20) -> list[dict[str, Any]]:
    rows = query_keyword_fastest_growing_notes(conn, keyword, limit * 3)
    rows.sort(key=lambda item: (item.get("comment_growth") or 0, item.get("growth_score") or 0), reverse=True)
    return rows[:limit]


def query_keyword_latest_run_diff(conn: sqlite3.Connection, keyword: str) -> dict[str, Any]:
    runs = query_recent_runs_for_keyword(conn, keyword, 2)
    if not runs:
        return {"keyword": keyword, "status": "no_runs", "added": [], "removed": [], "kept": []}
    latest = runs[0]
    previous = runs[1] if len(runs) > 1 else None
    latest_ids = {
        row["note_id"]
        for row in conn.execute("SELECT note_id FROM keyword_note_map WHERE run_id = ?", (latest["run_id"],)).fetchall()
    }
    previous_ids = set()
    if previous:
        previous_ids = {
            row["note_id"]
            for row in conn.execute("SELECT note_id FROM keyword_note_map WHERE run_id = ?", (previous["run_id"],)).fetchall()
        }
    added_ids = latest_ids - previous_ids
    removed_ids = previous_ids - latest_ids
    kept_ids = latest_ids & previous_ids

    def notes_for(ids: set[str]) -> list[dict[str, Any]]:
        if not ids:
            return []
        placeholders = ",".join("?" for _ in ids)
        rows = conn.execute(
            f"SELECT note_id, title, note_url, publish_time FROM notes WHERE note_id IN ({placeholders})",
            tuple(ids),
        ).fetchall()
        return rows_to_dicts(rows)

    return {
        "keyword": keyword,
        "status": "ok",
        "latest_run_id": latest["run_id"],
        "previous_run_id": previous["run_id"] if previous else None,
        "added_count": len(added_ids),
        "removed_count": len(removed_ids),
        "kept_count": len(kept_ids),
        "added": notes_for(added_ids),
        "removed": notes_for(removed_ids),
        "kept": notes_for(kept_ids),
    }

