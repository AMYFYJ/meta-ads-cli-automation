from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from meta_ads_pipeline.assets import ensure_sample_assets
from meta_ads_pipeline.planner import build_plan
from meta_ads_pipeline.storage import create_template, load_source
from meta_ads_pipeline.validators import has_blocking_errors, validate_dataset


class AudiencePlanningTest(unittest.TestCase):
    def test_sample_audiences_plan_graph_actions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workbook = root / "template.xlsx"
            ensure_sample_assets(str(root))
            create_template(str(workbook), with_sample=True)
            dataset = load_source(str(workbook))

            issues = validate_dataset(dataset)
            self.assertFalse(has_blocking_errors(issues), [issue.message for issue in issues])
            actions = build_plan(dataset)
            audience_actions = [action for action in actions if action.object_type == "audience"]

            self.assertEqual(len([action for action in audience_actions if action.operation == "create"]), 4)
            self.assertEqual(len([action for action in audience_actions if action.operation == "upload"]), 1)
            self.assertTrue(all(action.executor == "graph" for action in audience_actions))

            lookalike = next(action for action in audience_actions if action.object_key == "aud_vip_lookalike_us")
            self.assertIn("015_create_audience_aud_vip_buyers", lookalike.depends_on)
            self.assertEqual(lookalike.body["subtype"], "LOOKALIKE")
            self.assertEqual(lookalike.body["origin_audience_id"], "${audience:aud_vip_buyers}")

            upload = next(action for action in audience_actions if action.operation == "upload")
            self.assertEqual(upload.endpoint, "${audience:aud_vip_buyers}/users")
            self.assertEqual(upload.body["payload"]["schema"], ["EMAIL", "FN", "LN"])
            self.assertEqual(len(upload.body["payload"]["data"]), 2)

    def test_lookalike_requires_source_audience(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workbook = root / "template.xlsx"
            ensure_sample_assets(str(root))
            create_template(str(workbook), with_sample=True)
            dataset = load_source(str(workbook))
            dataset.tables["Audiences"][1]["source_audience_key"] = ""

            issues = validate_dataset(dataset)

            self.assertTrue(any(issue.table == "Audiences" and issue.field == "source_audience_key" for issue in issues))


if __name__ == "__main__":
    unittest.main()
