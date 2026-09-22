"""Local video retrieval. Importing this package never loads or downloads a model."""

from .errors import LakeError

__all__ = ["VideoLake", "LakeError", "Match", "SearchFilters"]


def __getattr__(name):
    if name == "VideoLake":
        from .engine import VideoLake
        return VideoLake
    if name in {"Match", "SearchFilters"}:
        from . import types
        return getattr(types, name)
    raise AttributeError(f"module 'suoku' has no attribute {name!r}")
