# Live Mode Guide

Mock mode is for demos and pipeline QA. Live mode calls Meta's official `meta-ads` CLI and the
Graph API fallback for account creation, audiences, uploads, targeting, duplicates, patches, and deletes.

## Sandbox first (start here)

If you are new to the Meta Marketing API, **do not point live mode at a real ad account yet.**
Set up a **Sandbox ad account** first — API calls behave like production but never deliver ads
or spend money. Follow [SANDBOX_SETUP.md](SANDBOX_SETUP.md), then come back here.

Run the preflight check before any live run:

```bash
python -m meta_ads_pipeline doctor --live
```

It verifies Python 3.12+, the `meta` CLI, env vars, your token (read-only `adaccount list`),
whether the target account is your sandbox, and **`account_access`** — a direct read of the
target `act_...` that fails fast when the token has no ads role on it (the `(#200)` /
"missing permissions" family). Resolve every `FAIL` before applying; for `account_access`
failures see [SANDBOX_SETUP.md → Troubleshooting](SANDBOX_SETUP.md#troubleshooting--token-cant-see-the-sandbox-ad-account).

## Prerequisites

- Python 3.12+ for Meta's official `meta-ads` package.
- A Meta app/access token with the required Marketing API permissions.
- Business Manager access to the ad account, Page, Instagram account, pixel/dataset, and catalog.
- Payment method, spend limits, and policy requirements already handled in Meta Business Manager.

Install:

```bash
python3.12 -m pip install 'meta-ads-workflow[live]'   # or: pip install meta-ads
```

Configure:

```bash
export ACCESS_TOKEN="..."
export AD_ACCOUNT_ID="act_123456789"
export BUSINESS_ID="123456789"
export META_API_VERSION="v21.0"
```

### Account scoping and safety flags

| Flag / env var | Effect |
| --- | --- |
| `apply/insights --account act_...` | Override the target ad account for this run. |
| `SANDBOX_AD_ACCOUNT_ID` | Your sandbox account id. |
| `META_SANDBOX=1` | Live runs default to the sandbox account until you unset it. |
| `apply --require-sandbox` / `META_REQUIRE_SANDBOX=1` | Refuse live `apply` unless the target is the sandbox account (exit 2). |

### Rate-limit handling

Meta enforces roughly **200 calls/hour**. Live subprocess and Graph calls automatically retry
on transient/rate-limit errors with exponential backoff (honoring `Retry-After`):

| Env var | Default | Meaning |
| --- | --- | --- |
| `META_RETRY_ATTEMPTS` | `5` | Max attempts per call. |
| `META_RETRY_BASE_DELAY` | `2.0` | Base backoff seconds (doubles each attempt). |
| `META_MIN_INTERVAL` | `0` | Optional fixed pause (seconds) between live actions. |

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

Apply only after reviewing the plan (sandbox-guarded):

```bash
python3 -m meta_ads_pipeline apply \
  --source examples/meta_ads_workflow_template.xlsx \
  --mode live --require-sandbox --account "$SANDBOX_AD_ACCOUNT_ID" \
  --out-source outputs/live_applied_workbook.xlsx \
  --yes
```

For a real (production) account, drop `--require-sandbox` and target the real `act_...`.

For insights with breakdowns:

```bash
python3 -m meta_ads_pipeline insights \
  --source outputs/live_applied_workbook.xlsx \
  --mode live \
  --level ad \
  --breakdown publisher_platform \
  --breakdown platform_position \
  --out-source outputs/live_insights_workbook.xlsx
```

## Safety Rules

- Keep new objects `PAUSED` until QA is complete.
- Use `BulkChanges` for changes instead of editing directly in Ads Manager.
- Review `PublishLog` after every live run.
- Use budget guardrails in `Accounts.max_daily_budget_cents`.
- Prefer one launch batch at a time for the first live rollout.
- Review Graph-backed actions carefully in `plan.json`: audiences, uploads, targeting presets, Advantage+ toggles, duplicate jobs, JSON patches, and deletes all use Graph API fallback.

## Known Limitation

The official CLI currently exposes `adaccount list/get/current`, not full ad-account creation. This project includes a guarded Graph API fallback for eligible Businesses. If Meta rejects account creation due to verification, policy, payment, or spend-limit requirements, create the account manually once and paste the `act_...` ID into `Accounts.ad_account_id`.
