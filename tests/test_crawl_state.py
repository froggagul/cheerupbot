import json
import tempfile
import unittest
from datetime import datetime, timezone
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

    def test_cleanup_expired_deadlines_keeps_today_future_and_unknown_dates(self):
        # 15:00 UTC is midnight in Seoul, so October 17 has just expired.
        now = datetime(2026, 10, 17, 15, tzinfo=timezone.utc)
        deadlines = ["2026-10-17", "2026-10-18", "2026-10-19", "OPEN", "", "unknown", "2026-02-30", None]
        with CrawlState(self.path) as state:
            state.record([posting(i, deadline=value) for i, value in enumerate(deadlines)], now=now.isoformat())
            self.assertEqual(state.cleanup(now), {"expired": 1, "stale": 0, "remaining": 7})
            self.assertEqual([json.loads(row[0])["deadline"] for row in
                              state.db.execute("SELECT payload FROM jobs ORDER BY link")], deadlines[1:])

    def test_cleanup_seven_day_boundary_uses_seoul_dates_for_all_deadlines(self):
        now = datetime(2026, 10, 17, 15, tzinfo=timezone.utc)  # October 18 KST
        with CrawlState(self.path) as state:
            # Same moment expressed in UTC, KST, and a legacy naive UTC timestamp.
            state.record([posting(1, deadline="OPEN")], now="2026-10-11T14:59:59Z")
            state.record([posting(2, deadline="")], now="2026-10-11T23:59:59+09:00")
            state.record([posting(3, deadline="unknown")], now="2026-10-11T14:59:59")
            state.record([posting(4)], now="2026-10-01T00:00:00+00:00")
            # Six calendar days ago; preserve even if a site is temporarily unavailable.
            state.record([posting(5)], now="2026-10-11T15:00:00+00:00")
            self.assertEqual(state.cleanup(now), {"expired": 0, "stale": 4, "remaining": 1})
            self.assertEqual(state.db.execute("SELECT link FROM jobs").fetchall(), [(posting(5)["link"],)])

    def test_rediscovered_jobs_reset_retention_and_updated_deadlines_take_precedence(self):
        now = datetime(2026, 10, 18, tzinfo=timezone.utc)
        with CrawlState(self.path) as state:
            state.record([posting(1, deadline="2026-10-01"), posting(2, deadline="OPEN")],
                         now="2026-09-01T00:00:00+00:00")
            state.record([posting(1, deadline="2026-11-01"), posting(2, deadline="OPEN")], now=now.isoformat())
            self.assertEqual(state.cleanup(now), {"expired": 0, "stale": 0, "remaining": 2})
            self.assertEqual(state.db.execute("SELECT DISTINCT first_seen FROM jobs").fetchall(),
                             [("2026-09-01T00:00:00+00:00",)])

    def test_cleanup_removes_cached_decisions_and_round_trips_existing_snapshots(self):
        rows = [posting(1, deadline="2026-10-10"), posting(2), posting(3)]
        with CrawlState(self.path) as state:
            state.record(rows, now="2026-10-10T00:00:00+00:00")
            state.record([rows[2]], now="2026-10-17T00:00:00+00:00")
            state.filter(rows, AgentJobFilter(FakeClient(), "test policy"), "test", "2026-10-10")
        now = datetime(2026, 10, 18, tzinfo=timezone.utc)
        with CrawlState(self.path) as state:
            self.assertEqual(state.cleanup(now), {"expired": 1, "stale": 1, "remaining": 1})
            self.assertEqual(state.db.execute("SELECT link FROM filter_decisions").fetchall(),
                             [(posting(3)["link"],)])
            self.assertEqual(state.cleanup(now), {"expired": 0, "stale": 0, "remaining": 1})
            self.assertEqual(state.db.execute("PRAGMA foreign_key_check").fetchall(), [])
        with CrawlState(self.path) as state:
            self.assertEqual(state.db.execute("SELECT link FROM jobs").fetchall(), [(posting(3)["link"],)])
            self.assertEqual(state.db.execute("SELECT count(*) FROM filter_decisions").fetchone()[0], 1)

    def test_cleanup_rolls_back_decision_deletions_if_job_deletion_fails(self):
        with CrawlState(self.path) as state:
            state.record([posting()], now="2026-10-01T00:00:00+00:00")
            state.filter([posting()], AgentJobFilter(FakeClient(), "test policy"), "test", "2026-10-01")
            state.db.execute("""CREATE TEMP TRIGGER prevent_delete BEFORE DELETE ON jobs
                                BEGIN SELECT RAISE(ABORT, 'test failure'); END""")
            with self.assertRaisesRegex(StateError, "Cannot clean up crawl state"):
                state.cleanup(datetime(2026, 10, 18, tzinfo=timezone.utc))
            self.assertEqual(state.db.execute("SELECT count(*) FROM jobs").fetchone()[0], 1)
            self.assertEqual(state.db.execute("SELECT count(*) FROM filter_decisions").fetchone()[0], 1)

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
