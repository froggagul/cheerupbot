#!/usr/bin/env python3
"""Restore/push only state.sql on a dedicated Git branch; never force-push."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from crawl_state import CrawlState, StateError


def git(*args: str, input: str | None = None, check: bool = True) -> subprocess.CompletedProcess:
    env = dict(os.environ, GIT_AUTHOR_NAME="cheerupbot", GIT_AUTHOR_EMAIL="41898282+github-actions[bot]@users.noreply.github.com",
               GIT_COMMITTER_NAME="cheerupbot", GIT_COMMITTER_EMAIL="41898282+github-actions[bot]@users.noreply.github.com")
    result = subprocess.run(["git", *args], input=input, text=True, capture_output=True, env=env)
    if check and result.returncode:
        raise RuntimeError(f"git {args[0]} failed: {result.stderr.strip()}")
    return result


def restore(directory: Path, branch: str) -> None:
    if directory.exists() and any(directory.iterdir()):
        raise RuntimeError(f"State restore directory must be empty: {directory}")
    ref = f"refs/heads/{branch}"
    git("check-ref-format", ref)
    remote = git("ls-remote", "--exit-code", "--heads", "origin", ref, check=False)
    if remote.returncode not in (0, 2):
        raise RuntimeError("Cannot read remote crawl state; refusing to initialize empty history")
    head = ""
    if remote.returncode == 0:
        git("fetch", "--no-tags", "origin", ref)
        head = git("rev-parse", "FETCH_HEAD").stdout.strip()
        files = git("ls-tree", "--name-only", head).stdout.splitlines()
        if files != ["state.sql"]:
            raise RuntimeError("The crawl-state branch must contain only state.sql")
        snapshot = git("show", f"{head}:state.sql").stdout
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "state.sql").write_text(snapshot, encoding="utf-8")
        state = CrawlState(directory / "state.sql")
        state.db.close()
    else:
        directory.mkdir(parents=True, exist_ok=True)
        with CrawlState(directory / "state.sql"):
            pass
    # Written only after successful restore/validation. A failed restore cannot be saved.
    (directory / "base.json").write_text(json.dumps({"branch": branch, "head": head}), encoding="utf-8")
    print(f"Restored {branch}: {head[:12] or 'new state'}")


def save(directory: Path, branch: str) -> None:
    base = json.loads((directory / "base.json").read_text(encoding="utf-8"))
    if base["branch"] != branch:
        raise RuntimeError("State branch changed after restore")
    snapshot = directory / "state.sql"
    state = CrawlState(snapshot)
    state.db.close()
    blob = git("hash-object", "-w", str(snapshot)).stdout.strip()
    tree = git("mktree", input=f"100644 blob {blob}\tstate.sql\n").stdout.strip()
    head = base["head"]
    if head and git("rev-parse", f"{head}^{{tree}}").stdout.strip() == tree:
        print("Crawl state unchanged; no commit needed")
        return
    parent = ["-p", head] if head else []
    commit = git("commit-tree", tree, *parent, "-m", "Update crawl state").stdout.strip()
    # A concurrent writer causes a non-fast-forward rejection, preserving their data.
    git("push", "origin", f"{commit}:refs/heads/{branch}")
    (directory / "base.json").write_text(json.dumps({"branch": branch, "head": commit}), encoding="utf-8")
    print(f"Saved {branch}: {commit[:12]}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["restore", "save"])
    parser.add_argument("--directory", type=Path, default=Path(".crawl-state"))
    parser.add_argument("--branch", default="crawl-state")
    args = parser.parse_args()
    try:
        (restore if args.action == "restore" else save)(args.directory, args.branch)
    except (OSError, ValueError, KeyError, StateError, RuntimeError) as exc:
        raise SystemExit(f"State sync failed: {exc}") from exc


if __name__ == "__main__":
    main()
