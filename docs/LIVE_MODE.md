# Live Mode Guide

Mock mode is for demos and pipeline QA. Live mode calls Meta's official CLI and the Graph API fallback for account creation.

## Prerequisites

- Python 3.12+ for Meta's official `meta-ads` package.
- A Meta app/access token with the required Marketing API permissions.
- Business Manager access to the ad account, Page, Instagram account, pixel/dataset, and catalog.
- Payment method, spend limits, and policy requirements already handled in Meta Business Manager.

Install:

```bash
python3.12 -m pip install meta-ads
```

Configure:

```bash
export ACCESS_TOKEN="..."
export AD_ACCOUNT_ID="act_123456789"
export BUSINESS_ID="123456789"
export META_API_VERSION="v21.0"
```

Smoke test:

```bash
meta ads adaccount list --output json
meta ads campaign list --output json
```

## Live Publish Flow

Always run validation and planning first:

```bash
python3 -m meta_ads_pipeline validate --source examples/meta_ads_workflow_template.xlsx
python3 -m meta_ads_pipeline plan --source examples/meta_ads_workflow_template.xlsx --out outputs/live_plan.json
```

Apply only after reviewing the plan:

```bash
python3 -m meta_ads_pipeline apply \
  --source examples/meta_ads_workflow_template.xlsx \
  --mode live \
  --out-source outputs/live_applied_workbook.xlsx \
  --yes
```

## Safety Rules

- Keep new objects `PAUSED` until QA is complete.
- Use `BulkChanges` for changes instead of editing directly in Ads Manager.
- Review `PublishLog` after every live run.
- Use budget guardrails in `Accounts.max_daily_budget_cents`.
- Prefer one launch batch at a time for the first live rollout.

## Known Limitation

The official CLI currently exposes `adaccount list/get/current`, not full ad-account creation. This project includes a guarded Graph API fallback for eligible Businesses. If Meta rejects account creation due to verification, policy, payment, or spend-limit requirements, create the account manually once and paste the `act_...` ID into `Accounts.ad_account_id`.
