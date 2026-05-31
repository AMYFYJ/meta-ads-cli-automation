from __future__ import annotations

from typing import Any

from .adapters import context_from_dataset, make_adapter, payload_hash, utc_now
from .models import Action, ActionResult, Dataset, ValidationIssue


def execute_actions(
    dataset: Dataset,
    actions: list[Action],
    mode: str,
    state_path: str | None = None,
    continue_on_error: bool = True,
) -> tuple[list[ActionResult], list[tuple[str, str, str, Any]], dict[str, list[dict[str, Any]]]]:
    adapter = make_adapter(mode, dataset, state_path)
    context = context_from_dataset(dataset)
    results: list[ActionResult] = []
    updates: list[tuple[str, str, str, Any]] = []
    logs: list[dict[str, Any]] = []

    completed: set[str] = set()
    for action in actions:
        unmet = [dep for dep in action.depends_on if dep not in completed]
        if unmet:
            result = ActionResult(
                action=action,
                ok=False,
                message=f"Skipped because dependencies were not completed: {', '.join(unmet)}",
            )
        else:
            result = adapter.apply(action, context)
        results.append(result)
        updates.extend(result.updates)
        logs.append(result.as_log_row(f"log_{len(logs) + 1:04d}", utc_now(), mode, payload_hash(action.payload)))
        if result.ok:
            completed.add(action.action_id)
        elif not continue_on_error:
            break
    adapter.save()
    return results, updates, {"PublishLog": logs}


def validation_rows(issues: list[ValidationIssue]) -> dict[str, list[dict[str, Any]]]:
    timestamp = utc_now()
    return {
        "ValidationErrors": [
            issue.as_row(f"val_{idx:04d}", timestamp)
            for idx, issue in enumerate(issues, start=1)
        ]
    }
