import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts.evaluate_agent_filter import RecordedClient, compare, token_cost, validate_baseline
from agent_filter import AgentJobFilter


def decision(row_id, keep):
    return {"id": row_id, "keep": keep, "reason": "test evidence"}


class AgentEvaluationTests(unittest.TestCase):
    def test_baseline_aligns_by_id_and_rejects_wrong_postings(self):
        rows = [{"title": "HR intern", "link": "https://example.com/1"},
                {"title": "Senior HR", "link": "https://example.com/2"}]
        baseline = {"input_count": 2, "decisions": [
            {**decision(1, False), **rows[1]}, {**decision(0, True), **rows[0]},
        ]}
        self.assertEqual(validate_baseline(rows, baseline), [decision(0, True), decision(1, False)])
        baseline["decisions"][0]["link"] = "https://example.com/unrelated"
        with self.assertRaisesRegex(ValueError, "identity mismatch"):
            validate_baseline(rows, baseline)

    def test_cached_tokens_are_not_charged_twice(self):
        from decimal import Decimal
        self.assertEqual(token_cost({"input_tokens": 2000, "output_tokens": 500,
                                     "input_tokens_details": {"cached_tokens": 1000}}),
                         Decimal("0.000525"))

    def test_comparison_separates_potential_misses_from_extra_keeps(self):
        report = compare([{}, {}, {}],
                         [decision(0, True), decision(1, False), decision(2, True)],
                         [decision(0, False), decision(1, True), decision(2, True)])
        self.assertEqual(report["agreement_count"], 1)
        self.assertEqual(report["codex_keep_candidate_reject"], 1)
        self.assertEqual(report["codex_reject_candidate_keep"], 1)
        self.assertNotIn("accuracy", report)

    def test_nano_cost_includes_reasoning_once_and_uses_nano_rates(self):
        from decimal import Decimal
        usage = {"input_tokens": 2000, "output_tokens": 500,
                 "input_tokens_details": {"cached_tokens": 1000},
                 "output_tokens_details": {"reasoning_tokens": 300}}
        self.assertEqual(token_cost(usage, "gpt-5-nano"), Decimal("0.000255"))

    def test_luna_cache_write_and_read_tokens_are_not_double_charged(self):
        from decimal import Decimal
        usage = {"input_tokens": 3000, "output_tokens": 500,
                 "input_tokens_details": {"cached_tokens": 1000, "cache_write_tokens": 1000}}
        self.assertEqual(token_cost(usage, "gpt-6-luna"), Decimal("0.000485"))
        with self.assertRaisesRegex(ValueError, "cache-write price"):
            token_cost(usage, "gpt-5-nano")

    def test_recorder_uses_production_schema_and_preserves_partial_response(self):
        response = SimpleNamespace(
            id="test-response", model="gpt-4o-mini-test", status="completed",
            output_text=json.dumps({"decisions": [decision(0, True)]}),
            usage=SimpleNamespace(model_dump=lambda: {
                "input_tokens": 100, "output_tokens": 20, "input_tokens_details": {"cached_tokens": 0},
            }),
        )
        with tempfile.TemporaryDirectory() as folder, \
                patch.dict("os.environ", {"OPENAI_API_KEY": "offline-test-only"}), \
                patch("openai.OpenAI") as sdk:
            mock_client = sdk.return_value.with_options.return_value
            create = mock_client.responses.create
            create.return_value = response
            client = RecordedClient(Path(folder))
            result = AgentJobFilter(client, "Only HR internships").filter(
                [{"title": "HR intern"}], today="2026-10-14")
            self.assertEqual(result.decisions, [decision(0, True)])
            kwargs = create.call_args.kwargs
            self.assertEqual(kwargs["model"], "gpt-4o-mini")
            self.assertEqual(json.loads(kwargs["input"])["today"], "2026-10-14")
            self.assertTrue(kwargs["text"]["format"]["strict"])
            self.assertFalse(kwargs["store"])
            saved = json.loads((Path(folder) / "batch_01.json").read_text())
            self.assertEqual(saved["usage"]["input_tokens"], 100)
            self.assertEqual(saved["model"], "gpt-4o-mini-test")

    def test_nano_settings_and_cost_are_recorded(self):
        response = SimpleNamespace(
            id="test-nano", model="gpt-5-nano-test", status="completed",
            output_text=json.dumps({"decisions": [decision(0, True)]}),
            usage=SimpleNamespace(model_dump=lambda: {"input_tokens": 100, "output_tokens": 20}),
        )
        with tempfile.TemporaryDirectory() as folder, \
                patch.dict("os.environ", {"OPENAI_API_KEY": "offline-test-only"}), \
                patch("openai.OpenAI") as sdk:
            create = sdk.return_value.with_options.return_value.responses.create
            create.return_value = response
            client = RecordedClient(Path(folder), "gpt-5-nano", "minimal", 4096)
            AgentJobFilter(client, "Only HR internships").filter([{"title": "HR intern"}])
            kwargs = create.call_args.kwargs
            self.assertEqual(kwargs["model"], "gpt-5-nano")
            self.assertEqual(kwargs["reasoning"], {"effort": "minimal"})
            self.assertEqual(kwargs["max_output_tokens"], 4096)
            self.assertEqual(kwargs["service_tier"], "default")
            from decimal import Decimal
            self.assertEqual(Decimal(client.calls[0]["estimated_cost_usd"]), Decimal("0.000013"))


if __name__ == "__main__":
    unittest.main()
