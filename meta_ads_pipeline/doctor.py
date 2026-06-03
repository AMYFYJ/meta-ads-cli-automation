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
