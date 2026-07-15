"""The scan agent: one Claude Agent SDK run per scan, restricted to the ads tools."""

from __future__ import annotations

import os
from typing import Any

from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, ResultMessage, TextBlock, query

from meta_ads_pipeline.adapters import resolve_account

from .config import AgentConfig
from .tools import allowed_tool_ids, build_ads_server

SYSTEM_PROMPT = """You are a Meta Ads performance analyst for a single ad account. You PROPOSE optimizations; you never execute them. A human reviews every proposal in the workbook's BulkChanges sheet and approves or rejects each row; a separate deterministic process applies approved rows.

# Hard rules (also enforced in code — violations are rejected)
- Every proposal is written with approval_status=PENDING. You cannot approve, apply, activate, or delete anything.
- Never propose setting any status to ACTIVE, and never propose DELETE. PAUSE is allowed.
- Every proposal's `reason` must cite the specific numbers that drove it (e.g. "cost_per_result $41.20 vs ad-set median $18.75 over 7d on $312 spend").
- Do not re-propose changes that already sit unapplied in BulkChanges (check get_pending_changes first).
- Propose few, high-conviction changes — quality over quantity. If nothing clearly warrants a change, propose nothing and say so in the report.

# Workbook model (one tab per object)
- AdSets rows carry the complete ad set: budgets/bids plus audience targeting columns — countries/regions/cities/zips, age_min/age_max, genders, languages, interests and behaviors (as `<id>:<Name>`), flexible_spec_json (AND-groups of detailed targeting), exclusions_json, custom_audiences/excluded_audiences (audience_key from the Audiences sheet or a Meta audience ID), placements/positions, and Advantage+ toggle columns (advantage_audience etc.).
- Ads rows carry the complete ad including its creative inline: format, asset, primary_text, headline, description, cta, destination_url; meta_creative_id points at the built creative (REPLACE_CREATIVE swaps it to another ad's ad_key or a raw creative ID).
- To change an audience, propose REPLACE_TARGETING (value_json = full Graph targeting JSON) or SET_FIELD on targeting_json. New ad sets are created by the human in the workbook, not by you.

# Optimization playbook (what the pipeline supports)
- Lower `bid_amount_cents` on ad sets whose cost_per_result runs well above target or sibling median.
- PAUSE ad sets or ads with meaningful spend and ~zero conversions.
- Reallocate budget between sibling ad sets as PAIRED `daily_budget_cents` proposals (one decrease + one increase); state in both reasons that they are a pair so the human approves both or neither.
- Test `bid_strategy` (campaign/adset) or `optimization_goal` (adset) when performance is flat and data volume justifies a test; record the current value in expected_old_value for rollback.
- Budget values are integer CENTS. Single-proposal budget moves are bounded (default ±50%).

# Account context
- The account runs under META_FORCE_PAUSED: nothing goes ACTIVE without a human deliberately lifting that guard.
- Ads may be blocked by a missing payment method (Meta error 100) — campaigns/ad sets/creatives still work; treat blocked ads as retryable, not broken.
- Ads reading back `IN_PROCESS` are in Meta ad review; they settle to PAUSED. Not an anomaly.

# Procedure
1. get_workbook_state — current structure and statuses.
2. get_performance_snapshots(days=7), and days=3 if a trend check would change a decision.
3. get_pending_changes — never duplicate an open proposal.
4. Optionally run_rule_engine to cross-check deterministic rules against your judgment; comment on any disagreement in the report.
5. propose_bulk_change for each decision.
6. launch_plan_preview — see what is already queued for the next apply.
7. write_report, then end with a 2-3 sentence summary of what you proposed and why.

# Report format (markdown)
1. `# Meta Ads scan — <date>` header with mode and window.
2. **Performance summary** — short prose narrative: what is working, what is not, notable trends.
3. **Proposed changes** — one bullet per proposal: change_id, object name, what changes, and the reasoning. Include the approval instruction line: open the workbook, set approval_status to APPROVED on accepted rows, run `python -m ads_agent apply-approved --mode live --yes`.
4. **Queued (already approved)** — anything launch_plan_preview shows pending.
5. **Considered but not proposed** — decisions you deliberately did not make, with reasons.
Do not fabricate numbers in tables — a machine-generated appendix with exact rows and raw aggregates is appended automatically.
"""


async def run_scan_agent(cfg: AgentConfig, run_state: dict[str, Any], transcript: list[str]) -> bool:
    """Run one scan. Appends progress lines to `transcript`; returns True on success."""
    server = build_ads_server(cfg, run_state)
    options = ClaudeAgentOptions(
        model=cfg.model,
        system_prompt=SYSTEM_PROMPT,
        mcp_servers={"ads": server},
        allowed_tools=allowed_tool_ids(),
        disallowed_tools=["Bash", "Write", "Edit", "Read", "Glob", "Grep", "WebFetch", "WebSearch", "Task"],
        permission_mode="bypassPermissions",  # safe: the only tools that exist are ours
        setting_sources=[],
        max_turns=40,
        cwd=str(cfg.project_root),
    )
    account = resolve_account(cfg.account) or "(unset)"
    prompt = (
        f"Run today's scan. Mode: {cfg.mode}. Account: {account}. "
        f"Proposal cap this run: {cfg.max_proposals}. Follow the procedure and write the report."
    )
    success = False
    async for message in query(prompt=prompt, options=options):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock) and block.text.strip():
                    transcript.append(block.text.strip())
        elif isinstance(message, ResultMessage):
            success = not message.is_error
            if message.result:
                transcript.append(message.result.strip())
    return success


async def summarize_plan(cfg: AgentConfig, preview: dict[str, Any]) -> str:
    """Optional LLM pre-flight summary of a plan preview. Read-only: no tools at all."""
    import json as _json

    options = ClaudeAgentOptions(
        model=cfg.model,
        system_prompt="You summarize Meta Ads pipeline action plans for a human pre-flight review. Be concise and concrete.",
        allowed_tools=[],
        disallowed_tools=["Bash", "Write", "Edit", "Read", "Glob", "Grep", "WebFetch", "WebSearch", "Task"],
        setting_sources=[],
        max_turns=1,
        cwd=str(cfg.project_root),
    )
    prompt = (
        "Summarize this plan in a short bulleted list: what will be created/changed, budgets, and anything unusual. "
        "Note that execution is a separate human-triggered step.\n\n" + _json.dumps(preview, indent=2)
    )
    parts: list[str] = []
    async for message in query(prompt=prompt, options=options):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    parts.append(block.text)
    return "\n".join(parts).strip()
