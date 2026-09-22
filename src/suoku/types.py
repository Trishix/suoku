"""Small shared data contracts used by retrieval, decoding, and adapters."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np
from PIL import Image

from .errors import LakeError as LakeError


@dataclass(frozen=True)
class MediaInfo:
    duration_ms: int
    width: int
    height: int
    codec: str


@dataclass(frozen=True)
class Frame:
    timestamp_ms: int
    image: Image.Image


@dataclass(frozen=True)
class Match:
    asset_id: str
    timestamp_ms: int
    start_ms: int
    end_ms: int
    score: float
    source_hash: str
    model_fingerprint: str
    camera_id: str | None


@dataclass(frozen=True)
class IngestProgress:
    asset_id: str
    processed: int
    complete: bool


@dataclass(frozen=True)
class SearchFilters:
    camera_id: str | None = None
    recorded_after: str | None = None
    recorded_before: str | None = None


class Embedder(Protocol):
    fingerprint: str
    dimensions: int

    def embed_frames(self, frames: list[Frame]) -> np.ndarray: ...

    def embed_query(self, text: str) -> np.ndarray: ...


class MediaReader(Protocol):
    def probe(self, path: Path) -> MediaInfo: ...

    def frames(self, path: Path, *, interval_ms: int, start_ms: int = 0) -> Iterator[Frame]: ...
