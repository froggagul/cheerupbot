import json
import tempfile
import unittest
from pathlib import Path

from agent_client import AgentError
from agent_filter import AgentJobFilter
from crawl_state import CrawlState, StateError


def posting(index=1, **updates):
    return {"site": "wanted", "link": f"https://www.wanted.co.kr/wd/{index}",
            "title": f"HR 신입 {index}", "deadline": "2026-10-31", **updates}


class FakeClient:
    model = "test-model"
    reasoning_effort = "medium"

    def __init__(self):
        self.calls = []

    def complete(self, instructions, data, schema):
        self.calls.append(data)
        return {"decisions": [{"id": row["id"], "keep": row["posting"]["title"] != "reject",
                               "reason": "테스트 근거"} for row in data["postings"]]}


class CrawlStateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "state.sql"

    def test_round_trip_quotes_unicode_and_deterministic_dump(self):
        row = posting(title="신입 '채용'; DROP TABLE jobs; --\n지원", private_token="never-save-me")
        with CrawlState(self.path) as state:
            state.record([posting(2), row], now="2026-10-14T00:00:00+00:00")
        original = self.path.read_bytes()
        self.assertNotIn(b"never-save-me", original)
        with CrawlState(self.path) as state:
            self.assertEqual(state.db.execute("SELECT count(*) FROM jobs").fetchone()[0], 2)
            payload = json.loads(state.db.execute("SELECT payload FROM jobs ORDER BY link LIMIT 1").fetchone()[0])
            self.assertEqual(payload["title"], row["title"])
        self.assertEqual(self.path.read_bytes(), original)
        other = self.path.parent / "other.sql"
        with CrawlState(other) as state:
            state.record([row, posting(2)], now="2026-10-14T00:00:00+00:00")
        self.assertEqual(other.read_bytes(), original)

    def test_upsert_tracks_new_changed_and_last_seen_without_deleting_missing_jobs(self):
        with CrawlState(self.path) as state:
            state.record([posting(), posting(2)], now="first")
            state.record([posting(keyword="인사", collected_at="later")], now="second")
            times = state.db.execute("SELECT first_seen, last_seen, changed_at FROM jobs ORDER BY link").fetchall()
            self.assertEqual(times, [("first", "second", "first"), ("first", "first", "first")])
            state.record([posting(title="HR 계약직")], now="third")
            times = state.db.execute("SELECT first_seen, last_seen, changed_at FROM jobs ORDER BY link LIMIT 1").fetchone()
            self.assertEqual(times, ("first", "third", "third"))

    def test_collected_data_survives_later_filter_failure(self):
        with self.assertRaises(AgentError):
            with CrawlState(self.path) as state:
                state.record([posting()])
                raise AgentError("API unavailable")
        with CrawlState(self.path) as state:
            self.assertEqual(state.db.execute("SELECT count(*) FROM jobs").fetchone()[0], 1)

    def test_corrupt_or_truncated_snapshot_is_not_overwritten(self):
        for bad in ("not SQL", "PRAGMA user_version=99;\nCOMMIT;", "BEGIN TRANSACTION;\nCREATE TABLE jobs (x);"):
            self.path.write_text(bad)
            with self.assertRaises(StateError):
                CrawlState(self.path)
            self.assertEqual(self.path.read_text(), bad)

    def test_snapshot_cannot_attach_another_database(self):
        target = self.path.parent / "unwanted.db"
        self.path.write_text(f"BEGIN; ATTACH DATABASE '{target}' AS other; COMMIT;")
        with self.assertRaises(StateError):
            CrawlState(self.path)
        self.assertFalse(target.exists())

    def test_filter_cache_restores_rejections_and_preserves_ids_for_partial_misses(self):
        client = FakeClient()
        agent = AgentJobFilter(client, "test policy")
        rows = [posting(), posting(2, title="reject")]
        with CrawlState(self.path) as state:
            state.record(rows)
            self.assertEqual(state.filter(rows, agent, "test", "2026-10-14").rows, rows[:1])
        with CrawlState(self.path) as state:
            rows = [posting(3), rows[1], rows[0]]
            state.record(rows)
            result = state.filter(rows, agent, "test", "2026-10-14")
            self.assertEqual(result.rows, [rows[0], rows[2]])
            self.assertEqual([item["id"] for item in result.decisions], [0, 1, 2])
            self.assertEqual([len(call["postings"]) for call in client.calls], [2, 1])

    def test_filter_cache_invalidates_for_content_policy_model_effort_provider_and_date(self):
        client = FakeClient()
        agent = AgentJobFilter(client, "test policy")
        with CrawlState(self.path) as state:
            rows = [posting()]
            state.record(rows)
            state.filter(rows, agent, "test", "2026-10-14")
            state.filter(rows, agent, "test", "2026-10-14")
            self.assertEqual(len(client.calls), 1)
            rows = [posting(title="changed")]
            state.record(rows)
            state.filter(rows, agent, "test", "2026-10-14")
            agent.instructions += " changed policy"
            state.filter(rows, agent, "test", "2026-10-14")
            client.model = "changed-model"
            state.filter(rows, agent, "test", "2026-10-14")
            client.reasoning_effort = "high"
            state.filter(rows, agent, "test", "2026-10-14")
            state.filter(rows, agent, "another-provider", "2026-10-14")
            state.filter(rows, agent, "another-provider", "2026-10-15")
            self.assertEqual(len(client.calls), 7)
            self.assertEqual(state.db.execute("SELECT count(*) FROM filter_decisions").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
