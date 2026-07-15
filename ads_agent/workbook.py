"""Workbook versioning for the agent layer.

The pipeline's save path is copy-on-write (``storage._save_xlsx_with_updates``
copies source -> output), and the verified live workflow chains applied
workbooks. ``commit_version`` reconciles both with a single canonical path the
human always opens: every write lands in ``data/archive/`` first, then is
promoted onto ``data/current.xlsx`` atomically.
"""

from __future__ import annotations

import datetime as _dt
import os
import shutil
from pathlib import Path
from typing import Any

from meta_ads_pipeline.models import Dataset
from meta_ads_pipeline.storage import load_source, save_with_updates


class WorkbookLockedError(RuntimeError):
    pass


def excel_lock_path(workbook: Path) -> Path:
    return workbook.parent / f"~${workbook.name}"


def assert_not_locked(workbook: Path) -> None:
    lock = excel_lock_path(workbook)
    if lock.exists():
        raise WorkbookLockedError(
            f"{workbook.name} appears to be open in Excel (found {lock.name}). "
            "Close the workbook and re-run."
        )


def load_workbook_dataset(workbook: Path) -> Dataset:
    if not workbook.exists():
        raise FileNotFoundError(
            f"Workbook not found: {workbook}. Set ADS_AGENT_WORKBOOK or create one "
            "(e.g. with examples/build_ad_campaign.py, saved to that path)."
        )
    return load_source(str(workbook))


def commit_version(
    dataset: Dataset,
    workbook: Path,
    archive_dir: Path,
    updates: list[tuple[str, str, str, Any]],
    append_rows: dict[str, list[dict[str, Any]]] | None = None,
    label: str = "",
) -> Path:
    """Write dataset changes to a new archived version, then promote it to the canonical path.

    Returns the archive path. Raises WorkbookLockedError if Excel has the file open.
    """
    assert_not_locked(workbook)
    archive_dir.mkdir(parents=True, exist_ok=True)
    archive_path = _new_archive_path(archive_dir, label)
    save_with_updates(dataset, str(archive_path), updates, append_rows or {})
    tmp = workbook.parent / f".{workbook.name}.tmp"
    workbook.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(archive_path, tmp)
    os.replace(tmp, workbook)
    return archive_path


def _new_archive_path(archive_dir: Path, label: str) -> Path:
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    suffix = f"_{label}" if label else ""
    candidate = archive_dir / f"ads_{stamp}{suffix}.xlsx"
    counter = 1
    while candidate.exists():
        candidate = archive_dir / f"ads_{stamp}{suffix}-{counter}.xlsx"
        counter += 1
    return candidate


def next_agent_change_id(rows: list[dict[str, Any]]) -> str:
    """Agent-namespaced change id: chg_agent_YYYYMMDD_NNN (never collides with chg_manual_*)."""
    stamp = _dt.date.today().strftime("%Y%m%d")
    existing = {str(row.get("change_id", "")) for row in rows}
    idx = 1
    while f"chg_agent_{stamp}_{idx:03d}" in existing:
        idx += 1
    return f"chg_agent_{stamp}_{idx:03d}"


# ---------------------------------------------------------------------------
# Summaries for the agent's read tools

_SUMMARY_COLUMNS = {
    "Campaigns": [
        "campaign_key", "meta_campaign_id", "name", "objective", "budget_mode",
        "daily_budget_cents", "lifetime_budget_cents", "bid_strategy",
        "desired_status", "approval_status", "last_result",
    ],
    "AdSets": [
        "adset_key", "campaign_key", "meta_adset_id", "name", "optimization_goal",
        "billing_event", "daily_budget_cents", "lifetime_budget_cents",
        "bid_amount_cents", "bid_strategy",
        "countries", "regions", "cities", "zips", "age_min", "age_max", "genders",
        "languages", "interests", "behaviors", "flexible_spec_json", "exclusions_json",
        "custom_audiences", "excluded_audiences", "placements",
        "advantage_audience", "advantage_placements", "desired_status",
        "approval_status", "last_result",
    ],
    "Audiences": [
        "audience_key", "meta_audience_id", "name", "audience_type", "subtype",
        "retention_days", "lookalike_ratio", "approval_status", "last_result",
    ],
    "Ads": [
        "ad_key", "adset_key", "meta_ad_id", "meta_creative_id", "name",
        "format", "headline", "primary_text", "cta", "destination_url",
        "variant_label", "desired_status", "approval_status", "last_result",
    ],
}


def summarize_workbook(dataset: Dataset, tables: list[str] | None = None) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "workbook": dataset.path,
        "table_counts": {name: len(rows) for name, rows in dataset.tables.items() if rows},
    }
    wanted = set(tables) if tables else set(_SUMMARY_COLUMNS) | {"BulkChanges"}
    for table, columns in _SUMMARY_COLUMNS.items():
        if table not in wanted:
            continue
        rows = dataset.tables.get(table, [])
        summary[table] = [
            {col: _clean(row.get(col)) for col in columns if _clean(row.get(col)) != ""}
            for row in rows
        ]
    if "BulkChanges" in wanted:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in dataset.tables.get("BulkChanges", []):
            status = (_clean(row.get("approval_status")) or "UNSET").upper()
            grouped.setdefault(status, []).append(
                {
                    col: _clean(row.get(col))
                    for col in (
                        "change_id", "operation", "object_level", "object_key_or_meta_id",
                        "field", "old_value", "new_value", "reason", "applied_at", "result",
                    )
                    if _clean(row.get(col)) != ""
                }
            )
        summary["BulkChanges"] = grouped
    in_process = [
        _clean(row.get("ad_key"))
        for row in dataset.tables.get("Ads", [])
        if "IN_PROCESS" in _clean(row.get("last_result")).upper()
    ]
    if in_process:
        summary["ads_in_review"] = {
            "note": "IN_PROCESS = Meta ad review in progress; settles to PAUSED. Not an anomaly.",
            "ad_keys": in_process,
        }
    return summary


def _clean(value: Any) -> str:
    return "" if value is None else str(value).strip()
