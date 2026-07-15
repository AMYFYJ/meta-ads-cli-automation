from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
from pathlib import Path

from .config import load_config
from .pipeline_ops import apply_plan, billing_preflight, fetch_and_append_insights, plan_preview
from .report import fallback_report, latest_report, write_report_files
from .workbook import excel_lock_path, load_workbook_dataset


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except Exception as exc:  # noqa: BLE001 - CLI surfaces a clear top-level failure
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ads_agent", description="LLM-agent automation layer over meta_ads_pipeline.")
    sub = parser.add_subparsers(required=True)

    scan = sub.add_parser("scan", help="Fetch insights, run the optimizer agent, write PENDING proposals + a report.")
    scan.add_argument("--mode", choices=["mock", "live"], default=None)
    scan.add_argument("--workbook", default=None)
    scan.add_argument("--skip-insights", action="store_true", help="Skip the insights fetch (reuse existing snapshots).")
    scan.add_argument("--max-proposals", type=int, default=None)
    scan.set_defaults(func=cmd_scan)

    apply_cmd = sub.add_parser("apply-approved", help="Apply APPROVED unapplied BulkChanges (and any pending creates). No LLM.")
    apply_cmd.add_argument("--mode", choices=["mock", "live"], default=None)
    apply_cmd.add_argument("--workbook", default=None)
    apply_cmd.add_argument("--account", default=None)
    apply_cmd.add_argument("--yes", action="store_true")
    apply_cmd.set_defaults(func=cmd_apply_approved, label="apply")

    launch = sub.add_parser("launch", help="Create campaigns/ad sets/ads from the workbook (human-triggered). No LLM.")
    launch.add_argument("--mode", choices=["mock", "live"], default=None)
    launch.add_argument("--workbook", default=None)
    launch.add_argument("--account", default=None)
    launch.add_argument("--yes", action="store_true")
    launch.add_argument("--summarize", action="store_true", help="Add an LLM-written pre-flight summary of the plan preview.")
    launch.set_defaults(func=cmd_launch, label="launch")

    report = sub.add_parser("report", help="Print the latest scan report path and contents.")
    report.add_argument("--workbook", default=None)
    report.set_defaults(func=cmd_report)

    doctor = sub.add_parser("doctor", help="Pipeline doctor plus agent-layer checks.")
    doctor.add_argument("--live", action="store_true")
    doctor.add_argument("--workbook", default=None)
    doctor.set_defaults(func=cmd_doctor)
    return parser


def _cfg(args: argparse.Namespace):
    overrides = {}
    for attr in ("mode", "workbook", "account", "max_proposals"):
        if getattr(args, attr, None) is not None:
            overrides[attr] = getattr(args, attr)
    return load_config(**overrides)


def cmd_scan(args: argparse.Namespace) -> int:
    from .agent import run_scan_agent  # deferred: keep non-LLM commands importable without the SDK

    cfg = _cfg(args)
    if not args.skip_insights:
        count, message = fetch_and_append_insights(cfg)
        print(f"[insights] {message}")
        if cfg.mode == "live" and count == 0 and "failed" in message:
            print("Aborting scan: no insight data.", file=sys.stderr)
            return 1
    run_state: dict = {}
    transcript: list[str] = []
    print(f"[agent] running scan ({cfg.model}, mode={cfg.mode}) ...")
    try:
        success = asyncio.run(run_scan_agent(cfg, run_state, transcript))
    finally:
        for line in transcript:
            print(f"[agent] {line}")
    proposals = run_state.get("change_ids", [])
    if not run_state.get("report_paths"):
        print("[report] agent did not write a report — writing deterministic fallback")
        dataset = load_workbook_dataset(cfg.workbook)
        markdown = fallback_report(cfg, dataset, proposals, note="Agent run ended without a report; data appendix only.")
        md_path, html_path = write_report_files(cfg, markdown)
        run_state["report_paths"] = [str(md_path), str(html_path)]
    print(f"[report] {run_state['report_paths'][0]}")
    print(f"[scan] {len(proposals)} proposal(s) written as PENDING: {', '.join(proposals) or '(none)'}")
    if proposals:
        print("Next: review the report, flip approved rows to APPROVED in the workbook, then run:")
        print("  python -m ads_agent apply-approved --mode live --yes")
    return 0 if success else 1


def cmd_apply_approved(args: argparse.Namespace) -> int:
    cfg = _cfg(args)
    if not args.yes:
        preview = plan_preview(cfg)
        print(json.dumps(preview, indent=2))
        print("\nRefusing to apply without --yes.", file=sys.stderr)
        return 2
    outcome = apply_plan(cfg, label=args.label)
    return _print_outcome(outcome)


def cmd_launch(args: argparse.Namespace) -> int:
    cfg = _cfg(args)
    ok, detail = billing_preflight(cfg)
    print(f"[billing] {detail}")
    preview = plan_preview(cfg)
    if preview["blocking"]:
        for line in preview["validation_issues"]:
            print(line, file=sys.stderr)
        return 1
    print(f"[plan] {len(preview['actions'])} action(s):")
    for action in preview["actions"]:
        print(f"  - {action.get('action_id')} ({action.get('operation')})")
    if args.summarize:
        from .agent import summarize_plan

        summary = asyncio.run(summarize_plan(cfg, preview))
        print("\n[summary]\n" + summary + "\n")
    if not args.yes:
        print("\nDry run only. Re-run with --yes to execute.", file=sys.stderr)
        return 2
    outcome = apply_plan(cfg, label=args.label)
    return _print_outcome(outcome)


def _print_outcome(outcome) -> int:
    print(f"[apply] {outcome.message}")
    for result in outcome.results:
        marker = "ok " if result["ok"] else "FAIL"
        line = f"  [{marker}] {result['action_id']}"
        if result["meta_id"]:
            line += f" -> {result['meta_id']}"
        if not result["ok"] and result["message"]:
            line += f" ({result['message'][:160]})"
        print(line)
    if outcome.archive_path:
        print(f"[workbook] new version committed: {outcome.archive_path}")
    return 0 if outcome.ok else 1


def cmd_report(args: argparse.Namespace) -> int:
    cfg = _cfg(args)
    path = latest_report(cfg)
    if path is None:
        print("No reports yet. Run `python -m ads_agent scan` first.", file=sys.stderr)
        return 1
    print(f"{path}\n")
    print(path.read_text(encoding="utf-8"))
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    from meta_ads_pipeline.doctor import run_doctor

    cfg = _cfg(args)
    report = run_doctor(live=args.live, account=cfg.account)
    checks = list(report["checks"])

    def check(name: str, ok: bool | None, detail: str, hint: str = "") -> None:
        status = "ok" if ok else ("warn" if ok is None else "fail")
        checks.append({"name": name, "status": status, "detail": detail, "hint": hint})

    has_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
    check("ANTHROPIC_API_KEY", True if has_key else None,
          "set" if has_key else "not set (agent scan will rely on `claude` CLI login if available)")
    try:
        import claude_agent_sdk  # noqa: F401
        check("claude-agent-sdk", True, "importable")
    except ImportError:
        check("claude-agent-sdk", False, "not importable", "pip install claude-agent-sdk into .venv")
    claude_bin = shutil.which("claude")
    check("claude CLI", True if claude_bin else None, claude_bin or "not on PATH (SDK bundles its own runtime; verify scan works)")
    check("workbook", cfg.workbook.exists(), str(cfg.workbook), "set ADS_AGENT_WORKBOOK or create the workbook")
    lock = excel_lock_path(cfg.workbook)
    check("excel lock", not lock.exists(), "no lock file" if not lock.exists() else f"{lock.name} present — close Excel")
    cfg.report_dir.mkdir(parents=True, exist_ok=True)
    check("report dir", os.access(cfg.report_dir, os.W_OK), str(cfg.report_dir))
    if args.live:
        ok, detail = billing_preflight(load_config(mode="live"))
        check("billing (funding source)", ok, detail)

    symbols = {"ok": "OK  ", "warn": "WARN", "fail": "FAIL"}
    failed = False
    for item in checks:
        print(f"[{symbols.get(item['status'], item['status'])}] {item['name']}: {item['detail']}")
        if item.get("hint") and item["status"] != "ok":
            print(f"       -> {item['hint']}")
        failed = failed or item["status"] == "fail"
    print("Agent doctor: READY." if not failed else "Agent doctor: NOT ready — resolve FAIL checks above.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
