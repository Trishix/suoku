"""Cooperative single-model worker; call tick() to dispatch one bounded unit of work."""

from __future__ import annotations

import time
from dataclasses import asdict
from pathlib import Path

from ..engine import VideoLake
from ..types import LakeError, SearchFilters
from .jobs import Jobs


class Worker:
    def __init__(self, data_dir: Path, embedder, *, reader=None):
        self.root = Path(data_dir).resolve()
        self.jobs = Jobs(self.root)
        self.lake = VideoLake.open(self.root / "index", embedder, reader=reader)
        self.jobs.recover()
        self.active = None
        self.steps = None
        self.search_burst = 0
        self._last_expire = 0.0

    def tick(self) -> bool:
        if time.monotonic() - self._last_expire > 60:
            self.jobs.expire()
            self._last_expire = time.monotonic()
        if self.active:
            current = self.jobs.get(self.active["id"], internal=True)
            if current["cancel"]:
                self.steps.close()
                self.jobs.finish(current["id"], cancelled=True)
                self.active = self.steps = None
                return True
            if self.search_burst < 4:
                urgent = self.jobs.claim(search_only=True)
                if urgent:
                    self._execute(urgent)
                    self.search_burst += 1
                    return True
            self.search_burst = 0
            self._advance()
            return True
        job = self.jobs.claim(nonsearch_only=self.search_burst >= 4)
        if job is None:
            job = self.jobs.claim()
        if job is None:
            return False
        if job["kind"] == "ingest":
            self.active = job
            payload = job["payload"]
            self.steps = self.lake.ingest_steps(
                payload["source"],
                asset_id=payload["asset_id"],
                camera_id=payload.get("camera_id"),
                recorded_at=payload.get("recorded_at"),
            )
            self.search_burst = 0
            self._advance()
        else:
            self._execute(job)
            self.search_burst = self.search_burst + 1 if job["kind"] == "search" else 0
        return True

    def _advance(self):
        job = self.active
        try:
            progress = next(self.steps)
            self.jobs.progress(job["id"], progress.processed)
            if progress.complete:
                self.jobs.upload_state(progress.asset_id, "ready")
                self.jobs.finish(job["id"], result={"asset_id": progress.asset_id})
                self.steps.close()
                self.active = self.steps = None
        except StopIteration:
            self._fail(job, LakeError("ingestion_failed", "Ingestion ended before completion."))
            self.active = self.steps = None
        except Exception as exc:
            self._fail(job, exc)
            self.steps.close()
            self.active = self.steps = None

    def _execute(self, job):
        payload = job["payload"]
        try:
            if job["kind"] == "search":
                matches = self.lake.search(
                    payload["query"],
                    limit=payload["limit"],
                    filters=SearchFilters(**payload.get("filters", {})),
                )
                result = {"matches": [asdict(match) for match in matches]}
            else:
                identifier = payload["asset_id"]
                info = self.jobs.upload(identifier)
                # A cancelled, unprocessed upload may never have reached the engine.
                try:
                    if job["kind"] == "purge":
                        self.lake.purge(identifier)
                    else:
                        self.lake.remove(identifier)
                except LakeError as exc:
                    if exc.code != "not_found":
                        raise
                self.jobs.upload_state(identifier, "removed")
                if job["kind"] == "purge" and payload.get("delete_media"):
                    path = self.root / "media" / (identifier + info["suffix"])
                    # Path is assembled only from validated IDs and server-owned suffixes.
                    path.unlink(missing_ok=True)
                    self.jobs.upload_state(identifier, "purged")
                result = {"asset_id": identifier}
            self.jobs.finish(job["id"], result=result)
        except Exception as exc:
            self._fail(job, exc)

    def _fail(self, job, exc):
        if isinstance(exc, LakeError):
            error = {"code": exc.code, "message": str(exc)}
        else:
            error = {
                "code": "processing_failed",
                "message": "Processing failed; check local diagnostics.",
            }
        self.jobs.finish(job["id"], error=error)
        if job["kind"] == "ingest":
            self.jobs.upload_state(job["payload"]["asset_id"], "failed")

    def run(self):
        try:
            while True:
                if not self.tick():
                    time.sleep(0.1)
        finally:
            self.close()

    def close(self):
        if self.steps is not None:
            self.steps.close()
        self.lake.close()
