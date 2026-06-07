from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from storage.database import SCHEMA_PATH, get_connection, init_database, resolve_db_path  # noqa: E402


REQUIRED_TABLES = [
    "keywords",
    "discovered_keywords",
    "crawl_runs",
    "notes",
    "metrics_snapshots",
    "observations",
    "insights",
    "failures",
]


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    return row is not None


def table_count(conn: sqlite3.Connection, table: str) -> int | None:
    if not table_exists(conn, table):
        return None
    row = conn.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()
    return int(row["count"])


def inspect_tables(db_path: Path) -> list[dict[str, Any]]:
    with get_connection(db_path) as conn:
        return [
            {
                "table": table,
                "exists": table_exists(conn, table),
                "count": table_count(conn, table),
            }
            for table in REQUIRED_TABLES
        ]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Initialize the local XHS trend SQLite database.")
    parser.add_argument("--db", default="data/xhs-trend.sqlite", help="SQLite database path.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    db_path = resolve_db_path(args.db)
    existed_before = db_path.exists()

    initialized_path = init_database(db_path)
    table_report = inspect_tables(initialized_path)
    missing = [item["table"] for item in table_report if not item["exists"]]

    print(f"Database: {initialized_path}")
    print(f"Schema: {SCHEMA_PATH}")
    print(f"Created database: {str(not existed_before).lower()}")
    print(f"Missing required tables: {len(missing)}")
    for item in table_report:
        status = "ok" if item["exists"] else "missing"
        count = 0 if item["count"] is None else item["count"]
        print(f"- {item['table']}: {status}, rows={count}")

    if missing:
        print("Warning: required tables are still missing: " + ", ".join(missing))
        return 1

    print("Database initialization OK. No test data was written.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
