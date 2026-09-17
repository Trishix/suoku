"""Deterministic test doubles, not semantic or production models."""

import numpy as np
from PIL import Image

from suoku.types import Frame, MediaInfo


class ColorEmbedder:
    dimensions = 3
    fingerprint = "test-only-color-v1"

    def embed_frames(self, frames):
        return np.array([np.asarray(frame.image).mean(axis=(0, 1)) + 0.01 for frame in frames])

    def embed_query(self, text):
        return np.array(
            {"red": [1, 0, 0], "green": [0, 1, 0], "blue": [0, 0, 1]}.get(text, [1, 1, 1])
        )


class FixtureReader:
    def probe(self, path):
        return MediaInfo(20_000, 224, 224, "h264")

    def frames(self, path, *, interval_ms, start_ms=0):
        for timestamp in range(0, 20_000, interval_ms):
            if timestamp >= start_ms:
                yield Frame(
                    timestamp, Image.new("RGB", (224, 224), "red" if timestamp < 10_000 else "blue")
                )
