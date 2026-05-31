from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ValidationIssue:
    severity: str
    table: str
    row_key: str
    field: str
    message: str

    def as_row(self, validation_id: str, timestamp: str) -> dict[str, Any]:
        return {
            "validation_id": validation_id,
            "timestamp": timestamp,
            "severity": self.severity,
            "table": self.table,
            "row_key": self.row_key,
            "field": self.field,
            "message": self.message,
        }


@dataclass
class Dataset:
    path: str
    kind: str
    tables: dict[str, list[dict[str, Any]]]


@dataclass
class Action:
    action_id: str
    operation: str
    object_type: str
    object_key: str
    command: list[str]
    payload: dict[str, Any]
    depends_on: list[str] = field(default_factory=list)
    reason: str = ""
    source_table: str = ""
    source_key: str = ""
    dry_run_only: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "operation": self.operation,
            "object_type": self.object_type,
            "object_key": self.object_key,
            "depends_on": self.depends_on,
            "reason": self.reason,
            "source_table": self.source_table,
            "source_key": self.source_key,
            "dry_run_only": self.dry_run_only,
            "command": self.command,
            "payload": self.payload,
        }


@dataclass
class ActionResult:
    action: Action
    ok: bool
    meta_id: str = ""
    message: str = ""
    stdout: str = ""
    stderr: str = ""
    updates: list[tuple[str, str, str, Any]] = field(default_factory=list)

    def as_log_row(self, log_id: str, timestamp: str, mode: str, payload_hash: str) -> dict[str, Any]:
        return {
            "log_id": log_id,
            "timestamp": timestamp,
            "mode": mode,
            "action_id": self.action.action_id,
            "operation": self.action.operation,
            "object_type": self.action.object_type,
            "object_key": self.action.object_key,
            "meta_id": self.meta_id,
            "command": " ".join(self.action.command),
            "payload_hash": payload_hash,
            "status": "OK" if self.ok else "ERROR",
            "message": self.message,
            "stdout": self.stdout,
            "stderr": self.stderr,
        }
