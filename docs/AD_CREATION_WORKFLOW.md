# Ad Creation Workflow (Live)

End-to-end recipe for publishing a complete campaign → ad set → ad tree to a real
ad account, based on the verified July 2026 live runs. Everything is created
`PAUSED`; keep `META_FORCE_PAUSED=1` set so an accidental `ACTIVE` in the
workbook aborts the apply.

## Prerequisites

- `.env` with `ACCESS_TOKEN`, `AD_ACCOUNT_ID`, `META_API_VERSION`, `META_CLI_BIN`,
  and `META_FORCE_PAUSED=1`. Load it before every command:

  ```bash
  set -a; source .env; set +a
  ```

- **Billing:** the ad account must have a payment method before any **ad** object
  can be created — even paused ones fail with API error 100 ("Update payment
  method") otherwise. Campaigns, ad sets, and creatives are unaffected. Verify
  via Graph before publishing ads:

  ```bash
  curl -s "https://graph.facebook.com/$META_API_VERSION/$AD_ACCOUNT_ID?fields=funding_source_details&access_token=$ACCESS_TOKEN"
  ```

  An empty result means no funding source: add one in **Billing & payments →
  Payment settings** for the target account, confirm it appears in the payment
  methods list, then re-check.

- Preflight: `python3 -m meta_ads_pipeline doctor --live` must be all OK/WARN.

## 1. Build the source workbook

```bash
python3 examples/build_ad_campaign.py \
  --ad-account-id "$AD_ACCOUNT_ID" \
  --page-id <PAGE_ID> \
  --name "My Test" \
  --image assets/hero.png \
  --out outputs/my_test.xlsx
```

Leave `--bid-strategy` blank to get the Highest Volume default
(`LOWEST_COST_WITHOUT_CAP`) — the planner emits a follow-up Graph call after
campaign create, since the meta CLI create has no bid-strategy flag. Use
`--creative-id` to reuse an existing creative instead of uploading an image.

## 2. Validate, plan, review

```bash
python3 -m meta_ads_pipeline validate --source outputs/my_test.xlsx
python3 -m meta_ads_pipeline plan --source outputs/my_test.xlsx --out outputs/my_test_plan.json
```

Expect four actions for a fresh tree: `010_create_campaign_*`,
`011_set_campaign_bid_strategy_*`, `020_create_adset_*`, `040_create_ad_*`
(plus `030_create_creative_*` when uploading a new creative). Review the plan
before applying.

## 3. Apply live

```bash
python3 -m meta_ads_pipeline apply \
  --source outputs/my_test.xlsx \
  --mode live \
  --out-source outputs/my_test_applied.xlsx \
  --yes
```

Meta IDs are written back into the applied workbook; `PublishLog` records every
action. **The newest applied workbook is the source of truth from here on** —
chain every follow-up run off it.

## 4. Iterate on the applied workbook

- **Retry failures** (e.g. an ad blocked on billing): fix the root cause, then
  re-apply with `--source <latest applied>.xlsx`. Only rows without Meta IDs are
  planned; already-created objects are skipped.
- **Add an ad set / ad to an existing campaign:** append a row with a new key to
  the latest applied workbook (country codes are ISO — the UK is `GB`), save as
  a new source, then plan + apply. Duplicate keys are rejected by validation
  before any API call.
- Freshly created ads read back as `effective_status: IN_PROCESS` while Meta
  runs ad review; they settle to `PAUSED` after.

## 5. See it in Ads Manager

Open `https://adsmanager.facebook.com/adsmanager/manage/campaigns?act=<ACCOUNT_NUMBER>`
(account number without the `act_` prefix). Paused campaigns show with their
toggle off; if one is missing, widen the date range (top right) and clear any
"Delivery" filter — never-delivered paused objects are hidden by it.
