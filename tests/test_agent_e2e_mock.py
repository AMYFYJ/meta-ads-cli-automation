"""End-to-end mock loop, no API key required.

Covers: build workbook -> launch (mock) -> insights -> scripted "agent" pass
(tools driven in the same order the real agent uses) -> human approval ->
apply-approved (mock) -> idempotent re-apply. An opt-in real-LLM smoke test
runs the full scan when RUN_AGENT_LLM_TESTS=1.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ads_agent.config import AgentConfig
from ads_agent.pipeline_ops import apply_plan, fetch_and_append_insights, plan_preview
from ads_agent.tools import build_tools
from ads_agent.workbook import commit_version
from meta_ads_pipeline.storage import create_template, load_source
from meta_ads_pipeline.assets import ensure_sample_assets


@pytest.fixture()
def cfg(tmp_path: Path) -> AgentConfig:
    ensure_sample_assets(str(tmp_path))
    workbook = tmp_path / "current.xlsx"
    create_template(str(workbook), with_sample=True)
    return AgentConfig(
        project_root=tmp_path,
        workbook=workbook,
        archive_dir=tmp_path / "archive",
        report_dir=tmp_path / "reports",
        mode="mock",
        state_path=tmp_path / "state.json",
    )


def _call(handlers, name: str, args: dict) -> dict:
    result = asyncio.run(handlers[name](args))
    payload = json.loads(result["content"][0]["text"])
    payload["_is_error"] = bool(result.get("is_error"))
    return payload


def test_full_mock_loop(cfg: AgentConfig) -> None:
    # 1. Launch: create the campaign tree in mock mode
    outcome = apply_plan(cfg, label="launch")
    assert outcome.ok, outcome.message
    assert outcome.applied == outcome.total > 0
    created = load_source(str(cfg.workbook))
    assert all(str(r.get("meta_campaign_id") or "").strip() for r in created.tables["Campaigns"])

    # 2. Idempotence: re-apply plans nothing new
    again = apply_plan(cfg, label="launch")
    assert again.total == 0 and "nothing to apply" in again.message

    # 3. Insights: mock snapshots appended, deduped on re-run
    count, message = fetch_and_append_insights(cfg)
    assert count > 0, message
    count2, message2 = fetch_and_append_insights(cfg)
    assert count2 == 0, message2

    # 4. Scripted agent pass: read -> decide -> propose -> report
    run_state: dict = {}
    handlers = {t.name: t.handler for t in build_tools(cfg, run_state)}
    state = _call(handlers, "get_workbook_state", {})
    assert state["table_counts"]["PerformanceSnapshots"] > 0
    perf = _call(handlers, "get_performance_snapshots", {"days": 7})
    worst = max(perf["by_adset"], key=lambda entry: entry["cost_per_result"])
    proposal = _call(handlers, "propose_bulk_change", {
        "object_level": "adset",
        "object_key_or_meta_id": worst["adset_key"],
        "operation": "SET_FIELD",
        "field": "bid_amount_cents",
        "new_value": "450",
        "expected_old_value": worst["bid_amount_cents"] or "",
        "reason": f"cost_per_result ${worst['cost_per_result']} on ${worst['spend']} spend over 7d",
    })
    assert not proposal["_is_error"]
    change_id = proposal["written"]["change_id"]
    report = _call(handlers, "write_report", {"markdown": "# Meta Ads scan — e2e\n\nOne bid-cap proposal."})
    assert Path(report["markdown"]).exists()

    # 5. Approval gate: PENDING rows are inert
    preview = plan_preview(cfg)
    assert preview["actions"] == [], "PENDING proposals must not plan any actions"

    # 6. Human approves in the workbook
    dataset = load_source(str(cfg.workbook))
    for row in dataset.tables["BulkChanges"]:
        if row["change_id"] == change_id:
            row_key = row["change_id"]
    commit_version(dataset, cfg.workbook, cfg.archive_dir, [("BulkChanges", row_key, "approval_status", "APPROVED")], {})

    # 7. Apply approved; change lands and is marked applied
    result = apply_plan(cfg, label="apply")
    assert result.ok and result.applied == 1, result.message
    final = load_source(str(cfg.workbook))
    applied_row = next(r for r in final.tables["BulkChanges"] if r["change_id"] == change_id)
    assert str(applied_row.get("applied_at") or "").strip()

    # 8. Idempotence again: applied rows never re-plan
    rerun = apply_plan(cfg, label="apply")
    assert rerun.total == 0


def test_force_paused_blocks_human_approved_activate(cfg: AgentConfig, monkeypatch) -> None:
    outcome = apply_plan(cfg, label="launch")
    assert outcome.ok
    dataset = load_source(str(cfg.workbook))
    campaign_key = dataset.tables["Campaigns"][0]["campaign_key"]
    commit_version(dataset, cfg.workbook, cfg.archive_dir, [], {"BulkChanges": [{
        "change_id": "chg_manual_activate", "operation": "SET_FIELD", "object_level": "campaign",
        "object_key_or_meta_id": campaign_key, "field": "desired_status", "new_value": "ACTIVE",
        "reason": "human wants it live", "requested_by": "amy", "approval_status": "APPROVED",
        "applied_at": "", "result": "", "error": "",
    }]})
    monkeypatch.setenv("META_FORCE_PAUSED", "1")
    monkeypatch.setenv("ACCESS_TOKEN", "test-token")
    monkeypatch.setenv("AD_ACCOUNT_ID", "act_1")
    monkeypatch.delenv("META_REQUIRE_SANDBOX", raising=False)
    monkeypatch.delenv("META_SANDBOX", raising=False)
    cfg.mode = "live"
    result = apply_plan(cfg, label="apply")
    assert not result.ok
    assert "FORCE_PAUSED" in result.message.upper() or "ACTIVE" in result.message


@pytest.mark.skipif(os.environ.get("RUN_AGENT_LLM_TESTS") != "1", reason="set RUN_AGENT_LLM_TESTS=1 to run the real-LLM smoke test")
def test_real_llm_scan_smoke(cfg: AgentConfig) -> None:
    from ads_agent.agent import run_scan_agent

    apply_plan(cfg, label="launch")
    fetch_and_append_insights(cfg)
    run_state: dict = {}
    transcript: list[str] = []
    success = asyncio.run(run_scan_agent(cfg, run_state, transcript))
    assert success, "\n".join(transcript)
    assert run_state.get("report_paths"), "agent should have written a report"
    dataset = load_source(str(cfg.workbook))
    agent_rows = [r for r in dataset.tables["BulkChanges"] if str(r.get("requested_by", "")).startswith("ads-agent")]
    assert all(r["approval_status"] == "PENDING" for r in agent_rows)
