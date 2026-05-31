from __future__ import annotations

import hashlib
import json
import os
import subprocess
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import Action, ActionResult, Dataset
from .planner import existing_ids
from .schema import OBJECT_CONFIG, TABLES, UPDATABLE_FIELDS, clean


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def payload_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


def context_from_dataset(dataset: Dataset) -> dict[str, dict[str, str]]:
    return existing_ids(dataset)


def make_adapter(mode: str, dataset: Dataset, state_path: str | None = None):
    if mode == "mock":
        return MockMetaAdapter(dataset=dataset, state_path=state_path)
    if mode == "live":
        return LiveMetaCliAdapter(dataset=dataset)
    raise ValueError(f"Unsupported mode: {mode}")


class MockMetaAdapter:
    def __init__(self, dataset: Dataset, state_path: str | None = None):
        self.dataset = dataset
        self.state_path = Path(state_path or "state/mock_state.json")
        self.state = self._load_state()

    def apply(self, action: Action, context: dict[str, dict[str, str]]) -> ActionResult:
        if action.dry_run_only:
            return ActionResult(action=action, ok=False, message=action.reason)
        if action.operation == "create":
            return self._create(action, context)
        if action.operation == "update":
            return self._update(action, context)
        return ActionResult(action=action, ok=False, message=f"Unsupported operation: {action.operation}")

    def save(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state["updated_at"] = utc_now()
        self.state_path.write_text(json.dumps(self.state, indent=2, sort_keys=True), encoding="utf-8")

    def _load_state(self) -> dict[str, Any]:
        if self.state_path.exists():
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        return {"ids": {key: {} for key in OBJECT_CONFIG}, "objects": {key: {} for key in OBJECT_CONFIG}, "updated_at": ""}

    def _create(self, action: Action, context: dict[str, dict[str, str]]) -> ActionResult:
        object_type = action.object_type
        key = action.object_key
        ids = self.state.setdefault("ids", {}).setdefault(object_type, {})
        meta_id = ids.get(key) or _mock_id(object_type, key)
        ids[key] = meta_id
        context.setdefault(object_type, {})[key] = meta_id
        self.state.setdefault("objects", {}).setdefault(object_type, {})[meta_id] = {
            "key": key,
            "payload": action.payload,
            "created_at": utc_now(),
        }
        table = action.payload.get("table")
        id_column = action.payload.get("id_column")
        updates = []
        if table and id_column:
            updates.extend(
                [
                    (table, key, id_column, meta_id),
                    (table, key, "last_result", "MOCK_CREATED"),
                    (table, key, "last_error", ""),
                    (table, key, "updated_at", utc_now()),
                ]
            )
        return ActionResult(
            action=action,
            ok=True,
            meta_id=meta_id,
            message=f"Mock created {object_type} {key}.",
            stdout=json.dumps({"id": meta_id, "mock": True}),
            updates=updates,
        )

    def _update(self, action: Action, context: dict[str, dict[str, str]]) -> ActionResult:
        object_type = action.object_type
        key_or_id = action.object_key
        meta_id = context.get(object_type, {}).get(key_or_id) or key_or_id
        if meta_id.startswith("${"):
            meta_id = _resolve_placeholder(meta_id, context)
        field = clean(action.payload.get("field"))
        value = _resolve_any(clean(action.payload.get("new_value")), context)
        self.state.setdefault("objects", {}).setdefault(object_type, {}).setdefault(meta_id, {})
        self.state["objects"][object_type][meta_id][field] = value
        self.state["objects"][object_type][meta_id]["updated_at"] = utc_now()

        updates: list[tuple[str, str, str, Any]] = [
            ("BulkChanges", action.source_key, "applied_at", utc_now()),
            ("BulkChanges", action.source_key, "result", "MOCK_UPDATED"),
            ("BulkChanges", action.source_key, "error", ""),
        ]
        table_name, key_column, _ = OBJECT_CONFIG[object_type]
        if _row_exists(self.dataset, table_name, key_column, key_or_id):
            if field in TABLES[table_name].columns:
                updates.append((table_name, key_or_id, field, value))
            if field == "desired_status" and "desired_status" in TABLES[table_name].columns:
                updates.append((table_name, key_or_id, "desired_status", value))
            updates.extend(
                [
                    (table_name, key_or_id, "last_result", "MOCK_UPDATED"),
                    (table_name, key_or_id, "last_error", ""),
                    (table_name, key_or_id, "updated_at", utc_now()),
                ]
            )
        return ActionResult(
            action=action,
            ok=True,
            meta_id=meta_id,
            message=f"Mock updated {object_type} {key_or_id}: {field}={value}.",
            stdout=json.dumps({"id": meta_id, "field": field, "value": value, "mock": True}),
            updates=updates,
        )


class LiveMetaCliAdapter:
    def __init__(self, dataset: Dataset):
        self.dataset = dataset

    def apply(self, action: Action, context: dict[str, dict[str, str]]) -> ActionResult:
        if action.dry_run_only:
            return ActionResult(action=action, ok=False, message=action.reason)
        if action.object_type == "account" and action.operation == "create":
            return self._create_ad_account(action, context)
        command = [_resolve_any(token, context) for token in action.command]
        if command and command[0] == "meta":
            command[0] = os.environ.get("META_CLI_BIN", "meta")
        try:
            completed = subprocess.run(command, capture_output=True, text=True, check=False)
        except FileNotFoundError as exc:
            return ActionResult(action=action, ok=False, message=str(exc), stderr=str(exc))

        ok = completed.returncode == 0
        meta_id = _extract_id(completed.stdout)
        updates = self._updates_for_live_result(action, ok, meta_id, completed.stderr)
        if ok and meta_id and action.operation == "create":
            context.setdefault(action.object_type, {})[action.object_key] = meta_id
        return ActionResult(
            action=action,
            ok=ok,
            meta_id=meta_id,
            message="Live command succeeded." if ok else "Live command failed.",
            stdout=completed.stdout,
            stderr=completed.stderr,
            updates=updates,
        )

    def save(self) -> None:
        return None

    def _create_ad_account(self, action: Action, context: dict[str, dict[str, str]]) -> ActionResult:
        token = os.environ.get("ACCESS_TOKEN")
        if not token:
            return ActionResult(action=action, ok=False, message="ACCESS_TOKEN is required for live ad account creation.")
        row = action.payload.get("row", {})
        business_id = clean(row.get("business_id")) or os.environ.get("BUSINESS_ID", "")
        if not business_id:
            return ActionResult(action=action, ok=False, message="business_id or BUSINESS_ID is required for live ad account creation.")
        version = os.environ.get("META_API_VERSION", "v21.0")
        url = f"https://graph.facebook.com/{version}/{business_id}/adaccount"
        form = {
            "access_token": token,
            "name": clean(row.get("account_name")),
            "currency": clean(row.get("currency")),
            "timezone_id": clean(row.get("timezone_id")),
            "end_advertiser": "NONE",
            "media_agency": "NONE",
        }
        data = urllib.parse.urlencode(form).encode("utf-8")
        try:
            request = urllib.request.Request(url, data=data, method="POST")
            with urllib.request.urlopen(request, timeout=60) as response:
                body = response.read().decode("utf-8")
        except Exception as exc:  # noqa: BLE001 - surface Meta/API errors in the workbook log.
            return ActionResult(action=action, ok=False, message="Ad account API create failed.", stderr=str(exc))
        meta_id = _extract_id(body)
        if meta_id:
            context.setdefault("account", {})[action.object_key] = meta_id
        updates = self._updates_for_live_result(action, bool(meta_id), meta_id, "")
        return ActionResult(
            action=action,
            ok=bool(meta_id),
            meta_id=meta_id,
            message="Ad account API create succeeded." if meta_id else "Ad account API create returned no ID.",
            stdout=body,
            updates=updates,
        )

    def _updates_for_live_result(self, action: Action, ok: bool, meta_id: str, error: str) -> list[tuple[str, str, str, Any]]:
        updates: list[tuple[str, str, str, Any]] = []
        if action.operation == "create":
            table = action.payload.get("table")
            id_column = action.payload.get("id_column")
            if table and id_column and ok:
                updates.append((table, action.object_key, id_column, meta_id))
            if table:
                updates.extend(
                    [
                        (table, action.object_key, "last_result", "LIVE_CREATED" if ok else "LIVE_ERROR"),
                        (table, action.object_key, "last_error", "" if ok else error),
                        (table, action.object_key, "updated_at", utc_now()),
                    ]
                )
        if action.operation == "update":
            updates.extend(
                [
                    ("BulkChanges", action.source_key, "applied_at", utc_now() if ok else ""),
                    ("BulkChanges", action.source_key, "result", "LIVE_UPDATED" if ok else "LIVE_ERROR"),
                    ("BulkChanges", action.source_key, "error", "" if ok else error),
                ]
            )
            object_type = action.object_type
            target_key = action.object_key
            table_name, key_column, _ = OBJECT_CONFIG[object_type]
            if ok and _row_exists(self.dataset, table_name, key_column, target_key):
                field = clean(action.payload.get("field"))
                value = clean(action.payload.get("new_value"))
                if field in TABLES[table_name].columns:
                    updates.append((table_name, target_key, field, value))
                updates.extend(
                    [
                        (table_name, target_key, "last_result", "LIVE_UPDATED"),
                        (table_name, target_key, "last_error", ""),
                        (table_name, target_key, "updated_at", utc_now()),
                    ]
                )
        return updates


def _mock_id(object_type: str, key: str) -> str:
    prefix = {
        "account": "act_mock",
        "campaign": "mock_cmp",
        "adset": "mock_adset",
        "creative": "mock_crt",
        "ad": "mock_ad",
    }[object_type]
    digest = hashlib.sha1(f"{object_type}:{key}".encode("utf-8")).hexdigest()[:10]
    return f"{prefix}_{digest}"


def _resolve_any(value: str, context: dict[str, dict[str, str]]) -> str:
    if value.startswith("${") and value.endswith("}"):
        return _resolve_placeholder(value, context)
    return value


def _resolve_placeholder(token: str, context: dict[str, dict[str, str]]) -> str:
    inner = token[2:-1]
    object_type, _, key = inner.partition(":")
    return context.get(object_type, {}).get(key, token)


def _extract_id(stdout: str) -> str:
    text = stdout.strip()
    if not text:
        return ""
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return ""
    if isinstance(parsed, dict):
        if "id" in parsed:
            return str(parsed["id"])
        if "data" in parsed and isinstance(parsed["data"], dict) and "id" in parsed["data"]:
            return str(parsed["data"]["id"])
        if "data" in parsed and isinstance(parsed["data"], list) and parsed["data"]:
            first = parsed["data"][0]
            if isinstance(first, dict) and "id" in first:
                return str(first["id"])
    return ""


def _row_exists(dataset: Dataset, table_name: str, key_column: str, key: str) -> bool:
    return any(clean(row.get(key_column)) == key for row in dataset.tables.get(table_name, []))
