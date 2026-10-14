"""SQLite during a run, a portable SQL text snapshot between runs."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agent_filter import FilterResult

SCHEMA_VERSION = 1
SCHEMA = """
CREATE TABLE jobs (
    site TEXT NOT NULL,
    link TEXT NOT NULL,
    payload TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    changed_at TEXT NOT NULL,
    PRIMARY KEY (site, link)
) WITHOUT ROWID;
CREATE TABLE filter_decisions (
    site TEXT NOT NULL,
    link TEXT NOT NULL,
    cache_key TEXT NOT NULL,
    evaluated_on TEXT NOT NULL,
    keep INTEGER NOT NULL CHECK (keep IN (0, 1)),
    reason TEXT NOT NULL,
    PRIMARY KEY (site, link),
    FOREIGN KEY (site, link) REFERENCES jobs(site, link)
) WITHOUT ROWID;
PRAGMA user_version = 1;
"""
# Retain normalized posting data only, never arbitrary imported metadata.
POSTING_FIELDS = (
    "site", "link", "title", "company", "location", "deadline", "description",
    "experience", "employment_type", "keyword", "matched_keywords", "source_url", "collected_at",
)
VOLATILE_FIELDS = {"keyword", "matched_keywords", "source_url", "collected_at"}


class StateError(RuntimeError):
    """The saved state could not be restored or persisted safely."""


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value: object) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _identity(row: dict) -> tuple[str, str] | None:
    site, link = row.get("site", ""), row.get("link", "")
    if not isinstance(site, str) or not isinstance(link, str) or not link.strip():
        return None
    return site, link


class CrawlState:
    def __init__(self, snapshot: str | Path) -> None:
        self.path = Path(snapshot)
        self.db = sqlite3.connect(":memory:")
        # A snapshot must not attach or modify another database on the runner.
        self.db.set_authorizer(lambda action, *args: sqlite3.SQLITE_DENY
                               if action in (sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH)
                               else sqlite3.SQLITE_OK)
        try:
            if self.path.exists():
                snapshot = self.path.read_text(encoding="utf-8")
                if not snapshot.rstrip().endswith("COMMIT;"):
                    raise StateError("Incomplete SQL snapshot (missing final COMMIT)")
                self.db.executescript(snapshot)
                version = self.db.execute("PRAGMA user_version").fetchone()[0]
                if version != SCHEMA_VERSION:
                    raise StateError(f"Unsupported crawl state version: {version}")
                # Reject truncated/invalid snapshots rather than silently resetting history.
                self.db.execute("SELECT site, link, payload, content_hash, first_seen, last_seen, changed_at FROM jobs LIMIT 0")
                self.db.execute("SELECT site, link, cache_key, evaluated_on, keep, reason FROM filter_decisions LIMIT 0")
                if self.db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise StateError("Crawl state integrity check failed")
                if self.db.execute("PRAGMA foreign_key_check").fetchone():
                    raise StateError("Crawl state contains orphaned decisions")
            else:
                self.db.executescript(SCHEMA)
            self.db.execute("PRAGMA foreign_keys = ON")
        except (OSError, UnicodeError, sqlite3.Error, StateError) as exc:
            self.db.close()
            raise StateError(f"Cannot restore crawl state {self.path}: {exc}") from exc

    def __enter__(self) -> CrawlState:
        return self

    def __exit__(self, *exc) -> None:
        try:
            # Keep collected data even when a subsequent filter/API request fails.
            self.save()
        finally:
            self.db.close()

    def record(self, rows: list[dict], now: str | None = None) -> None:
        now = now or datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self.db:
            for row in rows:
                identity = _identity(row)
                if identity is None:
                    continue
                payload = {key: row[key] for key in POSTING_FIELDS if key in row}
                fingerprint = _hash({key: value for key, value in payload.items() if key not in VOLATILE_FIELDS})
                self.db.execute("""
                    INSERT INTO jobs VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(site, link) DO UPDATE SET
                        payload = excluded.payload,
                        changed_at = CASE WHEN jobs.content_hash != excluded.content_hash
                                          THEN excluded.changed_at ELSE jobs.changed_at END,
                        content_hash = excluded.content_hash,
                        last_seen = excluded.last_seen
                """, (*identity, _json(payload), fingerprint, now, now, now))

    def filter(self, rows: list[dict], agent, provider: str, today: str | None = None) -> FilterResult:
        today = today or datetime.now(timezone(timedelta(hours=9))).date().isoformat()
        policy = {
            "instructions": agent.instructions,
            "provider": provider,
            "model": getattr(agent.client, "model", ""),
            "reasoning_effort": getattr(agent.client, "reasoning_effort", None),
            # The heuristic receives today's date, so decisions expire daily.
            "today": today,
        }
        decisions: dict[int, dict] = {}
        pending: list[dict] = []
        pending_ids: list[int] = []
        keys: dict[int, str] = {}
        for index, row in enumerate(rows):
            keys[index] = _hash([policy, agent._posting_data(row)])
            identity = _identity(row)
            cached = None if identity is None else self.db.execute(
                "SELECT keep, reason FROM filter_decisions WHERE site = ? AND link = ? AND cache_key = ?",
                (*identity, keys[index]),
            ).fetchone()
            if cached:
                decisions[index] = {"id": index, "keep": bool(cached[0]), "reason": cached[1]}
            else:
                pending.append(row)
                pending_ids.append(index)
        if pending:
            result = agent.filter(pending, today=today)
            with self.db:
                for item in result.decisions:
                    index = pending_ids[item["id"]]
                    decisions[index] = {**item, "id": index}
                    identity = _identity(rows[index])
                    if identity is not None:
                        self.db.execute("""
                            INSERT INTO filter_decisions VALUES (?, ?, ?, ?, ?, ?)
                            ON CONFLICT(site, link) DO UPDATE SET
                                cache_key = excluded.cache_key, evaluated_on = excluded.evaluated_on,
                                keep = excluded.keep, reason = excluded.reason
                        """, (*identity, keys[index], today, int(item["keep"]), item["reason"]))
        ordered = [decisions[index] for index in range(len(rows))]
        return FilterResult(rows=[rows[item["id"]] for item in ordered if item["keep"]], decisions=ordered)

    def save(self) -> None:
        temporary = None
        try:
            # WITHOUT ROWID primary keys give stable row order in the text dump.
            snapshot = f"PRAGMA user_version = {SCHEMA_VERSION};\n" + "\n".join(self.db.iterdump()) + "\n"
            if self.path.exists() and self.path.read_text(encoding="utf-8") == snapshot:
                return
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent,
                                             prefix=".state-", delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(snapshot)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(self.path)
        except (OSError, sqlite3.Error) as exc:
            raise StateError(f"Cannot save crawl state {self.path}: {exc}") from exc
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
