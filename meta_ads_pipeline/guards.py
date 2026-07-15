"""Safety guards shared by the pipeline CLI and the ads_agent layer."""

from __future__ import annotations

import json
from typing import Any

STATUS_KEYS = {"status", "desired_status", "status_option", "duplicate_status_option", "effective_status"}


def active_status_violations(actions: list[Any]) -> list[tuple[str, str]]:
    """Return (action_id, where) for every planned action that would set a status to ACTIVE."""
    violations: list[tuple[str, str]] = []
    for action in actions:
        for index, token in enumerate(action.command):
            if token == "--status" and index + 1 < len(action.command) and str(action.command[index + 1]).upper() == "ACTIVE":
                violations.append((action.action_id, "command --status ACTIVE"))
        for name, mapping in (("body", action.body), ("params", action.params), ("payload", action.payload)):
            path = find_active_status(mapping)
            if path:
                violations.append((action.action_id, f"{name}.{path}"))
    return violations


def find_active_status(value: Any, path: str = "") -> str:
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            if str(key).lower() in STATUS_KEYS and isinstance(child, str) and child.upper() == "ACTIVE":
                return child_path
            found = find_active_status(child, child_path)
            if found:
                return found
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found = find_active_status(child, f"{path}[{index}]")
            if found:
                return found
    elif isinstance(value, str) and value.strip().startswith(("{", "[")):
        try:
            return find_active_status(json.loads(value), path)
        except (ValueError, TypeError):
            return ""
    return ""
