import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from crawl_state import CrawlState

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/sync_crawl_state.py"


class StateSyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.remote = self.root / "remote.git"
        self.command("git", "init", "--bare", str(self.remote))
        self.work = self.root / "work"
        self.command("git", "clone", str(self.remote), str(self.work))

    def command(self, *args, cwd=None):
        return subprocess.run(args, cwd=cwd or self.root, check=True, capture_output=True, text=True).stdout.strip()

    def sync(self, action, directory, check=True):
        return subprocess.run([sys.executable, str(SCRIPT), action, "--directory", str(directory)],
                              cwd=self.work, check=check, capture_output=True, text=True)

    def test_initial_branch_restore_update_and_noop_leave_code_worktree_untouched(self):
        directory = self.root / "state1"
        (self.work / "unfinished.py").write_text("my existing work")
        self.sync("restore", directory)
        with CrawlState(directory / "state.sql") as state:
            state.record([{"site": "wanted", "link": "https://example.com/1", "title": "HR"}])
        self.sync("save", directory)
        head = self.command("git", "--git-dir", str(self.remote), "rev-parse", "crawl-state")
        self.assertEqual(self.command("git", "--git-dir", str(self.remote), "ls-tree", "--name-only", head), "state.sql")
        self.assertIn("unchanged", self.sync("save", directory).stdout)
        second = self.root / "state2"
        self.sync("restore", second)
        self.assertEqual((second / "state.sql").read_bytes(), (directory / "state.sql").read_bytes())
        with CrawlState(second / "state.sql") as state:
            state.record([{"site": "wanted", "link": "https://example.com/2", "title": "new"}])
        self.sync("save", second)
        self.assertEqual(self.command("git", "--git-dir", str(self.remote), "rev-parse", "crawl-state^"), head)
        self.assertEqual((self.work / "unfinished.py").read_text(), "my existing work")

    def test_concurrent_writer_is_rejected_without_overwriting_remote(self):
        one, two = self.root / "one", self.root / "two"
        self.sync("restore", one)
        self.sync("save", one)
        self.sync("restore", two)
        with CrawlState(one / "state.sql") as state:
            state.record([{"site": "a", "link": "https://example.com/one"}])
        with CrawlState(two / "state.sql") as state:
            state.record([{"site": "b", "link": "https://example.com/two"}])
        self.sync("save", one)
        expected = self.command("git", "--git-dir", str(self.remote), "rev-parse", "crawl-state")
        result = self.sync("save", two, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.command("git", "--git-dir", str(self.remote), "rev-parse", "crawl-state"), expected)

    def test_remote_failure_does_not_start_empty_history(self):
        self.command("git", "remote", "set-url", "origin", str(self.root / "missing.git"), cwd=self.work)
        directory = self.root / "state"
        self.assertNotEqual(self.sync("restore", directory, check=False).returncode, 0)
        self.assertFalse((directory / "base.json").exists())

    def test_missing_or_corrupt_snapshot_cannot_replace_remote_state(self):
        directory = self.root / "state"
        self.sync("restore", directory)
        self.sync("save", directory)
        expected = self.command("git", "--git-dir", str(self.remote), "rev-parse", "crawl-state")
        (directory / "state.sql").write_text("bad data")
        self.assertNotEqual(self.sync("save", directory, check=False).returncode, 0)
        self.assertEqual(self.command("git", "--git-dir", str(self.remote), "rev-parse", "crawl-state"), expected)


if __name__ == "__main__":
    unittest.main()
