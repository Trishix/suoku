# Suoku

Suoku is an open-source, local-first semantic video search library. It turns archived video into timestamped, searchable visual observations while keeping original media under your control.

Ask questions such as “a red vehicle near the gate” and receive matching moments with a source video, timestamp, playback window, similarity score, camera metadata, and model fingerprint.

## Features

- Python library for applications and notebooks
- Optional FastAPI service for other languages
- Typed `suoku` npm client for Node.js and TypeScript
- LanceDB vector storage and SQLite metadata/job state
- FFmpeg timestamped frame extraction
- Replaceable embedding adapters
- Optional local SigLIP image-text retrieval adapter
- Resumable, content-hash-based ingestion

Suoku v0.1 performs visual similarity search over archived video. It does not claim that a similarity score proves an event happened. Reliable action, identity, intent, or multi-minute behavior questions require temporal models and domain-specific evaluation. Live cameras, audio search, face recognition, semantic compression, and autonomous alerts are outside this release.

## Quick start: Python

Requirements: Python 3.11+ and FFmpeg/ffprobe. The included decoder supports MP4/MOV with H.264 and WebM with VP8/VP9.

```bash
python -m venv .venv
source .venv/bin/activate
pip install suoku
```

Provide an object implementing `fingerprint`, `dimensions`, `embed_frames(frames)`, and `embed_query(text)`. The repository includes an optional local adapter:

```bash
pip install 'suoku[local]'
suoku prepare-model .models/siglip \
  --revision 7fd15f0689c79d79e38b1c2e2e2370a7bf2761ed
```

```python
from pathlib import Path
from suoku import VideoLake
from suoku.adapters.siglip import SiglipEmbedder

model = SiglipEmbedder(".models/siglip")
with VideoLake.open(".lake/index", model) as lake:
    asset_id = lake.ingest(Path("./footage/gate.mp4"), camera_id="gate-1")
    for match in lake.search("a red vehicle near the gate", limit=5):
        print(match.asset_id, match.start_ms, match.end_ms, match.score)
```

The default sampling interval is two seconds. Results include a five-second playback padding window bounded by source duration. Ingestion resumes safely after interruption and unchanged files are not embedded again.

## FastAPI service

```bash
pip install 'suoku[server]'
export SUOKU_API_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
suoku serve --data .lake
```

Run the worker separately; it owns the model and engine:

```bash
suoku worker --data .lake --model .models/siglip --device cpu
```

The service binds to `127.0.0.1` by default. Every route except `/healthz` requires a bearer token. Jobs are durable and return `202 Accepted` while processing.

| Endpoint | Purpose |
| --- | --- |
| `POST /v1/videos?filename=...` | Stream a video upload into an ingestion job |
| `POST /v1/search` | Queue text search with camera/time filters |
| `GET /v1/jobs/{id}` | Read progress, result, or error |
| `POST /v1/jobs/{id}/cancel` | Cancel a job |
| `GET /v1/videos/{id}/content` | Authenticated range-capable playback |
| `DELETE /v1/videos/{id}` | Hide an asset from search |
| `DELETE /v1/videos/{id}/purge` | Explicitly purge managed media and derived records |

## TypeScript / npm client

The npm package is a typed client for the service; it does not embed Python or execute models inside Node.js.

```bash
npm install suoku
```

```ts
import { VideoLakeClient } from "suoku";

const lake = new VideoLakeClient({
  baseUrl: "http://127.0.0.1:8000",
  token: process.env.SUOKU_API_TOKEN!,
});

const matches = await lake.search("a red vehicle near the gate", {
  limit: 5,
  filters: { camera_id: "gate-1" },
});
```

Keep the service token server-side; never place it in browser bundles.

## Docker deployment

```bash
export SUOKU_API_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
mkdir -p .lake .models
# Place a prepared model at .models/siglip.
docker compose up --build
```

The worker has no network, runs as non-root, uses a read-only root filesystem, drops Linux capabilities, and has resource limits. Review [SECURITY.md](SECURITY.md) before exposing it to a network or processing hostile uploads.

## Data layout

```text
.lake/
├── index/       # LanceDB vectors and catalog.sqlite3
├── jobs.sqlite3 # service job state
└── media/       # service-managed uploads only
```

The Python library references external source paths and never deletes them. Service purge deletes only media uploaded into its managed directory. Database history and backups may retain derived data until maintenance.

## Benchmarking

Use `scripts/make_benchmark.py` and `scripts/benchmark.py` to create a synthetic 50-query corpus and record Recall@1, Recall@5, indexing time, search latency, model load time, and peak RSS. The synthetic benchmark tests color retrieval only and is not evidence of CCTV, action, identity, or safety-event accuracy.

## Security

Direct Python use is not a sandbox. For untrusted uploads, use the supplied container profile and keep FFmpeg, PyTorch, LanceDB, and the host patched. Uploads are bounded by size, duration, dimensions, quotas, and decode deadlines. The service rejects arbitrary server paths, URLs, SQL, model identifiers, and decoder flags.

See [SECURITY.md](SECURITY.md) for authentication, isolation, model integrity, retention, deletion, and vulnerability-reporting guidance.

## Development

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[server,dev]'
python -m pytest -q
python -m ruff check src tests scripts
python scripts/export_openapi.py
npm ci --prefix packages/client
npm test --prefix packages/client
```

Tests use generated videos and deterministic embeddings for pipeline behavior. Set `SUOKU_TEST_MODEL=/absolute/path/to/prepared/model` to enable the real local SigLIP smoke test.

## License and status

Suoku source code is licensed under Apache-2.0. It is an alpha release intended for local experimentation and evaluation. Benchmark your own footage, configure retention and backups, and validate the container boundary before production use.

