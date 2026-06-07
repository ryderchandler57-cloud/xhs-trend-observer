from __future__ import annotations

import argparse
import sqlite3
import sys
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


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def compact_text(value: Any, limit: int = 90) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "..."


def fetch_discovered(conn: sqlite3.Connection, discovered_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT *
        FROM discovered_keywords
        WHERE discovered_id = ?
        """,
        (discovered_id,),
    ).fetchone()
    return row_to_dict(row)


def fetch_keyword(conn: sqlite3.Connection, keyword: str) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT *
        FROM keywords
        WHERE keyword = ?
        """,
        (keyword,),
    ).fetchone()
    return row_to_dict(row)


def list_discovered(conn: sqlite3.Connection, status: str = "") -> list[dict[str, Any]]:
    if status:
        rows = conn.execute(
            """
            SELECT discovered_id, keyword, status, confidence, reason, first_seen_at, last_seen_at
            FROM discovered_keywords
            WHERE status = ?
            ORDER BY
              CASE status
                WHEN 'watching' THEN 1
                WHEN 'candidate' THEN 2
                WHEN 'rejected' THEN 3
                WHEN 'promoted' THEN 4
                ELSE 5
              END,
              confidence DESC,
              discovered_id ASC
            """,
            (status,),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT discovered_id, keyword, status, confidence, reason, first_seen_at, last_seen_at
            FROM discovered_keywords
            ORDER BY
              CASE status
                WHEN 'watching' THEN 1
                WHEN 'candidate' THEN 2
                WHEN 'rejected' THEN 3
                WHEN 'promoted' THEN 4
                ELSE 5
              END,
              confidence DESC,
              discovered_id ASC
            """
        ).fetchall()
    return [dict(row) for row in rows]


def print_list(rows: list[dict[str, Any]]) -> None:
    if not rows:
        print("No discovered keywords found.")
        return
    headers = ["discovered_id", "keyword", "status", "confidence", "reason", "first_seen_at", "last_seen_at"]
    widths = {
        "discovered_id": 13,
        "keyword": 34,
        "status": 10,
        "confidence": 10,
        "reason": 56,
        "first_seen_at": 20,
        "last_seen_at": 20,
    }
    print(" | ".join(header.ljust(widths[header]) for header in headers))
    print("-+-".join("-" * widths[header] for header in headers))
    for row in rows:
        values = {
            "discovered_id": str(row.get("discovered_id", "")),
            "keyword": compact_text(row.get("keyword"), widths["keyword"]),
            "status": str(row.get("status", "")),
            "confidence": f"{float(row.get('confidence') or 0):.3f}",
            "reason": compact_text(row.get("reason"), widths["reason"]),
            "first_seen_at": str(row.get("first_seen_at") or ""),
            "last_seen_at": str(row.get("last_seen_at") or ""),
        }
        print(" | ".join(values[header].ljust(widths[header]) for header in headers))


def insert_keyword_if_missing(conn: sqlite3.Connection, keyword: str, category: str, timestamp: str) -> tuple[int, bool]:
    existing = fetch_keyword(conn, keyword)
    if existing:
        return int(existing["keyword_id"]), False
    conn.execute(
        """
        INSERT INTO keywords(keyword, category, enabled, created_at, updated_at)
        VALUES (?, ?, 1, ?, ?)
        """,
        (keyword, category or "", timestamp, timestamp),
    )
    row = fetch_keyword(conn, keyword)
    if row is None:
        raise RuntimeError("Keyword insert failed unexpectedly.")
    return int(row["keyword_id"]), True


def promote_keyword(
    conn: sqlite3.Connection,
    discovered_id: int,
    category: str,
    dry_run: bool = False,
) -> dict[str, Any]:
    discovered = fetch_discovered(conn, discovered_id)
    if not discovered:
        raise ValueError(f"discovered_id={discovered_id} not found.")
    keyword = str(discovered.get("keyword") or "").strip()
    if not keyword:
        raise ValueError(f"discovered_id={discovered_id} has empty keyword.")

    existing = fetch_keyword(conn, keyword)
    timestamp = now_iso()
    actions = [
        {
            "action": "insert_keyword_if_missing",
            "keyword": keyword,
            "category": category or "",
            "will_insert": existing is None,
        },
        {
            "action": "update_discovered_keyword",
            "discovered_id": discovered_id,
            "status": "promoted",
            "promoted_at": timestamp,
        },
    ]
    if dry_run:
        return {
            "mode": "dry-run",
            "discovered_id": discovered_id,
            "keyword": keyword,
            "keyword_id": existing.get("keyword_id") if existing else None,
            "inserted_keyword": False,
            "actions": actions,
        }

    with transaction(conn):
        keyword_id, inserted = insert_keyword_if_missing(conn, keyword, category, timestamp)
        conn.execute(
            """
            UPDATE discovered_keywords
            SET status = 'promoted',
                promoted_at = ?,
                last_seen_at = ?
            WHERE discovered_id = ?
            """,
            (timestamp, timestamp, discovered_id),
        )
    return {
        "mode": "write",
        "discovered_id": discovered_id,
        "keyword": keyword,
        "keyword_id": keyword_id,
        "inserted_keyword": inserted,
        "actions": actions,
    }


def reject_keyword(conn: sqlite3.Connection, discovered_id: int, dry_run: bool = False) -> dict[str, Any]:
    discovered = fetch_discovered(conn, discovered_id)
    if not discovered:
        raise ValueError(f"discovered_id={discovered_id} not found.")
    timestamp = now_iso()
    keyword = str(discovered.get("keyword") or "")
    actions = [
        {
            "action": "update_discovered_keyword",
            "discovered_id": discovered_id,
            "keyword": keyword,
            "status": "rejected",
            "last_seen_at": timestamp,
        }
    ]
    if dry_run:
        return {
            "mode": "dry-run",
            "discovered_id": discovered_id,
            "keyword": keyword,
            "actions": actions,
        }
    with transaction(conn):
        conn.execute(
            """
            UPDATE discovered_keywords
            SET status = 'rejected',
                last_seen_at = ?
            WHERE discovered_id = ?
            """,
            (timestamp, discovered_id),
        )
    return {
        "mode": "write",
        "discovered_id": discovered_id,
        "keyword": keyword,
        "actions": actions,
    }


def print_action_report(report: dict[str, Any]) -> None:
    print(f"mode={report.get('mode')}")
    print(f"discovered_id={report.get('discovered_id')}")
    print(f"keyword={report.get('keyword')}")
    if "keyword_id" in report:
        print(f"keyword_id={report.get('keyword_id')}")
    if "inserted_keyword" in report:
        print(f"inserted_keyword={report.get('inserted_keyword')}")
    print("actions:")
    for action in report.get("actions", []):
        details = ", ".join(f"{key}={value}" for key, value in action.items())
        print(f" - {details}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Manually review discovered keywords and promote/reject them.")
    parser.add_argument("--db", default="data/xhs-trend.sqlite")
    parser.add_argument("--list", action="store_true", help="List discovered keyword candidates.")
    parser.add_argument(
        "--status",
        choices=["candidate", "watching", "rejected", "promoted"],
        default="",
        help="Optional list filter.",
    )
    parser.add_argument("--promote-id", type=int, default=None, help="Promote one discovered keyword into keywords.")
    parser.add_argument("--reject-id", type=int, default=None, help="Reject one discovered keyword.")
    parser.add_argument("--category", default="", help="Category to set when promoting into keywords.")
    parser.add_argument("--dry-run", action="store_true", help="Show planned writes without changing the database.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    chosen = sum(1 for value in [args.list, args.promote_id is not None, args.reject_id is not None] if value)
    if chosen != 1:
        print("Choose exactly one action: --list, --promote-id, or --reject-id.", file=sys.stderr)
        return 2

    db_path = init_database(args.db)
    print(f"db={resolve_db_path(db_path)}")
    with get_connection(db_path) as conn:
        if args.list:
            print_list(list_discovered(conn, args.status))
            return 0
        if args.promote_id is not None:
            report = promote_keyword(conn, args.promote_id, args.category, args.dry_run)
            print_action_report(report)
            return 0
        if args.reject_id is not None:
            report = reject_keyword(conn, args.reject_id, args.dry_run)
            print_action_report(report)
            return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
