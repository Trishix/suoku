"""Portable insight contracts; no provider SDK is imported here."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Protocol

from PIL import Image

from ..types import LakeError

PROMPT_VERSION = "suoku-observations-v1"

# Fingerprints are part of cache identity: changing prompts, schemas, or providers
# must never silently reuse observations produced under different semantics.


def canonical(value: object) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (ValueError, TypeError, RecursionError) as exc:
        raise LakeError("invalid_json", "Supply finite, bounded JSON values.") from exc


def fingerprint(value: object) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def safe_schema(schema: dict) -> dict:
    """Resolve a bounded JSON Schema subset without I/O or recursive references."""
    from jsonschema import Draft202012Validator
    from jsonschema.exceptions import SchemaError

    if not isinstance(schema, dict) or len(canonical(schema)) > 32768:
        raise LakeError("invalid_schema", "Schema must be a JSON object of at most 32 KiB.")
    allowed = {"type", "properties", "required", "additionalProperties", "items", "enum",
               "const", "description", "title", "$defs", "$ref", "$schema", "anyOf",
               "default", "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum",
               "minLength", "maxLength", "minItems", "maxItems"}
    nodes = 0

    def expand(node, depth=0, stack=()):
        nonlocal nodes
        nodes += 1
        if nodes > 2048 or depth > 16 or not isinstance(node, (dict, bool)):
            raise LakeError("invalid_schema", "Schema is too complex or malformed.")
        if isinstance(node, bool):
            return node
        if set(node) - allowed:
            raise LakeError("invalid_schema", "Unsupported schema keyword; see docs/recipes.md.")
        if "$schema" in node and node["$schema"] != "https://json-schema.org/draft/2020-12/schema":
            raise LakeError("invalid_schema", "Only JSON Schema draft 2020-12 is supported.")
        result = {}
        if "$ref" in node:
            ref = node["$ref"]
            if not isinstance(ref, str) or not re.fullmatch(r"#\/\$defs\/[A-Za-z0-9_.-]+", ref):
                raise LakeError("invalid_schema", "Only acyclic local $defs references are allowed.")
            if ref in stack or ref.rsplit("/", 1)[1] not in schema.get("$defs", {}):
                raise LakeError("invalid_schema", "Schema reference is missing or recursive.")
            result.update(expand(schema["$defs"][ref.rsplit("/", 1)[1]], depth + 1, (*stack, ref)))
        for key, value in node.items():
            if key in {"$ref", "$schema"}:
                continue
            if key in {"properties", "$defs"}:
                if not isinstance(value, dict):
                    raise LakeError("invalid_schema", "Schema properties and $defs must be objects.")
                mapped = {k: expand(v, depth + 1, stack) for k, v in value.items()}
                if key == "properties":
                    result[key] = mapped
            elif key in {"items", "additionalProperties"}:
                result[key] = expand(value, depth + 1, stack)
            elif key == "anyOf":
                if not isinstance(value, list) or not 1 <= len(value) <= 8:
                    raise LakeError("invalid_schema", "anyOf must contain 1 to 8 alternatives.")
                result[key] = [expand(v, depth + 1, stack) for v in value]
            else:
                result[key] = value
        return result

    try:
        normalized = expand(schema)
        Draft202012Validator.check_schema(normalized)
    except (SchemaError, TypeError, ValueError, RecursionError) as exc:
        raise LakeError("invalid_schema", "Schema is malformed or exceeds supported limits.") from exc
    if normalized.get("type") != "object":
        raise LakeError("invalid_schema", "The recipe payload must be an object.")
    return json.loads(canonical(normalized))


def validate_payload(value: object, schema: dict) -> None:
    from jsonschema import Draft202012Validator
    from jsonschema.exceptions import ValidationError
    from referencing import Registry

    if len(canonical(value)) > 65536:
        raise LakeError("invalid_output", "Provider output exceeds 64 KiB.")
    try:
        Draft202012Validator(schema, registry=Registry()).validate(value)
    except (ValidationError, RecursionError) as exc:
        raise LakeError("invalid_output", "Provider output did not match the requested schema.") from exc


@dataclass(frozen=True)
class InsightRecipe:
    id: str
    version: str
    name: str
    description: str
    prompt: str
    schema: dict

    def __post_init__(self):
        if not isinstance(self.id, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", self.id):
            raise LakeError("invalid_recipe", "Recipe ID must use lowercase letters, digits, _ or -.")
        for value, bound in [(self.version, 64), (self.name, 128),
                             (self.description, 2048), (self.prompt, 8192)]:
            if not isinstance(value, str) or len(value) > bound:
                raise LakeError("invalid_recipe", "Recipe text exceeds its supported length.")
        if not self.version.strip() or not self.prompt.strip() or not self.name.strip():
            raise LakeError("invalid_recipe", "Recipe version, name and prompt must be nonempty.")
        object.__setattr__(self, "schema", safe_schema(self.schema))

    @property
    def fingerprint(self) -> str:
        from dataclasses import asdict
        return fingerprint(asdict(self))

    @classmethod
    def from_pydantic(cls, model, *, id, prompt, version="1", name=None, description=""):
        return cls(id, version, name or id, description, prompt, model.model_json_schema())


@dataclass(frozen=True)
class EvidenceFrame:
    timestamp_ms: int
    image: Image.Image


class InsightProvider(Protocol):
    fingerprint: str

    def analyze_images(self, images: list[EvidenceFrame], prompt: str, schema: dict) -> dict: ...

    def reason(self, question: str, evidence: list[dict], schema: dict) -> dict: ...


@dataclass(frozen=True)
class Observation:
    id: str
    asset_id: str
    source_hash: str
    start_ms: int
    end_ms: int
    summary: str
    payload: dict
    evidence_timestamps: list[int]
    recipe_id: str
    recipe_fingerprint: str
    provider_fingerprint: str
    prompt_version: str


@dataclass(frozen=True)
class Citation:
    observation_id: str
    asset_id: str
    start_ms: int
    end_ms: int
    reason: str


@dataclass(frozen=True)
class InsightAnswer:
    answer: str
    citations: list[Citation]
    limitations: list[str]
    provider_fingerprint: str
    insufficient_evidence: bool = False
