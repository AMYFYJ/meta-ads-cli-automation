from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from meta_ads_pipeline.assets import ensure_sample_assets
from meta_ads_pipeline.executor import execute_actions
from meta_ads_pipeline.insights import generate_mock_insights
from meta_ads_pipeline.planner import build_plan
from meta_ads_pipeline.storage import create_template, load_source, save_with_updates


class InsightsBreakdownTest(unittest.TestCase):
    def test_mock_insights_can_emit_breakdown_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workbook = root / "template.xlsx"
            applied = root / "applied.xlsx"
            ensure_sample_assets(str(root))
            create_template(str(workbook), with_sample=True)
            dataset = load_source(str(workbook))
            _, updates, append_rows = execute_actions(dataset, build_plan(dataset), mode="mock", state_path=str(root / "state.json"))
            save_with_updates(dataset, str(applied), updates, append_rows)
            applied_dataset = load_source(str(applied))

            rows = generate_mock_insights(applied_dataset, breakdowns=["publisher_platform", "age"])["PerformanceSnapshots"]
            breakdown_rows = [row for row in rows if row["breakdown_type"]]

            self.assertGreater(len(rows), 2)
            self.assertTrue(any(row["publisher_platform"] == "facebook" for row in breakdown_rows))
            self.assertTrue(any(row["age"] == "25-34" for row in breakdown_rows))


if __name__ == "__main__":
    unittest.main()
