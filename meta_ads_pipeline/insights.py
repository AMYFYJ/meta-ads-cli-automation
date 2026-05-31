from __future__ import annotations

import json
import os
import random
import subprocess
from datetime import date, timedelta
from typing import Any

from .adapters import _extract_id, utc_now
from .models import Dataset
from .schema import clean


def generate_mock_insights(dataset: Dataset, breakdowns: list[str] | None = None) -> dict[str, list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    breakdowns = breakdowns or []
    today = date.today()
    for ad in dataset.tables.get("Ads", []):
        ad_id = clean(ad.get("meta_ad_id"))
        if not ad_id:
            continue
        ad_key = clean(ad.get("ad_key"))
        adset_key = clean(ad.get("adset_key"))
        campaign_key = _campaign_for_adset(dataset, adset_key)
        seed = int(sum(ord(char) for char in ad_key))
        rng = random.Random(seed)
        spend = round(rng.uniform(35, 180), 2)
        impressions = rng.randint(8000, 42000)
        clicks = rng.randint(120, 1200)
        conversions = rng.randint(4, 70)
        purchase_value = round(conversions * rng.uniform(42, 95), 2)
        base = _mock_insight_row(
            snapshot_key=f"snap_{ad_key}_{today.isoformat()}",
            snapshot_date=(today - timedelta(days=1)).isoformat(),
            object_level="ad",
            meta_object_id=ad_id,
            campaign_key=campaign_key,
            adset_key=adset_key,
            ad_key=ad_key,
            spend=spend,
            impressions=impressions,
            clicks=clicks,
            conversions=conversions,
            purchase_value=purchase_value,
            rng=rng,
        )
        rows.append(base)
        for breakdown in breakdowns:
            rows.extend(_mock_breakdown_rows(base, breakdown, rng))
    return {"PerformanceSnapshots": rows}


def get_live_insights(date_preset: str = "last_7d", level: str = "ad", breakdowns: list[str] | None = None) -> tuple[bool, str, list[dict[str, Any]]]:
    breakdowns = breakdowns or []
    command = [
        os.environ.get("META_CLI_BIN", "meta"),
        "--output",
        "json",
        "--no-input",
        "ads",
        "insights",
        "get",
        "--date-preset",
        date_preset,
        "--level",
        level,
    ]
    for breakdown in breakdowns:
        command.extend(["--breakdown", breakdown])
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        return False, completed.stderr, []
    try:
        parsed = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return False, "Meta CLI returned non-JSON insights output.", []
    data = parsed.get("data", parsed if isinstance(parsed, list) else [])
    rows = []
    for idx, item in enumerate(data if isinstance(data, list) else [], start=1):
        meta_id = str(item.get(f"{level}_id", item.get("ad_id", _extract_id(json.dumps(item)))))
        spend = _float(item.get("spend"))
        clicks = _float(item.get("clicks"))
        impressions = _float(item.get("impressions"))
        rows.append(
            {
                "snapshot_key": f"live_{level}_{idx}_{utc_now()}",
                "date": clean(item.get("date_stop")) or clean(item.get("date_start")),
                "object_level": level,
                "meta_object_id": meta_id,
                "campaign_key": clean(item.get("campaign_id")),
                "adset_key": clean(item.get("adset_id")),
                "ad_key": clean(item.get("ad_id")),
                "breakdown_type": ",".join(breakdowns),
                "breakdown_value": _breakdown_value(item, breakdowns),
                "publisher_platform": clean(item.get("publisher_platform")),
                "platform_position": clean(item.get("platform_position")),
                "country": clean(item.get("country")),
                "region": clean(item.get("region")),
                "age": clean(item.get("age")),
                "gender": clean(item.get("gender")),
                "device_platform": clean(item.get("device_platform")),
                "spend": spend,
                "impressions": impressions,
                "clicks": clicks,
                "ctr": _float(item.get("ctr")),
                "cpc": round(spend / clicks, 2) if clicks else "",
                "cpm": round(spend / impressions * 1000, 2) if impressions else "",
                "conversions": clean(item.get("conversions")),
                "cost_per_result": clean(item.get("cost_per_result")),
            }
        )
    return True, completed.stdout, rows


def _campaign_for_adset(dataset: Dataset, adset_key: str) -> str:
    for adset in dataset.tables.get("AdSets", []):
        if clean(adset.get("adset_key")) == adset_key:
            return clean(adset.get("campaign_key"))
    return ""


def _mock_insight_row(
    snapshot_key: str,
    snapshot_date: str,
    object_level: str,
    meta_object_id: str,
    campaign_key: str,
    adset_key: str,
    ad_key: str,
    spend: float,
    impressions: int,
    clicks: int,
    conversions: int,
    purchase_value: float,
    rng: random.Random,
) -> dict[str, Any]:
    return {
        "snapshot_key": snapshot_key,
        "date": snapshot_date,
        "object_level": object_level,
        "meta_object_id": meta_object_id,
        "campaign_key": campaign_key,
        "adset_key": adset_key,
        "ad_key": ad_key,
        "breakdown_type": "",
        "breakdown_value": "",
        "publisher_platform": "",
        "platform_position": "",
        "country": "",
        "region": "",
        "age": "",
        "gender": "",
        "device_platform": "",
        "spend": spend,
        "impressions": impressions,
        "reach": int(impressions * rng.uniform(0.72, 0.92)),
        "frequency": round(rng.uniform(1.1, 2.8), 2),
        "clicks": clicks,
        "link_clicks": int(clicks * rng.uniform(0.78, 0.96)),
        "landing_page_views": int(clicks * rng.uniform(0.60, 0.86)),
        "ctr": round(clicks / impressions, 4),
        "cpc": round(spend / clicks, 2),
        "cpm": round(spend / impressions * 1000, 2),
        "conversions": conversions,
        "cost_per_result": round(spend / conversions, 2) if conversions else "",
        "purchases": conversions,
        "purchase_value": purchase_value,
        "roas": round(purchase_value / spend, 2) if spend else "",
        "add_to_cart": int(conversions * rng.uniform(2.2, 4.5)),
        "initiate_checkout": int(conversions * rng.uniform(1.3, 2.4)),
        "leads": "",
        "cpl": "",
        "budget_utilization": round(rng.uniform(0.65, 1.08), 2),
        "pacing_ratio": round(rng.uniform(0.82, 1.16), 2),
    }


def _mock_breakdown_rows(base: dict[str, Any], breakdown: str, rng: random.Random) -> list[dict[str, Any]]:
    values_by_breakdown = {
        "publisher_platform": ["facebook", "instagram"],
        "platform_position": ["feed", "story", "reels"],
        "country": ["US", "CA"],
        "region": ["California", "New York"],
        "age": ["25-34", "35-44", "45-54"],
        "gender": ["female", "male"],
        "device_platform": ["mobile", "desktop"],
    }
    values = values_by_breakdown.get(breakdown, [breakdown])
    rows = []
    for idx, value in enumerate(values, start=1):
        row = dict(base)
        row["snapshot_key"] = f"{base['snapshot_key']}_{breakdown}_{idx}"
        row["breakdown_type"] = breakdown
        row["breakdown_value"] = value
        if breakdown in row:
            row[breakdown] = value
        factor = rng.uniform(0.25, 0.7)
        row["spend"] = round(float(base["spend"]) * factor, 2)
        row["impressions"] = int(float(base["impressions"]) * factor)
        row["clicks"] = int(float(base["clicks"]) * factor)
        row["conversions"] = max(1, int(float(base["conversions"]) * factor))
        row["cost_per_result"] = round(row["spend"] / row["conversions"], 2)
        rows.append(row)
    return rows


def _breakdown_value(item: dict[str, Any], breakdowns: list[str]) -> str:
    return "|".join(clean(item.get(breakdown)) for breakdown in breakdowns if clean(item.get(breakdown)))


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
