"""Deterministic performance aggregation over PerformanceSnapshots.

Handles the live/mock key mismatch: live insight rows carry Meta IDs in the
``campaign_key``/``adset_key``/``ad_key`` columns, mock rows carry workbook
keys — every join here resolves through both the key and ``meta_*_id`` columns.
"""

from __future__ import annotations

import datetime as _dt
from typing import Any

from meta_ads_pipeline.models import Dataset
from meta_ads_pipeline.schema import clean


def _float(value: Any) -> float:
    try:
        return float(clean(value))
    except ValueError:
        return 0.0


def _lookup(dataset: Dataset, table: str, key_col: str, meta_col: str) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for row in dataset.tables.get(table, []):
        for identifier in (clean(row.get(key_col)), clean(row.get(meta_col))):
            if identifier:
                index[identifier] = row
    return index


def snapshot_window(dataset: Dataset, days: int) -> list[dict[str, Any]]:
    """Non-breakdown snapshot rows within the last `days` days (undated rows included)."""
    cutoff = (_dt.date.today() - _dt.timedelta(days=days)).isoformat()
    rows = []
    for row in dataset.tables.get("PerformanceSnapshots", []):
        if clean(row.get("breakdown_type")):
            continue
        date = clean(row.get("date"))
        if date and date < cutoff:
            continue
        rows.append(row)
    return rows


def aggregate(dataset: Dataset, days: int = 7) -> dict[str, Any]:
    adsets = _lookup(dataset, "AdSets", "adset_key", "meta_adset_id")
    campaigns = _lookup(dataset, "Campaigns", "campaign_key", "meta_campaign_id")
    ads = _lookup(dataset, "Ads", "ad_key", "meta_ad_id")

    rows = snapshot_window(dataset, days)
    by_adset: dict[str, dict[str, float]] = {}
    for row in rows:
        adset_row = adsets.get(clean(row.get("adset_key")))
        if adset_row is None:
            ad_row = ads.get(clean(row.get("ad_key"))) or ads.get(clean(row.get("meta_object_id")))
            if ad_row is not None:
                adset_row = adsets.get(clean(ad_row.get("adset_key")))
        adset_key = clean(adset_row.get("adset_key")) if adset_row else clean(row.get("adset_key")) or "(unmatched)"
        bucket = by_adset.setdefault(adset_key, {"spend": 0.0, "impressions": 0.0, "clicks": 0.0, "conversions": 0.0, "purchase_value": 0.0, "rows": 0.0})
        bucket["spend"] += _float(row.get("spend"))
        bucket["impressions"] += _float(row.get("impressions"))
        bucket["clicks"] += _float(row.get("clicks"))
        bucket["conversions"] += _float(row.get("conversions"))
        bucket["purchase_value"] += _float(row.get("purchase_value"))
        bucket["rows"] += 1

    adset_entries = []
    campaign_totals: dict[str, dict[str, float]] = {}
    for adset_key, bucket in sorted(by_adset.items(), key=lambda item: -item[1]["spend"]):
        adset_row = adsets.get(adset_key, {})
        campaign_key = clean(adset_row.get("campaign_key"))
        campaign_row = campaigns.get(campaign_key, {})
        entry = _finalize(bucket)
        entry.update(
            adset_key=adset_key,
            adset_name=clean(adset_row.get("name")),
            campaign_key=campaign_key,
            campaign_name=clean(campaign_row.get("name")),
            daily_budget_cents=clean(adset_row.get("daily_budget_cents")),
            bid_amount_cents=clean(adset_row.get("bid_amount_cents")),
            status=clean(adset_row.get("desired_status")),
        )
        adset_entries.append(entry)
        totals = campaign_totals.setdefault(campaign_key or "(unmatched)", {"spend": 0.0, "impressions": 0.0, "clicks": 0.0, "conversions": 0.0, "purchase_value": 0.0, "rows": 0.0})
        for metric in ("spend", "impressions", "clicks", "conversions", "purchase_value", "rows"):
            totals[metric] += bucket[metric]

    campaign_entries = []
    for campaign_key, bucket in sorted(campaign_totals.items(), key=lambda item: -item[1]["spend"]):
        campaign_row = campaigns.get(campaign_key, {})
        entry = _finalize(bucket)
        entry.update(
            campaign_key=campaign_key,
            campaign_name=clean(campaign_row.get("name")),
            objective=clean(campaign_row.get("objective")),
            daily_budget_cents=clean(campaign_row.get("daily_budget_cents")),
            bid_strategy=clean(campaign_row.get("bid_strategy")),
        )
        campaign_entries.append(entry)

    grand = {"spend": 0.0, "impressions": 0.0, "clicks": 0.0, "conversions": 0.0, "purchase_value": 0.0, "rows": 0.0}
    for bucket in by_adset.values():
        for metric in grand:
            grand[metric] += bucket[metric]

    return {
        "window_days": days,
        "snapshot_rows_used": int(grand["rows"]),
        "totals": _finalize(grand),
        "by_campaign": campaign_entries,
        "by_adset": adset_entries,
    }


def _finalize(bucket: dict[str, float]) -> dict[str, Any]:
    spend = bucket["spend"]
    impressions = bucket["impressions"]
    clicks = bucket["clicks"]
    conversions = bucket["conversions"]
    purchase_value = bucket["purchase_value"]
    return {
        "spend": round(spend, 2),
        "impressions": int(impressions),
        "clicks": int(clicks),
        "ctr": round(clicks / impressions, 4) if impressions else 0.0,
        "cpc": round(spend / clicks, 2) if clicks else 0.0,
        "cpm": round(spend / impressions * 1000, 2) if impressions else 0.0,
        "conversions": int(conversions),
        "cost_per_result": round(spend / conversions, 2) if conversions else 0.0,
        "roas": round(purchase_value / spend, 2) if spend and purchase_value else 0.0,
    }
