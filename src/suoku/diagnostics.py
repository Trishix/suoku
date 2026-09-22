"""Offline preflight checks that never import or initialize embedding models."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import platform
import re
import shutil
import sqlite3
from pathlib import Path


def _model_is_valid(directory: Path) -> bool:
    try:
        root = directory.resolve(strict=True)
        manifest_path = root / "suoku-manifest.json"
        if manifest_path.stat().st_size > 1_048_576:
            return False
        manifest = json.loads(manifest_path.read_text())
        if manifest["model"] != "google/siglip-base-patch16-224" or not re.fullmatch(
            r"[0-9a-f]{40}", manifest["revision"]
        ):
            return False
        files = manifest["files"]
        if not isinstance(files, dict) or not files:
            return False
        for name, expected in files.items():
            source = (root / name).resolve(strict=True)
            if not source.is_relative_to(root) or not re.fullmatch(r"[0-9a-f]{64}", expected):
                return False
            digest = hashlib.sha256()
            with source.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            if digest.hexdigest() != expected:
                return False
        return True
    except (OSError, ValueError, KeyError, TypeError, RecursionError):
        return False


def diagnose(
    *, model: Path | None = None, data: Path | None = None, insights: bool = False
) -> dict:
    checks = []

    def check(name, ok, detail, fix):
        checks.append({"name": name, "ok": bool(ok), "detail": detail, "fix": "" if ok else fix})

    groups = [
        ("lancedb", "suoku"),
        ("numpy", "suoku"),
        ("PIL", "suoku"),
        ("filelock", "suoku"),
        ("fastapi", "suoku[server]"),
        ("uvicorn", "suoku[server]"),
    ]
    if model is not None:
        groups.extend(
            (name, "suoku[local]")
            for name in ("torch", "transformers", "safetensors", "sentencepiece", "google.protobuf")
        )
    if insights:
        groups.extend((name, "suoku[insights]") for name in ("litellm", "jsonschema"))
    for name, extra in groups:
        try:
            available = importlib.util.find_spec(name) is not None
        except (ImportError, ValueError, AttributeError):
            available = False
        check(
            "dependency:" + name,
            available,
            "Installed" if available else "Missing dependency",
            f"Run python -m pip install '{extra}'.",
        )
    for name in ("ffmpeg", "ffprobe"):
        found = shutil.which(name)
        check(
            name,
            found is not None,
            found or "Not found on PATH",
            "Install FFmpeg and add its bin directory to PATH.",
        )
    directory = (data or Path.cwd()).absolute()
    ancestor = directory
    while not ancestor.exists() and ancestor != ancestor.parent:
        ancestor = ancestor.parent
    writable = (
        ancestor.is_dir()
        and os.access(ancestor, os.W_OK | os.X_OK)
        and bool(ancestor.stat().st_mode & 0o222)
    )
    check(
        "data_writable",
        writable,
        "Data directory or its nearest existing parent is writable"
        if writable
        else "Data location is not writable",
        "Choose a writable directory with --data or correct its ownership and permissions.",
    )
    if model is not None:
        verified = _model_is_valid(model)
        check(
            "model_integrity",
            verified,
            "Pinned model files verified"
            if verified
            else "Missing or invalid pinned model manifest/files",
            "Run suoku prepare-model in a new empty directory with an immutable --revision, then pass --model.",
        )
    return {
        "ok": all(item["ok"] for item in checks),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "sqlite": sqlite3.sqlite_version,
        "checks": checks,
    }
