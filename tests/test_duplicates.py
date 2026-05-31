from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from meta_ads_pipeline.assets import ensure_sample_assets
from meta_ads_pipeline.executor import execute_actions
from meta_ads_pipeline.planner import build_plan
from meta_ads_pipeline.storage import create_template, load_source, save_with_updates
from meta_ads_pipeline.validators import has_blocking_errors, validate_dataset


class DuplicateJobsTest(unittest.TestCase):
    def test_duplicate_jobs_plan_copy_endpoints_and_dependencies(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workbook = root / "template.xlsx"
            ensure_sample_assets(str(root))
            create_template(str(workbook), with_sample=True)
            dataset = load_source(str(workbook))

            self.assertFalse(has_blocking_errors(validate_dataset(dataset)))
            duplicate_actions = [action for action in build_plan(dataset) if action.operation == "duplicate"]

            self.assertEqual(len(duplicate_actions), 2)
            self.assertTrue(all(action.executor == "graph" for action in duplicate_actions))

            ad_duplicate = next(action for action in duplicate_actions if action.object_type == "ad")
            self.assertEqual(ad_duplicate.endpoint, "${ad:ad_prospecting_static_a}/copies")
            self.assertEqual(ad_duplicate.body["destination_adset_id"], "${adset:adset_prospecting_us}")
            self.assertEqual(ad_duplicate.body["copy_count"], "2")
            self.assertIn("040_create_ad_ad_prospecting_static_a", ad_duplicate.depends_on)

            adset_duplicate = next(action for action in duplicate_actions if action.object_type == "adset")
            self.assertEqual(adset_duplicate.body["destination_campaign_id"], "${campaign:cmp_spring_launch}")
            self.assertTrue(adset_duplicate.body["deep_copy"])
            self.assertEqual(adset_duplicate.body["start_time"], "2026-06-18T09:00:00-04:00")

    def test_mock_duplicate_writeback_records_result_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workbook = root / "template.xlsx"
            applied = root / "applied.xlsx"
            state = root / "state.json"
            ensure_sample_assets(str(root))
            create_template(str(workbook), with_sample=True)
            dataset = load_source(str(workbook))

            results, updates, append_rows = execute_actions(dataset, build_plan(dataset), mode="mock", state_path=str(state))
            self.assertTrue(all(result.ok for result in results))
            save_with_updates(dataset, str(applied), updates, append_rows)
            applied_dataset = load_source(str(applied))

            first_job = applied_dataset.tables["DuplicateJobs"][0]
            self.assertEqual(first_job["result"], "MOCK_DUPLICATED")
            self.assertEqual(len(first_job["result_meta_ids"].split(",")), 2)


if __name__ == "__main__":
    unittest.main()
