from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ads_agent.config import AgentConfig
from ads_agent.tools import build_tools
from meta_ads_pipeline.insights import generate_mock_insights
from meta_ads_pipeline.storage import create_template, load_source, save_with_updates


@pytest.fixture()
def cfg(tmp_path: Path) -> AgentConfig:
    workbook = tmp_path / "current.xlsx"
    template = tmp_path / "template.xlsx"
    create_template(str(template), with_sample=True)
    dataset = load_source(str(template))
    # give the sample tree meta ids so insights/joins work
    updates = []
    for table, key_col, meta_col, prefix in (
        ("Campaigns", "campaign_key", "meta_campaign_id", "120"),
        ("AdSets", "adset_key", "meta_adset_id", "121"),
        ("Ads", "ad_key", "meta_ad_id", "122"),
    ):
        for idx, row in enumerate(dataset.tables.get(table, []), start=1):
            updates.append((table, row[key_col], meta_col, f"{prefix}00{idx}"))
    if dataset.tables.get("AdSets"):
        updates.append(("AdSets", dataset.tables["AdSets"][0]["adset_key"], "daily_budget_cents", "1000"))
    save_with_updates(dataset, str(workbook), updates, generate_mock_insights_with_ids(dataset))
    return AgentConfig(
        project_root=tmp_path,
        workbook=workbook,
        archive_dir=tmp_path / "archive",
        report_dir=tmp_path / "reports",
        mode="mock",
        state_path=tmp_path / "state.json",
    )


def generate_mock_insights_with_ids(dataset):
    for idx, ad in enumerate(dataset.tables.get("Ads", []), start=1):
        ad["meta_ad_id"] = f"12200{idx}"
    return generate_mock_insights(dataset)


def _tools(cfg: AgentConfig, run_state: dict | None = None):
    state = run_state if run_state is not None else {}
    handlers = {t.name: t.handler for t in build_tools(cfg, state)}
    return handlers, state


def _call(handlers, name: str, args: dict) -> dict:
    result = asyncio.run(handlers[name](args))
    payload = json.loads(result["content"][0]["text"])
    payload["_is_error"] = bool(result.get("is_error"))
    return payload


def _first_adset_key(cfg: AgentConfig) -> str:
    dataset = load_source(str(cfg.workbook))
    return dataset.tables["AdSets"][0]["adset_key"]


def test_propose_forces_pending(cfg: AgentConfig) -> None:
    handlers, state = _tools(cfg)
    adset = _first_adset_key(cfg)
    payload = _call(handlers, "propose_bulk_change", {
        "object_level": "adset", "object_key_or_meta_id": adset,
        "operation": "SET_FIELD", "field": "bid_amount_cents", "new_value": "450",
        "expected_old_value": "500", "reason": "cost_per_result $40 vs median $18 over 7d",
        "approval_status": "APPROVED",  # hostile arg — must be ignored
    })
    assert not payload["_is_error"]
    assert payload["written"]["approval_status"] == "PENDING"
    assert payload["written"]["requested_by"] == "ads-agent"
    assert payload["written"]["change_id"].startswith("chg_agent_")
    reloaded = load_source(str(cfg.workbook))
    row = next(r for r in reloaded.tables["BulkChanges"] if r["change_id"] == payload["written"]["change_id"])
    assert row["approval_status"] == "PENDING"
    assert state["change_ids"] == [payload["written"]["change_id"]]


@pytest.mark.parametrize("args,fragment", [
    ({"operation": "ACTIVATE"}, "human-only"),
    ({"operation": "DELETE"}, "human-only"),
    ({"operation": "SET_FIELD", "field": "desired_status", "new_value": "ACTIVE"}, "ACTIVE"),
    ({"operation": "PATCH_JSON", "value_json": json.dumps({"status": "ACTIVE"})}, "ACTIVE"),
    ({"operation": "SET_FIELD", "field": "not_a_field", "new_value": "1"}, "not updatable"),
    ({"operation": "SET_FIELD", "field": "bid_amount_cents", "new_value": "1", "reason": ""}, "reason"),
])
def test_propose_rejections(cfg: AgentConfig, args: dict, fragment: str) -> None:
    handlers, _ = _tools(cfg)
    base = {
        "object_level": "adset", "object_key_or_meta_id": _first_adset_key(cfg),
        "reason": "spend $300, conversions 0 over 7d",
    }
    base.update(args)
    payload = _call(handlers, "propose_bulk_change", base)
    assert payload["_is_error"], payload
    assert fragment.lower() in payload["error"].lower()


def test_propose_rejects_unknown_object(cfg: AgentConfig) -> None:
    handlers, _ = _tools(cfg)
    payload = _call(handlers, "propose_bulk_change", {
        "object_level": "adset", "object_key_or_meta_id": "adset_ghost",
        "operation": "PAUSE", "reason": "spend $220, 0 conversions over 7d",
    })
    assert payload["_is_error"] and "no adset" in payload["error"]


def test_propose_budget_bound(cfg: AgentConfig) -> None:
    handlers, _ = _tools(cfg)
    dataset = load_source(str(cfg.workbook))
    adset = next((r for r in dataset.tables["AdSets"] if str(r.get("daily_budget_cents") or "").strip()), None)
    if adset is None:
        pytest.skip("sample workbook has no ad set daily budget")
    current = float(adset["daily_budget_cents"])
    payload = _call(handlers, "propose_bulk_change", {
        "object_level": "adset", "object_key_or_meta_id": adset["adset_key"],
        "operation": "SET_FIELD", "field": "daily_budget_cents",
        "new_value": str(int(current * 3)), "reason": "reallocate: sibling cost_per_result 2x better",
    })
    assert payload["_is_error"] and "bound" in payload["error"]


def test_proposal_cap(cfg: AgentConfig) -> None:
    cfg.max_proposals = 1
    handlers, state = _tools(cfg)
    adset = _first_adset_key(cfg)
    ok_args = {
        "object_level": "adset", "object_key_or_meta_id": adset,
        "operation": "PAUSE", "reason": "spend $150, 0 conversions over 7d",
    }
    first = _call(handlers, "propose_bulk_change", ok_args)
    assert not first["_is_error"]
    second = _call(handlers, "propose_bulk_change", ok_args)
    assert second["_is_error"] and "cap" in second["error"]


def test_rule_engine_downgrades_to_pending(cfg: AgentConfig) -> None:
    from ads_agent.workbook import commit_version

    dataset = load_source(str(cfg.workbook))
    commit_version(dataset, cfg.workbook, cfg.archive_dir, [], {"OptimizationRules": [{
        "rule_key": "rule_pause_low", "scope_level": "ad", "metric": "spend", "operator": ">",
        "threshold": "0", "action_object_level": "adset", "action_field": "bid_amount_cents",
        "action_value": "400", "max_actions": "1", "approval_status": "APPROVED",
    }, {
        "rule_key": "rule_activate", "scope_level": "ad", "metric": "spend", "operator": ">",
        "threshold": "0", "action_object_level": "adset", "action_field": "desired_status",
        "action_value": "ACTIVE", "max_actions": "1", "approval_status": "APPROVED",
    }]})
    handlers, state = _tools(cfg)
    payload = _call(handlers, "run_rule_engine", {})
    assert not payload["_is_error"]
    assert payload["written"], "expected the bid rule to fire"
    assert all(row["approval_status"] == "PENDING" for row in payload["written"])
    assert all(row["requested_by"] == "ads-agent(rules)" for row in payload["written"])
    assert payload["skipped"], "expected the ACTIVATE rule to be skipped"
    reloaded = load_source(str(cfg.workbook))
    engine_rows = [r for r in reloaded.tables["BulkChanges"] if str(r.get("requested_by")) == "ads-agent(rules)"]
    assert engine_rows and all(r["approval_status"] == "PENDING" for r in engine_rows)
    # re-running the engine must not duplicate unapplied proposals
    rerun = _call(handlers, "run_rule_engine", {})
    assert rerun["written"] == []
    assert any("duplicate" in item["why"] for item in rerun["skipped"])


def test_launch_plan_preview_executes_nothing(cfg: AgentConfig) -> None:
    before = load_source(str(cfg.workbook))
    publish_before = len(before.tables.get("PublishLog", []))
    handlers, _ = _tools(cfg)
    payload = _call(handlers, "launch_plan_preview", {})
    assert not payload["_is_error"]
    assert isinstance(payload["actions"], list)
    after = load_source(str(cfg.workbook))
    assert len(after.tables.get("PublishLog", [])) == publish_before


def test_get_tools_read_only(cfg: AgentConfig) -> None:
    handlers, _ = _tools(cfg)
    state_payload = _call(handlers, "get_workbook_state", {})
    assert "table_counts" in state_payload
    perf = _call(handlers, "get_performance_snapshots", {"days": 7})
    assert perf["by_adset"], "expected aggregates from mock snapshots"
    pending = _call(handlers, "get_pending_changes", {})
    assert not pending["_is_error"]


def test_write_report_appends_machine_appendix(cfg: AgentConfig) -> None:
    handlers, state = _tools(cfg)
    payload = _call(handlers, "write_report", {"markdown": "# Meta Ads scan — test\n\nAll quiet."})
    assert not payload["_is_error"]
    content = Path(payload["markdown"]).read_text(encoding="utf-8")
    assert "Machine appendix" in content
    assert Path(payload["html"]).exists()
    assert state["report_paths"]
