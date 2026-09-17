"""Opt-in, local-only SigLIP adapter and explicit pinned model preparation."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import numpy as np

from ..types import Frame, LakeError

MODEL_ID = "google/siglip-base-patch16-224"


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def prepare_model(directory: str | Path, *, revision: str) -> Path:
    """Explicit network operation, never called by import, ingestion, or service startup."""
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise LakeError("invalid_revision", "Supply an immutable 40-character model commit SHA.")
    try:
        from huggingface_hub import snapshot_download
    except ImportError as exc:
        raise LakeError("missing_extra", "Install semantic-video-lake[local].") from exc
    directory = Path(directory).resolve()
    if directory.exists() and any(directory.iterdir()):
        raise LakeError("model_exists", "Model destination must be empty to avoid mixed revisions.")
    directory.mkdir(parents=True, exist_ok=True)
    snapshot_download(
        MODEL_ID,
        revision=revision,
        local_dir=directory,
        allow_patterns=["*.json", "*.model", "*.safetensors", "LICENSE", "README.md"],
    )
    files = {
        str(path.relative_to(directory)): file_hash(path)
        for path in sorted(directory.rglob("*"))
        if path.is_file() and ".cache" not in path.relative_to(directory).parts
    }
    manifest = {"model": MODEL_ID, "revision": revision, "files": files}
    (directory / "svl-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return directory


class SiglipEmbedder:
    dimensions = 768

    def __init__(self, directory: str | Path, *, device: str = "cpu"):
        directory = Path(directory).resolve()
        try:
            manifest = json.loads((directory / "svl-manifest.json").read_text())
            if manifest["model"] != MODEL_ID or not re.fullmatch(
                r"[0-9a-f]{40}", manifest["revision"]
            ):
                raise ValueError("invalid model identity")
            for name, digest in manifest["files"].items():
                path = (directory / name).resolve()
                if not path.is_relative_to(directory) or file_hash(path) != digest:
                    raise ValueError("model integrity mismatch")
            if not manifest["files"]:
                raise ValueError("empty manifest")
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise LakeError(
                "model_integrity", "Prepare and verify a pinned local model first."
            ) from exc
        try:
            import torch
            from transformers import AutoProcessor, SiglipModel
        except ImportError as exc:
            raise LakeError("missing_extra", "Install semantic-video-lake[local].") from exc
        self._torch = torch
        self.device = device
        self.processor = AutoProcessor.from_pretrained(
            str(directory), local_files_only=True, trust_remote_code=False, use_fast=False
        )
        self.model = (
            SiglipModel.from_pretrained(
                str(directory), local_files_only=True, trust_remote_code=False, use_safetensors=True
            )
            .to(device)
            .eval()
        )
        self.fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "manifest": manifest,
                    "adapter": "siglip-v1",
                    "dtype": "float32",
                    "metric": "cosine",
                    "prompt": "This is a photo of {query}.",
                    "preprocessing": "ffmpeg-center-crop-224+model-processor",
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()

    def embed_frames(self, frames: list[Frame]) -> np.ndarray:
        inputs = self.processor(images=[frame.image for frame in frames], return_tensors="pt")
        with self._torch.inference_mode():
            output = self.model.get_image_features(**inputs.to(self.device))
        return output.cpu().float().numpy()

    def embed_query(self, text: str) -> np.ndarray:
        inputs = self.processor(
            text=[f"This is a photo of {text}."],
            padding="max_length",
            truncation=True,
            return_tensors="pt",
        )
        with self._torch.inference_mode():
            output = self.model.get_text_features(**inputs.to(self.device))
        return output[0].cpu().float().numpy()
