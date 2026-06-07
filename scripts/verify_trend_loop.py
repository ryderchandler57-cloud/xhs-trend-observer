from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CORE_TABLES = [
    "keywords",
    "discovered_keywords",
    "crawl_runs",
    "notes",
    "metrics_snapshots",
    "observations",
    "insights",
    "failures",
]
REQUEST_JSON_PATH = PROJECT_ROOT / "outputs" / "smoke-test" / "request.json"
KNOWN_REQUEST_HASH = "44F97816988E4590B4EB30715C10F99E8503B42F4AC6445C029CFFDCEABACCA3"
CN_TZ = timezone(timedelta(hours=8))


def now_iso() -> str:
    return datetime.now(CN_TZ).isoformat(timespec="seconds")


def resolve_path(path: str | Path) -> Path:
    out = Path(path)
    if not out.is_absolute():
        out = PROJECT_ROOT / out
    return out


def connect_readonly(db_path: Path) -> sqlite3.Connection | None:
    if not db_path.exists():
        return None
    uri_path = urllib.parse.quote(db_path.resolve().as_posix(), safe="/:")
    conn = sqlite3.connect(f"file:{uri_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def table_exists(conn: sqlite3.Connection | None, table: str) -> bool:
    if conn is None:
        return False
    row = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?", (table,)).fetchone()
    return row is not None


def table_count(conn: sqlite3.Connection | None, table: str) -> int | None:
    if conn is None or not table_exists(conn, table):
        return None
    row = conn.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()
    return int(row["count"])


def query_one(conn: sqlite3.Connection | None, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any]:
    if conn is None:
        return {}
    row = conn.execute(sql, params).fetchone()
    return dict(row) if row else {}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def find_latest_file(filename: str) -> Path | None:
    outputs = PROJECT_ROOT / "outputs"
    if not outputs.exists():
        return None
    matches = sorted(outputs.rglob(filename), key=lambda item: item.stat().st_mtime, reverse=True)
    return matches[0] if matches else None


def add_issue(issues: list[dict[str, str]], name: str, severity: str, cause: str, next_step: str) -> None:
    issues.append(
        {
            "name": name,
            "severity": severity,
            "possible_cause": cause,
            "next_step": next_step,
        }
    )


def core_table_report(conn: sqlite3.Connection | None, issues: list[dict[str, str]]) -> dict[str, Any]:
    tables: dict[str, Any] = {}
    for table in CORE_TABLES:
        count = table_count(conn, table)
        exists = count is not None
        tables[table] = {"exists": exists, "count": count if count is not None else 0}
        if not exists:
            add_issue(issues, f"表缺失：{table}", "high", "数据库 schema 未初始化或版本不完整。", "运行数据库初始化/迁移脚本。")
    return tables


def integrity_checks(conn: sqlite3.Connection | None, tables: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    counts = {name: int(info.get("count") or 0) for name, info in tables.items()}
    checks: dict[str, Any] = {
        "notes_has_data": counts.get("notes", 0) > 0,
        "snapshots_more_than_notes": counts.get("metrics_snapshots", 0) > counts.get("notes", 0),
        "crawl_runs_at_least_one": counts.get("crawl_runs", 0) >= 1,
        "observations_match_runs": False,
        "insights_based_on_observations": False,
        "discovered_based_on_sources": False,
        "promoted_keyword_exists": False,
        "task_plan_exists": False,
        "request_draft_exists": False,
        "official_request_not_overwritten": False,
    }
    if conn is not None and table_exists(conn, "observations") and table_exists(conn, "crawl_runs"):
        row = query_one(
            conn,
            """
            SELECT COUNT(*) AS linked_count
            FROM observations o
            JOIN crawl_runs cr ON cr.run_id = o.run_id
            """,
        )
        checks["observation_run_link_count"] = int(row.get("linked_count") or 0)
        checks["observations_match_runs"] = checks["observation_run_link_count"] == counts.get("observations", 0) and counts.get("observations", 0) > 0
    if conn is not None and table_exists(conn, "insights"):
        row = query_one(conn, "SELECT COUNT(*) AS count FROM insights WHERE observation_count > 0")
        checks["insights_with_observations"] = int(row.get("count") or 0)
        checks["insights_based_on_observations"] = checks["insights_with_observations"] > 0
    if conn is not None and table_exists(conn, "discovered_keywords"):
        row = query_one(
            conn,
            """
            SELECT COUNT(*) AS count
            FROM discovered_keywords
            WHERE source_type LIKE 'insight_%'
               OR source_type LIKE 'observation_%'
               OR source_type LIKE 'analysis_result_%'
               OR source_type LIKE 'trend_summary_%'
            """,
        )
        checks["discovered_from_allowed_sources"] = int(row.get("count") or 0)
        checks["discovered_based_on_sources"] = checks["discovered_from_allowed_sources"] > 0
    if conn is not None and table_exists(conn, "discovered_keywords") and table_exists(conn, "keywords"):
        row = query_one(
            conn,
            """
            SELECT COUNT(*) AS count
            FROM discovered_keywords dk
            JOIN keywords k ON k.keyword = dk.keyword
            WHERE dk.status = 'promoted'
            """,
        )
        checks["promoted_keyword_count"] = int(row.get("count") or 0)
        checks["promoted_keyword_exists"] = checks["promoted_keyword_count"] > 0

    task_plan = find_latest_file("keyword-task-plan.json")
    request_draft = find_latest_file("request-draft.json")
    checks["task_plan_path"] = str(task_plan) if task_plan else ""
    checks["request_draft_path"] = str(request_draft) if request_draft else ""
    checks["task_plan_exists"] = task_plan is not None
    checks["request_draft_exists"] = request_draft is not None
    if REQUEST_JSON_PATH.exists():
        checks["official_request_path"] = str(REQUEST_JSON_PATH)
        checks["official_request_hash"] = sha256(REQUEST_JSON_PATH)
        checks["official_request_not_overwritten"] = checks["official_request_hash"] == KNOWN_REQUEST_HASH
    return checks


def score_health(tables: dict[str, Any], checks: dict[str, Any]) -> dict[str, Any]:
    counts = {name: int(info.get("count") or 0) for name, info in tables.items()}
    dimensions = {
        "database_structure": 0,
        "snapshot_accumulation": 0,
        "observations": 0,
        "insights": 0,
        "discovered_keywords": 0,
        "manual_promotion": 0,
        "task_planning": 0,
    }
    existing_table_count = sum(1 for info in tables.values() if info.get("exists"))
    dimensions["database_structure"] = round(20 * existing_table_count / len(CORE_TABLES), 2)

    if counts.get("notes", 0) > 0:
        dimensions["snapshot_accumulation"] += 4
    if counts.get("metrics_snapshots", 0) > counts.get("notes", 0):
        dimensions["snapshot_accumulation"] += 8
    if counts.get("crawl_runs", 0) >= 2:
        dimensions["snapshot_accumulation"] += 5
    elif counts.get("crawl_runs", 0) >= 1:
        dimensions["snapshot_accumulation"] += 3
    if counts.get("notes", 0) >= 50:
        dimensions["snapshot_accumulation"] += 3

    if counts.get("observations", 0) > 0:
        dimensions["observations"] += 8
    if checks.get("observations_match_runs"):
        dimensions["observations"] += 8
    if counts.get("observations", 0) >= 3:
        dimensions["observations"] += 4
    elif counts.get("observations", 0) >= 2:
        dimensions["observations"] += 2

    if counts.get("insights", 0) > 0:
        dimensions["insights"] += 8
    if checks.get("insights_based_on_observations"):
        dimensions["insights"] += 7

    if counts.get("discovered_keywords", 0) > 0:
        dimensions["discovered_keywords"] += 5
    if checks.get("discovered_based_on_sources"):
        dimensions["discovered_keywords"] += 5

    if checks.get("promoted_keyword_exists"):
        dimensions["manual_promotion"] += 10
    elif counts.get("discovered_keywords", 0) > 0:
        dimensions["manual_promotion"] += 4

    if checks.get("task_plan_exists"):
        dimensions["task_planning"] += 3
    if checks.get("request_draft_exists"):
        dimensions["task_planning"] += 2

    total = round(sum(dimensions.values()), 2)
    return {"score": min(100, total), "dimensions": dimensions}


def issue_report(tables: dict[str, Any], checks: dict[str, Any], issues: list[dict[str, str]]) -> list[dict[str, str]]:
    counts = {name: int(info.get("count") or 0) for name, info in tables.items()}
    if counts.get("notes", 0) == 0:
        add_issue(issues, "notes 无数据", "high", "尚未同步采集结果到长期数据库。", "先运行一次采集并完成 SQLite 旁路同步。")
    elif counts.get("notes", 0) < 50:
        add_issue(issues, "样本量偏少", "medium", "当前仍是 smoke/小样本级别数据。", "继续积累多轮真实采集数据。")
    if counts.get("metrics_snapshots", 0) <= counts.get("notes", 0):
        add_issue(issues, "快照积累不足", "medium", "每篇笔记还没有多轮互动快照。", "对同一批关键词多跑几轮采集。")
    if counts.get("crawl_runs", 0) < 2:
        add_issue(issues, "采集轮次不足", "medium", "少于两次 crawl_runs，增长判断很弱。", "至少完成两轮以上采集。")
    if counts.get("observations", 0) < 3:
        add_issue(issues, "observations 偏少", "medium", "观察层还不足以形成稳定日/周趋势。", "继续多跑几轮采集并生成 observations。")
    if counts.get("insights", 0) == 0:
        add_issue(issues, "insights 缺失", "high", "AI 洞察层尚未生成。", "运行 scripts/generate_insights.py。")
    if counts.get("discovered_keywords", 0) == 0:
        add_issue(issues, "候选关键词缺失", "medium", "关键词发现层尚未产出候选。", "运行 scripts/discover_keywords.py。")
    if not checks.get("promoted_keyword_exists"):
        add_issue(issues, "无 promoted 正式关键词", "medium", "候选关键词还没有经过人工晋升。", "使用 scripts/promote_keywords.py 审核并晋升。")
    if not checks.get("task_plan_exists"):
        add_issue(issues, "任务计划缺失", "low", "尚未生成 keyword-task-plan.json。", "运行 scripts/plan_keyword_tasks.py。")
    if not checks.get("request_draft_exists"):
        add_issue(issues, "request 草稿缺失", "low", "尚未生成 request-draft.json。", "运行 plan_keyword_tasks.py 时加 --request-draft。")
    if not checks.get("official_request_not_overwritten"):
        add_issue(issues, "正式 request.json 可能被覆盖", "high", "正式 request.json 哈希与验收基线不一致或文件不存在。", "检查 outputs/smoke-test/request.json 是否被计划脚本覆盖。")
    return issues


def next_actions(tables: dict[str, Any], checks: dict[str, Any]) -> list[str]:
    counts = {name: int(info.get("count") or 0) for name, info in tables.items()}
    actions: list[str] = []
    if counts.get("notes", 0) < 50:
        actions.append("数据量仍偏少，建议继续积累真实采集数据。")
    if counts.get("observations", 0) < 3:
        actions.append("observations 太少，建议先多跑几轮采集并生成观察记录。")
    if counts.get("insights", 0) == 0:
        actions.append("insights 太少，建议运行 scripts/generate_insights.py。")
    if counts.get("discovered_keywords", 0) == 0:
        actions.append("discovered_keywords 太少，建议运行 scripts/discover_keywords.py。")
    if checks.get("discovered_from_allowed_sources", 0) and not checks.get("promoted_keyword_exists"):
        actions.append("存在 candidate/watching 候选，建议人工审核后使用 scripts/promote_keywords.py。")
    if checks.get("promoted_keyword_exists"):
        actions.append("已经有 promoted 关键词，建议运行 scripts/plan_keyword_tasks.py 生成下一轮任务计划。")
    if checks.get("task_plan_exists") and checks.get("request_draft_exists"):
        actions.append("任务计划和 request-draft 已存在，可人工检查后决定是否执行下一轮采集。")
    return actions


def build_report(db_path: Path, output_dir: Path) -> dict[str, Any]:
    issues: list[dict[str, str]] = []
    conn = connect_readonly(db_path)
    try:
        if conn is None:
            add_issue(issues, "数据库不存在", "high", "传入的 SQLite 路径不存在。", "确认 --db 参数或先运行数据库同步。")
        tables = core_table_report(conn, issues)
        checks = integrity_checks(conn, tables, output_dir)
        issues = issue_report(tables, checks, issues)
        score = score_health(tables, checks)
        actions = next_actions(tables, checks)
    finally:
        if conn is not None:
            conn.close()
    completed_layers = [
        name
        for name, ok in [
            ("SQLite 旁路数据库", all(tables.get(t, {}).get("exists") for t in ["keywords", "notes", "crawl_runs"])),
            ("metrics_snapshots 快照层", tables.get("metrics_snapshots", {}).get("count", 0) > 0),
            ("observations 观察层", tables.get("observations", {}).get("count", 0) > 0),
            ("insights AI洞察层", tables.get("insights", {}).get("count", 0) > 0),
            ("discovered_keywords 关键词发现层", tables.get("discovered_keywords", {}).get("count", 0) > 0),
            ("人工审核晋升机制", checks.get("promoted_keyword_exists")),
            ("关键词任务规划层", checks.get("task_plan_exists")),
        ]
        if ok
    ]
    return {
        "generated_at": now_iso(),
        "db_path": str(db_path),
        "output_dir": str(output_dir),
        "tables": tables,
        "integrity_checks": checks,
        "health": score,
        "completed_layers": completed_layers,
        "issues": issues,
        "next_actions": actions,
        "safety": {
            "read_only_database": True,
            "no_collection_started": True,
            "no_gemini_called": True,
            "no_keywords_written": True,
            "official_request_overwritten": not checks.get("official_request_not_overwritten"),
        },
    }


def markdown_table(rows: list[dict[str, Any]], columns: list[tuple[str, str]]) -> str:
    if not rows:
        return "暂无"
    lines = ["| " + " | ".join(label for label, _ in columns) + " |", "| " + " | ".join("---" for _ in columns) + " |"]
    for row in rows:
        lines.append("| " + " | ".join(str(row.get(key, "")).replace("|", "/") for _, key in columns) + " |")
    return "\n".join(lines)


def render_markdown(report: dict[str, Any]) -> str:
    table_rows = [
        {"table": name, "exists": "yes" if info["exists"] else "warning: missing", "count": info["count"]}
        for name, info in report["tables"].items()
    ]
    dimensions = report["health"]["dimensions"]
    dimension_rows = [{"dimension": key, "score": value} for key, value in dimensions.items()]
    check_rows = [
        {"check": key, "value": value}
        for key, value in report["integrity_checks"].items()
        if not key.endswith("_path") and key not in {"official_request_hash"}
    ]
    lines = [
        "# 趋势闭环健康验收报告",
        "",
        f"- 生成时间：{report['generated_at']}",
        f"- 数据库：{report['db_path']}",
        f"- 健康评分：{report['health']['score']} / 100",
        "",
        "## 核心表",
        "",
        markdown_table(table_rows, [("表", "table"), ("存在", "exists"), ("数量", "count")]),
        "",
        "## 已完成层",
        "",
    ]
    lines.extend(f"- {layer}" for layer in report["completed_layers"])
    lines.extend(
        [
            "",
            "## 数据链路检查",
            "",
            markdown_table(check_rows, [("检查项", "check"), ("结果", "value")]),
            "",
            "## 评分维度",
            "",
            markdown_table(dimension_rows, [("维度", "dimension"), ("得分", "score")]),
            "",
            "## 问题清单",
            "",
        ]
    )
    lines.append(
        markdown_table(
            report["issues"],
            [("问题", "name"), ("严重程度", "severity"), ("可能原因", "possible_cause"), ("建议下一步", "next_step")],
        )
    )
    lines.extend(["", "## 下一步行动建议", ""])
    lines.extend(f"- {item}" for item in report["next_actions"])
    lines.extend(["", "## 安全边界", ""])
    for key, value in report["safety"].items():
        lines.append(f"- {key}: {value}")
    return "\n".join(lines) + "\n"


def write_outputs(report: dict[str, Any], output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    md_path = output_dir / "trend-loop-health.md"
    json_path = output_dir / "trend-loop-health.json"
    md_path.write_text(render_markdown(report), encoding="utf-8-sig")
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return md_path, json_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify the end-to-end trend intelligence loop health.")
    parser.add_argument("--db", default="data/xhs-trend.sqlite")
    parser.add_argument("--output-dir", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    db_path = resolve_path(args.db)
    output_dir = resolve_path(args.output_dir)
    report = build_report(db_path, output_dir)
    md_path, json_path = write_outputs(report, output_dir)
    print(f"db={db_path}")
    print(f"health_score={report['health']['score']}")
    print(f"completed_layers={len(report['completed_layers'])}")
    print(f"issues={len(report['issues'])}")
    print(f"markdown={md_path}")
    print(f"json={json_path}")
    print("No collection, Gemini call, keyword write, or request overwrite was performed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
