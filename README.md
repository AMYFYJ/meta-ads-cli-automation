# Meta Ads CLI Automation Pipeline

This project turns a campaign planning workbook or SQLite database into a repeatable Meta ads publishing workflow. It can run fully offline in `mock` mode for team demos, and it can run in `live` mode against Meta's official `meta-ads` CLI when Python 3.12+, credentials, and ad account access are available.

## What It Covers

- Account bootstrap tracking, including optional API-based ad account creation fallback.
- Campaign, ad set, creative, ad, custom audience, lookalike audience, website audience, and saved audience creation.
- Audience uploads, reusable targeting presets, Advantage+ / automation toggles, duplicate jobs, and optimization rules.
- Bulk edits for budgets, bid caps, bid strategy, optimization goal, targeting, flight dates, statuses, names, copy, URLs, CTAs, JSON patches, deletes, duplicates, and creative swaps.
- Validation, dry-run planning, apply, ID sync back into the source of truth, audit logging, and insight snapshots.
- Excel-first workflow with the same tables supported in SQLite for a database-backed version.

## Current Status

As of June 24, 2026, the live sandbox path has been verified locally:

- The Meta access token validates with `meta ads adaccount list`.
- The sandbox ad account is selected through `AD_ACCOUNT_ID`, `SANDBOX_AD_ACCOUNT_ID`, `META_SANDBOX=1`, and `META_REQUIRE_SANDBOX=1`.
- `doctor --live` passes against the sandbox.
- A campaign-only live apply created `Spring Launch | Sales | US` in the sandbox as `PAUSED`.
- The applied workbook writes the new Meta campaign ID back into the `Campaigns` sheet and records the result in `PublishLog`.

The next test is to build down the object tree one layer at a time:

1. Create an ad set under the sandbox campaign.
2. Create a creative after the required Page, asset, and actor IDs are confirmed.
3. Create an ad using the campaign, ad set, and creative IDs.
4. Pull live sandbox insights and write them into `PerformanceSnapshots`.
5. Run `optimize` against the insight rows and review generated `BulkChanges`.

### Blocker before step 2: token access to the sandbox ad account

A regenerated token can fix Page permissions while *losing* its role on the sandbox
ad account. The symptom is a creative/image-upload failure such as
`(#200) Ad account owner has NOT grant ads_management or ads_read permission` or
`Object with ID 'act_...' does not exist, cannot be loaded due to missing permissions`,
even though the token reads the Page fine. (Note: `/me/adaccounts` not listing a sandbox
account is normal and is **not** proof of access — a direct read is the real test.)

`doctor --live` now includes an **`account_access`** check that reads the target
`act_...` directly and fails at preflight (instead of mid-creative) when the token has no
ads role on it:

```bash
python3 -m meta_ads_pipeline doctor --live --account "$SANDBOX_AD_ACCOUNT_ID"
```

If `account_access` fails, follow
[docs/SANDBOX_SETUP.md → Troubleshooting](docs/SANDBOX_SETUP.md#troubleshooting--token-cant-see-the-sandbox-ad-account)
to reconnect the sandbox account to your token (same app, `ads_management` + `ads_read`,
add your user to the sandbox account), then resume the build-down at step 2.

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

## Coverage Matrix

| Workflow | Mock | Live path |
| --- | --- | --- |
| Campaign/ad set/creative/ad create/update | Yes | Official `meta-ads` CLI when fields are supported; Graph API for advanced targeting fields |
| Custom/lookalike/website/saved audiences | Yes | Graph API |
| Audience uploads | Yes | Graph API |
| Targeting presets and Advantage+ toggles | Yes | Graph API ad set payloads |
| Duplicate campaigns/ad sets/ads | Yes | Graph API copy endpoints |
| Budget/bid/date/status/copy/creative bulk changes | Yes | CLI or Graph depending on field |
| JSON patch/delete/duplicate bulk operations | Yes | Graph API |
| Insights and breakdowns | Yes | Official `meta-ads` CLI |
| Optimization rules to bulk changes | Yes | Local rule engine, then normal apply |

## Live Mode

Meta's official `meta-ads` package requires Python 3.12+. **New to the Meta API? Start with a
sandbox account** (real API, zero spend/delivery) — see
[docs/SANDBOX_SETUP.md](docs/SANDBOX_SETUP.md). Then validate readiness with the preflight:

```bash
python3.12 -m pip install 'meta-ads-workflow[live]'   # or: pip install meta-ads
export ACCESS_TOKEN="..."
export SANDBOX_AD_ACCOUNT_ID="act_SANDBOX"
export AD_ACCOUNT_ID="$SANDBOX_AD_ACCOUNT_ID"
export META_SANDBOX=1            # default target = sandbox
export META_REQUIRE_SANDBOX=1    # block live apply on non-sandbox accounts

python3 -m meta_ads_pipeline doctor --live      # preflight: CLI, token, sandbox check
python3 -m meta_ads_pipeline plan --source examples/meta_ads_workflow_template.xlsx --out outputs/live_plan.json

python3 -m meta_ads_pipeline apply \
  --source examples/meta_ads_workflow_template.xlsx \
  --mode live --require-sandbox --account "$SANDBOX_AD_ACCOUNT_ID" \
  --out-source outputs/live_applied_workbook.xlsx \
  --yes
```

New objects are created as `PAUSED` by default unless the workbook explicitly says otherwise.
Use a dry-run `plan` before live apply. To graduate to a real ad account, unset `META_SANDBOX`,
set the real `AD_ACCOUNT_ID`, and drop `--require-sandbox`. Live calls retry on rate limits
(~200/hr) with exponential backoff — see [docs/LIVE_MODE.md](docs/LIVE_MODE.md).

For the first sandbox smoke test, point the `Accounts.ad_account_id` cell at the existing
sandbox `act_...` account and set `create_ad_account` to `FALSE`. That prevents the sample
workbook from trying to create a new ad account before campaign creation.

## Important Meta Constraints

The official CLI exposes ad account list/get/current, but not full ad account creation. This project includes a guarded Graph API fallback for ad account creation when the Business is eligible and `ACCESS_TOKEN` is available. In practice, Business verification, payment method setup, spend limits, and policy review may still require human setup in Meta Business Manager.

## Source Notes

- Official package: [meta-ads on PyPI](https://pypi.org/project/meta-ads/)
- Official docs path: [Meta Ads CLI overview](https://developers.facebook.com/documentation/ads-commerce/ads-ai-connectors/ads-cli/ads-cli-overview)
