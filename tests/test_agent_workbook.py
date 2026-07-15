from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ads_agent.workbook import (
    WorkbookLockedError,
    assert_not_locked,
    commit_version,
    excel_lock_path,
    load_workbook_dataset,
    next_agent_change_id,
    summarize_workbook,
)
from meta_ads_pipeline.storage import create_template, load_source


@pytest.fixture()
def workbook(tmp_path: Path) -> Path:
    path = tmp_path / "current.xlsx"
    create_template(str(path), with_sample=True)
    return path


def test_commit_version_appends_and_archives(workbook: Path, tmp_path: Path) -> None:
    archive_dir = tmp_path / "archive"
    dataset = load_source(str(workbook))
    row = {"change_id": "chg_test_001", "operation": "SET_FIELD", "object_level": "adset",
           "object_key_or_meta_id": "adset_x", "field": "bid_amount_cents", "new_value": "500",
           "approval_status": "PENDING"}
    archive = commit_version(dataset, workbook, archive_dir, [], {"BulkChanges": [row]})
    assert archive.exists() and archive.parent == archive_dir
    reloaded = load_source(str(workbook))
    ids = [r["change_id"] for r in reloaded.tables["BulkChanges"]]
    assert "chg_test_001" in ids
    # a second commit produces a distinct archive file
    archive2 = commit_version(load_source(str(workbook)), workbook, archive_dir, [], {})
    assert archive2 != archive


def test_commit_version_refuses_excel_lock(workbook: Path, tmp_path: Path) -> None:
    excel_lock_path(workbook).write_text("locked")
    dataset = load_source(str(workbook))
    with pytest.raises(WorkbookLockedError):
        commit_version(dataset, workbook, tmp_path / "archive", [], {})


def test_assert_not_locked_passes_without_lock(workbook: Path) -> None:
    assert_not_locked(workbook)


def test_next_agent_change_id_namespacing() -> None:
    rows = [{"change_id": "chg_manual_0001"}, {"change_id": "chg_manual_0002"}]
    first = next_agent_change_id(rows)
    assert first.startswith("chg_agent_") and first.endswith("_001")
    rows.append({"change_id": first})
    second = next_agent_change_id(rows)
    assert second.endswith("_002") and second != first


def test_summarize_workbook_groups_bulk_changes(workbook: Path) -> None:
    dataset = load_source(str(workbook))
    dataset.tables["BulkChanges"] = [
        {"change_id": "a", "approval_status": "PENDING", "operation": "SET_FIELD"},
        {"change_id": "b", "approval_status": "APPROVED", "operation": "PAUSE"},
    ]
    summary = summarize_workbook(dataset)
    assert set(summary["BulkChanges"]) == {"PENDING", "APPROVED"}
    assert "Campaigns" in summary and "table_counts" in summary


def test_load_workbook_dataset_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_workbook_dataset(tmp_path / "nope.xlsx")
