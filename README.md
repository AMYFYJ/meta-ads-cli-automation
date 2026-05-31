# Meta Ads CLI Automation Pipeline

This project turns a campaign planning workbook or SQLite database into a repeatable Meta ads publishing workflow. It can run fully offline in `mock` mode for team demos, and it can run in `live` mode against Meta's official `meta-ads` CLI when Python 3.12+, credentials, and ad account access are available.

## What It Covers

- Account bootstrap tracking, including optional API-based ad account creation fallback.
- Campaign, ad set, creative, and ad creation.
- Bulk edits for budgets, bid caps, flight dates, statuses, names, copy, URLs, CTAs, and creative swaps.
- Validation, dry-run planning, apply, ID sync back into the source of truth, audit logging, and insight snapshots.
- Excel-first workflow with the same tables supported in SQLite for a database-backed version.

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
  --state outputs/mock_state.json \
  --out-source outputs/insights_workbook.xlsx
```

## Live Mode

Meta's official `meta-ads` package requires Python 3.12+.

```bash
python3.12 -m pip install meta-ads
export ACCESS_TOKEN="..."
export AD_ACCOUNT_ID="act_123456789"
export BUSINESS_ID="123456789"

python3 -m meta_ads_pipeline apply \
  --source examples/meta_ads_workflow_template.xlsx \
  --mode live \
  --out-source outputs/live_applied_workbook.xlsx \
  --yes
```

New objects are created as `PAUSED` by default unless the workbook explicitly says otherwise. Use a dry-run `plan` before live apply.

## Important Meta Constraints

The official CLI exposes ad account list/get/current, but not full ad account creation. This project includes a guarded Graph API fallback for ad account creation when the Business is eligible and `ACCESS_TOKEN` is available. In practice, Business verification, payment method setup, spend limits, and policy review may still require human setup in Meta Business Manager.

## Source Notes

- Official package: [meta-ads on PyPI](https://pypi.org/project/meta-ads/)
- Official docs path: [Meta Ads CLI overview](https://developers.facebook.com/documentation/ads-commerce/ads-ai-connectors/ads-cli/ads-cli-overview)
