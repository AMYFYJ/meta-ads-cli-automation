# Sandbox Setup — Your Safe On-Ramp to Real Meta Ads

This guide takes you from zero to running **real** Meta Marketing API actions through this
pipeline, without risking any ad spend. Do this first, before you ever point the tool at a
production ad account.

## What is a Marketing API Sandbox ad account?

A **Sandbox ad account** is a special ad account you create from your Meta app's dashboard.
API read/write calls against it behave exactly like production — you can create campaigns,
ad sets, ads, audiences, change budgets, pause, delete — **but:**

- **No ads are ever delivered.**
- **No impressions accrue and no money is spent.**
- No billing, payment method, or policy review is required.

It is the safe place to learn the CLI, confirm your access token and permissions work, and
test this whole workflow end to end. Think of it as a flight simulator for your ad account.

### Sandbox vs. production at a glance

| | Sandbox | Production |
|---|---|---|
| Real spend / delivery | No | Yes |
| Needs payment method | No | Yes |
| Good for | Learning, testing, CI | Real campaigns |
| How this tool targets it | `META_SANDBOX=1` + `SANDBOX_AD_ACCOUNT_ID`, or `--account` | real `AD_ACCOUNT_ID` |

## Prerequisites

- **Python 3.12+** (the official `meta-ads` CLI requires it; this pipeline's mock mode runs on 3.11).
- A Meta (Facebook) account you can use to create a developer app.

## Step 1 — Create a Meta app

1. Go to <https://developers.facebook.com/apps> and create a new app (type: **Business**).
2. In the app dashboard, add the **Marketing API** product.

## Step 2 — Get an access token with the right permissions

1. Open the **Graph API Explorer** (<https://developers.facebook.com/tools/explorer>).
2. Select your app, then request the permissions **`ads_management`** and **`ads_read`**.
3. Generate the token and copy it. (Tokens from the Explorer are short-lived; for longer use,
   exchange it for a long-lived token — see Meta's docs. For sandbox testing a short-lived
   token is fine to start.)

> 🚨 Treat the token like a password. Put it in environment variables / a local `.env`,
> never paste it into chat or commit it.

## Step 3 — Create a Sandbox ad account

1. In the app dashboard, go to **Marketing API → Tools**.
2. Find **Sandbox Ad Account Management** and create a new sandbox ad account.
3. Copy its id — it looks like `act_1234567890`.

## Step 4 — Configure your environment

Copy `.env.example` to `.env` and fill it in (or `export` the variables):

```bash
export ACCESS_TOKEN="your_token"
export SANDBOX_AD_ACCOUNT_ID="act_SANDBOX"   # the sandbox id from Step 3
export AD_ACCOUNT_ID="$SANDBOX_AD_ACCOUNT_ID" # default target = sandbox
export META_SANDBOX=1                          # prefer sandbox until you opt out
export META_REQUIRE_SANDBOX=1                  # block live apply on non-sandbox accounts
export BUSINESS_ID="your_business_id"          # optional
export META_API_VERSION="v21.0"
```

## Step 5 — Install the live CLI

```bash
pip install 'meta-ads-workflow[live]'   # pulls the official meta-ads CLI (Python 3.12+)
# or: pip install meta-ads
```

## Step 6 — Run the preflight check

```bash
python -m meta_ads_pipeline doctor --live
```

You want every line to read `OK`. The `doctor` command verifies:

- Python version, the `meta` CLI is installed, and required env vars are set.
- Your token works (a read-only `meta ads adaccount list` call).
- Whether the resolved account is your sandbox (it confirms a match against
  `SANDBOX_AD_ACCOUNT_ID`).

## Step 7 — Validate, plan, and apply against the sandbox

```bash
python -m meta_ads_pipeline validate --source examples/meta_ads_workflow_template.xlsx
python -m meta_ads_pipeline plan     --source examples/meta_ads_workflow_template.xlsx --out outputs/sandbox_plan.json

# Review outputs/sandbox_plan.json, then:
python -m meta_ads_pipeline apply \
  --source examples/meta_ads_workflow_template.xlsx \
  --mode live --require-sandbox --account "$SANDBOX_AD_ACCOUNT_ID" \
  --out-source outputs/sandbox_applied.xlsx --yes
```

Inspect the resulting workbook: real Meta ids are written back into the id columns and every
action is recorded in `PublishLog`. Because it's a sandbox, nothing was delivered or charged.

Pull (mock-shaped) insights the same way:

```bash
python -m meta_ads_pipeline insights \
  --source outputs/sandbox_applied.xlsx \
  --mode live --account "$SANDBOX_AD_ACCOUNT_ID" \
  --out-source outputs/sandbox_insights.xlsx
```

You can also run the opt-in live test:

```bash
RUN_LIVE_TESTS=1 python -m pytest tests/test_live_sandbox.py -q
```

## Step 8 — Graduate to production (when you're ready)

1. Create or get access to a **real** ad account (with a payment method) in Business Manager.
2. Update env: set `AD_ACCOUNT_ID` to the real `act_...`, and **unset `META_SANDBOX`**.
3. Re-run `doctor --live` and confirm the token has access to the real account.
4. Keep new objects **PAUSED** (`desired_status=PAUSED`) until you've QA'd them.
5. Run `apply` **without** `--require-sandbox` only once you intend to go live:

   ```bash
   python -m meta_ads_pipeline apply --source <your_workbook> \
     --mode live --account "$AD_ACCOUNT_ID" --out-source outputs/live_applied.xlsx --yes
   ```

See [LIVE_MODE.md](LIVE_MODE.md) for the full live reference, safety rules, and flag list.
