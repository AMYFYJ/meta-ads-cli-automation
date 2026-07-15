"""Custom MCP tools exposed to the scan agent.

Every guard the approval workflow depends on is enforced HERE, in code — the
system prompt repeats them for steering, but a hallucinated argument cannot
bypass them. No tool can execute a plan, set anything ACTIVE, delete objects,
or write approval_status=APPROVED.
"""

from __future__ import annotations

import json
from typing import Any

from claude_agent_sdk import create_sdk_mcp_server, tool

from meta_ads_pipeline.guards import STATUS_KEYS, find_active_status
from meta_ads_pipeline.optimization import generate_optimization_changes
from meta_ads_pipeline.schema import BULK_OPERATIONS, OBJECT_CONFIG, UPDATABLE_FIELDS, clean

from .analysis import aggregate
from .config import AgentConfig
from .pipeline_ops import plan_preview
from .report import build_appendix, write_report_files
from .workbook import commit_version, load_workbook_dataset, next_agent_change_id, summarize_workbook

SERVER_NAME = "ads"

TOOL_NAMES = [
    "get_workbook_state",
    "get_performance_snapshots",
    "propose_bulk_change",
    "run_rule_engine",
    "get_pending_changes",
    "launch_plan_preview",
    "write_report",
]

# Operations the agent may propose. ACTIVATE/DELETE/DUPLICATE are excluded by
# design: activation is blocked account-wide, deletion is irreversible, and
# duplication creates new spend surfaces — all human-only actions.
AGENT_OPERATIONS = {"SET_FIELD", "PATCH_JSON", "PAUSE", "REPLACE_CREATIVE", "REPLACE_TARGETING"}


def _ok(payload: Any) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": json.dumps(payload, indent=2, default=str)}]}


def _err(message: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": json.dumps({"error": message})}], "is_error": True}


def build_ads_server(cfg: AgentConfig, run_state: dict[str, Any]):
    """Create the in-process MCP server. run_state collects this run's change_ids and report path."""
    return create_sdk_mcp_server(name=SERVER_NAME, version="1.0.0", tools=build_tools(cfg, run_state))


def build_tools(cfg: AgentConfig, run_state: dict[str, Any]) -> list[Any]:
    """The @tool-decorated handlers, exposed as a list so tests can invoke them directly."""
    run_state.setdefault("change_ids", [])
    run_state.setdefault("report_paths", [])

    @tool(
        "get_workbook_state",
        "Read the current workbook: campaigns, ad sets (incl. audience targeting: geo, age, genders, interests, custom audiences), ads (incl. inline creative copy/asset fields), audiences, and BulkChanges grouped by approval status.",
        {
            "type": "object",
            "properties": {
                "tables": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional subset of tables (Campaigns, AdSets, Ads, Audiences, BulkChanges). Default: all.",
                }
            },
        },
    )
    async def get_workbook_state(args: dict[str, Any]) -> dict[str, Any]:
        dataset = load_workbook_dataset(cfg.workbook)
        return _ok(summarize_workbook(dataset, args.get("tables")))

    @tool(
        "get_performance_snapshots",
        "Aggregated performance from PerformanceSnapshots over a trailing window: totals plus per-campaign and per-ad-set spend, CTR, CPC, CPM, conversions, cost per result, ROAS (joined to workbook names).",
        {
            "type": "object",
            "properties": {
                "days": {"type": "integer", "description": "Trailing window in days (default 7)."},
            },
        },
    )
    async def get_performance_snapshots(args: dict[str, Any]) -> dict[str, Any]:
        dataset = load_workbook_dataset(cfg.workbook)
        days = int(args.get("days") or 7)
        return _ok(aggregate(dataset, days=days))

    @tool(
        "propose_bulk_change",
        "Append ONE optimization proposal to BulkChanges with approval_status=PENDING (a human must approve it in Excel before anything is applied). Rejected: ACTIVATE, DELETE, any status set to ACTIVE, unknown objects, non-updatable fields, oversized budget swings.",
        {
            "type": "object",
            "properties": {
                "object_level": {"type": "string", "enum": ["campaign", "adset", "creative", "ad"]},
                "object_key_or_meta_id": {"type": "string", "description": "Workbook key (e.g. adset_x) or Meta ID of the target object."},
                "operation": {"type": "string", "enum": sorted(AGENT_OPERATIONS)},
                "field": {"type": "string", "description": "Field to change (required for SET_FIELD; see UPDATABLE_FIELDS)."},
                "new_value": {"type": "string"},
                "value_json": {"type": "string", "description": "JSON payload for PATCH_JSON / REPLACE_* operations."},
                "expected_old_value": {"type": "string", "description": "Current value, recorded for rollback."},
                "reason": {"type": "string", "description": "Required. Quantitative justification citing the metrics that drove this."},
            },
            "required": ["object_level", "object_key_or_meta_id", "operation", "reason"],
        },
    )
    async def propose_bulk_change(args: dict[str, Any]) -> dict[str, Any]:
        if len(run_state["change_ids"]) >= cfg.max_proposals:
            return _err(f"proposal cap reached ({cfg.max_proposals} per run). Prioritize in the report instead.")
        error = _validate_proposal(cfg, args)
        if error:
            return _err(error)
        dataset = load_workbook_dataset(cfg.workbook)
        unknown = _unknown_object(dataset, args["object_level"], args["object_key_or_meta_id"])
        if unknown:
            return _err(unknown)
        budget_error = _budget_bound(cfg, dataset, args)
        if budget_error:
            return _err(budget_error)
        row = {
            "change_id": next_agent_change_id(dataset.tables.get("BulkChanges", [])),
            "operation": clean(args["operation"]).upper(),
            "object_level": clean(args["object_level"]).lower(),
            "object_key_or_meta_id": clean(args["object_key_or_meta_id"]),
            "field": clean(args.get("field")),
            "old_value": clean(args.get("expected_old_value")),
            "new_value": clean(args.get("new_value")),
            "value_json": clean(args.get("value_json")),
            "effective_at": "",
            "reason": clean(args["reason"]),
            "requested_by": "ads-agent",
            "approval_status": "PENDING",  # forced — never taken from the model
            "applied_at": "",
            "result": "",
            "error": "",
        }
        commit_version(dataset, cfg.workbook, cfg.archive_dir, [], {"BulkChanges": [row]}, label="proposal")
        run_state["change_ids"].append(row["change_id"])
        return _ok({"written": row})

    @tool(
        "run_rule_engine",
        "Run the deterministic OptimizationRules engine and append its suggestions to BulkChanges as PENDING proposals. Use to cross-check your own judgment; returns the rows written.",
        {"type": "object", "properties": {}},
    )
    async def run_rule_engine(args: dict[str, Any]) -> dict[str, Any]:
        dataset = load_workbook_dataset(cfg.workbook)
        changes = generate_optimization_changes(dataset)
        safe, skipped = [], []
        seen = {
            _change_identity(row)
            for row in dataset.tables.get("BulkChanges", [])
            if not clean(row.get("applied_at"))
        }
        for change in changes:
            # The upstream engine emits APPROVED (optimization.py); downgrade so the human gate holds.
            change["approval_status"] = "PENDING"
            change["requested_by"] = "ads-agent(rules)"
            if _would_activate(change):
                skipped.append({"change_id": change["change_id"], "why": "would set a status to ACTIVE"})
                continue
            identity = _change_identity(change)
            if identity in seen:
                skipped.append({"change_id": change["change_id"], "why": "duplicate of an existing unapplied change"})
                continue
            seen.add(identity)
            safe.append(change)
        if safe:
            commit_version(dataset, cfg.workbook, cfg.archive_dir, [], {"BulkChanges": safe}, label="rules")
            run_state["change_ids"].extend(change["change_id"] for change in safe)
        return _ok({"written": safe, "skipped": skipped})

    @tool(
        "get_pending_changes",
        "Unapplied BulkChanges rows grouped by approval status (PENDING vs APPROVED vs other). Check this before proposing to avoid duplicates.",
        {"type": "object", "properties": {}},
    )
    async def get_pending_changes(args: dict[str, Any]) -> dict[str, Any]:
        dataset = load_workbook_dataset(cfg.workbook)
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in dataset.tables.get("BulkChanges", []):
            if clean(row.get("applied_at")):
                continue
            status = (clean(row.get("approval_status")) or "UNSET").upper()
            grouped.setdefault(status, []).append(
                {
                    key: clean(row.get(key))
                    for key in ("change_id", "operation", "object_level", "object_key_or_meta_id", "field", "new_value", "reason", "requested_by")
                    if clean(row.get(key))
                }
            )
        return _ok(grouped)

    @tool(
        "launch_plan_preview",
        "Read-only dry run of the pipeline plan: validation issues plus the actions that WOULD execute on the next apply (pending creates + already-APPROVED changes). Never executes anything.",
        {"type": "object", "properties": {}},
    )
    async def launch_plan_preview(args: dict[str, Any]) -> dict[str, Any]:
        return _ok(plan_preview(cfg))

    @tool(
        "write_report",
        "Write the scan report for the human (markdown). A deterministic machine appendix with the exact rows written this run and raw aggregates is added automatically — do not fabricate your own appendix.",
        {
            "type": "object",
            "properties": {"markdown": {"type": "string", "description": "The full report body in markdown."}},
            "required": ["markdown"],
        },
    )
    async def write_report(args: dict[str, Any]) -> dict[str, Any]:
        dataset = load_workbook_dataset(cfg.workbook)
        markdown = str(args.get("markdown") or "").strip()
        if not markdown:
            return _err("markdown must be non-empty")
        markdown += build_appendix(dataset, run_state["change_ids"])
        md_path, html_path = write_report_files(cfg, markdown)
        run_state["report_paths"] = [str(md_path), str(html_path)]
        return _ok({"markdown": str(md_path), "html": str(html_path)})

    return [
        get_workbook_state,
        get_performance_snapshots,
        propose_bulk_change,
        run_rule_engine,
        get_pending_changes,
        launch_plan_preview,
        write_report,
    ]


def allowed_tool_ids() -> list[str]:
    return [f"mcp__{SERVER_NAME}__{name}" for name in TOOL_NAMES]


# ---------------------------------------------------------------------------
# Guard helpers


def _validate_proposal(cfg: AgentConfig, args: dict[str, Any]) -> str:
    operation = clean(args.get("operation")).upper()
    level = clean(args.get("object_level")).lower()
    field = clean(args.get("field"))
    if operation not in BULK_OPERATIONS:
        return f"unknown operation '{operation}'"
    if operation not in AGENT_OPERATIONS:
        return f"operation '{operation}' is human-only; the agent may use {sorted(AGENT_OPERATIONS)}"
    if level not in UPDATABLE_FIELDS:
        return f"unknown object_level '{level}'"
    if not clean(args.get("reason")):
        return "reason is required and must cite the metrics behind the proposal"
    if operation == "SET_FIELD":
        if not field:
            return "SET_FIELD requires a field"
        if field not in UPDATABLE_FIELDS[level]:
            return f"'{field}' is not updatable on {level}; allowed: {sorted(UPDATABLE_FIELDS[level])}"
    if _would_activate({"field": field, "new_value": args.get("new_value"), "value_json": args.get("value_json")}):
        return "proposals may never set a status to ACTIVE (account is force-paused; activation is a human decision)"
    return ""


def _change_identity(change: dict[str, Any]) -> tuple[str, ...]:
    return tuple(
        clean(change.get(key)).lower() if key in {"operation", "object_level"} else clean(change.get(key))
        for key in ("operation", "object_level", "object_key_or_meta_id", "field", "new_value", "value_json")
    )


def _would_activate(change: dict[str, Any]) -> bool:
    field = clean(change.get("field")).lower()
    if field in STATUS_KEYS and clean(change.get("new_value")).upper() == "ACTIVE":
        return True
    value_json = clean(change.get("value_json"))
    if value_json and find_active_status(value_json):
        return True
    return clean(change.get("new_value")).upper() == "ACTIVE" and "status" in field


def _unknown_object(dataset, level: str, identifier: str) -> str:
    table, key_col, meta_col = OBJECT_CONFIG[level]
    identifier = clean(identifier)
    for row in dataset.tables.get(table, []):
        if identifier in (clean(row.get(key_col)), clean(row.get(meta_col))):
            return ""
    return f"no {level} with key or Meta ID '{identifier}' in the workbook ({table} sheet)"


def _budget_bound(cfg: AgentConfig, dataset, args: dict[str, Any]) -> str:
    field = clean(args.get("field"))
    if not field.endswith("_budget_cents"):
        return ""
    try:
        new_value = float(clean(args.get("new_value")))
    except ValueError:
        return f"new_value for {field} must be numeric cents"
    table, key_col, meta_col = OBJECT_CONFIG[clean(args.get("object_level")).lower()]
    identifier = clean(args.get("object_key_or_meta_id"))
    for row in dataset.tables.get(table, []):
        if identifier in (clean(row.get(key_col)), clean(row.get(meta_col))):
            try:
                current = float(clean(row.get(field)))
            except ValueError:
                return ""
            if current > 0:
                pct = abs(new_value - current) / current * 100
                if pct > cfg.max_budget_change_pct:
                    return (
                        f"budget change of {pct:.0f}% exceeds the {cfg.max_budget_change_pct:.0f}% per-proposal bound "
                        f"(current {current:.0f} -> proposed {new_value:.0f}). Propose a smaller step and explain the rest in the report."
                    )
            return ""
    return ""
