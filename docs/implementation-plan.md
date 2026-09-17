# Semantic video lake implementation plan

The approved conversation plan is the specification. Build a Python library independent of FastAPI, optional local SigLIP model adapter, separate FastAPI API/worker with durable SQLite jobs, and lightweight npm client. Single host, local filesystem, single writer. No live cameras, compression, audio, identity recognition, or actions inferred from image similarity. No publication or remote deployment.

## Global constraints
- Preserve source media; timestamp results are evidence references, similarity is not confidence.
- Default sample interval 2000ms, batch size 8, playback padding 5000ms; model adapters are replaceable.
- Validate finite nonzero vectors and dimensions; fingerprint includes model revision, preprocessing, metric, sampling.
- Staging generations are hidden; atomic SQLite activation follows committed vectors; interrupted batches resume idempotently.
- HTTP accepts bytes and opaque IDs, never arbitrary server paths, URLs, SQL, model identifiers, or decoder options.
- Authenticate all HTTP routes except minimal liveness. Streaming upload cap 2 GiB; media max 4h and 4K.
- Worker offline, one resident model; prioritized search between ingestion batches with fairness.
- Search job data expires after 1h. Removal hides results and preserves originals. Purge is separate and explicit, with database-history caveat.
- No weights download on install, import, or server start. Explicit setup pins commit and records hashes.
- Test actual LanceDB and FFmpeg, fault recovery, auth, and installed npm client against HTTP service.

## Task 1: Core engine and persistence
Implement models/errors, SQLite catalog, LanceDB vector store, VideoLake orchestration, durability, and engine tests. Interface is specified in docs/contracts.md. Owner: engine implementer. Paths: src/semantic_video_lake/{types,catalog,engine}.py and tests/test_engine.py. Source decoder and adapters are owned by controller.

## Task 2: Media and local models
Implement secure bounded ffprobe/ffmpeg decoding, timestamp sampling, optional SigLIP adapter, explicit verified model setup, and diagnostics CLI. Tests use generated media; model inference gets an opt-in smoke test.

## Task 3: Service and SDK
Implement persistent jobs and cooperative worker, authenticated streamed uploads and media playback, cancellation/removal, API schema, generated TypeScript types and convenience client, real HTTP integration tests.

## Task 4: Distribution and release checks
Docker API/offline worker profile, docs, license, CI, fixtures and 50-query benchmark harness, Python and npm builds, review, full verification. Record actual measurements and unverified platforms honestly.

