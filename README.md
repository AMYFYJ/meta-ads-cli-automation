# Meta Ads CLI Automation Pipeline

This project turns a campaign planning workbook or SQLite database into a repeatable Meta ads publishing workflow. It can run fully offline in `mock` mode for team demos, and it can run in `live` mode against Meta's official `meta-ads` CLI when Python 3.12+, credentials, and ad account access are available.

## What It Covers

- **Consolidated 10-tab workbook, one tab per object**: each `AdSets` row carries the complete ad set — budgets, bids, bid strategy, flight dates, and full audience targeting — and each `Ads` row carries the complete ad including its creative (copy, CTA, URL, asset). See [docs/SCHEMA.md](docs/SCHEMA.md).
- **Campaign setup and launch**: campaigns (CBO/ABO with bid-strategy handling), ad sets, inline creatives, ads, plus custom / lookalike / website / saved audiences with CRM list uploads on the audience row.
- **Audience targeting from workbook columns**: geo (country/region/city/zip), age, gender, language, interests and behaviors as `<id>:<Name>`, detailed-targeting AND-groups (`flexible_spec_json`) and exclusions, custom audiences by `audience_key` or Meta ID, manual placements/positions, and Advantage+ toggles. Interest/behavior ID lookup via `targeting-search`. See [docs/TARGETING.md](docs/TARGETING.md).
- **Bulk operations** queued in `BulkChanges` with human approval: budgets, bid caps, bid strategy, optimization goal, targeting replacement, flight dates, statuses, names, copy, URLs, CTAs, JSON patches, deletes, duplicates, and creative swaps.
- **Optimization rules** that turn `PerformanceSnapshots` into proposed bulk changes, and mock/live insights with breakdowns.
- **LLM agent layer** (`ads_agent`) for the daily scan → propose → approve → apply loop; the agent can only write `PENDING` proposals. See [docs/AGENT.md](docs/AGENT.md).
- **Safety by default**: validation with blocking errors, dry-run planning, `META_FORCE_PAUSED` guard against anything going ACTIVE, ID writeback, `PublishLog` audit trail, and workbook version archiving.
- **Workbook migration** (`migrate`) that upgrades older multi-tab workbooks to the consolidated layout.
- Excel-first workflow with the same tables supported in SQLite for a database-backed version.

## Current Status

As of July 14, 2026, live mode runs against a real ad account with billing set up, forced `PAUSED` via `META_FORCE_PAUSED=1`:

- A full campaign → ad set → creative → ad tree publishes live end to end (`doctor --live` passes). See [docs/AD_CREATION_WORKFLOW.md](docs/AD_CREATION_WORKFLOW.md) for the recipe and [examples/build_ad_campaign.py](examples/build_ad_campaign.py) to generate a minimal source workbook.
- The workbook was consolidated from 15 tabs to 10: targeting presets and Advantage+ settings folded into `AdSets` columns, creatives folded into `Ads` rows, audience uploads folded into `Audiences`, and duplicate jobs folded into `BulkChanges`. `migrate` upgrades older workbooks in place.
- The full test suite (67 tests) plus a 52-check end-to-end mock scenario covers setup, targeting, launch, bulk operations, rule-driven optimization, and the force-paused guards.

## Agent Automation (ads_agent)

An LLM agent layer automates the daily loop: scan performance → propose optimizations
(bid caps, pauses, budget reallocation, strategy tests) as `PENDING` BulkChanges rows →
report to you with reasoning → apply only what you approve in Excel. The agent can never
activate, delete, or apply anything itself. See [docs/AGENT.md](docs/AGENT.md).

```bash
.venv/bin/python -m ads_agent scan --mode live      # daily via launchd, or on demand
# review outputs/reports/<date>_scan.md, approve rows in data/current.xlsx, then:
.venv/bin/python -m ads_agent apply-approved --mode live --yes
.venv/bin/python -m ads_agent launch --yes          # create campaigns from the workbook
```

## Quick Demo

Install local dependencies if needed:

```bash
python3 -m pip install -r requirements.txt
```

```bash
python3 -m meta_ads_pipeline demo
```

The demo creates:

- `outputs/demo/meta_ads_workflow_template.xlsx`
- `outputs/demo/plan.json`
- `outputs/demo/applied_workbook.xlsx`
- `outputs/demo/mock_state.json`
- `outputs/demo/insights_workbook.xlsx`

Open `outputs/demo/applied_workbook.xlsx` to show the team how source rows get Meta IDs and publish results after apply.

## Common Commands

Create a blank or sample Excel template:

```bash
python3 -m meta_ads_pipeline init --output examples/meta_ads_workflow_template.xlsx --with-sample
```

Validate a workbook:

```bash
python3 -m meta_ads_pipeline validate --source examples/meta_ads_workflow_template.xlsx
```

Generate a dry-run plan:

```bash
python3 -m meta_ads_pipeline plan --source examples/meta_ads_workflow_template.xlsx --out outputs/plan.json
```

Apply in mock mode:

```bash
python3 -m meta_ads_pipeline apply \
  --source examples/meta_ads_workflow_template.xlsx \
  --mode mock \
  --state outputs/mock_state.json \
  --out-source outputs/applied_workbook.xlsx \
  --yes
```

Queue a bulk edit into a copy of the workbook:

```bash
python3 -m meta_ads_pipeline bulk-edit \
  --source outputs/applied_workbook.xlsx \
  --out-source outputs/bulk_change_workbook.xlsx \
  --object-level adset \
  --object-key adset_prospecting_us \
  --field bid_amount_cents \
  --value 1800 \
  --reason "Raise bid cap for launch day volume" \
  --approval-status APPROVED
```

Generate mock insights:

```bash
python3 -m meta_ads_pipeline insights \
  --source outputs/applied_workbook.xlsx \
  --mode mock \
  --breakdown publisher_platform \
  --breakdown age \
  --out-source outputs/insights_workbook.xlsx
```

Generate optimization-rule bulk changes from the insight rows:

```bash
python3 -m meta_ads_pipeline optimize \
  --source outputs/insights_workbook.xlsx \
  --out-source outputs/optimized_workbook.xlsx
```

Look up interest/behavior IDs for the targeting columns (see [docs/TARGETING.md](docs/TARGETING.md)):

```bash
python3 -m meta_ads_pipeline targeting-search --q "yoga"
python3 -m meta_ads_pipeline targeting-search --q "travel" --kind behavior
```

Upgrade an older workbook to the consolidated tab layout (close Excel first):

```bash
python3 -m meta_ads_pipeline migrate --source data/current.xlsx --out-source data/current.xlsx
```

## Coverage Matrix

| Workflow | Mock | Live path |
| --- | --- | --- |
| Campaign/ad set/creative/ad create/update | Yes | Official `meta-ads` CLI when fields are supported; Graph API for advanced targeting fields |
| Custom/lookalike/website/saved audiences | Yes | Graph API |
| Audience uploads | Yes | Graph API |
| Audience targeting columns and Advantage+ toggles | Yes | Graph API ad set payloads |
| Duplicate campaigns/ad sets/ads | Yes | Graph API copy endpoints |
| Budget/bid/date/status/copy/creative bulk changes | Yes | CLI or Graph depending on field |
| JSON patch/delete/duplicate bulk operations | Yes | Graph API |
| Insights and breakdowns | Yes | Official `meta-ads` CLI |
| Optimization rules to bulk changes | Yes | Local rule engine, then normal apply |

## Live Mode

Meta's official `meta-ads` package requires Python 3.12+. Validate readiness with the
preflight, dry-run first, then apply:

```bash
python3.12 -m pip install 'meta-ads-workflow[live]'   # or: pip install meta-ads
export ACCESS_TOKEN="..."
export AD_ACCOUNT_ID="act_..."
export META_FORCE_PAUSED=1       # refuse any live action that would set a status to ACTIVE

python3 -m meta_ads_pipeline doctor --live      # preflight: CLI, token, account access, billing
python3 -m meta_ads_pipeline plan --source data/current.xlsx --out outputs/live_plan.json

python3 -m meta_ads_pipeline apply \
  --source data/current.xlsx \
  --mode live \
  --out-source outputs/live_applied_workbook.xlsx \
  --yes
```

New objects are created as `PAUSED` by default, and with `META_FORCE_PAUSED=1` the apply
refuses to run if any planned action would set a status to ACTIVE — activation is a
deliberate human step in Ads Manager or via an approved bulk change with the guard lifted.
Live calls retry on rate limits (~200/hr) with exponential backoff — see
[docs/LIVE_MODE.md](docs/LIVE_MODE.md). Note that Meta requires a valid payment method on
the ad account before **ad** objects can be created (API error 100); campaigns, ad sets,
and creatives work without one, and the `ads_agent launch` command preflights this.

## Important Meta Constraints

The official CLI exposes ad account list/get/current, but not full ad account creation. This project includes a guarded Graph API fallback for ad account creation when the Business is eligible and `ACCESS_TOKEN` is available. In practice, Business verification, payment method setup, spend limits, and policy review may still require human setup in Meta Business Manager.

## Source Notes

- Official package: [meta-ads on PyPI](https://pypi.org/project/meta-ads/)
- Official docs path: [Meta Ads CLI overview](https://developers.facebook.com/documentation/ads-commerce/ads-ai-connectors/ads-cli/ads-cli-overview)
