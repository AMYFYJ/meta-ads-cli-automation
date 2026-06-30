from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from meta_ads_pipeline.adapters import _extract_id, build_live_env, is_sandbox_account, resolve_account
from meta_ads_pipeline.doctor import classify_account_access, classify_page_access, run_doctor


RUN_LIVE = os.environ.get("RUN_LIVE_TESTS") == "1" and bool(os.environ.get("ACCESS_TOKEN"))


class DoctorStructureTest(unittest.TestCase):
    """Always runnable (no credentials): verifies the doctor report shape and helpers."""

    def test_run_doctor_offline_report_shape(self) -> None:
        report = run_doctor(live=False)
        self.assertIn("ok", report)
        names = {check["name"] for check in report["checks"]}
        self.assertIn("python", names)
        self.assertIn("meta_cli", names)
        for check in report["checks"]:
            self.assertIn(check["status"], {"ok", "warn", "fail"})

    def test_meta_cli_missing_is_fail(self) -> None:
        # No `meta` binary in this environment -> the meta_cli check must fail.
        report = run_doctor(live=False)
        meta_cli = next(check for check in report["checks"] if check["name"] == "meta_cli")
        self.assertEqual(meta_cli["status"], "fail")
        self.assertFalse(report["ok"])

    def test_resolve_account_precedence(self) -> None:
        saved = {key: os.environ.get(key) for key in ("META_SANDBOX", "SANDBOX_AD_ACCOUNT_ID", "AD_ACCOUNT_ID")}
        try:
            os.environ["META_SANDBOX"] = "1"
            os.environ["SANDBOX_AD_ACCOUNT_ID"] = "act_sandbox"
            os.environ["AD_ACCOUNT_ID"] = "act_prod"
            self.assertEqual(resolve_account(""), "act_sandbox")
            self.assertEqual(resolve_account("act_explicit"), "act_explicit")
            self.assertTrue(is_sandbox_account("act_sandbox"))
            self.assertFalse(is_sandbox_account("act_prod"))
            self.assertEqual(build_live_env("")["AD_ACCOUNT_ID"], "act_sandbox")
            os.environ.pop("META_SANDBOX")
            self.assertEqual(resolve_account(""), "act_prod")
        finally:
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def test_extract_id_accepts_meta_cli_list_response(self) -> None:
        self.assertEqual(_extract_id('[{"id": "6999147683583"}]'), "6999147683583")


class AccountAccessClassifierTest(unittest.TestCase):
    """Offline coverage for the doctor account-access error mapping (no credentials)."""

    def test_successful_read_is_ok(self) -> None:
        check = classify_account_access("act_123", True, '{"id": "act_123", "name": "Test"}')
        self.assertEqual(check["status"], "ok")
        self.assertEqual(check["name"], "account_access")

    def test_no_ads_role_error_200_points_to_reconnect(self) -> None:
        body = '{"error": {"message": "(#200) Ad account owner has NOT grant ads_management or ads_read permission", "type": "OAuthException", "code": 200}}'
        check = classify_account_access("act_2012963492669349", False, body)
        self.assertEqual(check["status"], "fail")
        self.assertIn("ads_management", check["hint"])
        self.assertIn("SANDBOX_SETUP", check["hint"])

    def test_missing_permissions_object_does_not_exist_is_reconnect(self) -> None:
        body = (
            '{"error": {"message": "Unsupported get request. Object with ID '
            "'act_2012963492669349' does not exist, cannot be loaded due to missing "
            'permissions", "code": 100}}'
        )
        check = classify_account_access("act_2012963492669349", False, body)
        self.assertEqual(check["status"], "fail")
        self.assertIn("no ads role", check["hint"])

    def test_expired_token_is_token_hint_not_reconnect(self) -> None:
        body = '{"error": {"message": "Error validating access token: Session has expired", "type": "OAuthException", "code": 190}}'
        check = classify_account_access("act_123", False, body)
        self.assertEqual(check["status"], "fail")
        self.assertIn("scopes", check["hint"])
        self.assertNotIn("add your user", check["hint"])


class PageAccessClassifierTest(unittest.TestCase):
    """Offline coverage for the doctor Page advertising-access mapping (no credentials)."""

    def test_page_with_advertise_task_is_ok(self) -> None:
        body = '{"id": "1074742885732865", "name": "My Page", "tasks": ["MODERATE", "ANALYZE", "ADVERTISE", "CREATE_CONTENT", "MANAGE"]}'
        check = classify_page_access("1074742885732865", True, body)
        self.assertEqual(check["status"], "ok")
        self.assertIn("ADVERTISE", check["detail"])

    def test_page_readable_without_advertise_task_is_warn(self) -> None:
        body = '{"id": "1074742885732865", "name": "My Page", "tasks": ["ANALYZE"]}'
        check = classify_page_access("1074742885732865", True, body)
        self.assertEqual(check["status"], "warn")
        self.assertIn("advertising", check["hint"].lower())

    def test_page_not_accessible_is_fail(self) -> None:
        body = '{"error": {"message": "Unsupported get request. Object does not exist or missing permissions", "code": 100}}'
        check = classify_page_access("1074742885732865", False, body)
        self.assertEqual(check["status"], "fail")
        self.assertIn("manages this Page", check["hint"])


@unittest.skipUnless(RUN_LIVE, "set RUN_LIVE_TESTS=1 and ACCESS_TOKEN to run live sandbox tests")
class LiveSandboxTest(unittest.TestCase):
    """Opt-in: exercises the real meta-ads CLI against a sandbox ad account."""

    def setUp(self) -> None:
        self.sandbox = os.environ.get("SANDBOX_AD_ACCOUNT_ID", "")
        if not self.sandbox:
            self.skipTest("SANDBOX_AD_ACCOUNT_ID not set")

    def test_doctor_live_passes(self) -> None:
        report = run_doctor(live=True, account=self.sandbox)
        self.assertTrue(report["ok"], [check for check in report["checks"] if check["status"] == "fail"])

    def test_sandbox_apply_creates_objects(self) -> None:
        from meta_ads_pipeline.executor import execute_actions
        from meta_ads_pipeline.planner import build_plan
        from meta_ads_pipeline.storage import create_template, load_source
        from meta_ads_pipeline.assets import ensure_sample_assets

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workbook = root / "template.xlsx"
            ensure_sample_assets(str(root))
            create_template(str(workbook), with_sample=True)
            dataset = load_source(str(workbook))
            actions = build_plan(dataset)
            results, _, _ = execute_actions(dataset, actions, mode="live", account_override=self.sandbox)
            self.assertTrue(any(result.ok for result in results), [result.message for result in results])


if __name__ == "__main__":
    unittest.main()
