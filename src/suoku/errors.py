"""Dependency-free public errors, available even when diagnostics find missing packages."""


class LakeError(Exception):
    """An error safe to expose at the public API boundary."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
