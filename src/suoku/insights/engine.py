"""Local evidence selection and cached observations for grounded provider answers."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from ..engine import _hash_file, _recorded_at
from ..types import LakeError
from .models import (
    PROMPT_VERSION,
    Citation,
    InsightAnswer,
    Observation,
    fingerprint,
    validate_payload,
)
from .recipes import resolve_recipe
from .store import InsightStore

ANSWER_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["answer", "citations", "limitations", "insufficient_evidence"],
    "properties": {
        "answer": {"type": "string", "minLength": 1, "maxLength": 16000},
        "insufficient_evidence": {"type": "boolean"},
        "limitations": {"type": "array", "maxItems": 30,
                        "items": {"type": "string", "maxLength": 2000}},
        "citations": {"type": "array", "maxItems": 20, "items": {
            "type": "object", "additionalProperties": False,
            "required": ["observation_id", "reason"], "properties": {
                "observation_id": {"type": "string", "maxLength": 64},
                "reason": {"type": "string", "maxLength": 2000}}}},
    },
}
# The answer schema is intentionally stricter than a free-form chat response: every
# citation must resolve to an observation supplied in the same reasoning request.
SAMPLED_LIMITATION = (
    "This answer uses selected, sampled video windows. It is not an exhaustive review; "
    "unseen events and precise durations cannot be established from these frames."
)


class InsightEngine:
    def __init__(self, lake, provider, *, extractor=None):
        if extractor is None:
            from .evidence import EvidenceExtractor
            extractor = EvidenceExtractor()
        self.lake = lake
        self.provider = provider
        self.extractor = extractor
        self.store = InsightStore(lake.path)

    @property
    def provider_fingerprint(self):
        value = getattr(self.provider, "fingerprint", None)
        if not isinstance(value, str) or not value or len(value) > 256:
            raise LakeError("invalid_provider", "Provider must have a stable fingerprint.")
        return value

    @staticmethod
    def _check_cancel(cancelled):
        if cancelled is not None and cancelled():
            raise LakeError("cancelled", "Insight job was cancelled.")

    def _source(self, asset_id):
        status = self.lake.status(asset_id)
        if status["state"] != "ready" or not status["source_hash"]:
            raise LakeError("not_ready", "Video must finish ingestion before analysis.")
        source = Path(status["source_path"])
        if _hash_file(source) != status["source_hash"]:
            raise LakeError("source_changed", "Video changed; ingest it again before asking questions.")
        return status, source

    def _window(self, asset_id, start, end, recipe, cancelled):
        self._check_cancel(cancelled)
        status, source = self._source(asset_id)
        profile = self.provider_fingerprint
        key = fingerprint({"asset": asset_id, "source": status["source_hash"],
                           "start": start, "end": end, "recipe": recipe.fingerprint,
                           "provider": profile, "prompt": PROMPT_VERSION,
                           "extractor": getattr(self.extractor, "fingerprint", "evidence-v1")})
        cached = self.store.get(key)
        if cached is not None:
            return cached
        frames = self.extractor.extract(source, start, end)
        timestamps = [frame.timestamp_ms for frame in frames]
        if not timestamps or any(type(t) is not int or not start <= t < end for t in timestamps):
            raise LakeError("invalid_evidence", "Decoder did not return frames inside the window.")
        schema = {"type": "object", "additionalProperties": False,
                  "required": ["summary", "payload"], "properties": {
                      "summary": {"type": "string", "minLength": 1, "maxLength": 4000},
                      "payload": recipe.schema}}
        self._check_cancel(cancelled)
        raw = self.provider.analyze_images(frames, recipe.prompt, schema)
        self._check_cancel(cancelled)
        validate_payload(raw, schema)
        if self.provider_fingerprint != profile:
            raise LakeError("provider_changed", "Provider changed during analysis; retry the job.")
        self._source(asset_id)  # Source can be modified during a remote call.
        observation = Observation(
            key, asset_id, str(status["source_hash"]), start, end, raw["summary"], raw["payload"],
            timestamps, recipe.id, recipe.fingerprint, profile, PROMPT_VERSION,
        )
        self.store.put(key, observation)
        return observation

    def analyze(self, asset_id, *, recipe="general", start_ms=0, end_ms=None, cancelled=None):
        self._check_cancel(cancelled)
        recipe = resolve_recipe(recipe)
        status, _ = self._source(asset_id)
        duration = int(status["duration_ms"])
        end = duration if end_ms is None else end_ms
        if (type(start_ms) is not int or type(end) is not int
                or not 0 <= start_ms < end <= duration):
            raise LakeError("invalid_range", "Use an increasing millisecond range within the video.")
        if (end - start_ms + 9999) // 10000 > 100:
            raise LakeError("analysis_limit", "Analyze at most 100 ten-second windows per request.")
        return [self._window(asset_id, start, min(start + 10000, end), recipe, cancelled)
                for start in range(start_ms, end, 10000)]

    def observations(self, asset_id, *, recipe=None):
        self.lake._ensure_open()
        from ..engine import _asset_id
        return InsightStore.read_visible(self.lake.path, _asset_id(asset_id), recipe=recipe,
                                         provider=self.provider_fingerprint)

    def ask(self, question, *, recipe="general", asset_ids=None, filters=None,
            candidate_limit=6, cancelled=None):
        self._check_cancel(cancelled)
        if not isinstance(question, str) or not question.strip() or len(question) > 4096:
            raise LakeError("invalid_query", "Question must contain 1 to 4096 characters.")
        if type(candidate_limit) is not int or not 1 <= candidate_limit <= 20:
            raise LakeError("invalid_limit", "Select between 1 and 20 candidate windows.")
        recipe = resolve_recipe(recipe)
        matches = self.lake.search(question, limit=candidate_limit, filters=filters,
                                   asset_ids=asset_ids)
        profile = self.provider_fingerprint
        observations = []
        seen = set()
        for match in matches:
            status = self.lake.status(match.asset_id)
            start = match.timestamp_ms // 10000 * 10000
            end = min(start + 10000, int(status["duration_ms"]))
            # A time filter bounds the frames sent externally, not just the search hit.
            if filters is not None and status["recorded_at"] is not None:
                _, recorded = _recorded_at(status["recorded_at"])
                _, after = _recorded_at(filters.recorded_after)
                _, before = _recorded_at(filters.recorded_before)
                if after is not None:
                    start = max(start, after - recorded)
                if before is not None:
                    end = min(end, before - recorded + 1)
            window = (match.asset_id, start, end)
            if end > start and window not in seen:
                seen.add(window)
                observations.append(self._window(*window, recipe, cancelled))
        if not observations:
            return InsightAnswer("Insufficient evidence in the selected videos and time range.", [],
                                 [SAMPLED_LIMITATION], profile, True)
        evidence = [asdict(observation) for observation in observations]
        lookup = {o.id: o for o in observations}
        for attempt in range(2):
            self._check_cancel(cancelled)
            prompt = question if attempt == 0 else (
                question + "\nReturn only citations to the supplied observation IDs. "
                "If these observations do not answer the question, set insufficient_evidence=true."
            )
            raw = self.provider.reason(prompt, evidence, ANSWER_SCHEMA)
            self._check_cancel(cancelled)
            validate_payload(raw, ANSWER_SCHEMA)
            valid = all(c["observation_id"] in lookup for c in raw["citations"])
            if valid and (raw["citations"] or raw["insufficient_evidence"]):
                break
        else:
            raise LakeError("invalid_evidence", "Provider returned citations outside supplied evidence.")
        if self.provider_fingerprint != profile:
            raise LakeError("provider_changed", "Provider changed during reasoning; retry the job.")
        for asset in {o.asset_id for o in observations}:
            self._source(asset)
        citations = []
        for cited in raw["citations"]:
            observation = lookup[cited["observation_id"]]
            citations.append(Citation(observation.id, observation.asset_id, observation.start_ms,
                                      observation.end_ms, cited["reason"]))
        return InsightAnswer(raw["answer"], citations, [*raw["limitations"], SAMPLED_LIMITATION],
                             profile, raw["insufficient_evidence"])
