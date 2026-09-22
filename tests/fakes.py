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


class ColorInsightProvider:
    """Test-only observations from real pixels; the call counter exposes cache misses."""

    model = "test/color-insights"
    fingerprint = "test-only-color-insights-v1"

    def __init__(self):
        self.calls = 0

    def analyze_images(self, images, prompt, schema):
        self.calls += 1
        means = np.mean([np.asarray(frame.image).mean(axis=(0, 1)) for frame in images], axis=0)
        color = ["red", "green", "blue"][int(np.argmax(means))]
        details = {
            "scene": color, "color": color,
            "frame_timestamps": [frame.timestamp_ms for frame in images],
            "aspect_ratio": images[0].image.width / images[0].image.height,
            "limitations": ["Test-only color analysis from sampled frames."],
        }
        properties = schema["properties"]["payload"]["properties"]
        payload = {key: details.get(key, []) for key in properties}
        return {"summary": f"{color} sampled frames; analysis call {self.calls}", "payload": payload}

    def reason(self, question, evidence, schema):
        return {
            "answer": evidence[0]["summary"],
            "citations": [{"observation_id": item["id"], "reason": "Sampled color evidence"}
                          for item in evidence],
            "limitations": ["Deterministic integration fixture; no semantic claims."],
            "insufficient_evidence": False,
        }
