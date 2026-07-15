"""Preflight checks for live/sandbox readiness.

`run_doctor` validates the pieces required to drive the real Meta `meta-ads` CLI:
Python version, CLI availability, environment variables, and (optionally) a read-only
token check against Meta. It never creates, edits, or deletes anything.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any

from .adapters import is_sandbox_account, redact_secrets, resolve_account
from .schema import clean


def run_doctor(live: bool = False, account: str = "") -> dict[str, Any]:
    checks: list[dict[str, Any]] = [
        _check_python_version(),
        _check_meta_cli(),
        _check_env_vars(live),
        _classify_account(resolve_account(account)),
    ]
    if live:
        token_checks = _check_token_graph(account)
        checks.extend(token_checks)
        # The CLI auth round-trip only matters once the token itself is valid.
        if all(check["status"] != "fail" for check in token_checks):
            cli_check = _check_token_readonly(account)
            cli_check["name"] = "cli_auth"
            checks.append(cli_check)
    ok = all(check["status"] != "fail" for check in checks)
    return {"checks": checks, "ok": ok}


def _check(name: str, status: str, detail: str, hint: str = "") -> dict[str, Any]:
    return {"name": name, "status": status, "detail": detail, "hint": hint}


def _check_python_version() -> dict[str, Any]:
    version = ".".join(str(part) for part in sys.version_info[:3])
    if sys.version_info < (3, 12):
        return _check(
            "python",
            "warn",
            f"Python {version} detected.",
            "The real meta-ads CLI requires Python 3.12+. Mock mode works on 3.11.",
        )
    return _check("python", "ok", f"Python {version} detected.")


def _check_meta_cli() -> dict[str, Any]:
    meta_bin = os.environ.get("META_CLI_BIN", "meta")
    try:
        completed = subprocess.run([meta_bin, "--version"], capture_output=True, text=True, check=False)
    except FileNotFoundError:
        return _check(
            "meta_cli",
            "fail",
            f"`{meta_bin}` not found on PATH.",
            "Install it (Python 3.12+): pip install 'meta-ads-workflow[live]'  or  pip install meta-ads",
        )
    if completed.returncode != 0:
        return _check("meta_cli", "fail", f"`{meta_bin} --version` exited {completed.returncode}.", completed.stderr.strip())
    return _check("meta_cli", "ok", (completed.stdout or completed.stderr).strip() or f"`{meta_bin}` available.")


def _check_env_vars(live: bool) -> dict[str, Any]:
    missing = [name for name in ("ACCESS_TOKEN", "AD_ACCOUNT_ID") if not clean(os.environ.get(name))]
    if missing:
        status = "fail" if live else "warn"
        return _check(
            "env",
            status,
            f"Missing env vars: {', '.join(missing)}.",
            "Set them in .env (see docs/LIVE_MODE.md). Required for live runs.",
        )
    optional = [name for name in ("BUSINESS_ID", "META_API_VERSION") if not clean(os.environ.get(name))]
    detail = "ACCESS_TOKEN and AD_ACCOUNT_ID are set."
    if optional:
        detail += f" Optional not set: {', '.join(optional)}."
    return _check("env", "ok", detail)


def _classify_account(account_id: str) -> dict[str, Any]:
    if not account_id:
        return _check("account", "warn", "No ad account resolved.", "Set AD_ACCOUNT_ID or pass --account act_...")
    if is_sandbox_account(account_id):
        return _check("account", "ok", f"{account_id} matches SANDBOX_AD_ACCOUNT_ID (sandbox, no real spend).")
    sandbox_set = bool(clean(os.environ.get("SANDBOX_AD_ACCOUNT_ID")))
    hint = (
        "This does NOT look like your sandbox account — live actions here may spend real money. "
        "Set SANDBOX_AD_ACCOUNT_ID and META_SANDBOX=1, or pass --require-sandbox to guard apply."
        if sandbox_set
        else "Sandbox status unknown (SANDBOX_AD_ACCOUNT_ID not set). Treat as production: real spend possible."
    )
    return _check("account", "warn", f"{account_id} is not flagged as sandbox.", hint)


def _check_token_readonly(account: str) -> dict[str, Any]:
    if not clean(os.environ.get("ACCESS_TOKEN")):
        return _check("token", "fail", "ACCESS_TOKEN not set; cannot validate.", "Set ACCESS_TOKEN.")
    from .adapters import build_live_env, with_retry  # local import to keep module import light

    meta_bin = os.environ.get("META_CLI_BIN", "meta")
    env = build_live_env(account)
    command = [meta_bin, "--output", "json", "--no-input", "ads", "adaccount", "list"]
    try:
        _, _, completed = with_retry(lambda: _run(command, env))
    except FileNotFoundError:
        return _check("token", "fail", f"`{meta_bin}` not found.", "Install meta-ads first.")
    if completed.returncode != 0:
        return _check("token", "fail", "Read-only token check failed.", redact_secrets(completed.stderr.strip()))
    try:
        json.loads(completed.stdout)
    except json.JSONDecodeError:
        return _check("token", "warn", "Token call succeeded but output was not JSON.", redact_secrets(completed.stdout[:200]))
    return _check("token", "ok", "Token validated via `meta ads adaccount list`.")


def _run(command: list[str], env: dict[str, str]):
    completed = subprocess.run(command, capture_output=True, text=True, check=False, env=env)
    return completed.returncode == 0, completed.stderr, completed


def _check_token_graph(account: str) -> list[dict[str, Any]]:
    """Validate the access token directly against the Graph API.

    This pinpoints the common sandbox failure modes that the CLI surfaces only as opaque
    errors: a malformed/mis-pasted token, an expired short-lived token, a token missing the
    ``ads_management``/``ads_read`` scopes, and — most importantly — a token minted from the
    wrong Meta app, which cannot see the sandbox ad account (Graph returns ``(#200) ... has
    NOT grant ads_management or ads_read permission``).
    """
    token = clean(os.environ.get("ACCESS_TOKEN"))
    if not token:
        return [_check("token", "fail", "ACCESS_TOKEN not set; cannot validate.", "Set ACCESS_TOKEN in your environment or .env.")]
    if not token.startswith("EAA"):
        return [
            _check(
                "token",
                "fail",
                "ACCESS_TOKEN is malformed (a Meta access token starts with 'EAA').",
                "Re-copy the token from the Graph API Explorer; watch for a stray leading character "
                "(e.g. 'EEAA...') or a dropped prefix.",
            )
        ]

    version = clean(os.environ.get("META_API_VERSION")) or "v21.0"
    data, err = _graph_get("debug_token", token, version, input_token=token)
    if err is not None:
        message = redact_secrets(_graph_error_message(err))
        expired = "expired" in message.lower() or "session has expired" in message.lower()
        hint = (
            "Graph API Explorer tokens are short-lived (~1-2h) and yours has expired. Generate a fresh "
            "token, then exchange it for a long-lived (~60 day) token so this stops happening."
            if expired
            else "Token rejected by Meta. Re-generate it from the app that owns the sandbox, with ads_management + ads_read."
        )
        return [_check("token", "fail", f"Token rejected by Meta: {message}", hint)]

    info = (data or {}).get("data", {})
    if not info.get("is_valid"):
        return [_check("token", "fail", "Meta reports this token is not valid.", "Generate a fresh token from the app that owns the sandbox.")]

    scopes = [str(scope) for scope in info.get("scopes", [])]
    app_label = str(info.get("application") or info.get("app_id") or "unknown app")
    expiry = _fmt_expiry(info.get("expires_at"))
    checks: list[dict[str, Any]] = []
    if "ads_management" not in scopes:
        status = "warn" if "ads_read" in scopes else "fail"
        checks.append(
            _check(
                "token",
                status,
                f"Token from {app_label} is valid ({expiry}) but missing the 'ads_management' scope.",
                "Re-generate with ads_management (create/publish) AND ads_read. ads_read alone cannot create ads.",
            )
        )
    else:
        checks.append(
            _check(
                "token",
                "ok",
                f"Token valid — app: {app_label}; scopes: {', '.join(scopes) or 'n/a'}; {expiry}.",
            )
        )
    checks.append(_check_sandbox_visibility(account, token, version, app_label))
    return checks


def _check_sandbox_visibility(account: str, token: str, version: str, app_label: str) -> dict[str, Any]:
    target = resolve_account(account)
    if not target:
        return _check("sandbox_access", "warn", "No ad account resolved to verify.", "Set AD_ACCOUNT_ID/SANDBOX_AD_ACCOUNT_ID or pass --account act_...")
    data, err = _graph_get("me/adaccounts", token, version, fields="id,name,account_status", limit=200)
    if err is not None:
        return _check("sandbox_access", "fail", f"Could not list ad accounts: {redact_secrets(_graph_error_message(err))}", "")
    accounts = (data or {}).get("data", [])
    ids = {str(acct.get("id")) for acct in accounts}
    if target in ids:
        name = next((acct.get("name") for acct in accounts if str(acct.get("id")) == target), "")
        return _check("sandbox_access", "ok", f"Token can see target account {target} ({name}).")
    visible = ", ".join(sorted(acct_id for acct_id in ids if acct_id)) or "(none)"
    return _check(
        "sandbox_access",
        "fail",
        f"Target account {target} is NOT visible to this token. Visible accounts: {visible}.",
        "Sandbox ad accounts are owned by one specific Meta app. Generate the token from that SAME app "
        f"(the app where {target} was created, currently authenticating as {app_label}), as an Admin/Developer/Tester "
        "of it, with ads_management + ads_read. A token from any other app cannot see the sandbox.",
    )


def _graph_get(endpoint: str, token: str, version: str, **params: Any) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    params["access_token"] = token
    query = urllib.parse.urlencode(params)
    url = f"https://graph.facebook.com/{version}/{endpoint.lstrip('/')}?{query}"
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8")), None
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        try:
            return None, json.loads(body)
        except json.JSONDecodeError:
            return None, {"error": {"message": body}}
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        return None, {"error": {"message": str(exc)}}


def _graph_error_message(err: dict[str, Any]) -> str:
    error = err.get("error") if isinstance(err, dict) else None
    if isinstance(error, dict):
        return str(error.get("message") or error)
    return str(error or err)


def _fmt_expiry(value: Any) -> str:
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        return "expiry unknown"
    if seconds == 0:
        return "no expiry (long-lived)"
    return "expires " + datetime.fromtimestamp(seconds, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
