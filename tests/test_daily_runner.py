import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class DailyRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        source = Path(__file__).resolve().parents[1]
        for filename in ("main.py", "agent_client.py", "agent_filter.py", "job_filters.py",
                         "job_crawler.py", "message_formatter.py", "email_sender.py", "crawl_state.py"):
            shutil.copy(source / filename, self.root / filename)
        shutil.copytree(source / "filters", self.root / "filters")
        (self.root / "scripts").mkdir()
        shutil.copy(source / "scripts/run_daily_email.sh", self.root / "scripts/run_daily_email.sh")
        (self.root / ".venv/bin").mkdir(parents=True)
        # Preserve the venv's interpreter path/imports through a small launcher.
        python = self.root / ".venv/bin/python"
        python.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
        python.chmod(0o700)
        (self.root / "recipients.txt").write_text("test@example.com\n")
        (self.root / "input.json").write_text(json.dumps([
            {"title": "People Operations", "link": "https://example.com/1"},
            {"title": "HR 인턴", "link": "https://example.com/2"},
        ]))
        self.fake_codex = self.root / "fake-codex"
        self.fake_codex.write_text(f"#!{sys.executable}\n" + '''
import json
import sys
from pathlib import Path
data = json.loads(sys.stdin.read().split("UNTRUSTED POSTING DATA (JSON):\\n")[1])
result = {"decisions": [{"id": p["id"], "keep": True, "reason": "test evidence"} for p in data["postings"]]}
Path(sys.argv[sys.argv.index("--output-last-message") + 1]).write_text(json.dumps(result))
calls = Path(__file__).parent / "calls.txt"
with calls.open("a") as stream:
    stream.write(str(len(data["postings"])) + "\\n")
''')
        self.fake_codex.chmod(0o700)
        (self.root / ".env").write_text(
            "JOB_AGENT_FILTER_PROVIDER=codex\nJOB_INPUT_JSON=input.json\nJOB_EMAIL_DRY_RUN=1\n"
            "SMTP_HOST=smtp.example.com\nSMTP_USER=test@example.com\nSMTP_FROM=test@example.com\n"
            f"CODEX_BIN={self.fake_codex}\n"
        )

    def run_daily(self, **overrides):
        env = {k: v for k, v in os.environ.items() if not k.startswith(("JOB_", "CODEX_", "SMTP_", "CHEERUPBOT_", "OPENAI_"))}
        env.update(overrides)
        return subprocess.run(["bash", str(self.root / "scripts/run_daily_email.sh")],
                              cwd="/tmp", env=env, capture_output=True, text=True, timeout=30)

    def test_dotenv_agent_replaces_default_regex_and_runs_once(self):
        result = self.run_daily()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(json.loads((self.root / "out/jobs_latest.json").read_text())), 2)
        self.assertEqual((self.root / "calls.txt").read_text(), "2\n")
        self.assertEqual(len(list((self.root / "out").glob("agent_decisions_*.json"))), 1)
        html_file = self.root / "out/jobs_message_latest.html"
        self.assertTrue(html_file.is_file())
        self.assertIn("People Operations", html_file.read_text())
        self.assertIn("HR 인턴", html_file.read_text())

    def test_exported_opt_out_preserves_legacy_filter(self):
        result = self.run_daily(JOB_AGENT_FILTER_PROVIDER="none")
        self.assertEqual(result.returncode, 0, result.stderr)
        rows = json.loads((self.root / "out/jobs_latest.json").read_text())
        self.assertEqual([row["title"] for row in rows], ["HR 인턴"])
        self.assertFalse((self.root / "calls.txt").exists())

    def test_default_openai_luna_medium_filters_once_and_records_effort(self):
        (self.root / ".env").write_text(
            "OPENAI_API_KEY=offline-test-only\nJOB_INPUT_JSON=input.json\nJOB_EMAIL_DRY_RUN=1\n"
            "SMTP_USER=test@example.com\n")
        # A local SDK substitute verifies the subprocess configuration without API calls.
        (self.root / "openai.py").write_text('''
import json
from pathlib import Path
from types import SimpleNamespace
OpenAIError = RuntimeError
class OpenAI:
    def __init__(self, **kwargs):
        self.responses = SimpleNamespace(create=self.create)
    def create(self, **kwargs):
        with (Path(__file__).parent / "api_calls.jsonl").open("a") as stream:
            stream.write(json.dumps(kwargs) + "\\n")
        data = json.loads(kwargs["input"])
        result = {"decisions": [{"id": p["id"], "keep": True, "reason": "test evidence"} for p in data["postings"]]}
        return SimpleNamespace(status="completed", output_text=json.dumps(result))
''')
        result = self.run_daily()
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = [json.loads(line) for line in (self.root / "api_calls.jsonl").read_text().splitlines()]
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["model"], "gpt-6-luna")
        self.assertEqual(calls[0]["reasoning"], {"effort": "medium"})
        self.assertEqual(len(json.loads(calls[0]["input"])["postings"]), 2)
        report = json.loads(next((self.root / "out").glob("agent_decisions_*.json")).read_text())
        self.assertEqual(report["reasoning_effort"], "medium")
        self.assertTrue((self.root / "out/jobs_message_latest.html").is_file())

    def test_explicit_dsl_is_applied_before_agent(self):
        result = self.run_daily(JOB_FILTER_FILE="filters/new_grad.json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root / "calls.txt").read_text(), "1\n")

    def test_second_run_restores_sql_and_reuses_matching_filter_decisions(self):
        for _ in range(2):
            result = self.run_daily()
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root / "calls.txt").read_text(), "2\n")
        snapshot = (self.root / "out/state.sql").read_text()
        self.assertIn("People Operations", snapshot)
        self.assertIn("filter_decisions", snapshot)
        self.assertNotIn("SMTP_", snapshot)
        self.assertEqual(len(json.loads((self.root / "out/jobs_latest.json").read_text())), 2)

    def test_sql_survives_agent_failure_and_allows_retry(self):
        original = self.fake_codex.read_text()
        self.fake_codex.write_text(f"#!{sys.executable}\nraise SystemExit(1)\n")
        result = self.run_daily()
        self.assertNotEqual(result.returncode, 0)
        snapshot = self.root / "out/state.sql"
        self.assertIn("People Operations", snapshot.read_text())
        self.fake_codex.write_text(original)
        result = self.run_daily()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_corrupt_sql_stops_before_email_without_overwriting_history(self):
        snapshot = self.root / "out/state.sql"
        snapshot.parent.mkdir()
        snapshot.write_text("broken SQL")
        result = self.run_daily()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Crawl state failed", result.stderr)
        self.assertEqual(snapshot.read_text(), "broken SQL")
        self.assertFalse((self.root / "out/jobs_message_latest.html").exists())

    def test_agent_failure_stops_before_message_or_email(self):
        self.fake_codex.write_text(f"#!{sys.executable}\nraise SystemExit(1)\n")
        result = self.run_daily()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Agent filter failed", result.stderr)
        self.assertFalse((self.root / "out/jobs_latest.json").exists())
        self.assertFalse((self.root / "out/jobs_message_latest.txt").exists())

    def test_crawl_failure_stops_before_message_or_email_and_saves_report(self):
        # Inject a transport failure into the isolated copy; never contact job sites or SMTP.
        crawler = self.root / "job_crawler.py"
        crawler.write_text(crawler.read_text() + '''
def _offline(*args, **kwargs):
    raise requests.ConnectionError("offline test")
requests.Session.get = _offline
''')
        result = self.run_daily(JOB_INPUT_JSON="", JOB_SITES="wanted", JOB_KEYWORDS="HR",
                                JOB_AGENT_FILTER_PROVIDER="none")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Crawl failed", result.stderr)
        self.assertFalse((self.root / "out/jobs_latest.json").exists())
        self.assertFalse((self.root / "out/jobs_message_latest.txt").exists())
        reports = list((self.root / "out").glob("crawl_report_*.json"))
        self.assertEqual(len(reports), 1)
        self.assertEqual(json.loads(reports[0].read_text())[0]["status"], "failed")


if __name__ == "__main__":
    unittest.main()
