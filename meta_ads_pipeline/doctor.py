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
import urllib.parse
import urllib.request
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
        checks.append(_check_token_readonly(account))
        checks.append(_check_account_access(account))
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
            "Set them (see docs/SANDBOX_SETUP.md). Required for live/sandbox runs.",
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


# Reconnect the sandbox account to your token. Shown whenever the token has no
# ads role on the target account (the (#200)/"does not exist due to missing
# permissions" family), which is the most common sandbox onboarding failure.
RECONNECT_HINT = (
    "Your token has no ads role on this account. This is a Meta-side fix, not a CLI bug: "
    "(1) generate the token from the SAME Meta app that owns the sandbox account, "
    "(2) add your user to the sandbox ad account with the ads_management + ads_read tasks "
    "(App Dashboard -> Marketing API -> Tools -> sandbox ad account -> users), then "
    "(3) re-run `doctor --live`. Note: sandbox accounts often do NOT appear in /me/adaccounts "
    "even when usable, so a direct read is the real test. See docs/SANDBOX_SETUP.md (Troubleshooting)."
)


def _check_account_access(account: str) -> dict[str, Any]:
    """Verify the token can actually *read* the resolved target account.

    ``adaccount list`` validating the token is not enough: a token can list its
    personal accounts while having no role on the sandbox account. This does a
    direct read of the resolved account so the failure surfaces at preflight
    instead of later at image upload / creative create.
    """
    account_id = resolve_account(account)
    if not account_id:
        return _check(
            "account_access",
            "warn",
            "No ad account resolved; skipping access check.",
            "Set AD_ACCOUNT_ID / SANDBOX_AD_ACCOUNT_ID or pass --account act_...",
        )
    token = clean(os.environ.get("ACCESS_TOKEN"))
    if not token:
        return _check("account_access", "fail", "ACCESS_TOKEN not set; cannot check account access.", "Set ACCESS_TOKEN.")
    from .adapters import with_retry  # local import to keep module import light

    version = os.environ.get("META_API_VERSION", "v21.0")
    query = urllib.parse.urlencode({"fields": "id,name,account_status", "access_token": token})
    url = f"https://graph.facebook.com/{version}/{account_id}?{query}"
    try:
        result = with_retry(lambda: _graph_get(url))
    except Exception as exc:  # noqa: BLE001 - network/URL errors become a FAIL with the raw reason.
        result = (False, str(exc), None)
    ok, body, _ = result if result else (False, "Account read produced no result.", None)
    return classify_account_access(account_id, ok, body or "")


def _graph_get(url: str):
    with urllib.request.urlopen(url, timeout=60) as response:  # noqa: S310 - fixed https graph.facebook.com host.
        body = response.read().decode("utf-8")
    return True, body, body


def classify_account_access(account_id: str, ok: bool, body: str) -> dict[str, Any]:
    """Map a Graph account-read result into a doctor check (pure; unit-testable).

    ``ok`` is whether the read succeeded; ``body`` is the JSON response or error
    body. The permission family ((#200), "does not grant", "does not exist /
    missing permissions") maps to the sandbox reconnect hint; invalid/expired
    tokens map to a token hint.
    """
    body = body or ""
    if ok:
        sandbox = is_sandbox_account(account_id)
        suffix = " (sandbox, no real spend)" if sandbox else ""
        return _check("account_access", "ok", f"Token can read {account_id}{suffix}.")
    low = body.lower()
    detail = _first_error_message(body) or "account read failed"
    # Order matters: permission errors also carry type "OAuthException", so the
    # reconnect family is matched before the generic token family.
    reconnect_markers = (
        "(#200)",
        "(#803)",
        "has not grant",
        "have not grant",
        "does not grant",
        "ads_management or ads_read",
        "cannot be loaded due to missing",
        "missing permission",
        "does not have permission",
        "do not have permission",
        "does not exist",
    )
    token_markers = (
        "(#190)",
        "session has expired",
        "session is invalid",
        "malformed access token",
        "invalid oauth access token",
        "access token could not be decrypted",
    )
    if any(marker in low for marker in reconnect_markers):
        return _check("account_access", "fail", f"Token cannot access {account_id}: {detail}", RECONNECT_HINT)
    if any(marker in low for marker in token_markers):
        return _check(
            "account_access",
            "fail",
            f"Token rejected reading {account_id}: {detail}",
            "Token is invalid/expired or missing the ads_management + ads_read scopes. "
            "Regenerate it in the Graph API Explorer with those scopes. See docs/SANDBOX_SETUP.md.",
        )
    return _check("account_access", "fail", f"Account read failed for {account_id}: {detail}", redact_secrets(detail))


def _first_error_message(body: str) -> str:
    text = (body or "").strip()
    if not text:
        return ""
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return redact_secrets(text[:200])
    if isinstance(parsed, dict) and isinstance(parsed.get("error"), dict):
        message = clean(parsed["error"].get("message"))
        if message:
            return redact_secrets(message)
    return redact_secrets(text[:200])
