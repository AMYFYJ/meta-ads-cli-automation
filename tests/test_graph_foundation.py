from __future__ import annotations

import os
import unittest
from urllib.parse import parse_qs, urlparse

from meta_ads_pipeline.adapters import build_graph_request, resolve_templates
from meta_ads_pipeline.models import Action


class GraphFoundationTest(unittest.TestCase):
    def test_resolve_templates_handles_nested_payloads(self) -> None:
        context = {"campaign": {"cmp_a": "123"}, "audience": {"aud_a": "456"}}
        payload = {
            "campaign_id": "${campaign:cmp_a}",
            "targeting": {"custom_audiences": [{"id": "${audience:aud_a}"}]},
            "name": "Copy of ${campaign:cmp_a}",
        }

        resolved = resolve_templates(payload, context)

        self.assertEqual(resolved["campaign_id"], "123")
        self.assertEqual(resolved["targeting"]["custom_audiences"][0]["id"], "456")
        self.assertEqual(resolved["name"], "Copy of 123")

    def test_build_graph_request_encodes_json_body_and_token(self) -> None:
        old_version = os.environ.get("META_API_VERSION")
        os.environ["META_API_VERSION"] = "v21.0"
        try:
            action = Action(
                action_id="test_graph",
                operation="create",
                object_type="audience",
                object_key="aud_a",
                command=[],
                payload={},
                executor="graph",
                method="POST",
                endpoint="${account:acct_a}/customaudiences",
                body={
                    "name": "VIP Buyers",
                    "subtype": "CUSTOM",
                    "customer_file_source": "USER_PROVIDED_ONLY",
                    "rule": {"inclusions": [{"event_sources": [{"id": "${dataset:pixel_a}"}]}]},
                },
            )
            request = build_graph_request(
                action,
                {"account": {"acct_a": "act_123"}, "dataset": {"pixel_a": "999"}},
                token="token_abc",
            )
            body = request.data.decode("utf-8")
            parsed_body = parse_qs(body)

            self.assertEqual(request.get_method(), "POST")
            self.assertEqual(request.full_url, "https://graph.facebook.com/v21.0/act_123/customaudiences")
            self.assertEqual(parsed_body["access_token"], ["token_abc"])
            self.assertEqual(parsed_body["name"], ["VIP Buyers"])
            self.assertIn('"999"', parsed_body["rule"][0])
        finally:
            if old_version is None:
                os.environ.pop("META_API_VERSION", None)
            else:
                os.environ["META_API_VERSION"] = old_version

    def test_build_graph_get_request_puts_params_in_query(self) -> None:
        action = Action(
            action_id="test_get",
            operation="read",
            object_type="campaign",
            object_key="cmp_a",
            command=[],
            payload={},
            executor="graph",
            method="GET",
            endpoint="${campaign:cmp_a}/insights",
            params={"fields": "spend,impressions", "limit": 25},
        )

        request = build_graph_request(action, {"campaign": {"cmp_a": "123"}}, token="token_abc")
        parsed = urlparse(request.full_url)
        query = parse_qs(parsed.query)

        self.assertEqual(request.get_method(), "GET")
        self.assertEqual(parsed.path, "/v21.0/123/insights")
        self.assertEqual(query["fields"], ["spend,impressions"])
        self.assertEqual(query["limit"], ["25"])
        self.assertEqual(query["access_token"], ["token_abc"])


if __name__ == "__main__":
    unittest.main()
