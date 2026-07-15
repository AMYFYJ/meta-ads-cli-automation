from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from .models import Dataset, ValidationIssue
from .schema import (
    AUDIENCE_TYPES,
    AUDIENCE_UPLOAD_OPERATIONS,
    BUDGET_MODES,
    BULK_OPERATIONS,
    CREATE_TABLES,
    OBJECT_CONFIG,
    OBJECTIVES,
    OPTIMIZATION_OPERATORS,
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
    audience_keys = _keys(dataset, "Audiences")
    campaign_keys = _keys(dataset, "Campaigns")
    adset_keys = _keys(dataset, "AdSets")

    for row in dataset.tables.get("Campaigns", []):
        key = row_key("Campaigns", row)
        if clean(row.get("account_key")) not in account_keys:
            issues.append(ValidationIssue("ERROR", "Campaigns", key, "account_key", "No matching Accounts row."))
    for row in dataset.tables.get("AdSets", []):
        key = row_key("AdSets", row)
        if clean(row.get("campaign_key")) not in campaign_keys:
            issues.append(ValidationIssue("ERROR", "AdSets", key, "campaign_key", "No matching Campaigns row."))
    for row in dataset.tables.get("Audiences", []):
        key = row_key("Audiences", row)
        if clean(row.get("account_key")) not in account_keys:
            issues.append(ValidationIssue("ERROR", "Audiences", key, "account_key", "No matching Accounts row."))
        source_key = clean(row.get("source_audience_key"))
        if source_key and source_key not in audience_keys:
            issues.append(ValidationIssue("ERROR", "Audiences", key, "source_audience_key", "No matching source Audiences row."))
    for row in dataset.tables.get("Ads", []):
        key = row_key("Ads", row)
        if clean(row.get("adset_key")) not in adset_keys:
            issues.append(ValidationIssue("ERROR", "Ads", key, "adset_key", "No matching AdSets row."))
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
    audience_rows = {row_key("Audiences", row): row for row in dataset.tables.get("Audiences", [])}
    for row in dataset.tables.get("AdSets", []):
        key = row_key("AdSets", row)
        _budget_guard(issues, "AdSets", key, "daily_budget_cents", row.get("daily_budget_cents"))
        _budget_guard(issues, "AdSets", key, "lifetime_budget_cents", row.get("lifetime_budget_cents"))
        _budget_guard(issues, "AdSets", key, "bid_amount_cents", row.get("bid_amount_cents"))
        _status_guard(issues, "AdSets", key, row.get("desired_status"))
        _date_guard(issues, "AdSets", key, row.get("start_time"), row.get("end_time"))
        _json_guard(issues, "AdSets", key, "targeting_json", row.get("targeting_json"))
        _json_guard(issues, "AdSets", key, "targeting_automation_json", row.get("targeting_automation_json"))
        _json_guard(issues, "AdSets", key, "promoted_object_json", row.get("promoted_object_json"))
        _targeting_columns_guard(issues, "AdSets", key, row)
        for field in ("custom_audiences", "excluded_audiences"):
            _audience_refs_guard(issues, "AdSets", key, field, row.get(field), audience_rows)
        _approval_guard(issues, "AdSets", key, row)
    for row in dataset.tables.get("Audiences", []):
        key = row_key("Audiences", row)
        audience_type = clean(row.get("audience_type")).upper()
        if audience_type and audience_type not in AUDIENCE_TYPES:
            issues.append(ValidationIssue("ERROR", "Audiences", key, "audience_type", f"Unsupported audience type: {audience_type}."))
        if audience_type == "LOOKALIKE" and is_blank(row.get("source_audience_key")):
            issues.append(ValidationIssue("ERROR", "Audiences", key, "source_audience_key", "Lookalike audiences require source_audience_key."))
        if audience_type == "WEBSITE" and is_blank(row.get("rule_json")):
            issues.append(ValidationIssue("ERROR", "Audiences", key, "rule_json", "Website audiences require rule_json."))
        ratio = to_int(row.get("lookalike_ratio"))
        if not is_blank(row.get("lookalike_ratio")) and ratio is None:
            issues.append(ValidationIssue("ERROR", "Audiences", key, "lookalike_ratio", "Lookalike ratio must be numeric."))
        _json_guard(issues, "Audiences", key, "targeting_json", row.get("targeting_json"))
        _json_guard(issues, "Audiences", key, "rule_json", row.get("rule_json"))
        _json_guard(issues, "Audiences", key, "lookalike_spec_json", row.get("lookalike_spec_json"))
        _audience_upload_guard(issues, key, row, source_dir)
        _approval_guard(issues, "Audiences", key, row)
    for row in dataset.tables.get("OptimizationRules", []):
        key = row_key("OptimizationRules", row)
        if clean(row.get("operator")) not in OPTIMIZATION_OPERATORS:
            issues.append(ValidationIssue("ERROR", "OptimizationRules", key, "operator", "Use >, >=, <, <=, ==, or !=."))
        if to_int(row.get("max_actions")) is None and not is_blank(row.get("max_actions")):
            issues.append(ValidationIssue("ERROR", "OptimizationRules", key, "max_actions", "max_actions must be numeric when provided."))
        try:
            float(clean(row.get("threshold")))
        except ValueError:
            issues.append(ValidationIssue("ERROR", "OptimizationRules", key, "threshold", "threshold must be numeric."))
        if not approved(row.get("approval_status")):
            issues.append(ValidationIssue("INFO", "OptimizationRules", key, "approval_status", "Optimization rule is not approved and will not generate changes."))
    for row in dataset.tables.get("Ads", []):
        key = row_key("Ads", row)
        _status_guard(issues, "Ads", key, row.get("desired_status"))
        destination = clean(row.get("destination_url"))
        if destination and not destination.startswith(("https://", "http://")):
            issues.append(ValidationIssue("ERROR", "Ads", key, "destination_url", "Destination URL must start with http:// or https://."))
        _approval_guard(issues, "Ads", key, row)
    return issues


def _validate_assets(dataset: Dataset) -> list[ValidationIssue]:
    """Creative fields live inline on Ads rows; they are only required when the
    row does not already reference an existing creative via meta_creative_id."""
    issues: list[ValidationIssue] = []
    source_dir = _source_dir(dataset)
    for row in dataset.tables.get("Ads", []):
        key = row_key("Ads", row)
        if not is_blank(row.get("meta_creative_id")):
            continue
        creative_format = clean(row.get("format")).lower()
        if creative_format not in {"image", "video", "dco"}:
            issues.append(ValidationIssue("ERROR", "Ads", key, "format", "Use image, video, or dco."))
        for field in ("primary_text", "headline", "cta", "destination_url"):
            if is_blank(row.get(field)):
                issues.append(ValidationIssue("ERROR", "Ads", key, field, "Required to build this ad's creative (or set meta_creative_id to reuse one)."))
        asset = clean(row.get("asset_path_or_url"))
        if (
            creative_format in {"image", "video"}
            and is_blank(asset)
            and is_blank(row.get("image_hash_or_video_id"))
        ):
            issues.append(ValidationIssue("ERROR", "Ads", key, "asset_path_or_url", "Asset path/URL or uploaded media ID/hash is required."))
        if asset and not asset.startswith(("http://", "https://")):
            path = Path(asset)
            if not path.is_absolute():
                path = source_dir / path
            if not path.exists():
                issues.append(ValidationIssue("ERROR", "Ads", key, "asset_path_or_url", f"Asset does not exist: {path}."))
    return issues


def _audience_upload_guard(issues: list[ValidationIssue], key: str, row: dict[str, Any], source_dir: Path) -> None:
    operation = clean(row.get("upload_operation")).upper()
    data_path = clean(row.get("upload_data_path"))
    has_data = bool(data_path) or not is_blank(row.get("upload_data_json"))
    if operation and operation not in AUDIENCE_UPLOAD_OPERATIONS:
        issues.append(ValidationIssue("ERROR", "Audiences", key, "upload_operation", "Use ADD, REMOVE, or REPLACE."))
    if not has_data:
        if operation or not is_blank(row.get("upload_schema")):
            issues.append(ValidationIssue("ERROR", "Audiences", key, "upload_data_path", "Provide upload_data_path or upload_data_json."))
        return
    if is_blank(row.get("upload_schema")):
        issues.append(ValidationIssue("ERROR", "Audiences", key, "upload_schema", "upload_schema is required, e.g. EMAIL,FN,LN."))
    if data_path:
        path = Path(data_path)
        if not path.is_absolute():
            path = source_dir / path
        if not path.exists():
            issues.append(ValidationIssue("ERROR", "Audiences", key, "upload_data_path", f"Upload file does not exist: {path}."))
    _json_guard(issues, "Audiences", key, "upload_data_json", row.get("upload_data_json"))


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
        operation = clean(row.get("operation")).upper() or "SET_FIELD"
        if operation not in BULK_OPERATIONS:
            issues.append(ValidationIssue("ERROR", "BulkChanges", key, "operation", f"Unsupported operation: {operation}."))
        if object_level not in UPDATABLE_FIELDS:
            issues.append(ValidationIssue("ERROR", "BulkChanges", key, "object_level", "Object level must be campaign, adset, creative, or ad."))
            continue
        if operation in {"SET_FIELD", "REPLACE_CREATIVE", "REPLACE_TARGETING"} and field not in UPDATABLE_FIELDS[object_level]:
            issues.append(ValidationIssue("ERROR", "BulkChanges", key, "field", f"Field is not bulk-editable for {object_level}."))
        if operation in {"SET_FIELD", "REPLACE_CREATIVE", "REPLACE_TARGETING"} and is_blank(row.get("new_value")):
            issues.append(ValidationIssue("ERROR", "BulkChanges", key, "new_value", "new_value is required for this operation."))
        if operation == "PATCH_JSON":
            _json_guard(issues, "BulkChanges", key, "value_json", row.get("value_json"))
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


def _json_guard(issues: list[ValidationIssue], table: str, key: str, field: str, value: Any) -> None:
    text = clean(value)
    if not text:
        return
    import json

    try:
        json.loads(text)
    except json.JSONDecodeError:
        issues.append(ValidationIssue("ERROR", table, key, field, "Value must be valid JSON."))


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


_GENDER_VALUES = {"all", "male", "m", "1", "female", "f", "2"}


def _targeting_columns_guard(issues: list[ValidationIssue], table: str, key: str, row: dict[str, Any]) -> None:
    """Audience-targeting column checks for AdSets rows."""
    age_min = clean(row.get("age_min"))
    age_max = clean(row.get("age_max"))
    parsed_min = to_int(age_min) if age_min else None
    parsed_max = to_int(age_max) if age_max else None
    if age_min and (parsed_min is None or not 13 <= parsed_min <= 65):
        issues.append(ValidationIssue("ERROR", table, key, "age_min", "age_min must be a number between 13 and 65."))
    if age_max and (parsed_max is None or not 13 <= parsed_max <= 65):
        issues.append(ValidationIssue("ERROR", table, key, "age_max", "age_max must be a number between 13 and 65 (65 means 65+)."))
    if parsed_min is not None and parsed_max is not None and parsed_max < parsed_min:
        issues.append(ValidationIssue("ERROR", table, key, "age_max", "age_max must be >= age_min."))
    for gender in _csv_values(row.get("genders")):
        if gender.lower() not in _GENDER_VALUES:
            issues.append(ValidationIssue("ERROR", table, key, "genders", f"Unsupported gender '{gender}'. Use all, male, or female."))
    for field in ("interests", "behaviors"):
        for entry in _csv_values(row.get(field)):
            entry_id = entry.split(":", 1)[0].strip()
            if not entry_id.isdigit():
                issues.append(
                    ValidationIssue(
                        "ERROR", table, key, field,
                        f"'{entry}' needs a numeric Meta targeting ID, written as <id> or <id>:<Name>. "
                        "Find IDs with: python -m meta_ads_pipeline targeting-search --q \"<term>\".",
                    )
                )
    for country in _csv_values(row.get("countries")):
        if not (len(country) == 2 and country.isalpha()):
            issues.append(ValidationIssue("WARNING", table, key, "countries", f"'{country}' does not look like a 2-letter ISO country code (UK is GB)."))
    _json_guard(issues, table, key, "flexible_spec_json", row.get("flexible_spec_json"))
    _json_guard(issues, table, key, "exclusions_json", row.get("exclusions_json"))
    _json_shape_guard(issues, table, key, "flexible_spec_json", row.get("flexible_spec_json"), (list, dict), "a JSON list of {interests/behaviors/...} groups")
    _json_shape_guard(issues, table, key, "exclusions_json", row.get("exclusions_json"), (dict,), "a JSON object like {\"interests\": [...]}")


def _audience_refs_guard(
    issues: list[ValidationIssue],
    table: str,
    key: str,
    field: str,
    value: Any,
    audience_rows: dict[str, dict[str, Any]],
) -> None:
    for entry in _csv_values(value):
        audience = audience_rows.get(entry)
        if audience is None:
            if not _looks_like_meta_id(entry):
                issues.append(
                    ValidationIssue(
                        "ERROR", table, key, field,
                        f"'{entry}' is neither an audience_key from the Audiences sheet nor a numeric Meta audience ID.",
                    )
                )
        elif is_blank(audience.get("meta_audience_id")) and not approved(audience.get("approval_status")):
            issues.append(
                ValidationIssue(
                    "WARNING", table, key, field,
                    f"Audience '{entry}' has no Meta ID and is not approved, so this ad set would be skipped at apply time.",
                )
            )


def _json_shape_guard(
    issues: list[ValidationIssue],
    table: str,
    key: str,
    field: str,
    value: Any,
    allowed_types: tuple[type, ...],
    expected: str,
) -> None:
    text = clean(value)
    if not text:
        return
    import json

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return  # _json_guard already reported it
    if not isinstance(parsed, allowed_types):
        issues.append(ValidationIssue("ERROR", table, key, field, f"Value must be {expected}."))


def _looks_like_meta_id(value: str) -> bool:
    return value.startswith(("act_", "cmp_", "adset_", "crt_", "ad_", "mock_", "fb_")) or value.isdigit()


def _csv_values(value: Any) -> list[str]:
    return [part.strip() for part in clean(value).split(",") if part.strip()]
