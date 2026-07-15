"""Deterministic glue over meta_ads_pipeline. No LLM imports here.

Everything that touches Meta or mutates the workbook lives in this module (via
the existing plan -> apply seam), so the agent layer's side effects are
auditable and testable without a model in the loop.
"""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from meta_ads_pipeline.adapters import is_sandbox_account, resolve_account
from meta_ads_pipeline.executor import execute_actions, validation_rows
from meta_ads_pipeline.guards import active_status_violations
from meta_ads_pipeline.insights import generate_mock_insights, get_live_insights
from meta_ads_pipeline.planner import action_dicts, build_plan
from meta_ads_pipeline.schema import clean
from meta_ads_pipeline.validators import has_blocking_errors, validate_dataset

from .config import AgentConfig
from .workbook import commit_version, load_workbook_dataset


@dataclass
class ApplyOutcome:
    ok: bool
    message: str
    applied: int = 0
    total: int = 0
    archive_path: str = ""
    results: list[dict[str, Any]] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)


def _truthy_env(name: str) -> bool:
    return os.environ.get(name, "").lower() in {"1", "true", "yes", "on"}


# ---------------------------------------------------------------------------
# Insights


def fetch_and_append_insights(
    cfg: AgentConfig,
    date_preset: str = "last_7d",
    breakdowns: list[str] | None = None,
) -> tuple[int, str]:
    """Fetch mock or live insights and append deduped rows to PerformanceSnapshots.

    Returns (row_count_appended, message).
    """
    dataset = load_workbook_dataset(cfg.workbook)
    if cfg.mode == "mock":
        rows = generate_mock_insights(dataset, breakdowns=breakdowns or [])["PerformanceSnapshots"]
    else:
        ok, message, rows = get_live_insights(date_preset, "ad", breakdowns=breakdowns or [], account=cfg.account)
        if not ok:
            return 0, f"live insights failed: {message}"
    rows = _dedupe_snapshots(dataset.tables.get("PerformanceSnapshots", []), rows)
    if not rows:
        return 0, "no new snapshot rows (already captured for this window)"
    archive = commit_version(dataset, cfg.workbook, cfg.archive_dir, [], {"PerformanceSnapshots": rows}, label="insights")
    return len(rows), f"appended {len(rows)} snapshot rows ({archive.name})"


def _dedupe_snapshots(existing: list[dict[str, Any]], new_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def identity(row: dict[str, Any]) -> tuple[str, ...]:
        return (
            clean(row.get("date")),
            clean(row.get("object_level")),
            clean(row.get("meta_object_id")),
            clean(row.get("breakdown_type")),
            clean(row.get("breakdown_value")),
        )

    seen = {identity(row) for row in existing}
    kept = []
    for row in new_rows:
        key = identity(row)
        if key in seen:
            continue
        seen.add(key)
        kept.append(row)
    return kept


# ---------------------------------------------------------------------------
# Plan preview (read-only)


def plan_preview(cfg: AgentConfig) -> dict[str, Any]:
    dataset = load_workbook_dataset(cfg.workbook)
    issues = validate_dataset(dataset)
    result: dict[str, Any] = {
        "validation_issues": [
            f"{issue.severity}: {issue.table}[{issue.row_key}] {issue.field}: {issue.message}" for issue in issues
        ],
        "blocking": has_blocking_errors(issues),
        "actions": [],
    }
    if not result["blocking"]:
        actions = build_plan(dataset)
        result["actions"] = action_dicts(actions)
    return result


# ---------------------------------------------------------------------------
# Apply (creates + approved bulk changes) — the ONLY execution path


def apply_plan(cfg: AgentConfig, label: str) -> ApplyOutcome:
    """Validate, plan, guard, execute, and commit a new workbook version.

    Used by both `launch` and `apply-approved`; build_plan naturally covers
    creates (rows without Meta IDs) and APPROVED unapplied BulkChanges.
    """
    if cfg.mode == "live":
        target_account = resolve_account(cfg.account)
        if _truthy_env("META_REQUIRE_SANDBOX") and not is_sandbox_account(target_account):
            return ApplyOutcome(False, f"refusing live apply: '{target_account or '(unset)'}' is not the sandbox account (META_REQUIRE_SANDBOX is on)")
    dataset = load_workbook_dataset(cfg.workbook)
    issues = validate_dataset(dataset)
    issue_lines = [f"{i.severity}: {i.table}[{i.row_key}] {i.field}: {i.message}" for i in issues]
    if has_blocking_errors(issues):
        return ApplyOutcome(False, "validation errors block the apply", issues=issue_lines)
    actions = build_plan(dataset)
    if not actions:
        return ApplyOutcome(True, "nothing to apply (no pending creates or approved changes)", issues=issue_lines)
    if cfg.mode == "live" and _truthy_env("META_FORCE_PAUSED"):
        violations = active_status_violations(actions)
        if violations:
            details = "; ".join(f"{action_id} ({where})" for action_id, where in violations)
            return ApplyOutcome(False, f"refusing live apply under META_FORCE_PAUSED — actions would set ACTIVE: {details}", issues=issue_lines)
    results, updates, append_rows = execute_actions(
        dataset,
        actions,
        mode=cfg.mode,
        state_path=str(cfg.state_path),
        continue_on_error=True,
        account_override=cfg.account,
    )
    append_rows.setdefault("ValidationErrors", []).extend(validation_rows(issues).get("ValidationErrors", []))
    archive = commit_version(dataset, cfg.workbook, cfg.archive_dir, updates, append_rows, label=label)
    ok_count = sum(1 for result in results if result.ok)
    return ApplyOutcome(
        ok=ok_count == len(results),
        message=f"applied {ok_count}/{len(results)} actions in {cfg.mode} mode",
        applied=ok_count,
        total=len(results),
        archive_path=str(archive),
        results=[
            {"action_id": r.action.action_id, "ok": r.ok, "meta_id": r.meta_id, "message": r.message}
            for r in results
        ],
        issues=issue_lines,
    )


# ---------------------------------------------------------------------------
# Billing preflight (Graph funding_source_details)


def billing_preflight(cfg: AgentConfig) -> tuple[bool | None, str]:
    """Check the ad account has a funding source. Returns (ok, detail); ok=None when unknown/skipped."""
    token = os.environ.get("ACCESS_TOKEN", "")
    account = resolve_account(cfg.account)
    if cfg.mode != "live":
        return None, "mock mode — billing check skipped"
    if not token or not account:
        return None, "ACCESS_TOKEN or account unset — billing check skipped"
    version = os.environ.get("META_API_VERSION", "v21.0")
    url = (
        f"https://graph.facebook.com/{version}/{account}?"
        + urllib.parse.urlencode({"fields": "funding_source_details", "access_token": token})
    )
    try:
        with urllib.request.urlopen(url, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 - preflight should degrade to a warning, not crash
        return None, f"billing check failed: {type(exc).__name__}"
    details = payload.get("funding_source_details")
    if details:
        return True, f"funding source present: {details.get('display_string', 'on file')}"
    return False, (
        "no payment method on the ad account — ad creates will fail with error 100 "
        "(campaigns/ad sets/creatives are unaffected). Add one under Billing & payments, then re-run."
    )
