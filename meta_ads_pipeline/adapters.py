from __future__ import annotations

import hashlib
import json
import os
import random
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .models import Action, ActionResult, Dataset
from .planner import existing_ids
from .schema import OBJECT_CONFIG, TABLES, clean


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def resolve_account(account_override: str = "") -> str:
    """Resolve the ad account id to target for live actions.

    Precedence: explicit ``--account`` override > sandbox account (when ``META_SANDBOX=1``)
    > ``AD_ACCOUNT_ID``. This keeps sandbox the default target until the user opts out.
    """
    if account_override:
        return account_override
    if _truthy(os.environ.get("META_SANDBOX")):
        sandbox = clean(os.environ.get("SANDBOX_AD_ACCOUNT_ID"))
        if sandbox:
            return sandbox
    return clean(os.environ.get("AD_ACCOUNT_ID"))


def build_live_env(account_override: str = "") -> dict[str, str]:
    """Return a copy of the environment with ``AD_ACCOUNT_ID`` scoped to the resolved account."""
    env = dict(os.environ)
    account = resolve_account(account_override)
    if account:
        env["AD_ACCOUNT_ID"] = account
    return env


def is_sandbox_account(account_id: str) -> bool:
    sandbox = clean(os.environ.get("SANDBOX_AD_ACCOUNT_ID"))
    return bool(account_id) and account_id == sandbox


def _truthy(value: str | None) -> bool:
    return clean(value).lower() in {"1", "true", "yes", "on"}


_TRANSIENT_TOKENS = (
    "rate limit",
    "request limit reached",
    "user request limit reached",
    "(#4)",
    "(#17)",
    "(#32)",
    "(#80004)",
    "(#613)",
    "temporarily",
    "try again",
)
_TRANSIENT_HTTP = {429, 500, 502, 503, 504}


def _is_transient(message: str, code: int | None) -> bool:
    if code in _TRANSIENT_HTTP:
        return True
    text = (message or "").lower()
    return any(token in text for token in _TRANSIENT_TOKENS)


def with_retry(call: Callable[[], Any], *, attempts: int | None = None, base_delay: float | None = None, max_delay: float = 60.0) -> Any:
    """Invoke ``call`` with exponential backoff on transient/rate-limit failures.

    ``call`` should return ``(ok: bool, message: str, payload)`` or raise ``urllib.error.HTTPError``.
    Only transient failures (rate limits, HTTP 429/5xx) are retried; everything else returns immediately.
    """
    attempts = attempts if attempts is not None else int(os.environ.get("META_RETRY_ATTEMPTS", "5"))
    base_delay = base_delay if base_delay is not None else float(os.environ.get("META_RETRY_BASE_DELAY", "2.0"))
    last_result: Any = None
    for attempt in range(1, max(1, attempts) + 1):
        retry_after: float | None = None
        try:
            ok, message, payload = call()
            last_result = (ok, message, payload)
            if ok or attempt >= attempts or not _is_transient(message, None):
                return last_result
        except urllib.error.HTTPError as exc:
            body = ""
            try:
                body = exc.read().decode("utf-8")
            except Exception:  # noqa: BLE001 - body is best-effort for diagnostics.
                body = str(exc)
            last_result = (False, body or str(exc), None)
            if attempt >= attempts or not _is_transient(body, exc.code):
                return last_result
            header = exc.headers.get("Retry-After") if exc.headers else None
            if header:
                try:
                    retry_after = float(header)
                except ValueError:
                    retry_after = None
        delay = retry_after if retry_after is not None else min(max_delay, base_delay * (2 ** (attempt - 1)))
        time.sleep(delay + random.uniform(0, 0.5))
    return last_result


def payload_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


def context_from_dataset(dataset: Dataset) -> dict[str, dict[str, str]]:
    return existing_ids(dataset)


def make_adapter(mode: str, dataset: Dataset, state_path: str | None = None, account_override: str = ""):
    if mode == "mock":
        return MockMetaAdapter(dataset=dataset, state_path=state_path)
    if mode == "live":
        return LiveMetaCliAdapter(dataset=dataset, account_override=account_override)
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
        if action.operation == "upload":
            return self._upload(action, context)
        if action.operation == "duplicate":
            return self._duplicate(action, context)
        if action.operation == "patch":
            return self._patch(action, context)
        if action.operation == "delete":
            return self._delete(action, context)
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

    def _upload(self, action: Action, context: dict[str, dict[str, str]]) -> ActionResult:
        meta_id = context.get(action.object_type, {}).get(action.object_key) or action.object_key
        uploaded = len(action.body.get("payload", {}).get("data", []))
        self.state.setdefault("objects", {}).setdefault(action.object_type, {}).setdefault(meta_id, {})
        self.state["objects"][action.object_type][meta_id].setdefault("uploads", []).append(
            {
                "upload_key": action.source_key,
                "operation": action.body.get("operation", "ADD"),
                "rows": uploaded,
                "updated_at": utc_now(),
            }
        )
        updates = [
            ("AudienceUploads", action.source_key, "applied_at", utc_now()),
            ("AudienceUploads", action.source_key, "result", f"MOCK_UPLOADED_{uploaded}_ROWS"),
            ("AudienceUploads", action.source_key, "error", ""),
        ]
        return ActionResult(
            action=action,
            ok=True,
            meta_id=meta_id,
            message=f"Mock uploaded {uploaded} audience rows.",
            stdout=json.dumps({"id": meta_id, "rows": uploaded, "mock": True}),
            updates=updates,
        )

    def _duplicate(self, action: Action, context: dict[str, dict[str, str]]) -> ActionResult:
        source_id = context.get(action.object_type, {}).get(action.object_key) or action.object_key
        count = int(action.body.get("copy_count", 1))
        copied_ids = [
            _mock_id(action.object_type, f"{action.object_key}:{action.source_key}:{idx}")
            for idx in range(1, count + 1)
        ]
        self.state.setdefault("objects", {}).setdefault(action.object_type, {})
        for copied_id in copied_ids:
            self.state["objects"][action.object_type][copied_id] = {
                "source_id": source_id,
                "duplicate_job": action.source_key,
                "payload": action.body,
                "created_at": utc_now(),
            }
        updates = [
            ("DuplicateJobs", action.source_key, "applied_at", utc_now()),
            ("DuplicateJobs", action.source_key, "result_meta_ids", ",".join(copied_ids)),
            ("DuplicateJobs", action.source_key, "result", "MOCK_DUPLICATED"),
            ("DuplicateJobs", action.source_key, "error", ""),
        ]
        return ActionResult(
            action=action,
            ok=True,
            meta_id=",".join(copied_ids),
            message=f"Mock duplicated {action.object_type} {count} time(s).",
            stdout=json.dumps({"ids": copied_ids, "mock": True}),
            updates=updates,
        )

    def _patch(self, action: Action, context: dict[str, dict[str, str]]) -> ActionResult:
        meta_id = context.get(action.object_type, {}).get(action.object_key) or action.object_key
        self.state.setdefault("objects", {}).setdefault(action.object_type, {}).setdefault(meta_id, {})
        self.state["objects"][action.object_type][meta_id].update(action.body)
        updates = [
            ("BulkChanges", action.source_key, "applied_at", utc_now()),
            ("BulkChanges", action.source_key, "result", "MOCK_PATCHED"),
            ("BulkChanges", action.source_key, "error", ""),
        ]
        return ActionResult(
            action=action,
            ok=True,
            meta_id=meta_id,
            message=f"Mock patched {action.object_type}.",
            stdout=json.dumps({"id": meta_id, "body": action.body, "mock": True}),
            updates=updates,
        )

    def _delete(self, action: Action, context: dict[str, dict[str, str]]) -> ActionResult:
        meta_id = context.get(action.object_type, {}).get(action.object_key) or action.object_key
        self.state.setdefault("objects", {}).setdefault(action.object_type, {}).setdefault(meta_id, {})
        self.state["objects"][action.object_type][meta_id]["deleted"] = True
        updates = [
            ("BulkChanges", action.source_key, "applied_at", utc_now()),
            ("BulkChanges", action.source_key, "result", "MOCK_DELETED"),
            ("BulkChanges", action.source_key, "error", ""),
        ]
        table_config = OBJECT_CONFIG.get(action.object_type)
        if table_config and _row_exists(self.dataset, table_config[0], table_config[1], action.object_key):
            status_column = "desired_status" if "desired_status" in TABLES[table_config[0]].columns else "status"
            if status_column in TABLES[table_config[0]].columns:
                updates.append((table_config[0], action.object_key, status_column, "DELETED"))
        return ActionResult(
            action=action,
            ok=True,
            meta_id=meta_id,
            message=f"Mock deleted {action.object_type}.",
            stdout=json.dumps({"id": meta_id, "deleted": True, "mock": True}),
            updates=updates,
        )


class LiveMetaCliAdapter:
    def __init__(self, dataset: Dataset, account_override: str = ""):
        self.dataset = dataset
        self.account_override = account_override
        self._env = build_live_env(account_override)

    def account_id(self) -> str:
        return resolve_account(self.account_override)

    def apply(self, action: Action, context: dict[str, dict[str, str]]) -> ActionResult:
        if action.dry_run_only:
            return ActionResult(action=action, ok=False, message=action.reason)
        if action.executor == "graph":
            return self._call_graph(action, context)
        if action.object_type == "account" and action.operation == "create":
            return self._create_ad_account(action, context)
        command = [_resolve_any(token, context) for token in action.command]
        if command and command[0] == "meta":
            command[0] = os.environ.get("META_CLI_BIN", "meta")
        command = self._apply_account_override(command)
        try:
            completed = with_retry(lambda: _run_cli(command, self._env))
        except FileNotFoundError as exc:
            return ActionResult(action=action, ok=False, message=str(exc), stderr=str(exc))
        if completed is None:
            return ActionResult(action=action, ok=False, message="Live command produced no result.")
        completed = completed[2]

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

    def _apply_account_override(self, command: list[str]) -> list[str]:
        account = resolve_account(self.account_override)
        if not account:
            return command
        result = list(command)
        for idx, token in enumerate(result[:-1]):
            if token == "--ad-account-id":
                result[idx + 1] = account
        return result

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
        request = urllib.request.Request(url, data=data, method="POST")
        try:
            ok, message, body = with_retry(lambda: _urlopen_text(request))
        except Exception as exc:  # noqa: BLE001 - surface Meta/API errors in the workbook log.
            return ActionResult(action=action, ok=False, message="Ad account API create failed.", stderr=str(exc))
        if not ok:
            return ActionResult(action=action, ok=False, message="Ad account API create failed.", stderr=message)
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

    def _call_graph(self, action: Action, context: dict[str, dict[str, str]]) -> ActionResult:
        token = os.environ.get("ACCESS_TOKEN")
        if not token:
            return ActionResult(action=action, ok=False, message="ACCESS_TOKEN is required for Graph API actions.")
        request = build_graph_request(action, context, token=token)
        try:
            graph_ok, message, response_body = with_retry(lambda: _urlopen_text(request))
        except Exception as exc:  # noqa: BLE001 - keep Meta/API failures visible in PublishLog.
            return ActionResult(action=action, ok=False, message="Graph API action failed.", stderr=str(exc))
        if not graph_ok:
            return ActionResult(action=action, ok=False, message="Graph API action failed.", stderr=message)

        meta_id = _extract_duplicate_ids(response_body) if action.operation == "duplicate" else _extract_path(response_body, action.id_path)
        ok = bool(meta_id) or action.operation in {"update", "delete", "upload", "duplicate", "patch"}
        if ok and meta_id:
            context.setdefault(action.object_type, {})[action.object_key] = meta_id
        updates = self._updates_for_live_result(action, ok, meta_id, "")
        return ActionResult(
            action=action,
            ok=ok,
            meta_id=meta_id,
            message="Graph API action succeeded." if ok else "Graph API response did not include an ID.",
            stdout=response_body,
            updates=updates,
        )

    def _updates_for_live_result(self, action: Action, ok: bool, meta_id: str, error: str) -> list[tuple[str, str, str, Any]]:
        updates: list[tuple[str, str, str, Any]] = []
        if action.writeback and ok:
            for table_name, key, column, value in action.writeback:
                if isinstance(value, str):
                    resolved_value = value.replace("${result:id}", meta_id).replace("$result_id", meta_id).replace("$now", utc_now())
                    resolved_value = resolve_templates(resolved_value, {action.object_type: {action.object_key: meta_id}})
                else:
                    resolved_value = value
                updates.append((table_name, key, column, resolved_value))
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
        "audience": "mock_aud",
        "campaign": "mock_cmp",
        "adset": "mock_adset",
        "creative": "mock_crt",
        "ad": "mock_ad",
    }[object_type]
    digest = hashlib.sha1(f"{object_type}:{key}".encode("utf-8")).hexdigest()[:10]
    return f"{prefix}_{digest}"


def _run_cli(command: list[str], env: dict[str, str] | None = None):
    completed = subprocess.run(command, capture_output=True, text=True, check=False, env=env)
    return completed.returncode == 0, completed.stderr, completed


def _urlopen_text(request: urllib.request.Request):
    with urllib.request.urlopen(request, timeout=60) as response:
        body = response.read().decode("utf-8")
    return True, body, body


def _resolve_any(value: str, context: dict[str, dict[str, str]]) -> str:
    if value.startswith("${") and value.endswith("}"):
        return _resolve_placeholder(value, context)
    return value


def resolve_templates(value: Any, context: dict[str, dict[str, str]]) -> Any:
    if isinstance(value, str):
        if value.startswith("${") and value.endswith("}"):
            return _resolve_placeholder(value, context)
        result = value
        for object_type, values in context.items():
            for key, meta_id in values.items():
                result = result.replace(f"${{{object_type}:{key}}}", meta_id)
        return result
    if isinstance(value, list):
        return [resolve_templates(item, context) for item in value]
    if isinstance(value, dict):
        return {key: resolve_templates(item, context) for key, item in value.items()}
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


def _extract_path(stdout: str, path: str) -> str:
    if not path:
        return ""
    text = stdout.strip()
    if not text:
        return ""
    try:
        current: Any = json.loads(text)
    except json.JSONDecodeError:
        return ""
    for part in path.split("."):
        if not part:
            continue
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list):
            try:
                current = current[int(part)]
            except (ValueError, IndexError):
                return ""
        else:
            return ""
    return "" if current is None else str(current)


def _extract_duplicate_ids(stdout: str) -> str:
    text = stdout.strip()
    if not text:
        return ""
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return ""
    ids: list[str] = []

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key.endswith("_id") or key == "id":
                    if isinstance(item, (str, int)):
                        ids.append(str(item))
                else:
                    visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(parsed)
    return ",".join(dict.fromkeys(ids))


def build_graph_request(action: Action, context: dict[str, dict[str, str]], token: str) -> urllib.request.Request:
    version = os.environ.get("META_API_VERSION", "v21.0")
    endpoint = str(resolve_templates(action.endpoint, context)).lstrip("/")
    params = dict(resolve_templates(action.params, context))
    body = dict(resolve_templates(action.body, context))
    params["access_token"] = token
    method = action.method.upper()
    query = urllib.parse.urlencode(_json_ready(params), doseq=True)
    url = f"https://graph.facebook.com/{version}/{endpoint}"
    if method == "GET":
        url = f"{url}?{query}" if query else url
        return urllib.request.Request(url, method=method)
    data = urllib.parse.urlencode(_json_ready(body | {"access_token": token}), doseq=True).encode("utf-8")
    if method == "DELETE" and body:
        return urllib.request.Request(f"{url}?{query}" if query else url, data=data, method=method)
    if method == "DELETE":
        return urllib.request.Request(f"{url}?{query}" if query else url, method=method)
    return urllib.request.Request(f"{url}?{query}" if query and method not in {"POST", "PUT"} else url, data=data, method=method)


def _json_ready(values: dict[str, Any]) -> dict[str, Any]:
    ready: dict[str, Any] = {}
    for key, value in values.items():
        if value in (None, ""):
            continue
        if isinstance(value, (dict, list)):
            ready[key] = json.dumps(value, separators=(",", ":"))
        else:
            ready[key] = value
    return ready


def _row_exists(dataset: Dataset, table_name: str, key_column: str, key: str) -> bool:
    return any(clean(row.get(key_column)) == key for row in dataset.tables.get(table_name, []))
