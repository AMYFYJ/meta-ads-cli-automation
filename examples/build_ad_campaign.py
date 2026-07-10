"""Build a minimal one-campaign / one-ad-set / one-ad workbook for live publishing.

Produces a source workbook that plans exactly four actions: campaign create,
bid-strategy follow-up (defaults to Highest Volume), ad set create, and ad
create. Pair it with docs/AD_CREATION_WORKFLOW.md:

    python3 examples/build_ad_campaign.py \
        --ad-account-id act_1234567890 \
        --page-id 111222333444555 \
        --out outputs/my_campaign.xlsx

Everything is created PAUSED. An existing creative can be reused with
--creative-id; otherwise supply --image so the plan uploads one.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from meta_ads_pipeline.storage import create_template, load_source, save_with_updates


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ad-account-id", required=True, help="Target ad account, e.g. act_1234567890.")
    parser.add_argument("--page-id", required=True, help="Facebook Page id the ad runs from.")
    parser.add_argument("--out", required=True, help="Path for the generated source workbook (.xlsx).")
    parser.add_argument("--name", default="CLI Test", help="Base name used for the campaign/ad set/ad.")
    parser.add_argument("--objective", default="OUTCOME_TRAFFIC")
    parser.add_argument("--daily-budget-cents", default="500", help="CBO daily budget in cents (default $5).")
    parser.add_argument("--bid-strategy", default="", help="Blank defaults to LOWEST_COST_WITHOUT_CAP (Highest Volume).")
    parser.add_argument("--countries", default="US", help="Comma-separated ISO country codes (UK is GB).")
    parser.add_argument("--creative-id", default="", help="Reuse an existing Meta creative id instead of uploading.")
    parser.add_argument("--image", default="", help="Image path/URL for a new creative (ignored with --creative-id).")
    parser.add_argument("--link-url", default="https://example.com", help="Destination URL for the ad.")
    parser.add_argument("--primary-text", default="Automated pipeline test ad.")
    parser.add_argument("--headline", default="Pipeline test")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.creative_id and not args.image:
        print("Provide --creative-id to reuse a creative or --image to upload one.", file=sys.stderr)
        return 2

    slug = args.name.lower().replace(" ", "_")
    creative_row = {
        "creative_key": f"creative_{slug}_a",
        "account_key": "acct_main",
        "meta_creative_id": args.creative_id,
        "name": f"{args.name} Creative A",
        "format": "image",
        "asset_path_or_url": args.image,
        "primary_text": args.primary_text,
        "headline": args.headline,
        "cta": "LEARN_MORE",
        "destination_url": args.link_url,
        "page_id": args.page_id,
        "approval_status": "APPROVED",
    }
    rows = {
        "Accounts": [{
            "account_key": "acct_main",
            "ad_account_id": args.ad_account_id,
            "create_ad_account": "FALSE",
            "account_name": "Main",
            "currency": "USD",
            "timezone_id": "1",
            "page_id": args.page_id,
            "approval_required": "FALSE",
        }],
        "Campaigns": [{
            "campaign_key": f"cmp_{slug}",
            "account_key": "acct_main",
            "name": f"{args.name} | Traffic",
            "objective": args.objective,
            "buying_type": "AUCTION",
            "budget_mode": "CBO",
            "daily_budget_cents": args.daily_budget_cents,
            "bid_strategy": args.bid_strategy,
            "special_ad_categories": "[]",
            "desired_status": "PAUSED",
            "approval_status": "APPROVED",
        }],
        "AdSets": [{
            "adset_key": f"adset_{slug}",
            "campaign_key": f"cmp_{slug}",
            "name": f"{args.name} | Broad",
            "optimization_goal": "LINK_CLICKS",
            "billing_event": "IMPRESSIONS",
            "countries": args.countries,
            "age_min": "18",
            "age_max": "65",
            "genders": "all",
            "placements": "automatic",
            "desired_status": "PAUSED",
            "approval_status": "APPROVED",
        }],
        "Creatives": [creative_row],
        "Ads": [{
            "ad_key": f"ad_{slug}_a",
            "adset_key": f"adset_{slug}",
            "creative_key": f"creative_{slug}_a",
            "name": f"{args.name} | Static A",
            "desired_status": "PAUSED",
            "approval_status": "APPROVED",
        }],
    }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        template = Path(tmp) / "template.xlsx"
        create_template(str(template), with_sample=False)
        save_with_updates(load_source(str(template)), str(out), [], rows)
    print(f"Wrote {out}. Next: validate, plan, then apply (see docs/AD_CREATION_WORKFLOW.md).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
