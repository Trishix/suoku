"""Cooperative single-model worker; call tick() to dispatch one bounded unit of work."""

from __future__ import annotations

import logging
import threading
import time
import traceback
import uuid
from dataclasses import asdict
from pathlib import Path

from ..engine import VideoLake
from ..types import LakeError, SearchFilters
from .jobs import Jobs

logger = logging.getLogger(__name__)

# A single worker owns model execution. The heartbeat is independent of provider
# latency so the API can distinguish a live worker from a merely configured one.


class Worker:
    def __init__(self, data_dir: Path, embedder, *, reader=None, provider=None, extractor=None):
        self.root = Path(data_dir).resolve()
        self.jobs = Jobs(self.root)
        self.lake = VideoLake.open(self.root / "index", embedder, reader=reader)
        self.insights = None
        try:
            if provider is not None:
                from ..insights import InsightEngine
                self.insights = InsightEngine(self.lake, provider, extractor=extractor)
        except Exception:
            self.lake.close()
            raise
        self.jobs.recover()
        self.active = None
        self.steps = None
        self.search_burst = 0
        self._last_expire = 0.0
        self._closed = False
        self._owner = uuid.uuid4().hex
        self._stop = threading.Event()
        model = getattr(provider, "model", None) if provider is not None else None
        self.jobs.start_worker(self._owner, model, self.insights is not None)
        self._heartbeat_thread = threading.Thread(target=self._heartbeat, daemon=True)
        self._heartbeat_thread.start()

    def _heartbeat(self):
        while not self._stop.wait(2):
            try:
                self.jobs.heartbeat(self._owner)
            except Exception:
                logger.error("Worker heartbeat write failed; check storage availability")

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
        started = time.monotonic()
        try:
            if job["kind"] == "search":
                matches = self.lake.search(
                    payload["query"],
                    limit=payload["limit"],
                    filters=SearchFilters(**payload.get("filters", {})),
                )
                result = {"matches": [asdict(match) for match in matches]}
            elif job["kind"] in {"analysis", "answer"}:
                if self.insights is None:
                    raise LakeError("insights_disabled", "Configure an insight model and provider key on the worker.")

                def cancelled():
                    return bool(self.jobs.get(job["id"], internal=True)["cancel"])

                arguments = {**payload, "cancelled": cancelled}
                if job["kind"] == "analysis":
                    result = {"observations": [asdict(item)
                              for item in self.insights.analyze(**arguments)]}
                else:
                    arguments["filters"] = SearchFilters(**payload.get("filters", {}))
                    result = {"answer": asdict(self.insights.ask(**arguments))}
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
                self.jobs.invalidate_insights(identifier)
                if job["kind"] == "purge" and payload.get("delete_media"):
                    path = self.root / "media" / (identifier + info["suffix"])
                    # Path is assembled only from validated IDs and server-owned suffixes.
                    path.unlink(missing_ok=True)
                    self.jobs.upload_state(identifier, "purged")
                result = {"asset_id": identifier}
            self.jobs.finish(job["id"], result=result)
            logger.info("job=%s kind=%s completed duration_ms=%d", job["id"], job["kind"],
                        round((time.monotonic() - started) * 1000))
        except Exception as exc:
            self._fail(job, exc)

    def _fail(self, job, exc):
        if isinstance(exc, LakeError) and exc.code == "cancelled":
            self.jobs.finish(job["id"], cancelled=True)
            return
        if isinstance(exc, LakeError):
            error = {"code": exc.code, "message": str(exc)}
        else:
            # Stack locations aid diagnosis without leaking exception text, locals or provider data.
            locations = [(Path(f.filename).name, f.lineno, f.name)
                         for f in traceback.extract_tb(exc.__traceback__)]
            logger.error("job=%s failure=%s stack=%s", job["id"], type(exc).__name__, locations)
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
        if self._closed:
            return
        self._closed = True
        self._stop.set()
        self._heartbeat_thread.join(timeout=12)
        self.jobs.stop_worker(self._owner)
        if self.steps is not None:
            self.steps.close()
        self.lake.close()
