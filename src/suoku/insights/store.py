"""Connection-per-operation observation cache. Safe for API readers and one engine writer."""

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path

from .models import Observation, canonical


class InsightStore:
    def __init__(self, directory: Path):
        self.path = directory / "insights.sqlite3"
        with self.connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS observations (
                cache_key TEXT PRIMARY KEY, asset_id TEXT NOT NULL, source_hash TEXT NOT NULL,
                recipe_id TEXT NOT NULL, provider_fingerprint TEXT NOT NULL, body TEXT NOT NULL
            )""")
            db.execute("CREATE INDEX IF NOT EXISTS observations_asset ON observations(asset_id)")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            db.execute("PRAGMA journal_mode=WAL")
            yield db
            db.commit()
        finally:
            db.close()

    def get(self, key: str) -> Observation | None:
        with self.connect() as db:
            row = db.execute("SELECT body FROM observations WHERE cache_key=?", (key,)).fetchone()
        return Observation(**json.loads(row[0])) if row else None

    def put(self, key: str, observation: Observation):
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO observations VALUES(?,?,?,?,?,?)",
                       (key, observation.asset_id, observation.source_hash, observation.recipe_id,
                        observation.provider_fingerprint, canonical(asdict(observation))))

    @staticmethod
    def read_visible(directory: Path, asset_id: str, *, recipe=None, provider=None):
        """Read without opening VideoLake or taking its exclusive writer lock."""
        from ..engine import _hash_file
        from ..types import LakeError

        cache = directory / "insights.sqlite3"
        catalog = directory / "catalog.sqlite3"
        if not cache.is_file() or not catalog.is_file():
            return []
        db = sqlite3.connect(cache.as_uri() + "?mode=ro", uri=True)
        try:
            db.execute("ATTACH DATABASE ? AS catalog", (catalog.as_uri() + "?mode=ro",))
            source = db.execute("""SELECT g.source_path,g.source_hash FROM catalog.assets a
                JOIN catalog.generations g ON g.id=a.active_generation
                WHERE a.id=? AND a.state='ready' AND g.complete=1""", (asset_id,)).fetchone()
            if source is None:
                return []
            try:
                if _hash_file(Path(source[0])) != source[1]:
                    return []
            except LakeError:
                return []
            rows = db.execute("""SELECT o.body FROM observations o
                JOIN catalog.assets a ON a.id=o.asset_id
                JOIN catalog.generations g ON g.id=a.active_generation
                WHERE a.id=? AND a.state='ready' AND g.complete=1 AND o.source_hash=g.source_hash
                AND (? IS NULL OR o.recipe_id=?) AND (? IS NULL OR o.provider_fingerprint=?)
                ORDER BY json_extract(o.body, '$.start_ms'), o.cache_key""",
                (asset_id, recipe, recipe, provider, provider)).fetchall()
            return [Observation(**json.loads(row[0])) for row in rows]
        finally:
            db.close()
