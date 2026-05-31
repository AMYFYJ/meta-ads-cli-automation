from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from .models import Action, Dataset
from .schema import (
    OBJECT_CONFIG,
    TABLES,
    UPDATABLE_FIELDS,
    approved,
    clean,
    is_blank,
    row_key,
    truthy,
)


def build_plan(dataset: Dataset) -> list[Action]:
    actions: list[Action] = []
    actions.extend(_account_actions(dataset))
    actions.extend(_audience_actions(dataset))
    actions.extend(_audience_upload_actions(dataset))
    actions.extend(_campaign_actions(dataset))
    actions.extend(_adset_actions(dataset))
    actions.extend(_creative_actions(dataset))
    actions.extend(_ad_actions(dataset))
    actions.extend(_bulk_change_actions(dataset))
    return actions


def _audience_actions(dataset: Dataset) -> list[Action]:
    actions: list[Action] = []
    accounts = _index(dataset, "Accounts")
    audiences = _index(dataset, "Audiences")
    for row in dataset.tables.get("Audiences", []):
        key = row_key("Audiences", row)
        if clean(row.get("meta_audience_id")) or not approved(row.get("approval_status")):
            continue
        account_key = clean(row.get("account_key"))
        account = accounts.get(account_key, {})
        audience_type = clean(row.get("audience_type")).upper()
        endpoint = f"{placeholder('account', account_key)}/saved_audiences" if audience_type == "SAVED" else f"{placeholder('account', account_key)}/customaudiences"
        body = _audience_body(row, audiences)
        depends = []
        if _needs_account_create(account):
            depends.append(f"001_create_account_{account_key}")
        source_key = clean(row.get("source_audience_key"))
        if source_key and not clean(audiences.get(source_key, {}).get("meta_audience_id")):
            depends.append(f"015_create_audience_{source_key}")
        actions.append(
            Action(
                action_id=f"015_create_audience_{key}",
                operation="create",
                object_type="audience",
                object_key=key,
                command=["GRAPH", "POST", endpoint],
                payload={"row": row, "table": "Audiences", "id_column": "meta_audience_id"},
                executor="graph",
                method="POST",
                endpoint=endpoint,
                body=body,
                depends_on=depends,
                reason=f"Approved {audience_type.lower()} audience has no Meta audience ID.",
                source_table="Audiences",
                source_key=key,
            )
        )
    return actions


def _audience_upload_actions(dataset: Dataset) -> list[Action]:
    actions: list[Action] = []
    audiences = _index(dataset, "Audiences")
    base_dir = source_dir(dataset)
    for row in dataset.tables.get("AudienceUploads", []):
        key = row_key("AudienceUploads", row)
        if clean(row.get("applied_at")) or not approved(row.get("approval_status")):
            continue
        audience_key = clean(row.get("audience_key"))
        audience = audiences.get(audience_key, {})
        audience_id = clean(audience.get("meta_audience_id")) or placeholder("audience", audience_key)
        body = {
            "operation": clean(row.get("operation")).upper() or "ADD",
            "payload": {
                "schema": [part.strip() for part in clean(row.get("schema")).split(",") if part.strip()],
                "data": _audience_upload_data(row, base_dir),
            },
        }
        depends = []
        if not clean(audience.get("meta_audience_id")):
            depends.append(f"015_create_audience_{audience_key}")
        actions.append(
            Action(
                action_id=f"016_upload_audience_{key}",
                operation="upload",
                object_type="audience",
                object_key=audience_key,
                command=["GRAPH", "POST", f"{audience_id}/users"],
                payload={"row": row, "table": "AudienceUploads"},
                executor="graph",
                method="POST",
                endpoint=f"{audience_id}/users",
                body=body,
                id_path="",
                writeback=[
                    ("AudienceUploads", key, "applied_at", "$now"),
                    ("AudienceUploads", key, "result", "LIVE_UPLOADED"),
                    ("AudienceUploads", key, "error", ""),
                ],
                depends_on=depends,
                reason="Approved audience upload has not been applied.",
                source_table="AudienceUploads",
                source_key=key,
            )
        )
    return actions


def action_dicts(actions: list[Action]) -> list[dict[str, Any]]:
    return [action.as_dict() for action in actions]


def source_dir(dataset: Dataset) -> Path:
    path = Path(dataset.path)
    return path if dataset.kind == "csv_dir" else path.parent


def placeholder(object_type: str, key: str) -> str:
    return f"${{{object_type}:{key}}}"


def existing_ids(dataset: Dataset) -> dict[str, dict[str, str]]:
    ids: dict[str, dict[str, str]] = {}
    for object_type, (table_name, key_column, id_column) in OBJECT_CONFIG.items():
        ids[object_type] = {}
        for row in dataset.tables.get(table_name, []):
            key = clean(row.get(key_column))
            meta_id = clean(row.get(id_column))
            if key and meta_id:
                ids[object_type][key] = meta_id
    return ids


def resolve_object_ref(dataset: Dataset, object_type: str, key_or_id: str) -> str:
    ids = existing_ids(dataset).get(object_type, {})
    if key_or_id in ids:
        return ids[key_or_id]
    table_name, key_column, _ = OBJECT_CONFIG[object_type]
    if any(clean(row.get(key_column)) == key_or_id for row in dataset.tables.get(table_name, [])):
        return placeholder(object_type, key_or_id)
    return key_or_id


def _account_actions(dataset: Dataset) -> list[Action]:
    actions: list[Action] = []
    for row in dataset.tables.get("Accounts", []):
        key = row_key("Accounts", row)
        if clean(row.get("ad_account_id")):
            continue
        if truthy(row.get("create_ad_account")):
            actions.append(
                Action(
                    action_id=f"001_create_account_{key}",
                    operation="create",
                    object_type="account",
                    object_key=key,
                    command=[
                        "POST",
                        "/{business_id}/adaccount",
                        "--name",
                        clean(row.get("account_name")),
                        "--currency",
                        clean(row.get("currency")),
                        "--timezone-id",
                        clean(row.get("timezone_id")),
                    ],
                    payload={"row": row, "table": "Accounts", "id_column": "ad_account_id"},
                    reason="Account has no ad_account_id and create_ad_account is TRUE.",
                    source_table="Accounts",
                    source_key=key,
                )
            )
        else:
            actions.append(
                Action(
                    action_id=f"001_manual_account_{key}",
                    operation="manual",
                    object_type="account",
                    object_key=key,
                    command=[],
                    payload={"row": row, "table": "Accounts", "id_column": "ad_account_id"},
                    reason="Account has no ad_account_id. Create or connect an ad account before live publishing.",
                    source_table="Accounts",
                    source_key=key,
                    dry_run_only=True,
                )
            )
    return actions


def _campaign_actions(dataset: Dataset) -> list[Action]:
    actions: list[Action] = []
    accounts = _index(dataset, "Accounts")
    for row in dataset.tables.get("Campaigns", []):
        key = row_key("Campaigns", row)
        if clean(row.get("meta_campaign_id")) or not approved(row.get("approval_status")):
            continue
        account_key = clean(row.get("account_key"))
        command = _meta_prefix(accounts.get(account_key, {})) + ["campaign", "create"]
        _extend(command, "--name", row.get("name"))
        _extend(command, "--objective", row.get("objective"))
        if clean(row.get("budget_mode")).upper() == "ABO":
            command.append("--adset-budget-sharing")
        else:
            _extend(command, "--daily-budget", row.get("daily_budget_cents"))
            _extend(command, "--lifetime-budget", row.get("lifetime_budget_cents"))
        _extend(command, "--status", row.get("desired_status") or "PAUSED")
        actions.append(
            Action(
                action_id=f"010_create_campaign_{key}",
                operation="create",
                object_type="campaign",
                object_key=key,
                command=command,
                payload={"row": row, "table": "Campaigns", "id_column": "meta_campaign_id"},
                depends_on=[f"001_create_account_{account_key}"] if _needs_account_create(accounts.get(account_key, {})) else [],
                reason="Approved campaign has no Meta campaign ID.",
                source_table="Campaigns",
                source_key=key,
            )
        )
    return actions


def _adset_actions(dataset: Dataset) -> list[Action]:
    actions: list[Action] = []
    campaigns = _index(dataset, "Campaigns")
    accounts = _index(dataset, "Accounts")
    for row in dataset.tables.get("AdSets", []):
        key = row_key("AdSets", row)
        if clean(row.get("meta_adset_id")) or not approved(row.get("approval_status")):
            continue
        campaign_key = clean(row.get("campaign_key"))
        campaign = campaigns.get(campaign_key, {})
        account = accounts.get(clean(campaign.get("account_key")), {})
        campaign_id = clean(campaign.get("meta_campaign_id")) or placeholder("campaign", campaign_key)
        command = _meta_prefix(account) + ["adset", "create", campaign_id]
        _extend(command, "--name", row.get("name"))
        _extend(command, "--optimization-goal", row.get("optimization_goal"))
        _extend(command, "--billing-event", row.get("billing_event"))
        _extend(command, "--daily-budget", row.get("daily_budget_cents"))
        _extend(command, "--lifetime-budget", row.get("lifetime_budget_cents"))
        _extend(command, "--bid-amount", row.get("bid_amount_cents"))
        _extend(command, "--start-time", row.get("start_time"))
        _extend(command, "--end-time", row.get("end_time"))
        _extend(command, "--status", row.get("desired_status") or "PAUSED")
        _extend(command, "--targeting-countries", row.get("countries"))
        _extend(command, "--pixel-id", row.get("pixel_dataset_id"))
        _extend(command, "--custom-event-type", row.get("pixel_event"))
        actions.append(
            Action(
                action_id=f"020_create_adset_{key}",
                operation="create",
                object_type="adset",
                object_key=key,
                command=command,
                payload={"row": row, "table": "AdSets", "id_column": "meta_adset_id"},
                depends_on=[f"010_create_campaign_{campaign_key}"] if not clean(campaign.get("meta_campaign_id")) else [],
                reason="Approved ad set has no Meta ad set ID.",
                source_table="AdSets",
                source_key=key,
            )
        )
    return actions


def _creative_actions(dataset: Dataset) -> list[Action]:
    actions: list[Action] = []
    accounts = _index(dataset, "Accounts")
    base_dir = source_dir(dataset)
    for row in dataset.tables.get("Creatives", []):
        key = row_key("Creatives", row)
        if clean(row.get("meta_creative_id")) or not approved(row.get("approval_status")):
            continue
        account = accounts.get(clean(row.get("account_key")), {})
        command = _meta_prefix(account) + ["creative", "create"]
        _extend(command, "--name", row.get("name"))
        media_arg = "--video" if clean(row.get("format")).lower() == "video" else "--image"
        asset = clean(row.get("asset_path_or_url"))
        if asset and not asset.startswith(("http://", "https://")):
            asset_path = Path(asset)
            if not asset_path.is_absolute():
                asset = str((base_dir / asset_path).resolve())
        _extend(command, media_arg, asset)
        _extend(command, "--page-id", row.get("page_id") or account.get("page_id"))
        _extend(command, "--body", row.get("primary_text"))
        _extend(command, "--title", row.get("headline"))
        _extend(command, "--description", row.get("description"))
        _extend(command, "--link-url", row.get("destination_url"))
        _extend(command, "--call-to-action", row.get("cta"))
        _extend(command, "--instagram-actor-id", row.get("instagram_actor_id") or account.get("instagram_actor_id"))
        actions.append(
            Action(
                action_id=f"030_create_creative_{key}",
                operation="create",
                object_type="creative",
                object_key=key,
                command=command,
                payload={"row": row, "table": "Creatives", "id_column": "meta_creative_id"},
                depends_on=[f"001_create_account_{row.get('account_key')}"] if _needs_account_create(account) else [],
                reason="Approved creative has no Meta creative ID.",
                source_table="Creatives",
                source_key=key,
            )
        )
    return actions


def _ad_actions(dataset: Dataset) -> list[Action]:
    actions: list[Action] = []
    adsets = _index(dataset, "AdSets")
    creatives = _index(dataset, "Creatives")
    campaigns = _index(dataset, "Campaigns")
    accounts = _index(dataset, "Accounts")
    for row in dataset.tables.get("Ads", []):
        key = row_key("Ads", row)
        if clean(row.get("meta_ad_id")) or not approved(row.get("approval_status")):
            continue
        adset_key = clean(row.get("adset_key"))
        creative_key = clean(row.get("creative_key"))
        adset = adsets.get(adset_key, {})
        campaign = campaigns.get(clean(adset.get("campaign_key")), {})
        account = accounts.get(clean(campaign.get("account_key")), {})
        adset_id = clean(adset.get("meta_adset_id")) or placeholder("adset", adset_key)
        creative_id = clean(creatives.get(creative_key, {}).get("meta_creative_id")) or placeholder("creative", creative_key)
        command = _meta_prefix(account) + ["ad", "create", adset_id]
        _extend(command, "--name", row.get("name"))
        _extend(command, "--creative-id", creative_id)
        _extend(command, "--status", row.get("desired_status") or "PAUSED")
        _extend(command, "--tracking-specs", row.get("tracking_specs"))
        depends = []
        if not clean(adset.get("meta_adset_id")):
            depends.append(f"020_create_adset_{adset_key}")
        if not clean(creatives.get(creative_key, {}).get("meta_creative_id")):
            depends.append(f"030_create_creative_{creative_key}")
        actions.append(
            Action(
                action_id=f"040_create_ad_{key}",
                operation="create",
                object_type="ad",
                object_key=key,
                command=command,
                payload={"row": row, "table": "Ads", "id_column": "meta_ad_id"},
                depends_on=depends,
                reason="Approved ad has no Meta ad ID.",
                source_table="Ads",
                source_key=key,
            )
        )
    return actions


def _bulk_change_actions(dataset: Dataset) -> list[Action]:
    actions: list[Action] = []
    ids = existing_ids(dataset)
    for row in dataset.tables.get("BulkChanges", []):
        change_key = row_key("BulkChanges", row)
        if clean(row.get("applied_at")) or not approved(row.get("approval_status")):
            continue
        object_type = clean(row.get("object_level")).lower()
        target = clean(row.get("object_key_or_meta_id"))
        field = clean(row.get("field"))
        if object_type not in UPDATABLE_FIELDS or field not in UPDATABLE_FIELDS[object_type]:
            continue
        target_ref = ids.get(object_type, {}).get(target) or (
            placeholder(object_type, target) if _target_is_known_key(dataset, object_type, target) else target
        )
        command = _meta_prefix_for_object(dataset, object_type, target) + [object_type, "update", target_ref]
        value = clean(row.get("new_value"))
        if object_type == "ad" and field == "creative_key":
            value = ids.get("creative", {}).get(value) or placeholder("creative", value)
        _extend(command, UPDATABLE_FIELDS[object_type][field], value)
        depends = []
        if target_ref.startswith("${"):
            depends.append(_create_action_id(object_type, target))
        if object_type == "ad" and field == "creative_key" and value.startswith("${"):
            depends.append(_create_action_id("creative", clean(row.get("new_value"))))
        actions.append(
            Action(
                action_id=f"090_bulk_{change_key}",
                operation="update",
                object_type=object_type,
                object_key=target,
                command=command,
                payload={"row": row, "field": field, "new_value": value, "target": target},
                depends_on=depends,
                reason=clean(row.get("reason")) or "Approved bulk change.",
                source_table="BulkChanges",
                source_key=change_key,
            )
        )
    return actions


def _meta_prefix(account_row: dict[str, Any]) -> list[str]:
    command = ["meta", "--output", "json", "--no-input"]
    ad_account_id = clean(account_row.get("ad_account_id"))
    account_key = clean(account_row.get("account_key"))
    if ad_account_id:
        command.extend(["--ad-account-id", ad_account_id])
    elif account_key:
        command.extend(["--ad-account-id", placeholder("account", account_key)])
    return command + ["ads"]


def _meta_prefix_for_object(dataset: Dataset, object_type: str, key_or_id: str) -> list[str]:
    accounts = _index(dataset, "Accounts")
    if object_type == "campaign":
        campaign = _index(dataset, "Campaigns").get(key_or_id, {})
        return _meta_prefix(accounts.get(clean(campaign.get("account_key")), {}))
    if object_type == "adset":
        adset = _index(dataset, "AdSets").get(key_or_id, {})
        campaign = _index(dataset, "Campaigns").get(clean(adset.get("campaign_key")), {})
        return _meta_prefix(accounts.get(clean(campaign.get("account_key")), {}))
    if object_type == "creative":
        creative = _index(dataset, "Creatives").get(key_or_id, {})
        return _meta_prefix(accounts.get(clean(creative.get("account_key")), {}))
    if object_type == "audience":
        audience = _index(dataset, "Audiences").get(key_or_id, {})
        return _meta_prefix(accounts.get(clean(audience.get("account_key")), {}))
    if object_type == "ad":
        ad = _index(dataset, "Ads").get(key_or_id, {})
        adset = _index(dataset, "AdSets").get(clean(ad.get("adset_key")), {})
        campaign = _index(dataset, "Campaigns").get(clean(adset.get("campaign_key")), {})
        return _meta_prefix(accounts.get(clean(campaign.get("account_key")), {}))
    return ["meta", "--output", "json", "--no-input", "ads"]


def _extend(command: list[str], flag: str, value: Any) -> None:
    value_text = clean(value)
    if value_text:
        command.extend([flag, value_text])


def _index(dataset: Dataset, table_name: str) -> dict[str, dict[str, Any]]:
    spec = TABLES[table_name]
    return {clean(row.get(spec.key_column)): row for row in dataset.tables.get(table_name, []) if clean(row.get(spec.key_column))}


def _needs_account_create(account: dict[str, Any]) -> bool:
    return bool(account) and not clean(account.get("ad_account_id")) and truthy(account.get("create_ad_account"))


def _target_is_known_key(dataset: Dataset, object_type: str, key: str) -> bool:
    table_name, key_column, _ = OBJECT_CONFIG[object_type]
    return any(clean(row.get(key_column)) == key for row in dataset.tables.get(table_name, []))


def _create_action_id(object_type: str, key: str) -> str:
    prefixes = {
        "account": "001_create_account_",
        "audience": "015_create_audience_",
        "campaign": "010_create_campaign_",
        "adset": "020_create_adset_",
        "creative": "030_create_creative_",
        "ad": "040_create_ad_",
    }
    return f"{prefixes[object_type]}{key}"


def _audience_body(row: dict[str, Any], audiences: dict[str, dict[str, Any]]) -> dict[str, Any]:
    audience_type = clean(row.get("audience_type")).upper()
    body: dict[str, Any] = {
        "name": clean(row.get("name")),
        "description": clean(row.get("description")),
    }
    if audience_type == "SAVED":
        body["targeting"] = _json_value(row.get("targeting_json"), {})
        return body
    subtype = clean(row.get("subtype")).upper() or ("LOOKALIKE" if audience_type == "LOOKALIKE" else "CUSTOM")
    if audience_type == "WEBSITE":
        subtype = "WEBSITE"
    body["subtype"] = subtype
    if clean(row.get("customer_file_source")):
        body["customer_file_source"] = clean(row.get("customer_file_source"))
    if clean(row.get("retention_days")):
        body["retention_days"] = clean(row.get("retention_days"))
    if clean(row.get("rule_json")):
        body["rule"] = _json_value(row.get("rule_json"), {})
    if audience_type == "LOOKALIKE":
        source_key = clean(row.get("source_audience_key"))
        source_id = clean(audiences.get(source_key, {}).get("meta_audience_id")) or placeholder("audience", source_key)
        lookalike_spec = _json_value(row.get("lookalike_spec_json"), {})
        if not lookalike_spec:
            lookalike_spec = {
                "type": "similarity",
                "ratio": float(clean(row.get("lookalike_ratio")) or "0.01"),
                "country": clean(row.get("countries")) or "US",
            }
        body["origin_audience_id"] = source_id
        body["lookalike_spec"] = lookalike_spec
    return body


def _audience_upload_data(row: dict[str, Any], base_dir: Path) -> list[list[str]]:
    if clean(row.get("data_json")):
        loaded = _json_value(row.get("data_json"), [])
        return loaded if isinstance(loaded, list) else []
    data_path = Path(clean(row.get("data_path")))
    if not data_path.is_absolute():
        data_path = base_dir / data_path
    with data_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.reader(handle)
        rows = list(reader)
    if rows and [cell.strip().upper() for cell in rows[0]] == [part.strip().upper() for part in clean(row.get("schema")).split(",") if part.strip()]:
        rows = rows[1:]
    return rows


def _json_value(value: Any, default: Any) -> Any:
    text = clean(value)
    if not text:
        return default
    return json.loads(text)
