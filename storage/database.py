from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "xhs-trend.sqlite"
SCHEMA_PATH = Path(__file__).resolve().with_name("schema.sql")


def resolve_db_path(path: str | os.PathLike[str] | None = None) -> Path:
    raw = str(path or os.environ.get("XHS_DB_PATH") or DEFAULT_DB_PATH).strip()
    db_path = Path(raw)
    if not db_path.is_absolute():
        db_path = PROJECT_ROOT / db_path
    return db_path


def get_connection(path: str | os.PathLike[str] | None = None) -> sqlite3.Connection:
    db_path = resolve_db_path(path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_database(path: str | os.PathLike[str] | None = None) -> Path:
    db_path = resolve_db_path(path)
    with get_connection(db_path) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.commit()
    return db_path


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
