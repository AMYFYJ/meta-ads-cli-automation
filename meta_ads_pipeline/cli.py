from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from .adapters import is_sandbox_account, resolve_account
from .assets import ensure_sample_assets
from .doctor import run_doctor
from .executor import execute_actions, validation_rows
from .guards import active_status_violations, find_active_status
from .insights import generate_mock_insights, get_live_insights
from .optimization import generate_optimization_changes
from .planner import action_dicts, build_plan
from .migrate import migrate_workbook
from .schema import TABLES
from .storage import create_sqlite_template, create_template, load_source, save_with_updates
from .targeting import SEARCH_KINDS, search_targeting, workbook_cell
from .validators import has_blocking_errors, validate_dataset


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except Exception as exc:  # noqa: BLE001 - CLI should surface a clear top-level failure.
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Automate Meta ads publishing from Excel or SQLite.")
    sub = parser.add_subparsers(required=True)

    init = sub.add_parser("init", help="Create an Excel or SQLite campaign source template.")
    init.add_argument("--output", required=True)
    init.add_argument("--format", choices=["xlsx", "sqlite"], default="xlsx")
    init.add_argument("--with-sample", action="store_true")
    init.set_defaults(func=cmd_init)

    validate = sub.add_parser("validate", help="Validate a source workbook/database.")
    validate.add_argument("--source", required=True)
    validate.add_argument("--out-source", help="Optional copy with ValidationErrors appended.")
    validate.set_defaults(func=cmd_validate)

    plan = sub.add_parser("plan", help="Build a dry-run action plan.")
    plan.add_argument("--source", required=True)
    plan.add_argument("--out", default="outputs/plan.json")
    plan.set_defaults(func=cmd_plan)

    apply = sub.add_parser("apply", help="Apply creates and approved bulk changes.")
    apply.add_argument("--source", required=True)
    apply.add_argument("--mode", choices=["mock", "live"], default="mock")
    apply.add_argument("--state", default="state/mock_state.json")
    apply.add_argument("--out-source", required=True)
    apply.add_argument("--plan-out", default="")
    apply.add_argument("--continue-on-error", action="store_true")
    apply.add_argument("--account", default="", help="Override ad account id (act_...) for this run.")
    apply.add_argument("--require-sandbox", action="store_true", help="Refuse live apply unless the target is the sandbox account.")
    apply.add_argument("--force-paused", action="store_true", help="Refuse live apply if any action would set a status to ACTIVE.")
    apply.add_argument("--yes", action="store_true", help="Confirm execution.")
    apply.set_defaults(func=cmd_apply)

    bulk = sub.add_parser("bulk-edit", help="Append an approved bulk edit row to a source copy.")
    bulk.add_argument("--source", required=True)
    bulk.add_argument("--out-source", required=True)
    bulk.add_argument("--object-level", required=True, choices=["campaign", "adset", "creative", "ad"])
    bulk.add_argument("--object-key", required=True)
    bulk.add_argument("--field", required=True)
    bulk.add_argument("--value", required=True)
    bulk.add_argument("--operation", default="SET_FIELD")
    bulk.add_argument("--value-json", default="")
    bulk.add_argument("--reason", default="")
    bulk.add_argument("--requested-by", default="")
    bulk.add_argument("--approval-status", default="APPROVED")
    bulk.set_defaults(func=cmd_bulk_edit)

    insights = sub.add_parser("insights", help="Append mock or live insights snapshots.")
    insights.add_argument("--source", required=True)
    insights.add_argument("--mode", choices=["mock", "live"], default="mock")
    insights.add_argument("--out-source", required=True)
    insights.add_argument("--date-preset", default="last_7d")
    insights.add_argument("--level", default="ad")
    insights.add_argument("--breakdown", action="append", default=[])
    insights.add_argument("--account", default="", help="Override ad account id (act_...) for live insights.")
    insights.set_defaults(func=cmd_insights)

    optimize = sub.add_parser("optimize", help="Generate approved bulk changes from OptimizationRules and PerformanceSnapshots.")
    optimize.add_argument("--source", required=True)
    optimize.add_argument("--out-source", required=True)
    optimize.set_defaults(func=cmd_optimize)

    demo = sub.add_parser("demo", help="Run the complete team demo workflow in mock mode.")
    demo.add_argument("--workdir", default="outputs/demo")
    demo.set_defaults(func=cmd_demo)

    migrate = sub.add_parser("migrate", help="Migrate a workbook to the consolidated tab layout (presets/automation -> AdSets, creatives -> Ads, uploads -> Audiences).")
    migrate.add_argument("--source", required=True)
    migrate.add_argument("--out-source", required=True, help="Destination xlsx (may equal --source to migrate in place).")
    migrate.set_defaults(func=cmd_migrate)

    tsearch = sub.add_parser("targeting-search", help="Look up detailed-targeting IDs to paste into the interests/behaviors columns.")
    tsearch.add_argument("--q", required=True, help="Search term, e.g. 'yoga'.")
    tsearch.add_argument("--kind", default="interest", choices=list(SEARCH_KINDS), help="Targeting category to search.")
    tsearch.add_argument("--limit", type=int, default=15)
    tsearch.set_defaults(func=cmd_targeting_search)

    doctor = sub.add_parser("doctor", help="Preflight checks for live/sandbox readiness.")
    doctor.add_argument("--live", action="store_true", help="Also run a read-only token check against Meta.")
    doctor.add_argument("--account", default="", help="Override ad account id (act_...) for the check.")
    doctor.add_argument("--json", action="store_true", help="Emit a machine-readable JSON report.")
    doctor.set_defaults(func=cmd_doctor)
    return parser


def cmd_init(args: argparse.Namespace) -> int:
    output = Path(args.output)
    if args.with_sample:
        ensure_sample_assets(str(output.parent))
    if args.format == "xlsx":
        create_template(str(output), with_sample=args.with_sample)
    else:
        create_sqlite_template(str(output), with_sample=args.with_sample)
    print(f"Created {output}")
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    dataset = load_source(args.source)
    issues = validate_dataset(dataset)
    _print_issues(issues)
    if args.out_source:
        save_with_updates(dataset, args.out_source, [], validation_rows(issues))
        print(f"Wrote validation report to {args.out_source}")
    return 1 if has_blocking_errors(issues) else 0


def cmd_plan(args: argparse.Namespace) -> int:
    dataset = load_source(args.source)
    issues = validate_dataset(dataset)
    if has_blocking_errors(issues):
        _print_issues(issues)
        return 1
    actions = build_plan(dataset)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"actions": action_dicts(actions)}, indent=2), encoding="utf-8")
    print(f"Planned {len(actions)} actions -> {out}")
    return 0


# Force-paused guard logic lives in guards.py so the ads_agent layer shares one implementation.
_active_status_violations = active_status_violations
_find_active_status = find_active_status


def cmd_apply(args: argparse.Namespace) -> int:
    if not args.yes:
        print("Refusing to apply without --yes. Run plan first, then re-run apply with --yes.", file=sys.stderr)
        return 2
    if args.mode == "live":
        target_account = resolve_account(args.account)
        require_sandbox = args.require_sandbox or os.environ.get("META_REQUIRE_SANDBOX", "").lower() in {"1", "true", "yes", "on"}
        if require_sandbox and not is_sandbox_account(target_account):
            print(
                f"Refusing live apply: target account '{target_account or '(unset)'}' is not the sandbox account. "
                "Set SANDBOX_AD_ACCOUNT_ID (+ META_SANDBOX=1) or pass --account <sandbox>, or unset META_REQUIRE_SANDBOX.",
                file=sys.stderr,
            )
            return 2
    dataset = load_source(args.source)
    issues = validate_dataset(dataset)
    if has_blocking_errors(issues):
        _print_issues(issues)
        save_with_updates(dataset, args.out_source, [], validation_rows(issues))
        print(f"Wrote validation errors to {args.out_source}")
        return 1
    actions = build_plan(dataset)
    if args.mode == "live":
        force_paused = args.force_paused or os.environ.get("META_FORCE_PAUSED", "").lower() in {"1", "true", "yes", "on"}
        if force_paused:
            violations = _active_status_violations(actions)
            if violations:
                print(
                    "Refusing live apply: force-paused is on (META_FORCE_PAUSED / --force-paused) and these actions "
                    "would set a status to ACTIVE:",
                    file=sys.stderr,
                )
                for action_id, where in violations:
                    print(f"  - {action_id} ({where})", file=sys.stderr)
                print("Set desired_status/status_option to PAUSED on those rows, then re-run.", file=sys.stderr)
                return 2
    if args.plan_out:
        out = Path(args.plan_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({"actions": action_dicts(actions)}, indent=2), encoding="utf-8")
    results, updates, append_rows = execute_actions(
        dataset,
        actions,
        mode=args.mode,
        state_path=args.state,
        continue_on_error=args.continue_on_error,
        account_override=args.account,
    )
    append_rows.setdefault("ValidationErrors", []).extend(validation_rows(issues).get("ValidationErrors", []))
    save_with_updates(dataset, args.out_source, updates, append_rows)
    ok_count = sum(1 for result in results if result.ok)
    print(f"Applied {ok_count}/{len(results)} actions in {args.mode} mode -> {args.out_source}")
    return 0 if ok_count == len(results) else 1


def cmd_bulk_edit(args: argparse.Namespace) -> int:
    dataset = load_source(args.source)
    next_id = _next_change_id(dataset.tables.get("BulkChanges", []))
    row = {
        "change_id": next_id,
        "operation": args.operation,
        "object_level": args.object_level,
        "object_key_or_meta_id": args.object_key,
        "field": args.field,
        "old_value": "",
        "new_value": args.value,
        "value_json": args.value_json,
        "effective_at": "",
        "reason": args.reason,
        "requested_by": args.requested_by,
        "approval_status": args.approval_status,
        "applied_at": "",
        "result": "",
        "error": "",
    }
    save_with_updates(dataset, args.out_source, [], {"BulkChanges": [row]})
    print(f"Queued bulk change {next_id} -> {args.out_source}")
    return 0


def cmd_insights(args: argparse.Namespace) -> int:
    dataset = load_source(args.source)
    if args.mode == "mock":
        append_rows = generate_mock_insights(dataset, breakdowns=args.breakdown)
    else:
        ok, message, rows = get_live_insights(args.date_preset, args.level, breakdowns=args.breakdown, account=args.account)
        if not ok:
            print(message, file=sys.stderr)
            return 1
        append_rows = {"PerformanceSnapshots": rows}
    save_with_updates(dataset, args.out_source, [], append_rows)
    print(f"Appended {len(append_rows.get('PerformanceSnapshots', []))} insights rows -> {args.out_source}")
    return 0


def cmd_optimize(args: argparse.Namespace) -> int:
    dataset = load_source(args.source)
    issues = validate_dataset(dataset)
    if has_blocking_errors(issues):
        _print_issues(issues)
        return 1
    changes = generate_optimization_changes(dataset)
    save_with_updates(dataset, args.out_source, [], {"BulkChanges": changes})
    print(f"Generated {len(changes)} optimization bulk changes -> {args.out_source}")
    return 0


def cmd_demo(args: argparse.Namespace) -> int:
    workdir = Path(args.workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    workbook = workdir / "meta_ads_workflow_template.xlsx"
    applied = workdir / "applied_workbook.xlsx"
    insights = workdir / "insights_workbook.xlsx"
    plan = workdir / "plan.json"
    state = workdir / "mock_state.json"

    ensure_sample_assets(str(workdir))
    create_template(str(workbook), with_sample=True)
    dataset = load_source(str(workbook))
    issues = validate_dataset(dataset)
    if has_blocking_errors(issues):
        _print_issues(issues)
        return 1
    actions = build_plan(dataset)
    plan.write_text(json.dumps({"actions": action_dicts(actions)}, indent=2), encoding="utf-8")
    results, updates, append_rows = execute_actions(dataset, actions, mode="mock", state_path=str(state))
    append_rows.setdefault("ValidationErrors", []).extend(validation_rows(issues).get("ValidationErrors", []))
    save_with_updates(dataset, str(applied), updates, append_rows)
    applied_dataset = load_source(str(applied))
    save_with_updates(applied_dataset, str(insights), [], generate_mock_insights(applied_dataset, breakdowns=["publisher_platform", "age"]))
    print(f"Demo complete: {workdir}")
    print(f"- Workbook: {workbook}")
    print(f"- Plan: {plan}")
    print(f"- Applied workbook: {applied}")
    print(f"- Mock state: {state}")
    print(f"- Insights workbook: {insights}")
    print(f"- Actions succeeded: {sum(1 for result in results if result.ok)}/{len(results)}")
    return 0 if all(result.ok for result in results) else 1


def cmd_migrate(args: argparse.Namespace) -> int:
    source = Path(args.source)
    if source.suffix.lower() != ".xlsx":
        print("migrate currently supports .xlsx workbooks only.", file=sys.stderr)
        return 2
    notes = migrate_workbook(args.source, args.out_source)
    print(f"Migrated {args.source} -> {args.out_source} ({len(TABLES)} consolidated tabs).")
    for note in notes:
        print(f"  - {note}")
    return 0


def cmd_targeting_search(args: argparse.Namespace) -> int:
    _load_dotenv()
    results = search_targeting(args.q, kind=args.kind, limit=args.limit)
    if not results:
        print(f"No {args.kind} results for '{args.q}'.")
        return 1
    width = max(len(row["id"]) for row in results)
    for row in results:
        size = f"  (~{row['audience_size']:,})" if isinstance(row["audience_size"], int) else ""
        path = f"  [{row['path']}]" if row["path"] else ""
        print(f"{row['id']:<{width}}  {row['name']}{size}{path}")
    print("\nPaste into the workbook interests/behaviors cell (keep only the ones you want):")
    print(workbook_cell(results))
    return 0


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:  # pragma: no cover - dotenv is in requirements
        return
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")


def cmd_doctor(args: argparse.Namespace) -> int:
    report = run_doctor(live=args.live, account=args.account)
    if args.json:
        print(json.dumps(report, indent=2))
        return 0 if report["ok"] else 1
    symbols = {"ok": "OK  ", "warn": "WARN", "fail": "FAIL"}
    for check in report["checks"]:
        print(f"[{symbols.get(check['status'], check['status'])}] {check['name']}: {check['detail']}")
        if check.get("hint") and check["status"] != "ok":
            print(f"       -> {check['hint']}")
    print("Doctor: READY for live/sandbox." if report["ok"] else "Doctor: NOT ready — resolve FAIL checks above.")
    return 0 if report["ok"] else 1


def _print_issues(issues: list[Any]) -> None:
    if not issues:
        print("No validation issues.")
        return
    for issue in issues:
        print(f"{issue.severity}: {issue.table}[{issue.row_key}] {issue.field}: {issue.message}")


def _next_change_id(rows: list[dict[str, Any]]) -> str:
    existing = {str(row.get("change_id", "")) for row in rows}
    idx = len(existing) + 1
    while f"chg_manual_{idx:04d}" in existing:
        idx += 1
    return f"chg_manual_{idx:04d}"


if __name__ == "__main__":
    raise SystemExit(main())
