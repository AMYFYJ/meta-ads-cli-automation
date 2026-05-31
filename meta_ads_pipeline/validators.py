from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from .models import Dataset, ValidationIssue
from .schema import (
    BUDGET_MODES,
    CREATE_TABLES,
    OBJECT_CONFIG,
    OBJECTIVES,
    STATUSES,
    TABLES,
    UPDATABLE_FIELDS,
    approved,
    clean,
    is_blank,
    row_key,
    to_int,
    truthy,
)


def validate_dataset(dataset: Dataset) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    issues.extend(_validate_required(dataset))
    issues.extend(_validate_uniqueness(dataset))
    issues.extend(_validate_relationships(dataset))
    issues.extend(_validate_values(dataset))
    issues.extend(_validate_assets(dataset))
    issues.extend(_validate_bulk_changes(dataset))
    return issues


def has_blocking_errors(issues: list[ValidationIssue]) -> bool:
    return any(issue.severity.upper() == "ERROR" for issue in issues)


def _validate_required(dataset: Dataset) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for table_name, spec in TABLES.items():
        for row in dataset.tables.get(table_name, []):
            key = row_key(table_name, row)
            for field in spec.required:
                if is_blank(row.get(field)):
                    issues.append(ValidationIssue("ERROR", table_name, key, field, "Required field is blank."))
    return issues


def _validate_uniqueness(dataset: Dataset) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for table_name, spec in TABLES.items():
        seen: set[str] = set()
        for row in dataset.tables.get(table_name, []):
            key = row_key(table_name, row)
            if not key:
                continue
            if key in seen:
                issues.append(ValidationIssue("ERROR", table_name, key, spec.key_column, "Duplicate key."))
            seen.add(key)
    return issues


def _validate_relationships(dataset: Dataset) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    account_keys = _keys(dataset, "Accounts")
    campaign_keys = _keys(dataset, "Campaigns")
    adset_keys = _keys(dataset, "AdSets")
    creative_keys = _keys(dataset, "Creatives")

    for row in dataset.tables.get("Campaigns", []):
        key = row_key("Campaigns", row)
        if clean(row.get("account_key")) not in account_keys:
            issues.append(ValidationIssue("ERROR", "Campaigns", key, "account_key", "No matching Accounts row."))
    for row in dataset.tables.get("AdSets", []):
        key = row_key("AdSets", row)
        if clean(row.get("campaign_key")) not in campaign_keys:
            issues.append(ValidationIssue("ERROR", "AdSets", key, "campaign_key", "No matching Campaigns row."))
    for row in dataset.tables.get("Creatives", []):
        key = row_key("Creatives", row)
        if clean(row.get("account_key")) not in account_keys:
            issues.append(ValidationIssue("ERROR", "Creatives", key, "account_key", "No matching Accounts row."))
    for row in dataset.tables.get("Ads", []):
        key = row_key("Ads", row)
        if clean(row.get("adset_key")) not in adset_keys:
            issues.append(ValidationIssue("ERROR", "Ads", key, "adset_key", "No matching AdSets row."))
        if clean(row.get("creative_key")) not in creative_keys:
            issues.append(ValidationIssue("ERROR", "Ads", key, "creative_key", "No matching Creatives row."))
    return issues


def _validate_values(dataset: Dataset) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    source_dir = _source_dir(dataset)
    for row in dataset.tables.get("Accounts", []):
        key = row_key("Accounts", row)
        if is_blank(row.get("ad_account_id")) and truthy(row.get("create_ad_account")) and is_blank(row.get("business_id")):
            issues.append(ValidationIssue("ERROR", "Accounts", key, "business_id", "Business ID is required to create an ad account."))
        _budget_guard(issues, "Accounts", key, "max_daily_budget_cents", row.get("max_daily_budget_cents"))
    for row in dataset.tables.get("Campaigns", []):
        key = row_key("Campaigns", row)
        objective = clean(row.get("objective")).upper()
        if objective and objective not in OBJECTIVES:
            issues.append(ValidationIssue("ERROR", "Campaigns", key, "objective", f"Unsupported objective: {objective}."))
        budget_mode = clean(row.get("budget_mode")).upper()
        if budget_mode and budget_mode not in BUDGET_MODES:
            issues.append(ValidationIssue("ERROR", "Campaigns", key, "budget_mode", "Use CBO or ABO."))
        status = clean(row.get("desired_status")).upper()
        if status and status not in STATUSES:
            issues.append(ValidationIssue("ERROR", "Campaigns", key, "desired_status", f"Unsupported status: {status}."))
        _budget_guard(issues, "Campaigns", key, "daily_budget_cents", row.get("daily_budget_cents"))
        _budget_guard(issues, "Campaigns", key, "lifetime_budget_cents", row.get("lifetime_budget_cents"))
        _approval_guard(issues, "Campaigns", key, row)
    for row in dataset.tables.get("AdSets", []):
        key = row_key("AdSets", row)
        _budget_guard(issues, "AdSets", key, "daily_budget_cents", row.get("daily_budget_cents"))
        _budget_guard(issues, "AdSets", key, "lifetime_budget_cents", row.get("lifetime_budget_cents"))
        _budget_guard(issues, "AdSets", key, "bid_amount_cents", row.get("bid_amount_cents"))
        _status_guard(issues, "AdSets", key, row.get("desired_status"))
        _date_guard(issues, "AdSets", key, row.get("start_time"), row.get("end_time"))
        _approval_guard(issues, "AdSets", key, row)
    for row in dataset.tables.get("Creatives", []):
        key = row_key("Creatives", row)
        destination = clean(row.get("destination_url"))
        if destination and not destination.startswith(("https://", "http://")):
            issues.append(ValidationIssue("ERROR", "Creatives", key, "destination_url", "Destination URL must start with http:// or https://."))
        _approval_guard(issues, "Creatives", key, row)
        asset = clean(row.get("asset_path_or_url"))
        if asset and not asset.startswith(("http://", "https://")):
            path = Path(asset)
            if not path.is_absolute():
                path = source_dir / path
            if not path.exists():
                issues.append(ValidationIssue("ERROR", "Creatives", key, "asset_path_or_url", f"Asset does not exist: {path}."))
    for row in dataset.tables.get("Ads", []):
        key = row_key("Ads", row)
        _status_guard(issues, "Ads", key, row.get("desired_status"))
        _approval_guard(issues, "Ads", key, row)
    return issues


def _validate_assets(dataset: Dataset) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for row in dataset.tables.get("Creatives", []):
        key = row_key("Creatives", row)
        creative_format = clean(row.get("format")).lower()
        if creative_format not in {"image", "video", "dco"}:
            issues.append(ValidationIssue("ERROR", "Creatives", key, "format", "Use image, video, or dco."))
        if creative_format in {"image", "video"} and is_blank(row.get("asset_path_or_url")) and is_blank(row.get("image_hash_or_video_id")):
            issues.append(ValidationIssue("ERROR", "Creatives", key, "asset_path_or_url", "Asset path/URL or uploaded media ID/hash is required."))
    return issues


def _validate_bulk_changes(dataset: Dataset) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    keys_by_object = {
        object_type: _keys(dataset, table_name)
        for object_type, (table_name, _, _) in OBJECT_CONFIG.items()
    }
    for row in dataset.tables.get("BulkChanges", []):
        key = row_key("BulkChanges", row)
        if clean(row.get("applied_at")):
            continue
        object_level = clean(row.get("object_level")).lower()
        field = clean(row.get("field"))
        target = clean(row.get("object_key_or_meta_id"))
        if object_level not in UPDATABLE_FIELDS:
            issues.append(ValidationIssue("ERROR", "BulkChanges", key, "object_level", "Object level must be campaign, adset, creative, or ad."))
            continue
        if field not in UPDATABLE_FIELDS[object_level]:
            issues.append(ValidationIssue("ERROR", "BulkChanges", key, "field", f"Field is not bulk-editable for {object_level}."))
        if target and target not in keys_by_object.get(object_level, set()) and not _looks_like_meta_id(target):
            issues.append(ValidationIssue("WARNING", "BulkChanges", key, "object_key_or_meta_id", "Target is not a known row key; treating it as an existing Meta ID."))
        if not approved(row.get("approval_status")):
            issues.append(ValidationIssue("INFO", "BulkChanges", key, "approval_status", "Change is not approved and will not apply."))
    return issues


def _keys(dataset: Dataset, table_name: str) -> set[str]:
    return {row_key(table_name, row) for row in dataset.tables.get(table_name, []) if row_key(table_name, row)}


def _source_dir(dataset: Dataset) -> Path:
    path = Path(dataset.path)
    return path if dataset.kind == "csv_dir" else path.parent


def _budget_guard(issues: list[ValidationIssue], table: str, key: str, field: str, value: Any) -> None:
    if is_blank(value):
        return
    parsed = to_int(value)
    if parsed is None or parsed < 0:
        issues.append(ValidationIssue("ERROR", table, key, field, "Budget/bid values must be non-negative integer minor units, e.g. cents."))


def _status_guard(issues: list[ValidationIssue], table: str, key: str, value: Any) -> None:
    status = clean(value).upper()
    if status and status not in STATUSES:
        issues.append(ValidationIssue("ERROR", table, key, "desired_status", f"Unsupported status: {status}."))


def _date_guard(issues: list[ValidationIssue], table: str, key: str, start: Any, end: Any) -> None:
    start_text = clean(start)
    end_text = clean(end)
    start_dt = _parse_iso(start_text)
    end_dt = _parse_iso(end_text)
    if start_text and not start_dt:
        issues.append(ValidationIssue("ERROR", table, key, "start_time", "Use ISO date-time format, e.g. 2026-06-03T09:00:00-04:00."))
    if end_text and not end_dt:
        issues.append(ValidationIssue("ERROR", table, key, "end_time", "Use ISO date-time format, e.g. 2026-06-17T23:59:00-04:00."))
    if start_dt and end_dt and end_dt <= start_dt:
        issues.append(ValidationIssue("ERROR", table, key, "end_time", "End time must be after start time."))


def _parse_iso(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _approval_guard(issues: list[ValidationIssue], table: str, key: str, row: dict[str, Any]) -> None:
    if table not in CREATE_TABLES:
        return
    if not approved(row.get("approval_status")):
        issues.append(ValidationIssue("INFO", table, key, "approval_status", "Row is not approved and will not be created."))


def _looks_like_meta_id(value: str) -> bool:
    return value.startswith(("act_", "cmp_", "adset_", "crt_", "ad_", "mock_", "fb_")) or value.isdigit()
