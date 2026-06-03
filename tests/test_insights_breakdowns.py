from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from meta_ads_pipeline.assets import ensure_sample_assets
from meta_ads_pipeline.executor import execute_actions
from meta_ads_pipeline.insights import generate_mock_insights, get_live_insights
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

    def test_live_insights_command_matches_meta_cli_1_surface(self) -> None:
        calls: list[list[str]] = []

        def fake_run(command, **kwargs):
            calls.append(command)
            return subprocess.CompletedProcess(command, 0, "[]", "")

        saved = os.environ.get("META_CLI_BIN")
        try:
            os.environ["META_CLI_BIN"] = "meta"
            with patch("meta_ads_pipeline.insights.subprocess.run", side_effect=fake_run):
                ok, message, rows = get_live_insights(date_preset="last_7d", level="campaign", account="act_123")
        finally:
            if saved is None:
                os.environ.pop("META_CLI_BIN", None)
            else:
                os.environ["META_CLI_BIN"] = saved

        self.assertTrue(ok, message)
        self.assertEqual(rows, [])
        command = calls[0]
        self.assertNotIn("--level", command)
        self.assertLess(command.index("--ad-account-id"), command.index("insights"))
        self.assertEqual(command[command.index("--ad-account-id") + 1], "act_123")


if __name__ == "__main__":
    unittest.main()
