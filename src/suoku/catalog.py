"""SQLite catalog for asset identity, ingestion state, and source metadata.

The catalog is deliberately separate from the vector index: it is the authoritative
place to decide whether media is ready, active, or safe to expose to insight jobs.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

# Keep schema creation idempotent so an existing local lake can be opened after an
# upgrade without a destructive migration step.
SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS assets (
    id TEXT PRIMARY KEY,
    canonical_path TEXT NOT NULL,
    camera_key TEXT NOT NULL,
    recorded_key TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('ingesting', 'ready', 'removed', 'failed')),
    active_generation TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS assets_registration
    ON assets(canonical_path, camera_key, recorded_key);
CREATE TABLE IF NOT EXISTS generations (
    id TEXT PRIMARY KEY,
    asset_id TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    source_hash TEXT NOT NULL,
    source_path TEXT NOT NULL,
    camera_id TEXT,
    recorded_at TEXT,
    recorded_at_ms INTEGER,
    duration_ms INTEGER NOT NULL,
    width INTEGER NOT NULL,
    height INTEGER NOT NULL,
    codec TEXT NOT NULL,
    processed INTEGER NOT NULL DEFAULT 0,
    next_start_ms INTEGER NOT NULL DEFAULT 0,
    complete INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS generations_asset ON generations(asset_id);
"""


class Catalog:
    def __init__(self, path: Path) -> None:
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(SCHEMA)

    def close(self) -> None:
        self.connection.close()

    def metadata(self) -> dict[str, str]:
        return {
            row["key"]: row["value"]
            for row in self.connection.execute("SELECT key, value FROM metadata")
        }

    def set_metadata(self, values: dict[str, str]) -> None:
        with self.connection:
            self.connection.executemany(
                "INSERT INTO metadata(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                values.items(),
            )

    def asset(self, asset_id: str) -> sqlite3.Row | None:
        return self.connection.execute("SELECT * FROM assets WHERE id=?", (asset_id,)).fetchone()

    def registered(self, path: str, camera_key: str, recorded_key: str) -> sqlite3.Row | None:
        return self.connection.execute(
            "SELECT * FROM assets WHERE canonical_path=? AND camera_key=? AND recorded_key=?",
            (path, camera_key, recorded_key),
        ).fetchone()

    def create_asset(self, asset_id: str, path: str, camera_key: str, recorded_key: str) -> None:
        with self.connection:
            self.connection.execute(
                "INSERT INTO assets(id,canonical_path,camera_key,recorded_key,state) "
                "VALUES(?,?,?,?, 'ingesting')",
                (asset_id, path, camera_key, recorded_key),
            )

    def generation(self, generation_id: str) -> sqlite3.Row | None:
        return self.connection.execute(
            "SELECT * FROM generations WHERE id=?", (generation_id,)
        ).fetchone()

    def resumable(
        self,
        asset_id: str,
        source_hash: str,
        source_path: str,
        camera_id: str | None,
        recorded_at: str | None,
    ) -> sqlite3.Row | None:
        return self.connection.execute(
            "SELECT * FROM generations WHERE asset_id=? AND source_hash=? AND source_path=? "
            "AND camera_id IS ? AND recorded_at IS ? AND complete=0 "
            "ORDER BY created_at DESC LIMIT 1",
            (asset_id, source_hash, source_path, camera_id, recorded_at),
        ).fetchone()

    def create_generation(self, values: dict[str, Any]) -> None:
        columns = ",".join(values)
        placeholders = ",".join("?" for _ in values)
        with self.connection:
            self.connection.execute(
                f"INSERT INTO generations({columns}) VALUES({placeholders})",
                tuple(values.values()),
            )
            self.connection.execute(
                "UPDATE assets SET state=CASE WHEN active_generation IS NULL "
                "THEN 'ingesting' ELSE state END WHERE id=?",
                (values["asset_id"],),
            )

    def checkpoint(self, generation_id: str, added: int, next_start_ms: int) -> int:
        with self.connection:
            self.connection.execute(
                "UPDATE generations SET processed=processed+?, next_start_ms=? WHERE id=?",
                (added, next_start_ms, generation_id),
            )
        row = self.generation(generation_id)
        assert row is not None
        return int(row["processed"])

    def activate(self, asset_id: str, generation_id: str) -> bool:
        with self.connection:
            result = self.connection.execute(
                "UPDATE assets SET active_generation=?, state='ready' "
                "WHERE id=? AND state != 'removed'",
                (generation_id, asset_id),
            )
            if result.rowcount:
                self.connection.execute(
                    "UPDATE generations SET complete=1 WHERE id=?", (generation_id,)
                )
        return result.rowcount > 0

    def active_generations(self) -> list[sqlite3.Row]:
        return self.connection.execute(
            "SELECT g.* FROM generations g JOIN assets a ON a.active_generation=g.id "
            "WHERE a.state='ready' AND g.complete=1"
        ).fetchall()

    def status(self, asset_id: str) -> sqlite3.Row | None:
        return self.connection.execute(
            "SELECT a.id, a.state, g.processed, g.source_hash, g.duration_ms, "
            "g.source_path, g.camera_id, g.recorded_at "
            "FROM assets a LEFT JOIN generations g ON g.id=COALESCE("
            "a.active_generation, (SELECT id FROM generations WHERE asset_id=a.id "
            "ORDER BY created_at DESC LIMIT 1)) WHERE a.id=?",
            (asset_id,),
        ).fetchone()

    def remove(self, asset_id: str) -> bool:
        with self.connection:
            result = self.connection.execute(
                "UPDATE assets SET state='removed', active_generation=NULL WHERE id=?",
                (asset_id,),
            )
        return result.rowcount > 0

    def purge(self, asset_id: str) -> list[str] | None:
        if self.asset(asset_id) is None:
            return None
        generation_ids = [
            row[0]
            for row in self.connection.execute(
                "SELECT id FROM generations WHERE asset_id=?", (asset_id,)
            )
        ]
        with self.connection:
            self.connection.execute("DELETE FROM assets WHERE id=?", (asset_id,))
        return generation_ids
