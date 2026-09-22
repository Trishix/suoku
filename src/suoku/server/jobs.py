"""Single-host persistent queue. No model, API credential, or raw media in this database."""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from ..types import LakeError

TERMINAL = {"succeeded", "failed", "cancelled"}


class Jobs:
    def __init__(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / "jobs.sqlite3"
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, kind TEXT NOT NULL, state TEXT NOT NULL,
                    payload TEXT NOT NULL, progress INTEGER NOT NULL DEFAULT 0,
                    result TEXT, error TEXT, cancel INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL, updated_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS queue_order ON jobs(state, kind, created_at);
                CREATE TABLE IF NOT EXISTS uploads (
                    id TEXT PRIMARY KEY, suffix TEXT NOT NULL, size INTEGER NOT NULL,
                    state TEXT NOT NULL DEFAULT 'pending'
                );
                CREATE TABLE IF NOT EXISTS worker_status (
                    id INTEGER PRIMARY KEY CHECK(id=1), owner TEXT NOT NULL,
                    heartbeat REAL NOT NULL, model TEXT, insights INTEGER NOT NULL,
                    active INTEGER NOT NULL
                );
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=FULL")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def submit(self, kind: str, payload: dict, *, capacity: int = 1000, upload=None) -> dict:
        now = time.time()
        identifier = uuid.uuid4().hex
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            count = db.execute(
                "SELECT count(*) FROM jobs WHERE state IN ('queued','running')"
            ).fetchone()[0]
            if count >= capacity:
                raise LakeError("queue_full", "Job queue is full; retry later.")
            if kind in {"remove", "purge"}:
                for row in db.execute(
                    "SELECT id,payload FROM jobs WHERE kind='ingest' AND state IN ('queued','running')"
                ).fetchall():
                    if json.loads(row["payload"]).get("asset_id") == payload["asset_id"]:
                        db.execute(
                            "UPDATE jobs SET cancel=1,state=CASE WHEN state='queued' THEN 'cancelled' ELSE state END,updated_at=? WHERE id=?",
                            (now, row["id"]),
                        )
            if upload:
                db.execute("INSERT INTO uploads(id,suffix,size) VALUES(?,?,?)", upload)
            db.execute(
                "INSERT INTO jobs(id,kind,state,payload,created_at,updated_at) VALUES(?,?,'queued',?,?,?)",
                (identifier, kind, json.dumps(payload), now, now),
            )
        return self.get(identifier)

    def get(self, identifier: str, *, internal=False) -> dict:
        with self.connect() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (identifier,)).fetchone()
            expired = row and (
                (row["kind"] == "search" and row["created_at"] < time.time() - 3600)
                or (row["kind"] in {"answer", "analysis"} and row["state"] in TERMINAL
                    and row["updated_at"] < time.time() - 3600)
            )
            if not row or expired:
                raise LakeError("not_found", "Job does not exist or has expired.")
        result = dict(row)
        for key in ("payload", "result", "error"):
            result[key] = json.loads(result[key]) if result[key] is not None else None
        if not internal:
            result.pop("payload")
            result.pop("cancel")
        return result

    def claim(self, *, search_only=False, nonsearch_only=False) -> dict | None:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            extra = (
                " AND kind='search'"
                if search_only
                else " AND kind!='search'"
                if nonsearch_only
                else ""
            )
            row = db.execute(
                "SELECT id FROM jobs WHERE state='queued' AND cancel=0"
                + extra
                + " ORDER BY CASE kind WHEN 'remove' THEN 0 WHEN 'purge' THEN 0 WHEN 'search' THEN 1 ELSE 2 END, created_at LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            identifier = row[0]
            db.execute(
                "UPDATE jobs SET state='running',updated_at=? WHERE id=?", (time.time(), identifier)
            )
        return self.get(identifier, internal=True)

    def finish(self, identifier: str, *, result=None, error=None, cancelled=False):
        state = "cancelled" if cancelled else "failed" if error else "succeeded"
        with self.connect() as db:
            db.execute(
                "UPDATE jobs SET state=?,result=?,error=?,payload='{}',updated_at=? WHERE id=?",
                (state, json.dumps(result), json.dumps(error), time.time(), identifier),
            )

    def progress(self, identifier: str, processed: int):
        with self.connect() as db:
            db.execute(
                "UPDATE jobs SET progress=?,updated_at=? WHERE id=?",
                (processed, time.time(), identifier),
            )

    def cancel(self, identifier: str) -> dict:
        self.get(identifier)
        with self.connect() as db:
            db.execute(
                "UPDATE jobs SET cancel=1,state=CASE WHEN state='queued' THEN 'cancelled' ELSE state END,updated_at=? WHERE id=? AND state NOT IN ('succeeded','failed','cancelled')",
                (time.time(), identifier),
            )
        return self.get(identifier)

    def cancel_asset(self, asset_id: str):
        with self.connect() as db:
            rows = db.execute(
                "SELECT id,payload FROM jobs WHERE kind='ingest' AND state IN ('queued','running')"
            ).fetchall()
        for row in rows:
            if json.loads(row["payload"]).get("asset_id") == asset_id:
                self.cancel(row["id"])

    def upload(self, asset_id: str) -> dict:
        with self.connect() as db:
            row = db.execute("SELECT * FROM uploads WHERE id=?", (asset_id,)).fetchone()
        if row is None:
            raise LakeError("not_found", "Media does not exist.")
        return dict(row)

    def upload_state(self, asset_id: str, state: str):
        with self.connect() as db:
            db.execute("UPDATE uploads SET state=? WHERE id=?", (state, asset_id))

    def recover(self):
        """Only call after acquiring exclusive engine ownership."""
        with self.connect() as db:
            db.execute(
                "UPDATE jobs SET state=CASE WHEN cancel=1 THEN 'cancelled' ELSE 'queued' END WHERE state='running'"
            )
        self.expire()

    def expire(self):
        with self.connect() as db:
            db.execute(
                "DELETE FROM jobs WHERE (kind='search' AND created_at<?) OR "
                "(kind IN ('answer','analysis') AND state IN ('succeeded','failed','cancelled') "
                "AND updated_at<?)",
                (time.time() - 3600, time.time() - 3600),
            )

    def start_worker(self, owner, model, insights):
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO worker_status VALUES(1,?,?,?,?,1)",
                       (owner, time.time(), model, int(insights)))

    def heartbeat(self, owner):
        with self.connect() as db:
            db.execute("UPDATE worker_status SET heartbeat=? WHERE owner=? AND active=1",
                       (time.time(), owner))

    def stop_worker(self, owner):
        with self.connect() as db:
            db.execute("UPDATE worker_status SET active=0 WHERE owner=?", (owner,))

    def worker_status(self):
        with self.connect() as db:
            row = db.execute("SELECT * FROM worker_status WHERE id=1").fetchone()
        available = bool(row and row["active"] and 0 <= time.time() - row["heartbeat"] < 15)
        return {"worker_available": available,
                "last_heartbeat": row["heartbeat"] if row else None,
                "insight_model": row["model"] if row else None,
                "insights_ready": bool(available and row["insights"])}

    def invalidate_insights(self, asset_id):
        """Do not expose stored answer/analysis copies after removing their source.

        Citations are not complete dependency lists: an answer may reason over
        uncited evidence. In this single-host alpha any source removal clears
        all answer snapshots, including legacy jobs without dependency metadata.
        Per-video observations and analysis results remain precisely scoped.
        """
        with self.connect() as db:
            rows = db.execute("SELECT id,kind,result FROM jobs WHERE kind IN ('answer','analysis') "
                              "AND result IS NOT NULL").fetchall()
            for row in rows:
                result = json.loads(row["result"]) or {}
                records = result.get("observations", [])
                if ((row["kind"] == "answer" and result.get("answer"))
                        or any(record.get("asset_id") == asset_id for record in records)):
                    db.execute("UPDATE jobs SET result=NULL,state='failed',error=? WHERE id=?",
                               (json.dumps({"code": "source_removed", "message":
                                            "A video was removed; rerun the query on remaining sources."}),
                                row["id"]))
