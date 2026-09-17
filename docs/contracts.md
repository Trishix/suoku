# Internal integration contracts

Python package: suoku. All times milliseconds, source timestamps relative to the first video presentation timestamp. Errors: LakeError(code, message), exposed code and safe message. Custom adapters execute trusted application code.

## Shared types (engine implementer owns types.py)
Frozen dataclasses:
- MediaInfo(duration_ms: int, width: int, height: int, codec: str)
- Frame(timestamp_ms: int, image: PIL.Image.Image)
- Match(asset_id: str, timestamp_ms: int, start_ms: int, end_ms: int, score: float, source_hash: str, model_fingerprint: str, camera_id: str | None)
- IngestProgress(asset_id: str, processed: int, complete: bool)
- SearchFilters(camera_id: str | None = None, recorded_after: str | None = None, recorded_before: str | None = None)

Embedder protocol: fingerprint: str, dimensions: int, embed_frames(frames: list[Frame]) -> numpy.ndarray [N,D], embed_query(text: str) -> numpy.ndarray [D]. Core normalizes and uses cosine distance. Later clip adapters may use separate protocol; v1 implements visual baseline, with adapter contract explicitly frame-only.
MediaReader protocol: probe(path: Path) -> MediaInfo; frames(path: Path, *, interval_ms: int, start_ms: int = 0) -> Iterator[Frame]. Controller implements FFmpegReader. Duck typing allows deterministic test fixtures.

## VideoLake (engine implementer owns engine.py and catalog.py)
VideoLake.open(path: str | Path, embedder: Embedder, *, reader: MediaReader | None = None, interval_ms: int = 2000, batch_size: int = 8) -> VideoLake. If reader None, lazy import FFmpegReader from media.py. Acquire nonblocking filelock for lifetime, fail LakeError('lake_busy',...) on second owner. Context manager/close releases it.
ingest(source: str | Path, *, asset_id: str | None = None, camera_id: str | None = None, recorded_at: str | None = None) -> str, consumes ingest_steps.
ingest_steps(same args) -> Iterator[IngestProgress], yields after committed batches and after complete; resume same asset/hash/profile, preserve original, new ID defaults uuid4().hex. If asset_id not supplied, repeated exact canonical path+camera+recorded_at must reuse registration. IDs accepted only canonical 32 lowercase hex. Re-ingest same asset with changed content activates a new generation only at completion. Model/profile mismatch on reopen fails; an explicit new collection is needed.
search(text: str, *, limit: int = 10, filters: SearchFilters | None = None) -> list[Match]. Limits 1..100; nonempty text <=4096 chars; exact cosine retrieval, filter active generation IDs BEFORE ranking; remove duplicate nearby hits per asset, refill candidate pool until limit or exhaustion. Playback +/-5000ms bounded by duration. Recorded filters compare UTC instants, exclude unknown recording times, and evaluate matched timestamps (recorded_at + timestamp), not only video's start.
status(asset_id: str) -> dict with id, state, processed, source_hash, duration_ms, source_path, camera_id, recorded_at.
remove(asset_id: str) -> None hides generations, preserves originals; missing ID LakeError('not_found',...). purge(asset_id: str, *, delete_source: bool = False) removes vectors/cached catalog references; delete_source must NEVER delete external referenced files (core should reject true; server separately manages its uploads). Document versioned history retained until maintenance.
close(); context manager.

Core catalog is lake/catalog.sqlite3. Service jobs use a SEPARATE lake/jobs.sqlite3 managed by controller to avoid schema collision. Engine status doesn't expose source_path to HTTP. Service manages upload registry independently in jobs database. Worker owns engine exclusively. All calls to engine by worker occur on one thread; queue dispatch can run search between ingest iterator next calls. API never opens VideoLake.

Service API:
- POST /v1/videos: raw streaming body, query filename plus optional camera_id and recorded_at; returns 202 Job.
- POST /v1/search: JSON {query,limit=10,filters?}; 202 Job.
- GET /v1/jobs/{id}: Job, result for ingest {asset_id}, search {matches:[Match]}.
- POST /v1/jobs/{id}/cancel: Job.
- DELETE /v1/videos/{id}: 202 removal Job.
- DELETE /v1/videos/{id}/purge?delete_media=false: 202 purge Job, explicit opt in for managed bytes.
- GET /v1/videos/{id}/content: authenticated range-supporting content.
Job {id,kind,state,progress,result,error,created_at,updated_at}; kind ingest/search/remove/purge; state queued/running/succeeded/failed/cancelled; error {code,message} or null. IDs uuid4 hex. Server factory create_app(settings: Settings) -> FastAPI. Settings data_dir: Path, token: str (>=32 chars), max_upload_bytes: int = 2147483648, max_queued_jobs: int = 1000. API key Bearer token. Worker accepts injected embedder/reader for real integration tests; deterministic fixtures are never marketed as semantic models.
