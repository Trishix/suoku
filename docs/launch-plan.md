# Semantic layer alpha: implementation contract

Approved goal: archived video → local retrieval → selected evidence frames → schema-validated
observations → answers with source citations. Ship Python, CLI, HTTP and TypeScript together.

## Tasks

1. Insight recipes, typed observations/answers, SQLite cache, engine, lifecycle integration.
2. Optional LiteLLM provider and bounded, aspect-preserving evidence extraction.
3. Secure CLI setup, diagnostics, upload/analyze/ask/observations commands.
4. Durable worker jobs, heartbeat, authenticated API, generated TypeScript client.
5. Sample, runnable examples, evaluation guidance, packaging, CI, release documentation.
6. Integration tests, package-install checks and independent review.

## Interfaces shared across implementation tasks

- `suoku.insights.models.EvidenceFrame(timestamp_ms: int, image: PIL.Image.Image)`.
- `InsightProvider.fingerprint: str`; `analyze_images(images, prompt, schema) -> dict`;
  `reason(question, evidence: list[dict], schema) -> dict`. No keys in fingerprints or repr.
- `suoku.providers.LiteLLMProvider(model: str, api_key: str)` implements that protocol.
- `suoku.insights.evidence.EvidenceExtractor.extract(path, start_ms, end_ms) -> list[EvidenceFrame]`:
  three actual timestamped frames, no crop, longest side <=768, bounded FFmpeg subprocesses.
- `InsightRecipe(id, version, name, description, prompt, schema)`; JSON serializable;
  built-ins general, safety, warehouse. Safe bounded local JSON Schema, no remote references.
- `InsightEngine(lake, provider, *, extractor=None)`; `ask(question, *, recipe='general',
  asset_ids=None, filters=None, candidate_limit=6, cancelled=None) -> InsightAnswer`;
  `analyze(asset_id, *, recipe='general', start_ms=0, end_ms=None, cancelled=None) -> list[Observation]`;
  `observations(asset_id, *, recipe=None) -> list[Observation]`. Dataclasses serialize with asdict.
- Observations: id, asset_id, source_hash, start_ms, end_ms, summary, payload,
  evidence_timestamps, recipe_id, recipe_fingerprint, provider_fingerprint, prompt_version.
- Answers: answer, citations, limitations, provider_fingerprint, insufficient_evidence.
  Citations: observation_id, asset_id, start_ms, end_ms, reason.
- HTTP POST /v1/answers: question, recipe (ID or inline object), asset_ids?, filters?,
  candidate_limit=6. POST /v1/analyses: asset_id, recipe, start_ms=0, end_ms?. Both return Job.
  Job.result.answer is InsightAnswer; Job.result.observations is list[Observation].
  GET /v1/videos/{id}/observations?recipe=ID -> list[Observation]. GET /v1/insight-recipes -> list[recipe].
  GET /v1/status -> worker_available, last_heartbeat, insight_model, insights_ready.
- Worker(data_dir, embedder, *, reader=None, provider=None, extractor=None).
- CLI loads .suoku/config.env explicitly, process env wins; key SUOKU_PROVIDER_API_KEY,
  model SUOKU_INSIGHT_MODEL, service token SUOKU_API_TOKEN, base SUOKU_BASE_URL default loopback.

## Decisions and constraints

- One model and operator-provided key per deployment; cloud calls require configured provider.
- Existing offline worker container stays available; an explicit insights override enables egress.
- Cache stores under the lake index; keys include asset/source, window, extraction, recipe,
  provider and prompt fingerprints. Current source hash and active asset checked before use.
- Retrieval uses actual asset filters before ranking; partial sampled evidence is stated in answers.
- Arbitrary JSON Schemas cannot safely execute regex/remote refs/recursive logic in the API;
  support a documented bounded subset plus acyclic local $defs for Pydantic export.
- No publishing, hosted service, live streams or claims of validated semantic accuracy.

## Validation

Test cache invalidation, source replacement/removal/purge, malicious/invalid schemas and citations,
no-evidence answers, cancellation/restart, provider transient failures/redaction, secure config,
aspect ratio and real timestamps, HTTP and TypeScript complete flows. Build and install local
wheel/npm artifacts. Live provider tests remain opt-in and do not run without supplied keys.
