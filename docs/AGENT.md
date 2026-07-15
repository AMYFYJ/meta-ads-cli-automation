# ads_agent — LLM Optimization Agent Runbook

An agent layer over `meta_ads_pipeline`: a single Claude agent scans performance
daily, **proposes** optimizations, and reports to you. Nothing touches Meta
without your approval — the agent writes `BulkChanges` rows as `PENDING`; a
deterministic (no-LLM) command applies only rows you flip to `APPROVED`.

```
launchd (daily 08:00) ── python -m ads_agent scan --mode live
    1. fetch insights → append PerformanceSnapshots (new workbook version)
    2. Claude agent (custom tools only): analyze → propose PENDING changes → write report
    3. report in outputs/reports/YYYY-MM-DD_scan.md (+ .html)

you: read the report, open data/current.xlsx, set approval_status=APPROVED
     on the rows you accept, save, close Excel

python -m ads_agent apply-approved --mode live --yes    # deterministic apply
```

## Safety model

- **The LLM proposes; code executes.** The agent's only write abilities are:
  append `PENDING` BulkChanges rows, append snapshots, write report files.
- Tool-level hard guards (code, not prompt): `approval_status=PENDING` is forced;
  `ACTIVATE`/`DELETE` operations and any status set to `ACTIVE` are rejected;
  unknown objects and >±50% single-step budget swings are rejected.
- Applies run through the existing pipeline guards: `approved()` filtering,
  `META_FORCE_PAUSED` (blocks any ACTIVE), `META_REQUIRE_SANDBOX` if set.
- Every workbook write is archived in `data/archive/` before promoting to
  `data/current.xlsx`; scans abort if Excel has the file open (`~$` lock file).

## Commands

| Command | What it does | LLM? |
|---|---|---|
| `python -m ads_agent scan [--mode mock\|live] [--skip-insights] [--max-proposals N]` | Insights + agent analysis + PENDING proposals + report | yes |
| `python -m ads_agent apply-approved --mode live --yes` | Apply APPROVED unapplied changes (and pending creates) | no |
| `python -m ads_agent launch --yes [--summarize]` | Create campaigns/ad sets/ads from the workbook, including per-ad-set audience targeting columns (see [TARGETING.md](TARGETING.md)) (billing preflight included) | only `--summarize` |
| `python -m ads_agent report` | Print latest report | no |
| `python -m ads_agent doctor [--live]` | Pipeline doctor + agent checks (API key, SDK, workbook, lock, billing) | no |

Run everything from the repo root with the venv:
`cd "/Users/amyfang/Documents/Meta CLI" && .venv/bin/python -m ads_agent ...`

## Setup

1. `.env` (already present) plus these keys:

   ```bash
   ANTHROPIC_API_KEY=sk-ant-...
   ADS_AGENT_WORKBOOK=data/current.xlsx
   ADS_AGENT_MODE=live
   ADS_AGENT_REPORT_DIR=outputs/reports
   ADS_AGENT_MODEL=claude-opus-4-8
   # keep the existing safety settings:
   META_FORCE_PAUSED=1
   ```

2. Put your campaign workbook at `data/current.xlsx`. To start from scratch:

   ```bash
   .venv/bin/python examples/build_ad_campaign.py \
     --ad-account-id "$AD_ACCOUNT_ID" --page-id <PAGE_ID> \
     --name "My Campaign" --image assets/hero.png --out data/current.xlsx
   ```

3. Verify: `.venv/bin/python -m ads_agent doctor --live`

## Daily schedule (launchd)

```bash
mkdir -p ~/Library/LaunchAgents "/Users/amyfang/Documents/Meta CLI/outputs/logs"
cp "/Users/amyfang/Documents/Meta CLI/launchd/com.amyfang.ads-agent.scan.plist" ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.amyfang.ads-agent.scan.plist

# run once now to test:
launchctl kickstart gui/$(id -u)/com.amyfang.ads-agent.scan

# uninstall:
launchctl bootout gui/$(id -u)/com.amyfang.ads-agent.scan
```

Logs: `outputs/logs/agent-scan.{out,err}.log`. The plist sets an explicit PATH
(launchd's default is minimal and would hide `node`, which the Claude Agent SDK
runtime needs). Secrets stay in `.env` — never in the plist.

## The approval workflow in detail

1. After a scan, read `outputs/reports/<date>_scan.md` (or the `.html` sibling).
   Proposals appear under **Proposed changes** with the agent's reasoning; the
   **machine appendix** at the bottom lists the exact rows written and the raw
   aggregates they were based on.
2. Open `data/current.xlsx` → `BulkChanges` sheet. Agent rows have
   `requested_by = ads-agent` (or `ads-agent(rules)`) and
   `approval_status = PENDING`.
3. Set `approval_status` to `APPROVED` on rows you accept. Leave the rest as
   `PENDING` (the agent won't re-propose them) or set `REJECTED`.
4. Save and **close** the workbook, then:
   `.venv/bin/python -m ads_agent apply-approved --mode live --yes`
5. Applied rows get `applied_at` + `result`; `PublishLog` records every API call.
   Already-applied rows are never re-planned.

Paired budget reallocations (one decrease + one increase) are flagged as a pair
in both reasons — approve both or neither.

## Testing

```bash
.venv/bin/python -m pytest tests/test_agent_workbook.py tests/test_agent_tools.py tests/test_agent_e2e_mock.py -q
# full loop incl. a real LLM scan in mock mode (needs ANTHROPIC_API_KEY):
RUN_AGENT_LLM_TESTS=1 .venv/bin/python -m pytest tests/test_agent_e2e_mock.py -q
# no-spend rehearsal of the whole workflow:
.venv/bin/python -m ads_agent scan --mode mock
```

## Known constraints

- **Billing:** the live account has no payment method yet — ad creates fail with
  Meta error 100 (campaigns/ad sets/creatives work). `launch` preflights this
  and reports blocked ads as retryable; re-running after adding a payment method
  creates only the missing ads.
- **IN_PROCESS ads** are in Meta ad review; they settle to PAUSED.
- The workbook grows with snapshots/logs; prune or archive periodically.
