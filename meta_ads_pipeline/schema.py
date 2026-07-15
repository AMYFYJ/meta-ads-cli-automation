from __future__ import annotations

from dataclasses import dataclass
from typing import Any


TRUE_VALUES = {"1", "true", "yes", "y", "on", "approved"}
FALSE_VALUES = {"0", "false", "no", "n", "off", ""}


@dataclass(frozen=True)
class TableSpec:
    name: str
    key_column: str
    columns: list[str]
    required: list[str]


TABLES: dict[str, TableSpec] = {
    "Accounts": TableSpec(
        name="Accounts",
        key_column="account_key",
        columns=[
            "account_key",
            "business_id",
            "ad_account_id",
            "create_ad_account",
            "account_name",
            "currency",
            "timezone_id",
            "page_id",
            "instagram_actor_id",
            "pixel_dataset_id",
            "catalog_id",
            "status",
            "owner",
            "max_daily_budget_cents",
            "approval_required",
            "last_result",
            "last_error",
            "updated_at",
        ],
        required=["account_key", "account_name", "currency", "timezone_id"],
    ),
    "Campaigns": TableSpec(
        name="Campaigns",
        key_column="campaign_key",
        columns=[
            "campaign_key",
            "account_key",
            "meta_campaign_id",
            "name",
            "objective",
            "buying_type",
            "budget_mode",
            "daily_budget_cents",
            "lifetime_budget_cents",
            "bid_strategy",
            "special_ad_categories",
            "desired_status",
            "launch_batch",
            "approval_status",
            "last_result",
            "last_error",
            "updated_at",
        ],
        required=["campaign_key", "account_key", "name", "objective", "budget_mode", "desired_status"],
    ),
    "AdSets": TableSpec(
        name="AdSets",
        key_column="adset_key",
        columns=[
            "adset_key",
            "campaign_key",
            "meta_adset_id",
            "name",
            "optimization_goal",
            "billing_event",
            "daily_budget_cents",
            "lifetime_budget_cents",
            "bid_amount_cents",
            "bid_strategy",
            "start_time",
            "end_time",
            "countries",
            "regions",
            "cities",
            "zips",
            "age_min",
            "age_max",
            "genders",
            "languages",
            "interests",
            "behaviors",
            "flexible_spec_json",
            "exclusions_json",
            "custom_audiences",
            "excluded_audiences",
            "placements",
            "facebook_positions",
            "instagram_positions",
            "device_platforms",
            "advantage_audience",
            "detailed_targeting_expansion",
            "custom_audience_expansion",
            "advantage_placements",
            "targeting_json",
            "targeting_automation_json",
            "promoted_object_json",
            "pixel_dataset_id",
            "pixel_event",
            "attribution_window",
            "desired_status",
            "approval_status",
            "last_result",
            "last_error",
            "updated_at",
        ],
        required=["adset_key", "campaign_key", "name", "optimization_goal", "billing_event", "countries", "desired_status"],
    ),
    "Ads": TableSpec(
        name="Ads",
        key_column="ad_key",
        columns=[
            "ad_key",
            "adset_key",
            "meta_ad_id",
            "meta_creative_id",
            "name",
            "format",
            "asset_path_or_url",
            "image_hash_or_video_id",
            "primary_text",
            "headline",
            "description",
            "cta",
            "destination_url",
            "url_tags",
            "utm_template",
            "page_id",
            "instagram_actor_id",
            "tracking_specs",
            "variant_label",
            "desired_status",
            "launch_batch",
            "approval_status",
            "last_result",
            "last_error",
            "updated_at",
        ],
        required=["ad_key", "adset_key", "name", "desired_status"],
    ),
    "Audiences": TableSpec(
        name="Audiences",
        key_column="audience_key",
        columns=[
            "audience_key",
            "account_key",
            "meta_audience_id",
            "name",
            "audience_type",
            "subtype",
            "description",
            "source_audience_key",
            "pixel_dataset_id",
            "retention_days",
            "countries",
            "lookalike_ratio",
            "targeting_json",
            "rule_json",
            "lookalike_spec_json",
            "customer_file_source",
            "upload_operation",
            "upload_schema",
            "upload_data_path",
            "upload_data_json",
            "upload_hash_type",
            "upload_applied_at",
            "upload_result",
            "upload_error",
            "approval_status",
            "last_result",
            "last_error",
            "updated_at",
        ],
        required=["audience_key", "account_key", "name", "audience_type", "approval_status"],
    ),
    "BulkChanges": TableSpec(
        name="BulkChanges",
        key_column="change_id",
        columns=[
            "change_id",
            "operation",
            "object_level",
            "object_key_or_meta_id",
            "field",
            "old_value",
            "new_value",
            "value_json",
            "effective_at",
            "reason",
            "requested_by",
            "approval_status",
            "applied_at",
            "result",
            "error",
        ],
        required=["change_id", "object_level", "object_key_or_meta_id", "approval_status"],
    ),
    "OptimizationRules": TableSpec(
        name="OptimizationRules",
        key_column="rule_key",
        columns=[
            "rule_key",
            "scope_level",
            "metric",
            "operator",
            "threshold",
            "action_object_level",
            "action_field",
            "action_value",
            "max_actions",
            "approval_status",
            "generated_change_prefix",
            "last_result",
            "last_error",
            "updated_at",
        ],
        required=["rule_key", "scope_level", "metric", "operator", "threshold", "action_object_level", "action_field", "action_value", "approval_status"],
    ),
    "PerformanceSnapshots": TableSpec(
        name="PerformanceSnapshots",
        key_column="snapshot_key",
        columns=[
            "snapshot_key",
            "date",
            "object_level",
            "meta_object_id",
            "campaign_key",
            "adset_key",
            "ad_key",
            "breakdown_type",
            "breakdown_value",
            "publisher_platform",
            "platform_position",
            "country",
            "region",
            "age",
            "gender",
            "device_platform",
            "spend",
            "impressions",
            "reach",
            "frequency",
            "clicks",
            "link_clicks",
            "landing_page_views",
            "ctr",
            "cpc",
            "cpm",
            "conversions",
            "cost_per_result",
            "purchases",
            "purchase_value",
            "roas",
            "add_to_cart",
            "initiate_checkout",
            "leads",
            "cpl",
            "budget_utilization",
            "pacing_ratio",
        ],
        required=[],
    ),
    "PublishLog": TableSpec(
        name="PublishLog",
        key_column="log_id",
        columns=[
            "log_id",
            "timestamp",
            "mode",
            "action_id",
            "operation",
            "object_type",
            "object_key",
            "meta_id",
            "command",
            "payload_hash",
            "status",
            "message",
            "stdout",
            "stderr",
        ],
        required=[],
    ),
    "ValidationErrors": TableSpec(
        name="ValidationErrors",
        key_column="validation_id",
        columns=[
            "validation_id",
            "timestamp",
            "severity",
            "table",
            "row_key",
            "field",
            "message",
        ],
        required=[],
    ),
}


CREATE_TABLES = ["Accounts", "Audiences", "Campaigns", "AdSets", "Ads"]
CONTROL_TABLES = ["BulkChanges", "PerformanceSnapshots", "PublishLog", "ValidationErrors"]

OBJECT_CONFIG = {
    "account": ("Accounts", "account_key", "ad_account_id"),
    "audience": ("Audiences", "audience_key", "meta_audience_id"),
    "campaign": ("Campaigns", "campaign_key", "meta_campaign_id"),
    "adset": ("AdSets", "adset_key", "meta_adset_id"),
    # Creative objects are defined inline on the Ads sheet: one ad row carries
    # both its creative fields (keyed by ad_key) and the resulting IDs.
    "creative": ("Ads", "ad_key", "meta_creative_id"),
    "ad": ("Ads", "ad_key", "meta_ad_id"),
}

UPDATABLE_FIELDS = {
    "campaign": {
        "name": "--name",
        "status": "--status",
        "desired_status": "--status",
        "daily_budget_cents": "--daily-budget",
        "lifetime_budget_cents": "--lifetime-budget",
        "bid_strategy": "bid_strategy",
        "spend_cap": "spend_cap",
        "campaign_budget_optimization": "campaign_budget_optimization",
    },
    "adset": {
        "name": "--name",
        "status": "--status",
        "desired_status": "--status",
        "optimization_goal": "optimization_goal",
        "billing_event": "billing_event",
        "daily_budget_cents": "--daily-budget",
        "lifetime_budget_cents": "--lifetime-budget",
        "bid_amount_cents": "--bid-amount",
        "bid_strategy": "bid_strategy",
        "daily_min_spend_target": "daily_min_spend_target",
        "daily_spend_cap": "daily_spend_cap",
        "start_time": "--start-time",
        "end_time": "--end-time",
        "targeting_json": "targeting",
        "targeting_automation_json": "targeting_automation",
        "promoted_object_json": "promoted_object",
    },
    "creative": {
        "name": "--name",
        "primary_text": "--body",
        "headline": "--title",
        "description": "--description",
        "cta": "--call-to-action",
        "destination_url": "--link-url",
        "asset_path_or_url": "--image",
    },
    "ad": {
        "name": "--name",
        "meta_creative_id": "--creative-id",
        "status": "--status",
        "desired_status": "--status",
    },
    "audience": {
        "name": "name",
        "description": "description",
        "retention_days": "retention_days",
        "rule_json": "rule",
        "targeting_json": "targeting",
    },
}

OBJECTIVES = {
    "OUTCOME_AWARENESS",
    "OUTCOME_TRAFFIC",
    "OUTCOME_ENGAGEMENT",
    "OUTCOME_LEADS",
    "OUTCOME_APP_PROMOTION",
    "OUTCOME_SALES",
}

STATUSES = {"ACTIVE", "PAUSED", "DELETED", "ARCHIVED"}
BUDGET_MODES = {"CBO", "ABO"}
AUDIENCE_TYPES = {"CUSTOM", "LOOKALIKE", "WEBSITE", "SAVED"}
AUDIENCE_UPLOAD_OPERATIONS = {"ADD", "REMOVE", "REPLACE"}
BULK_OPERATIONS = {"SET_FIELD", "PATCH_JSON", "ACTIVATE", "PAUSE", "DELETE", "DUPLICATE", "REPLACE_CREATIVE", "REPLACE_TARGETING"}
OPTIMIZATION_OPERATORS = {">", ">=", "<", "<=", "==", "!="}


def clean(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def is_blank(value: Any) -> bool:
    return clean(value) == ""


def truthy(value: Any) -> bool:
    return clean(value).lower() in TRUE_VALUES


def approved(value: Any) -> bool:
    return clean(value).upper() == "APPROVED"


def to_int(value: Any) -> int | None:
    text = clean(value)
    if not text:
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


def row_key(table: str, row: dict[str, Any]) -> str:
    return clean(row.get(TABLES[table].key_column))
