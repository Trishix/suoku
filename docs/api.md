# HTTP API reference

All routes except `GET /healthz` require:

```http
Authorization: Bearer SUOKU_API_TOKEN
```

The API base URL is `http://127.0.0.1:8000` by default. Responses for work-producing
requests use `202 Accepted` and return a durable job. Poll `GET /v1/jobs/{job_id}` until a
terminal state.

## Health and status

`GET /healthz` is unauthenticated and only indicates that the API process can answer.

`GET /v1/status` returns:

```json
{
  "worker_available": true,
  "last_heartbeat": 1730000000.0,
  "insight_model": "openai/your-vision-model",
  "insights_ready": true
}
```

`insights_ready` means the worker has an insight provider configured and a recent heartbeat;
it does not validate provider credentials or semantic quality.

## Upload

```http
POST /v1/videos?filename=clip.mp4&camera_id=gate-1&recorded_at=2026-01-01T12:00:00Z
Content-Type: application/octet-stream
```

Send the raw video bytes. Supported extensions are `.mp4`, `.mov`, and `.webm`. The response
is an `ingest` job. The successful result is `{ "asset_id": "..." }`.

## Search

```http
POST /v1/search
Content-Type: application/json

{"query":"a person near the gate","limit":5,"filters":{"camera_id":"gate-1"}}
```

The successful result is `{ "matches": [...] }`. A match includes `asset_id`,
`timestamp_ms`, `start_ms`, `end_ms`, `score`, `source_hash`, `model_fingerprint`, and
`camera_id`.

## Analysis

```http
POST /v1/analyses
Content-Type: application/json

{
  "asset_id":"ASSET_ID",
  "recipe":"general",
  "start_ms":0,
  "end_ms":15000
}
```

The result contains `observations`. Each observation stores its source hash, ten-second
window, summary, structured payload, actual evidence timestamps, recipe fingerprint,
provider fingerprint, and prompt version.

## Questions and answers

```http
POST /v1/answers
Content-Type: application/json

{
  "question":"What happened near the gate?",
  "recipe":"general",
  "asset_ids":["ASSET_ID"],
  "candidate_limit":6,
  "filters":{"camera_id":"gate-1"}
}
```

The result contains an `answer` object:

```json
{
  "answer":"...",
  "citations":[
    {"observation_id":"...","asset_id":"...","start_ms":0,"end_ms":10000,"reason":"..."}
  ],
  "limitations":["..."],
  "provider_fingerprint":"litellm-v2:...",
  "insufficient_evidence":false
}
```

An insufficient-evidence answer may legitimately have no citations. Treat that result as
uncertain and do not turn it into an automated decision.

## Jobs

`GET /v1/jobs/{job_id}` returns `id`, `kind`, `state`, `progress`, `result`, `error`,
`created_at`, and `updated_at`. `POST /v1/jobs/{job_id}/cancel` requests cooperative
cancellation. A provider call already in progress may finish before cancellation is seen.

Jobs are retained for a limited period after completion. Store application-level references
if you need durable audit history, while considering the privacy implications.

## Media and observations

- `GET /v1/videos/{asset_id}/observations?recipe=general` returns visible cached observations.
- `GET /v1/videos/{asset_id}/content` returns authenticated source media and supports HTTP
  range requests through the framework response.
- `DELETE /v1/videos/{asset_id}` hides the asset and invalidates related observations.
- `DELETE /v1/videos/{asset_id}/purge?delete_media=true` also deletes managed uploaded bytes.

Direct Python source files are never deleted by the core purge operation. The service only
deletes its own managed upload path.

## Errors

Errors use a safe shape such as:

```json
{"code":"not_ready","message":"Video must finish ingestion before analysis."}
```

Common codes include `unauthorized`, `not_found`, `not_ready`, `queue_full`,
`invalid_query`, `invalid_schema`, `decode_failed`, `provider_error`, `authentication`,
`invalid_response`, `source_changed`, and `cancelled`. Do not parse human messages as a
stable contract; use `code` for application behavior.

The generated OpenAPI document is [schema/openapi.json](../schema/openapi.json).
