from __future__ import annotations

import unittest

from meta_ads_pipeline.cli import _active_status_violations
from meta_ads_pipeline.models import Action


def _action(action_id: str, **kwargs) -> Action:
    defaults = {
        "operation": "create",
        "object_type": "campaign",
        "object_key": action_id,
        "command": [],
        "payload": {},
    }
    defaults.update(kwargs)
    return Action(action_id=action_id, **defaults)


class ForcePausedGuardTest(unittest.TestCase):
    def test_paused_actions_pass(self) -> None:
        actions = [
            _action("a1", command=["meta", "ads", "campaign", "create", "--status", "PAUSED"]),
            _action("a2", body={"status": "PAUSED"}),
        ]
        self.assertEqual(_active_status_violations(actions), [])

    def test_cli_status_active_flagged(self) -> None:
        actions = [_action("a1", command=["meta", "ads", "campaign", "create", "--status", "ACTIVE"])]
        self.assertEqual(_active_status_violations(actions), [("a1", "command --status ACTIVE")])

    def test_graph_body_active_flagged(self) -> None:
        actions = [_action("a1", body={"status": "ACTIVE"})]
        self.assertEqual(_active_status_violations(actions), [("a1", "body.status")])

    def test_json_string_patch_active_flagged(self) -> None:
        actions = [_action("a1", body={"value_json": '{"status": "ACTIVE"}'})]
        self.assertEqual(_active_status_violations(actions), [("a1", "body.value_json.status")])

    def test_nested_desired_status_active_flagged(self) -> None:
        actions = [_action("a1", body={"patch": {"desired_status": "ACTIVE"}})]
        self.assertEqual(_active_status_violations(actions), [("a1", "body.patch.desired_status")])

    def test_non_status_active_value_ignored(self) -> None:
        actions = [_action("a1", body={"name": "ACTIVE", "objective": "ACTIVE"})]
        self.assertEqual(_active_status_violations(actions), [])


if __name__ == "__main__":
    unittest.main()
