# Architecture and data flow

Suoku is intentionally split into a lightweight API process and a worker process. The API
accepts authenticated requests and writes durable jobs. The worker owns the video decoder,
local embedding model, insight provider, and result cache.

```text
application backend
        │ Authorization: Bearer SUOKU_API_TOKEN
        ▼
┌─────────────────────┐       SQLite jobs       ┌────────────────────────┐
│ FastAPI API         │ ───────────────────────▶ │ Suoku worker            │
│ upload/status/jobs  │                          │ FFmpeg + SigLIP         │
└─────────┬───────────┘                          │ retrieval + insight     │
          │                                      └───────────┬────────────┘
          │                                                  │ selected frames/text
          ▼                                                  ▼
   managed media + catalog + LanceDB                 configured provider
```

## Upload and retrieval flow

1. The API streams an upload into `.lake/media` (or the Compose data volume), enforcing
   filename, extension, size, quota, and timeout limits.
2. It creates an `ingest` job and returns `202 Accepted`.
3. The worker probes the media, samples frames, computes embeddings, and writes catalog
   state and vector rows in committed batches.
4. The job becomes `succeeded` with an `asset_id`. Only an asset in the `ready` state is
   searchable or available for playback.
5. `search` applies camera/time filters and active-generation filtering before ranking.
   Matches contain a source timestamp and a bounded playback interval.

## Insight flow

`ask` performs two distinct operations:

1. Local retrieval chooses up to the requested candidate limit (1–20, default 6).
2. Each unique ten-second window is decoded into up to three actual, full-frame timestamps.
3. The configured provider turns those frames into a schema-validated observation. The
   observation is cached using source, window, recipe, provider, prompt, and extractor
   fingerprints.
4. The provider receives the question and serialized observations, not the entire video.
5. Suoku validates citation IDs against the observations supplied to that call and maps
   valid citations to source asset/time ranges.

The default analysis limit is 100 ten-second windows per request. Evidence is sampled, so
the system cannot establish unseen events, exact durations, intent, identity, or exhaustive
absence. The answer always includes limitations for sampled evidence.

## Storage layout

Native CLI use stores:

```text
.lake/
├── media/                 # managed uploaded originals
├── index/                 # LanceDB vectors and catalog.sqlite3
│   └── insights.sqlite3   # validated observation cache
└── jobs.sqlite3           # durable service queue and worker heartbeat
```

Direct Python ingestion may reference an external source file; removal never deletes that
external original. The service's explicit purge operation can delete managed upload bytes
when `delete_media=true`. Backups, provider retention, SQLite free pages, and exported
copies require separate retention policies.

## Process and trust boundaries

- The API does not load the embedding model or provider SDK.
- The worker reads all media in its configured collection and executes trusted adapter code.
- Provider keys are configured on the worker, never accepted in a request body.
- The default worker container has no network. `compose.insights.yaml` explicitly enables
  provider egress.
- The TypeScript client is designed for server-side use. Do not ship the deployment token
  in a browser bundle.

See [SECURITY.md](../SECURITY.md) before exposing the API beyond loopback.
