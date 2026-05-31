# Team Demo Script

This script shows the full workflow without touching a real Meta ad account.

## 1. Run the Demo

```bash
python3 -m meta_ads_pipeline demo
```

Open:

```text
outputs/demo/meta_ads_workflow_template.xlsx
outputs/demo/applied_workbook.xlsx
outputs/demo/insights_workbook.xlsx
outputs/demo/plan.json
```

## 2. Explain the Source of Truth

Show the workbook tabs:

- `Accounts`: account/business setup.
- `Campaigns`: campaign shell and CBO/ABO budget strategy.
- `AdSets`: targeting, conversion event, bid caps, flight dates.
- `Audiences` / `AudienceUploads`: custom, lookalike, website, saved audiences, and CRM uploads.
- `TargetingPresets` / `AutomationSettings`: reusable audiences plus Advantage+ toggles.
- `Creatives`: copy, CTA, URL, asset references.
- `Ads`: ad set to creative mapping.
- `DuplicateJobs`: campaign/ad set/ad copy jobs.
- `BulkChanges`: approved updates.
- `OptimizationRules`: rules that create bulk changes from performance.
- `PublishLog`: audit log.
- `PerformanceSnapshots`: reporting feed.

## 3. Show Dry-Run Planning

```bash
python3 -m meta_ads_pipeline plan \
  --source outputs/demo/meta_ads_workflow_template.xlsx \
  --out outputs/demo/team_plan.json
```

Open `outputs/demo/team_plan.json`. It should include account creation, audience creation/upload, campaign creation, Graph-backed ad set creation with targeting/Advantage+ settings, creative creation, ad creation, duplicate jobs, and approved bulk edits.

## 4. Show Apply and ID Sync

```bash
python3 -m meta_ads_pipeline apply \
  --source outputs/demo/meta_ads_workflow_template.xlsx \
  --mode mock \
  --state outputs/demo/mock_state.json \
  --out-source outputs/demo/team_applied.xlsx \
  --yes
```

Open `outputs/demo/team_applied.xlsx` and point out:

- `ad_account_id`, `meta_campaign_id`, `meta_adset_id`, `meta_creative_id`, and `meta_ad_id` are filled in.
- `BulkChanges.applied_at` and `BulkChanges.result` are filled in.
- The original budget/bid/date fields reflect the approved edits.
- `PublishLog` records every action.

## 5. Queue a New Bulk Edit

```bash
python3 -m meta_ads_pipeline bulk-edit \
  --source outputs/demo/team_applied.xlsx \
  --out-source outputs/demo/team_pending_change.xlsx \
  --object-level adset \
  --object-key adset_prospecting_us \
  --field bid_amount_cents \
  --value 1800 \
  --reason "Increase bid cap after strong first-day CVR" \
  --requested-by "Growth Team" \
  --approval-status APPROVED
```

Then apply:

```bash
python3 -m meta_ads_pipeline apply \
  --source outputs/demo/team_pending_change.xlsx \
  --mode mock \
  --state outputs/demo/mock_state.json \
  --out-source outputs/demo/team_after_bulk_edit.xlsx \
  --yes
```

## 6. Show Insights

```bash
python3 -m meta_ads_pipeline insights \
  --source outputs/demo/team_after_bulk_edit.xlsx \
  --mode mock \
  --breakdown publisher_platform \
  --breakdown age \
  --out-source outputs/demo/team_insights.xlsx
```

Open `team_insights.xlsx` and show the ad-level performance plus breakdown rows. In live mode this table is fed by `meta ads insights get`.

## 7. Generate Optimization Changes

```bash
python3 -m meta_ads_pipeline optimize \
  --source outputs/demo/team_insights.xlsx \
  --out-source outputs/demo/team_optimized.xlsx
```

Open `team_optimized.xlsx` and show the new optimization-generated rows in `BulkChanges`.
