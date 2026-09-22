from __future__ import annotations

import hashlib
import math
import re
import uuid
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Self

import lancedb
import numpy as np
import pyarrow as pa
from filelock import FileLock, Timeout

from .catalog import Catalog
from .types import (
    Embedder,
    Frame,
    IngestProgress,
    LakeError,
    Match,
    MediaReader,
    SearchFilters,
)

_ID = re.compile(r"^[0-9a-f]{32}$")
_MAX_TEXT = 4096
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def _asset_id(value: str) -> str:
    if not isinstance(value, str) or _ID.fullmatch(value) is None:
        raise LakeError("invalid_asset_id", "asset_id must be 32 lowercase hexadecimal characters")
    return value


def _recorded_at(value: str | None, *, field: str = "recorded_at") -> tuple[str | None, int | None]:
    if value is None:
        return None, None
    if not isinstance(value, str) or not value.strip():
        raise LakeError("invalid_time", f"{field} must be an ISO 8601 timestamp with a timezone")
    try:
        parsed = datetime.fromisoformat(value.strip())
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise LakeError("invalid_time", f"{field} must include a timezone")
        parsed = parsed.astimezone(UTC)
    except (ValueError, OverflowError) as exc:
        raise LakeError(
            "invalid_time", f"{field} must be an ISO 8601 timestamp with a valid timezone"
        ) from exc
    normalized = parsed.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    delta = parsed - _EPOCH
    milliseconds = (delta.days * 86_400 + delta.seconds) * 1000 + delta.microseconds // 1000
    return normalized, milliseconds


def _camera_id(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > 256 or "\x00" in value:
        raise LakeError(
            "invalid_camera_id", "camera_id must be a nonempty string of at most 256 characters"
        )
    return value


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
    except OSError as exc:
        raise LakeError("source_unreadable", "source cannot be read") from exc
    return digest.hexdigest()


def _normalized_vector(value: object, dimensions: int, *, rows: int | None = None) -> np.ndarray:
    try:
        array = np.asarray(value, dtype=np.float32)
    except (TypeError, ValueError) as exc:
        raise LakeError("invalid_embedding", "embedder returned a non-numeric vector") from exc
    expected = (dimensions,) if rows is None else (rows, dimensions)
    if array.shape != expected:
        raise LakeError(
            "invalid_embedding", f"embedder returned shape {array.shape}; expected {expected}"
        )
    if not np.isfinite(array).all():
        raise LakeError("invalid_embedding", "embedder returned a non-finite vector")
    if rows is None:
        norm = float(np.linalg.norm(array))
        if not math.isfinite(norm) or norm <= 0:
            raise LakeError("invalid_embedding", "embedder returned a zero-length vector")
        return array / norm
    norms = np.linalg.norm(array, axis=1)
    if not np.isfinite(norms).all() or bool(np.any(norms <= 0)):
        raise LakeError("invalid_embedding", "embedder returned a zero-length vector")
    return array / norms[:, np.newaxis]


class VideoLake:
    def __init__(
        self,
        path: Path,
        embedder: Embedder,
        reader: MediaReader,
        interval_ms: int,
        batch_size: int,
        lock: FileLock,
        model_fingerprint: str,
        dimensions: int,
    ) -> None:
        self.path = path
        self.embedder = embedder
        self.reader = reader
        self.interval_ms = interval_ms
        self.batch_size = batch_size
        self._lock = lock
        self._closed = False
        self._model_fingerprint = model_fingerprint
        self._dimensions = dimensions
        self._assert_profile()
        self.catalog = Catalog(path / "catalog.sqlite3")
        self._db = lancedb.connect(path / "lance")
        schema = pa.schema(
            [
                pa.field("id", pa.string(), nullable=False),
                pa.field("generation_id", pa.string(), nullable=False),
                pa.field("asset_id", pa.string(), nullable=False),
                pa.field("timestamp_ms", pa.int64(), nullable=False),
                pa.field("duration_ms", pa.int64(), nullable=False),
                pa.field("source_hash", pa.string(), nullable=False),
                pa.field("model_fingerprint", pa.string(), nullable=False),
                pa.field("camera_id", pa.string()),
                pa.field("recorded_at_ms", pa.int64()),
                pa.field("vector", pa.list_(pa.float32(), dimensions), nullable=False),
            ]
        )
        if "frames" in self._db.list_tables().tables:
            self._table = self._db.open_table("frames")
        else:
            self._table = self._db.create_table("frames", schema=schema)

    @classmethod
    def open(
        cls,
        path: str | Path,
        embedder: Embedder,
        *,
        reader: MediaReader | None = None,
        interval_ms: int = 2000,
        batch_size: int = 8,
    ) -> VideoLake:
        lake_path = Path(path).expanduser().resolve()
        if not isinstance(interval_ms, int) or isinstance(interval_ms, bool) or interval_ms <= 0:
            raise LakeError("invalid_config", "interval_ms must be a positive integer")
        if not isinstance(batch_size, int) or isinstance(batch_size, bool) or batch_size <= 0:
            raise LakeError("invalid_config", "batch_size must be a positive integer")
        fingerprint = getattr(embedder, "fingerprint", None)
        dimensions = getattr(embedder, "dimensions", None)
        if not isinstance(fingerprint, str) or not fingerprint.strip():
            raise LakeError("invalid_embedder", "embedder fingerprint must be a nonempty string")
        if not isinstance(dimensions, int) or isinstance(dimensions, bool) or dimensions <= 0:
            raise LakeError("invalid_embedder", "embedder dimensions must be a positive integer")
        lake_path.mkdir(parents=True, exist_ok=True)
        lock = FileLock(lake_path / ".writer.lock")
        try:
            lock.acquire(timeout=0)
        except Timeout as exc:
            raise LakeError("lake_busy", "lake is already open by another writer") from exc
        try:
            if reader is None:
                from .media import FFmpegReader

                reader = FFmpegReader()
            instance = cls(
                lake_path,
                embedder,
                reader,
                interval_ms,
                batch_size,
                lock,
                fingerprint,
                dimensions,
            )
            expected = {
                "format_version": "1",
                "model_fingerprint": fingerprint,
                "dimensions": str(dimensions),
                "interval_ms": str(interval_ms),
            }
            existing = instance.catalog.metadata()
            if existing and any(existing.get(key) != value for key, value in expected.items()):
                instance.close()
                raise LakeError(
                    "incompatible_profile",
                    "lake was created with a different embedding model or sampling profile",
                )
            if not existing:
                instance.catalog.set_metadata(expected)
            return instance
        except Exception:
            if lock.is_locked:
                lock.release()
            raise

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _ensure_open(self) -> None:
        if self._closed:
            raise LakeError("lake_closed", "lake is closed")

    def _assert_profile(self) -> None:
        if (
            getattr(self.embedder, "fingerprint", None) != self._model_fingerprint
            or getattr(self.embedder, "dimensions", None) != self._dimensions
        ):
            raise LakeError(
                "incompatible_profile",
                "embedder profile changed after the lake was opened",
            )

    def _embed_frames(self, frames: list[Frame]) -> object:
        self._assert_profile()
        try:
            return self.embedder.embed_frames(frames)
        finally:
            self._assert_profile()

    def _embed_query(self, text: str) -> object:
        self._assert_profile()
        try:
            return self.embedder.embed_query(text)
        finally:
            self._assert_profile()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.catalog.close()
        self._lock.release()

    def ingest(
        self,
        source: str | Path,
        *,
        asset_id: str | None = None,
        camera_id: str | None = None,
        recorded_at: str | None = None,
    ) -> str:
        result = None
        for progress in self.ingest_steps(
            source, asset_id=asset_id, camera_id=camera_id, recorded_at=recorded_at
        ):
            result = progress.asset_id
        assert result is not None
        return result

    def ingest_steps(
        self,
        source: str | Path,
        *,
        asset_id: str | None = None,
        camera_id: str | None = None,
        recorded_at: str | None = None,
    ) -> Iterator[IngestProgress]:
        self._ensure_open()
        camera_supplied = camera_id is not None
        recorded_supplied = recorded_at is not None
        camera_id = _camera_id(camera_id)
        recorded_at, recorded_at_ms = _recorded_at(recorded_at)
        try:
            source_path = Path(source).expanduser().resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise LakeError("source_not_found", "source file does not exist") from exc
        if not source_path.is_file():
            raise LakeError("source_not_found", "source must be a regular file")
        canonical = str(source_path)
        if asset_id is None:
            camera_key = camera_id or ""
            recorded_key = recorded_at or ""
            existing = self.catalog.registered(canonical, camera_key, recorded_key)
            asset_id = existing["id"] if existing is not None else uuid.uuid4().hex
        else:
            asset_id = _asset_id(asset_id)
            existing = self.catalog.asset(asset_id)
            if existing is not None:
                if not camera_supplied:
                    camera_id = existing["camera_key"] or None
                if not recorded_supplied:
                    recorded_at, recorded_at_ms = _recorded_at(existing["recorded_key"] or None)
            camera_key = camera_id or ""
            recorded_key = recorded_at or ""
        if existing is not None and existing["state"] == "removed":
            raise LakeError("asset_removed", "removed asset cannot be resumed")
        if existing is None:
            self.catalog.create_asset(asset_id, canonical, camera_key, recorded_key)
        source_hash = _hash_file(source_path)
        current_asset = self.catalog.asset(asset_id)
        assert current_asset is not None
        if current_asset["active_generation"] is not None:
            active = self.catalog.generation(current_asset["active_generation"])
            assert active is not None
            if (
                active["source_hash"] == source_hash
                and active["source_path"] == canonical
                and active["camera_id"] == camera_id
                and active["recorded_at"] == recorded_at
            ):
                yield IngestProgress(asset_id, int(active["processed"]), True)
                return
        generation = self.catalog.resumable(
            asset_id, source_hash, canonical, camera_id, recorded_at
        )
        if generation is None:
            try:
                info = self.reader.probe(source_path)
            except LakeError:
                raise
            except Exception as exc:
                raise LakeError("media_error", "source media could not be probed") from exc
            if info.duration_ms < 0 or info.width <= 0 or info.height <= 0:
                raise LakeError("media_error", "source media metadata is invalid")
            generation_id = uuid.uuid4().hex
            self.catalog.create_generation(
                {
                    "id": generation_id,
                    "asset_id": asset_id,
                    "source_hash": source_hash,
                    "source_path": canonical,
                    "camera_id": camera_id,
                    "recorded_at": recorded_at,
                    "recorded_at_ms": recorded_at_ms,
                    "duration_ms": int(info.duration_ms),
                    "width": int(info.width),
                    "height": int(info.height),
                    "codec": str(info.codec),
                }
            )
            generation = self.catalog.generation(generation_id)
            assert generation is not None
        generation_id = generation["id"]
        start_ms = int(generation["next_start_ms"])
        processed = int(generation["processed"])
        batch: list[Frame] = []

        def commit(frames: Sequence[Frame]) -> int:
            current = self.catalog.asset(asset_id)
            if current is None or current["state"] == "removed":
                raise LakeError("asset_removed", "removed asset cannot be resumed")
            vectors = _normalized_vector(
                self._embed_frames(list(frames)),
                self._dimensions,
                rows=len(frames),
            )
            rows = []
            for frame, vector in zip(frames, vectors, strict=True):
                rows.append(
                    {
                        "id": f"{generation_id}:{frame.timestamp_ms}",
                        "generation_id": generation_id,
                        "asset_id": asset_id,
                        "timestamp_ms": int(frame.timestamp_ms),
                        "duration_ms": int(generation["duration_ms"]),
                        "source_hash": source_hash,
                        "model_fingerprint": self._model_fingerprint,
                        "camera_id": camera_id,
                        "recorded_at_ms": recorded_at_ms,
                        "vector": vector.tolist(),
                    }
                )
            self._table.merge_insert(
                "id"
            ).when_matched_update_all().when_not_matched_insert_all().execute(rows)
            return self.catalog.checkpoint(
                generation_id, len(frames), int(frames[-1].timestamp_ms) + 1
            )

        last_timestamp = start_ms - 1
        try:
            frames = self.reader.frames(
                source_path, interval_ms=self.interval_ms, start_ms=start_ms
            )
            for frame in frames:
                timestamp = frame.timestamp_ms
                if not isinstance(timestamp, int) or isinstance(timestamp, bool) or timestamp < 0:
                    raise LakeError("media_error", "reader returned an invalid frame timestamp")
                if timestamp < start_ms:
                    continue
                if timestamp > int(generation["duration_ms"]):
                    raise LakeError("media_error", "reader returned a frame beyond media duration")
                if timestamp <= last_timestamp:
                    raise LakeError("media_error", "reader returned unordered frame timestamps")
                last_timestamp = timestamp
                batch.append(frame)
                if len(batch) == self.batch_size:
                    processed = commit(batch)
                    batch.clear()
                    yield IngestProgress(asset_id, processed, False)
            if batch:
                processed = commit(batch)
                batch.clear()
                yield IngestProgress(asset_id, processed, False)
        except LakeError:
            raise
        except Exception as exc:
            raise LakeError("media_error", "source media could not be decoded") from exc
        if _hash_file(source_path) != source_hash:
            raise LakeError("source_changed", "source changed while it was being ingested")
        if not self.catalog.activate(asset_id, generation_id):
            raise LakeError("asset_removed", "removed asset cannot be resumed")
        yield IngestProgress(asset_id, processed, True)

    def search(
        self, text: str, *, limit: int = 10, filters: SearchFilters | None = None,
        asset_ids: list[str] | None = None,
    ) -> list[Match]:
        self._ensure_open()
        if not isinstance(text, str) or not text.strip() or len(text) > _MAX_TEXT:
            raise LakeError("invalid_query", "query must be nonempty and at most 4096 characters")
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
            raise LakeError("invalid_limit", "limit must be between 1 and 100")
        if filters is None:
            filters = SearchFilters()
        if not isinstance(filters, SearchFilters):
            raise LakeError("invalid_filters", "filters must be SearchFilters")
        selected_ids = None
        if asset_ids is not None:
            if not isinstance(asset_ids, list) or not 1 <= len(asset_ids) <= 100:
                raise LakeError("invalid_filters", "asset_ids must contain 1 to 100 asset IDs")
            selected_ids = {_asset_id(value) for value in asset_ids}
        camera = _camera_id(filters.camera_id)
        _, after_ms = _recorded_at(filters.recorded_after, field="recorded_after")
        _, before_ms = _recorded_at(filters.recorded_before, field="recorded_before")
        if after_ms is not None and before_ms is not None and after_ms >= before_ms:
            raise LakeError("invalid_filters", "recorded_after must precede recorded_before")
        generations = self.catalog.active_generations()
        clauses: list[str] = []
        selected = []
        for generation in generations:
            if selected_ids is not None and generation["asset_id"] not in selected_ids:
                continue
            if camera is not None and generation["camera_id"] != camera:
                continue
            recorded_ms = generation["recorded_at_ms"]
            if (after_ms is not None or before_ms is not None) and recorded_ms is None:
                continue
            terms = [f"generation_id = '{generation['id']}'"]
            if after_ms is not None:
                terms.append(f"recorded_at_ms + timestamp_ms >= {after_ms}")
            if before_ms is not None:
                terms.append(f"recorded_at_ms + timestamp_ms <= {before_ms}")
            clauses.append("(" + " AND ".join(terms) + ")")
            selected.append(generation)
        if not clauses:
            return []
        query = _normalized_vector(self._embed_query(text), self._dimensions)
        predicate = " OR ".join(clauses)
        total = sum(int(generation["processed"]) for generation in selected)
        if total == 0:
            return []
        # Grow the candidate window to refill results removed by timestamp deduplication.
        candidate_limit = min(max(limit * 2, 16), total)
        matches: list[Match] = []
        while True:
            rows = (
                self._table.search(query)
                .distance_type("cosine")
                .where(predicate, prefilter=True)
                .limit(candidate_limit)
                .to_list()
            )
            matches = self._matches(rows, limit)
            if len(matches) >= limit or len(rows) < candidate_limit or candidate_limit >= total:
                return matches
            candidate_limit = min(candidate_limit * 2, total)

    def _matches(self, rows: list[dict[str, object]], limit: int) -> list[Match]:
        accepted: list[Match] = []
        intervals: dict[str, list[tuple[int, int]]] = {}
        for row in sorted(rows, key=lambda item: float(item["_distance"])):
            asset_id = str(row["asset_id"])
            timestamp = int(row["timestamp_ms"])
            duration = int(row["duration_ms"])
            start_ms = max(0, timestamp - 5000)
            end_ms = min(duration, timestamp + 5000)
            seen = intervals.setdefault(asset_id, [])
            if any(
                start_ms <= other_end and other_start <= end_ms for other_start, other_end in seen
            ):
                continue
            seen.append((start_ms, end_ms))
            distance = float(row["_distance"])
            accepted.append(
                Match(
                    asset_id=asset_id,
                    timestamp_ms=timestamp,
                    start_ms=start_ms,
                    end_ms=end_ms,
                    score=max(-1.0, min(1.0, 1.0 - distance)),
                    source_hash=str(row["source_hash"]),
                    model_fingerprint=str(row["model_fingerprint"]),
                    camera_id=None if row["camera_id"] is None else str(row["camera_id"]),
                )
            )
            if len(accepted) == limit:
                break
        return accepted

    def status(self, asset_id: str) -> dict[str, object]:
        self._ensure_open()
        asset_id = _asset_id(asset_id)
        row = self.catalog.status(asset_id)
        if row is None:
            raise LakeError("not_found", "asset was not found")
        return dict(row)

    def remove(self, asset_id: str) -> None:
        self._ensure_open()
        asset_id = _asset_id(asset_id)
        if not self.catalog.remove(asset_id):
            raise LakeError("not_found", "asset was not found")

    def purge(self, asset_id: str, *, delete_source: bool = False) -> None:
        self._ensure_open()
        asset_id = _asset_id(asset_id)
        if delete_source:
            raise LakeError(
                "external_source", "core never deletes externally referenced source media"
            )
        asset = self.catalog.asset(asset_id)
        if asset is None:
            raise LakeError("not_found", "asset was not found")
        generation_ids = [
            row[0]
            for row in self.catalog.connection.execute(
                "SELECT id FROM generations WHERE asset_id=?", (asset_id,)
            )
        ]
        for generation_id in generation_ids:
            self._table.delete(f"generation_id = '{generation_id}'")
        insights = self.path / "insights.sqlite3"
        if insights.is_file():
            import sqlite3
            db = sqlite3.connect(insights)
            try:
                with db:
                    db.execute("DELETE FROM observations WHERE asset_id=?", (asset_id,))
            finally:
                db.close()
        self.catalog.purge(asset_id)
