"""Duplication now runs through BulkChanges rows with operation=DUPLICATE
(the separate DuplicateJobs tab was folded away in the tab consolidation)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from meta_ads_pipeline.assets import ensure_sample_assets
from meta_ads_pipeline.executor import execute_actions
from meta_ads_pipeline.planner import build_plan
from meta_ads_pipeline.storage import create_template, load_source, save_with_updates
from meta_ads_pipeline.validators import has_blocking_errors, validate_dataset


def _duplicate_change(change_id: str, level: str, target: str, copies: str, value_json: str = "") -> dict[str, str]:
    return {
        "change_id": change_id,
        "operation": "DUPLICATE",
        "object_level": level,
        "object_key_or_meta_id": target,
        "field": "",
        "old_value": "",
        "new_value": copies,
        "value_json": value_json,
        "effective_at": "",
        "reason": "test duplicate",
        "requested_by": "test",
        "approval_status": "APPROVED",
        "applied_at": "",
        "result": "",
        "error": "",
    }


class DuplicateChangesTest(unittest.TestCase):
    def test_duplicate_changes_plan_copy_endpoints_and_dependencies(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workbook = root / "template.xlsx"
            ensure_sample_assets(str(root))
            create_template(str(workbook), with_sample=True)
            dataset = load_source(str(workbook))
            dataset.tables["BulkChanges"].extend(
                [
                    _duplicate_change("chg_dup_ad", "ad", "ad_prospecting_static_a", "2"),
                    _duplicate_change(
                        "chg_dup_adset", "adset", "adset_retargeting_us", "1",
                        '{"deep_copy": true, "start_time": "2026-06-18T09:00:00-04:00"}',
                    ),
                ]
            )

            self.assertFalse(has_blocking_errors(validate_dataset(dataset)))
            duplicate_actions = [action for action in build_plan(dataset) if action.operation == "duplicate"]

            self.assertEqual(len(duplicate_actions), 2)
            self.assertTrue(all(action.executor == "graph" for action in duplicate_actions))

            ad_duplicate = next(action for action in duplicate_actions if action.object_type == "ad")
            self.assertEqual(ad_duplicate.endpoint, "${ad:ad_prospecting_static_a}/copies")
            self.assertEqual(ad_duplicate.body["copy_count"], "2")
            self.assertIn("040_create_ad_ad_prospecting_static_a", ad_duplicate.depends_on)

            adset_duplicate = next(action for action in duplicate_actions if action.object_type == "adset")
            self.assertTrue(adset_duplicate.body["deep_copy"])
            self.assertEqual(adset_duplicate.body["start_time"], "2026-06-18T09:00:00-04:00")

    def test_mock_duplicate_writeback_records_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workbook = root / "template.xlsx"
            applied = root / "applied.xlsx"
            state = root / "state.json"
            ensure_sample_assets(str(root))
            create_template(str(workbook), with_sample=True)
            queued = root / "queued.xlsx"
            save_with_updates(
                load_source(str(workbook)), str(queued), [],
                {"BulkChanges": [_duplicate_change("chg_dup_ad", "ad", "ad_prospecting_static_a", "2")]},
            )
            dataset = load_source(str(queued))

            results, updates, append_rows = execute_actions(dataset, build_plan(dataset), mode="mock", state_path=str(state))
            self.assertTrue(all(result.ok for result in results), [result.message for result in results])
            save_with_updates(dataset, str(applied), updates, append_rows)
            applied_dataset = load_source(str(applied))

            row = next(r for r in applied_dataset.tables["BulkChanges"] if r["change_id"] == "chg_dup_ad")
            self.assertTrue(str(row["result"]).startswith("MOCK_DUPLICATED"))
            self.assertTrue(row["applied_at"])


if __name__ == "__main__":
    unittest.main()
