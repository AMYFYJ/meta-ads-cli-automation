# Workbook and Database Schema

Use the same table names and columns in Excel, SQLite, Airtable, Postgres, BigQuery, or any other database. Excel is convenient for the team demo; a database is better once multiple people or scheduled jobs need to edit the plan.

## Core Setup Tables

### Accounts

One row per ad account or intended ad account.

Important fields:

- `account_key`: Stable internal key used by every other table.
- `business_id`: Meta Business ID; required for dataset and ad-account creation attempts.
- `ad_account_id`: Existing `act_...` ID or blank before creation.
- `create_ad_account`: `TRUE` to attempt ad account creation.
- `currency`, `timezone_id`, `page_id`, `instagram_actor_id`, `pixel_dataset_id`, `catalog_id`: Account-level defaults.
- `max_daily_budget_cents`, `approval_required`: Guardrails for launch control.

### Campaigns

One row per campaign.

Important fields:

- `campaign_key`: Stable internal campaign key.
- `account_key`: Joins to `Accounts`.
- `meta_campaign_id`: Written back after publish.
- `objective`: Meta outcome objective, e.g. `OUTCOME_SALES`.
- `budget_mode`: `CBO` for campaign budget optimization, `ABO` for ad-set budgets.
- `daily_budget_cents`, `lifetime_budget_cents`, `bid_strategy`: Budget strategy.
- `desired_status`: Default should be `PAUSED`.
- `approval_status`: Must be `APPROVED` before creation.

### AdSets

One row per ad set.

Important fields:

- `adset_key`, `campaign_key`, `meta_adset_id`.
- `optimization_goal`, `billing_event`, `bid_amount_cents`.
- `daily_budget_cents`, `lifetime_budget_cents`: Use when campaign is `ABO`.
- `start_time`, `end_time`: ISO date-times with timezone offset.
- `countries`, `regions`, `age_min`, `age_max`, `genders`, `placements`.
- `custom_audiences`, `excluded_audiences`, `interests`.
- `pixel_dataset_id`, `pixel_event`, `attribution_window`.

### Creatives

One row per creative concept/variant.

Important fields:

- `creative_key`, `account_key`, `meta_creative_id`.
- `format`: `image`, `video`, or `dco`.
- `asset_path_or_url`, `image_hash_or_video_id`.
- `primary_text`, `headline`, `description`, `cta`.
- `destination_url`, `utm_template`.
- `creative_angle`, `variant_label`, `compliance_notes`.

### Ads

One row per ad object that links an ad set to a creative.

Important fields:

- `ad_key`, `adset_key`, `creative_key`, `meta_ad_id`.
- `tracking_specs`, `url_tags`.
- `desired_status`, `launch_batch`, `approval_status`.

## Operations Tables

### BulkChanges

Queue all edits here instead of manually changing live objects in Ads Manager.

Use cases:

- Campaign budget changes: `object_level=campaign`, `field=daily_budget_cents`.
- Bid cap changes: `object_level=adset`, `field=bid_amount_cents`.
- Flight date changes: `object_level=adset`, `field=start_time` or `field=end_time`.
- Status changes: `field=desired_status` with `new_value=ACTIVE` or `PAUSED`.
- Creative swaps: `object_level=ad`, `field=creative_key`, `new_value=<creative_key>`.

The pipeline applies only approved, unapplied rows.

### PublishLog

Audit trail for every create/update command:

- `action_id`, `operation`, `object_type`, `object_key`, `meta_id`.
- `command`, `payload_hash`, `status`, `message`, `stdout`, `stderr`.

### ValidationErrors

Validation output written back for workbook review.

### PerformanceSnapshots

Daily or hourly performance table for monitoring and optimization.

Recommended metrics:

- Delivery: `spend`, `impressions`, `reach`, `frequency`.
- Traffic: `clicks`, `link_clicks`, `landing_page_views`, `ctr`, `cpc`, `cpm`.
- Conversion: `conversions`, `cost_per_result`, `purchases`, `purchase_value`, `roas`, `leads`, `cpl`.
- Funnel: `add_to_cart`, `initiate_checkout`.
- Pacing: `budget_utilization`, `pacing_ratio`.

Use these to trigger optimization rules such as pausing low-ROAS ads, increasing budgets on under-paced winners, reducing bid caps on expensive ad sets, or flagging high frequency.
