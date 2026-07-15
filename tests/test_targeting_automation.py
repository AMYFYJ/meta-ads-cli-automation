from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from meta_ads_pipeline.assets import ensure_sample_assets
from meta_ads_pipeline.planner import build_plan
from meta_ads_pipeline.storage import create_template, load_source
from meta_ads_pipeline.validators import has_blocking_errors, validate_dataset


class TargetingAutomationTest(unittest.TestCase):
    def test_adsets_with_targeting_and_automation_use_graph_payloads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workbook = root / "template.xlsx"
            ensure_sample_assets(str(root))
            create_template(str(workbook), with_sample=True)
            dataset = load_source(str(workbook))

            issues = validate_dataset(dataset)
            self.assertFalse(has_blocking_errors(issues), [issue.message for issue in issues])
            actions = build_plan(dataset)
            adset_actions = [action for action in actions if action.object_type == "adset" and action.operation == "create"]

            self.assertEqual(len(adset_actions), 2)
            self.assertTrue(all(action.executor == "graph" for action in adset_actions))

            prospecting = next(action for action in adset_actions if action.object_key == "adset_prospecting_us")
            self.assertEqual(prospecting.body["targeting"]["geo_locations"]["countries"], ["US"])
            self.assertEqual(prospecting.body["targeting_automation"]["advantage_audience"], 1)
            self.assertNotIn("publisher_platforms", prospecting.body["targeting"])
            self.assertEqual(prospecting.body["promoted_object"]["custom_event_type"], "PURCHASE")

            retargeting = next(action for action in adset_actions if action.object_key == "adset_retargeting_us")
            self.assertEqual(retargeting.body["targeting_automation"]["advantage_audience"], 0)
            self.assertEqual(retargeting.body["targeting"]["publisher_platforms"], ["facebook", "instagram"])
            self.assertEqual(
                retargeting.body["targeting"]["custom_audiences"],
                [{"id": "${audience:aud_site_visitors_30d}"}],
            )
            self.assertEqual(
                retargeting.body["targeting"]["excluded_custom_audiences"],
                [{"id": "${audience:aud_vip_buyers}"}],
            )
            self.assertIn("015_create_audience_aud_site_visitors_30d", retargeting.depends_on)
            self.assertIn("015_create_audience_aud_vip_buyers", retargeting.depends_on)
            self.assertEqual(
                prospecting.body["targeting"]["interests"],
                [{"id": "6003306084421", "name": "Yoga"}, {"id": "6003384248805", "name": "Fitness and wellness"}],
            )

    def test_advantage_placement_toggle_strips_manual_placements(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workbook = root / "template.xlsx"
            ensure_sample_assets(str(root))
            create_template(str(workbook), with_sample=True)
            dataset = load_source(str(workbook))
            adset = dataset.tables["AdSets"][1]  # retargeting: manual placements
            adset["advantage_placements"] = "TRUE"

            actions = build_plan(dataset)
            retargeting = next(
                action for action in actions
                if action.object_type == "adset" and action.object_key == "adset_retargeting_us"
            )

            for field in ("publisher_platforms", "facebook_positions", "instagram_positions", "device_platforms"):
                self.assertNotIn(field, retargeting.body["targeting"])


if __name__ == "__main__":
    unittest.main()
