from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from storage.database import init_database, resolve_db_path  # noqa: E402
from sync_run_to_database import sync_run  # noqa: E402


def find_runs(outputs_dir: Path) -> list[dict[str, str]]:
    runs: list[dict[str, str]] = []
    for report_path in sorted(outputs_dir.rglob("trend-report.json")):
        run_dir = report_path.parent
        request_path = run_dir / "request.json"
        raw_path = run_dir / "raw-xhs.json"
        if not request_path.exists() or not raw_path.exists():
            continue
        runs.append(
            {
                "out_dir": str(run_dir),
                "request": str(request_path),
                "raw": str(raw_path),
                "report": str(report_path),
                "ai_input": str(run_dir / "ai_input.json") if (run_dir / "ai_input.json").exists() else "",
                "ai_output": str(run_dir / "ai_output.json") if (run_dir / "ai_output.json").exists() else "",
                "quality_audit": str(run_dir / "quality-audit.json") if (run_dir / "quality-audit.json").exists() else "",
            }
        )
    return runs


def migrate(outputs_dir: str, db_path: str, limit: int = 0) -> dict[str, Any]:
    db = init_database(db_path)
    runs = find_runs(Path(outputs_dir))
    if limit > 0:
        runs = runs[:limit]

    migrated: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    totals = {
        "runs_seen": len(runs),
        "runs_migrated": 0,
        "notes": 0,
        "comments": 0,
        "snapshots": 0,
        "failures": 0,
        "analysis_results": 0,
    }

    for item in runs:
        try:
            result = sync_run(
                db_path=str(db),
                request_path=item["request"],
                raw_path=item["raw"],
                report_path=item["report"],
                ai_input_path=item["ai_input"],
                ai_output_path=item["ai_output"],
                quality_audit_path=item["quality_audit"],
                out_dir=item["out_dir"],
            )
            migrated.append(result)
            totals["runs_migrated"] += 1
            totals["notes"] += int(result.get("notes") or 0)
            totals["comments"] += int(result.get("comments") or 0)
            totals["snapshots"] += int(result.get("snapshots") or 0)
            totals["failures"] += int(result.get("failures") or 0)
            totals["analysis_results"] += 1 if result.get("analysis_saved") else 0
        except Exception as exc:  # noqa: BLE001 - migration must skip bad historical runs.
            failures.append({"out_dir": item["out_dir"], "error": str(exc)})

    return {
        "db_path": str(db.resolve()),
        "outputs_dir": str(Path(outputs_dir).resolve()),
        "totals": totals,
        "migration_failures": failures,
        "migrated_runs": migrated,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate existing XHS JSON output directories into SQLite.")
    parser.add_argument("--outputs-dir", default=str(PROJECT_ROOT / "outputs"))
    parser.add_argument("--db", default=str(resolve_db_path()))
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--report-out", default="")
    args = parser.parse_args()

    report = migrate(args.outputs_dir, args.db, args.limit)
    if args.report_out:
        out = Path(args.report_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    totals = report["totals"]
    print(
        "migration_done "
        f"runs={totals['runs_migrated']}/{totals['runs_seen']} "
        f"notes={totals['notes']} comments={totals['comments']} "
        f"snapshots={totals['snapshots']} failures={totals['failures']} "
        f"db={report['db_path']}"
    )
    if report["migration_failures"]:
        print(f"migration_warnings={len(report['migration_failures'])}")


if __name__ == "__main__":
    main()

