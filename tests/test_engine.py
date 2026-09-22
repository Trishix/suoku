"""Core ingestion, retrieval, source-integrity, and lifecycle tests."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from suoku.engine import VideoLake, _recorded_at
from suoku.types import Frame, LakeError, MediaInfo, SearchFilters


class DeterministicEmbedder:
    fingerprint = "test-colour-v1"
    dimensions = 3

    def embed_frames(self, frames: list[Frame]) -> np.ndarray:
        return np.asarray(
            [np.asarray(frame.image, dtype=np.float32).mean(axis=(0, 1)) + 1 for frame in frames]
        )

    def embed_query(self, text: str) -> np.ndarray:
        return {
            "red": np.array([1.0, 0.01, 0.01]),
            "green": np.array([0.01, 1.0, 0.01]),
            "blue": np.array([0.01, 0.01, 1.0]),
        }.get(text, np.ones(3))


class CountingEmbedder(DeterministicEmbedder):
    def __init__(self) -> None:
        self.frame_calls: list[tuple[int, ...]] = []

    def embed_frames(self, frames: list[Frame]) -> np.ndarray:
        self.frame_calls.append(tuple(frame.timestamp_ms for frame in frames))
        return super().embed_frames(frames)


class Reader:
    def __init__(self) -> None:
        self.timestamps = [0, 1000, 2000, 3000]
        self.colours = [(255, 0, 0), (240, 0, 0), (0, 255, 0), (0, 0, 255)]
        self.fail_after: int | None = None
        self.max_batch_seen = 0

    def probe(self, path: Path) -> MediaInfo:
        return MediaInfo(duration_ms=4000, width=2, height=2, codec="fixture")

    def frames(self, path: Path, *, interval_ms: int, start_ms: int = 0):
        yielded = 0
        for timestamp, colour in zip(self.timestamps, self.colours, strict=True):
            if timestamp < start_ms:
                continue
            if self.fail_after is not None and yielded >= self.fail_after:
                raise RuntimeError("injected decode failure")
            yielded += 1
            yield Frame(timestamp, Image.new("RGB", (2, 2), colour))


@pytest.fixture
def source(tmp_path: Path) -> Path:
    path = tmp_path / "video.bin"
    path.write_bytes(b"video version one")
    return path


def assert_error(code: str, function, *args, **kwargs) -> LakeError:
    with pytest.raises(LakeError) as caught:
        function(*args, **kwargs)
    assert caught.value.code == code
    return caught.value


def test_ingest_search_status_and_duplicate_registration(tmp_path: Path, source: Path) -> None:
    reader = Reader()
    with VideoLake.open(
        tmp_path / "lake", DeterministicEmbedder(), reader=reader, batch_size=2
    ) as lake:
        progress = list(
            lake.ingest_steps(source, camera_id="north", recorded_at="2026-01-01T05:30:00+05:30")
        )
        assert [item.processed for item in progress] == [2, 4, 4]
        assert [item.complete for item in progress] == [False, False, True]
        asset_id = progress[-1].asset_id
        assert (
            lake.ingest(source, camera_id="north", recorded_at="2026-01-01T00:00:00Z") == asset_id
        )
        assert lake.status(asset_id) == {
            "id": asset_id,
            "state": "ready",
            "processed": 4,
            "source_hash": lake.status(asset_id)["source_hash"],
            "duration_ms": 4000,
            "source_path": str(source.resolve()),
            "camera_id": "north",
            "recorded_at": "2026-01-01T00:00:00.000Z",
        }
        matches = lake.search("red", limit=2)
        assert len(matches) == 1
        assert matches[0].asset_id == asset_id
        assert matches[0].timestamp_ms in {0, 1000}
        assert (matches[0].start_ms, matches[0].end_ms) == (0, 4000)


def test_resume_after_iterator_interruption_is_idempotent(tmp_path: Path, source: Path) -> None:
    lake_path = tmp_path / "lake"
    reader = Reader()
    lake = VideoLake.open(lake_path, DeterministicEmbedder(), reader=reader, batch_size=2)
    iterator = lake.ingest_steps(source, camera_id="camera", recorded_at="2026-01-01T00:00:00Z")
    first = next(iterator)
    assert first.processed == 2
    iterator.close()
    lake.close()

    with VideoLake.open(lake_path, DeterministicEmbedder(), reader=reader, batch_size=2) as resumed:
        rest = list(resumed.ingest_steps(source, asset_id=first.asset_id))
        assert rest[-1].processed == 4
        assert resumed._table.count_rows() == 4
        assert resumed.status(first.asset_id)["camera_id"] == "camera"
        assert resumed.status(first.asset_id)["recorded_at"] == "2026-01-01T00:00:00.000Z"


def test_resume_after_vector_commit_before_checkpoint_is_idempotent(
    tmp_path: Path, source: Path
) -> None:
    lake_path = tmp_path / "lake"
    embedder = CountingEmbedder()
    lake = VideoLake.open(lake_path, embedder, reader=Reader(), batch_size=2)
    lake.catalog.checkpoint = lambda *args: (_ for _ in ()).throw(RuntimeError("crash"))  # type: ignore[method-assign]
    assert_error("media_error", lambda: next(lake.ingest_steps(source)))
    assert lake._table.count_rows() == 2
    generation = lake.catalog.connection.execute("SELECT processed FROM generations").fetchone()
    assert generation["processed"] == 0
    lake.close()

    with VideoLake.open(lake_path, embedder, reader=Reader(), batch_size=2) as resumed:
        asset_id = resumed.ingest(source)
        assert resumed.status(asset_id)["processed"] == 4
        assert resumed._table.count_rows() == 4
    assert embedder.frame_calls == [(0, 1000), (0, 1000), (2000, 3000)]


def test_failed_replacement_does_not_replace_active_generation(
    tmp_path: Path, source: Path
) -> None:
    reader = Reader()
    with VideoLake.open(
        tmp_path / "lake", DeterministicEmbedder(), reader=reader, batch_size=2
    ) as lake:
        asset_id = lake.ingest(source)
        old_hash = lake.status(asset_id)["source_hash"]
        source.write_bytes(b"video version two")
        reader.colours = [(0, 0, 255)] * 4
        reader.fail_after = 2
        assert_error("media_error", lambda: list(lake.ingest_steps(source, asset_id=asset_id)))
        assert lake.status(asset_id)["source_hash"] == old_hash
        assert lake.search("red", limit=1)[0].source_hash == old_hash


def test_source_change_and_removal_cannot_activate_paused_ingest(
    tmp_path: Path, source: Path
) -> None:
    reader = Reader()
    with VideoLake.open(
        tmp_path / "lake", DeterministicEmbedder(), reader=reader, batch_size=2
    ) as lake:
        changing = lake.ingest_steps(source)
        first = next(changing)
        source.write_bytes(b"changed while decoding")
        assert_error("source_changed", lambda: list(changing))
        assert lake.status(first.asset_id)["state"] == "ingesting"

        source.write_bytes(b"a third version")
        paused = lake.ingest_steps(source, asset_id=first.asset_id)
        next(paused)
        lake.remove(first.asset_id)
        assert_error("asset_removed", lambda: next(paused))
        assert lake.status(first.asset_id)["state"] == "removed"


def test_filters_use_matched_instant_and_unknown_times_are_excluded(
    tmp_path: Path, source: Path
) -> None:
    reader = Reader()
    second = tmp_path / "second.bin"
    second.write_bytes(b"second")
    with VideoLake.open(tmp_path / "lake", DeterministicEmbedder(), reader=reader) as lake:
        known = lake.ingest(source, camera_id="A", recorded_at="2026-01-01T00:00:00Z")
        lake.ingest(second, camera_id="A")
        results = lake.search(
            "green",
            limit=10,
            filters=SearchFilters(
                camera_id="A",
                recorded_after="2026-01-01T00:00:01.500Z",
                recorded_before="2026-01-01T00:00:02.500Z",
            ),
        )
        assert [(match.asset_id, match.timestamp_ms) for match in results] == [(known, 2000)]
        assert lake.search("red", filters=SearchFilters(camera_id="missing")) == []


def test_time_conversion_handles_extremes_and_pre_epoch_submilliseconds() -> None:
    assert _recorded_at("1969-12-31T23:59:59.999500Z") == (
        "1969-12-31T23:59:59.999Z",
        -1,
    )
    assert _recorded_at("9999-12-31T23:59:59Z") == (
        "9999-12-31T23:59:59.000Z",
        253402300799000,
    )
    assert_error("invalid_time", _recorded_at, "9999-12-31T23:59:59-23:59")


def test_dedup_uses_bounded_playback_overlap_and_refills(tmp_path: Path, source: Path) -> None:
    reader = Reader()
    reader.timestamps = [0, 9000, 18000]
    reader.colours = [(99, 0, 0), (255, 0, 0), (180, 0, 0)]
    reader.probe = lambda path: MediaInfo(23000, 2, 2, "fixture")  # type: ignore[method-assign]
    with VideoLake.open(tmp_path / "lake", DeterministicEmbedder(), reader=reader) as lake:
        lake.ingest(source)
        matches = lake.search("red", limit=2)
        assert [match.timestamp_ms for match in matches] == [0, 18000]
        assert matches[0].score >= matches[1].score


def test_remove_purge_and_source_preservation(tmp_path: Path, source: Path) -> None:
    with VideoLake.open(tmp_path / "lake", DeterministicEmbedder(), reader=Reader()) as lake:
        asset_id = lake.ingest(source)
        lake.remove(asset_id)
        assert lake.search("red") == []
        assert_error("asset_removed", lake.ingest, source, asset_id=asset_id)
        assert_error("external_source", lake.purge, asset_id, delete_source=True)
        assert source.exists()
        lake.purge(asset_id)
        assert source.exists()
        assert_error("not_found", lake.status, asset_id)


@pytest.mark.parametrize(
    ("bad", "code"),
    [
        (np.array([0.0, 0.0, 0.0]), "invalid_embedding"),
        (np.array([np.nan, 0.0, 0.0]), "invalid_embedding"),
        (np.array([1.0, 2.0]), "invalid_embedding"),
    ],
)
def test_query_vector_validation(tmp_path: Path, source: Path, bad: np.ndarray, code: str) -> None:
    embedder = DeterministicEmbedder()
    with VideoLake.open(tmp_path / "lake", embedder, reader=Reader()) as lake:
        lake.ingest(source)
        embedder.embed_query = lambda text: bad  # type: ignore[method-assign]
        assert_error(code, lake.search, "red")


def test_frame_vector_validation_does_not_checkpoint(tmp_path: Path, source: Path) -> None:
    embedder = DeterministicEmbedder()
    embedder.embed_frames = lambda frames: np.zeros((len(frames), 3))  # type: ignore[method-assign]
    with VideoLake.open(tmp_path / "lake", embedder, reader=Reader(), batch_size=2) as lake:
        iterator = lake.ingest_steps(source)
        assert_error("invalid_embedding", lambda: next(iterator))


def test_mutated_embedder_profile_is_rejected_before_and_after_adapter_calls(
    tmp_path: Path, source: Path
) -> None:
    embedder = DeterministicEmbedder()
    with VideoLake.open(tmp_path / "lake", embedder, reader=Reader(), batch_size=2) as lake:
        embedder.dimensions = 4
        assert_error("incompatible_profile", lambda: next(lake.ingest_steps(source)))
        embedder.dimensions = 3

        original_frames = embedder.embed_frames

        def mutate_during_frames(frames: list[Frame]) -> np.ndarray:
            result = original_frames(frames)
            embedder.fingerprint = "changed-after-open"
            return result

        embedder.embed_frames = mutate_during_frames  # type: ignore[method-assign]
        assert_error("incompatible_profile", lambda: next(lake.ingest_steps(source)))
        assert lake._table.count_rows() == 0

        embedder.fingerprint = "test-colour-v1"
        embedder.embed_frames = original_frames  # type: ignore[method-assign]
        lake.ingest(source)
        original_query = embedder.embed_query

        def mutate_during_query(text: str) -> np.ndarray:
            result = original_query(text)
            embedder.fingerprint = "changed-after-open"
            return result

        embedder.embed_query = mutate_during_query  # type: ignore[method-assign]
        assert_error("incompatible_profile", lake.search, "red")
        embedder.fingerprint = "test-colour-v1"
        embedder.embed_query = original_query  # type: ignore[method-assign]
        assert lake.search("red")[0].model_fingerprint == "test-colour-v1"


def test_validation_profile_and_lifetime_lock(tmp_path: Path, source: Path) -> None:
    lake_path = tmp_path / "lake"
    lake = VideoLake.open(lake_path, DeterministicEmbedder(), reader=Reader())
    try:
        assert_error(
            "lake_busy", VideoLake.open, lake_path, DeterministicEmbedder(), reader=Reader()
        )
        assert_error("invalid_asset_id", lake.status, "ABC")
        assert_error("invalid_time", lake.ingest, source, recorded_at="2026-01-01")
        assert_error("invalid_limit", lake.search, "x", limit=0)
    finally:
        lake.close()

    incompatible = replace_embedder(fingerprint="test-colour-v2")
    assert_error("incompatible_profile", VideoLake.open, lake_path, incompatible, reader=Reader())
    different_interval = DeterministicEmbedder()
    assert_error(
        "incompatible_profile",
        VideoLake.open,
        lake_path,
        different_interval,
        reader=Reader(),
        interval_ms=1000,
    )


def replace_embedder(*, fingerprint: str) -> DeterministicEmbedder:
    embedder = DeterministicEmbedder()
    embedder.fingerprint = fingerprint
    return embedder
