from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from meta_ads_pipeline.assets import ensure_sample_assets
from meta_ads_pipeline.executor import execute_actions
from meta_ads_pipeline.optimization import generate_optimization_changes
from meta_ads_pipeline.planner import build_plan
from meta_ads_pipeline.storage import create_template, load_source, save_with_updates
from meta_ads_pipeline.validators import has_blocking_errors, validate_dataset


class BulkOptimizationTest(unittest.TestCase):
    def test_expanded_bulk_operations_plan_graph_and_cli_actions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workbook = root / "template.xlsx"
            ensure_sample_assets(str(root))
            create_template(str(workbook), with_sample=True)
            dataset = load_source(str(workbook))

            self.assertFalse(has_blocking_errors(validate_dataset(dataset)))
            bulk_actions = [action for action in build_plan(dataset) if action.source_table == "BulkChanges"]

            self.assertEqual(len(bulk_actions), 4)
            patch = next(action for action in bulk_actions if action.operation == "patch")
            self.assertEqual(patch.executor, "graph")
            self.assertEqual(patch.body["targeting_automation"]["advantage_audience"], 0)
            set_field = next(action for action in bulk_actions if action.source_key == "chg_raise_campaign_budget")
            self.assertEqual(set_field.executor, "cli")

    def test_campaign_bid_strategy_bulk_change_uses_graph_patch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workbook = root / "template.xlsx"
            ensure_sample_assets(str(root))
            create_template(str(workbook), with_sample=True)
            dataset = load_source(str(workbook))
            dataset.tables["BulkChanges"].append(
                {
                    "change_id": "chg_change_bid_strategy",
                    "operation": "SET_FIELD",
                    "object_level": "campaign",
                    "object_key_or_meta_id": "cmp_spring_launch",
                    "field": "bid_strategy",
                    "old_value": "LOWEST_COST_WITHOUT_CAP",
                    "new_value": "LOWEST_COST_WITH_BID_CAP",
                    "value_json": "",
                    "approval_status": "APPROVED",
                }
            )

            self.assertFalse(has_blocking_errors(validate_dataset(dataset)))
            action = next(action for action in build_plan(dataset) if action.source_key == "chg_change_bid_strategy")

            self.assertEqual(action.executor, "graph")
            self.assertEqual(action.body, {"bid_strategy": "LOWEST_COST_WITH_BID_CAP"})

    def test_optimization_rules_generate_bulk_changes_from_snapshots(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workbook = root / "template.xlsx"
            applied = root / "applied.xlsx"
            optimized = root / "optimized.xlsx"
            ensure_sample_assets(str(root))
            create_template(str(workbook), with_sample=True)
            dataset = load_source(str(workbook))
            _, updates, append_rows = execute_actions(dataset, build_plan(dataset), mode="mock", state_path=str(root / "state.json"))
            snapshot = {
                "snapshot_key": "snap_manual_high_cpa",
                "date": "2026-06-05",
                "object_level": "ad",
                "meta_object_id": "mock_ad_manual",
                "campaign_key": "cmp_spring_launch",
                "adset_key": "adset_prospecting_us",
                "ad_key": "ad_prospecting_static_a",
                "cost_per_result": "55",
                "roas": "4.2",
            }
            append_rows.setdefault("PerformanceSnapshots", []).append(snapshot)
            save_with_updates(dataset, str(applied), updates, append_rows)
            applied_dataset = load_source(str(applied))

            changes = generate_optimization_changes(applied_dataset)
            save_with_updates(applied_dataset, str(optimized), [], {"BulkChanges": changes})
            optimized_dataset = load_source(str(optimized))
            generated = [row for row in optimized_dataset.tables["BulkChanges"] if row["change_id"].startswith("opt_")]

            self.assertGreaterEqual(len(generated), 2)
            self.assertTrue(any(row["field"] == "desired_status" and row["new_value"] == "PAUSED" for row in generated))
            self.assertTrue(any(row["object_level"] == "campaign" and row["field"] == "daily_budget_cents" for row in generated))


if __name__ == "__main__":
    unittest.main()
