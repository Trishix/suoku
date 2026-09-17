# Task 1 report: core engine

Implemented the persistent `VideoLake` core in:

- `src/semantic_video_lake/types.py`
- `src/semantic_video_lake/catalog.py`
- `src/semantic_video_lake/engine.py`
- `tests/test_engine.py`

## Design and behavior

- SQLite owns asset registration, immutable generation metadata, active-generation
  pointers, progress checkpoints, removal tombstones, and the embedding profile.
- LanceDB owns normalized frame vectors and trace metadata. Frame IDs combine the
  generation ID and source-relative timestamp, so `merge_insert` is idempotent.
- A batch becomes resumable only after the LanceDB merge succeeds and SQLite records
  its checkpoint. Replaying a batch after a crash overwrites the same vector IDs.
- Replacements remain inactive until all frames are committed and the source hash is
  rechecked. Activation atomically changes the catalog pointer; a failed replacement
  therefore leaves the prior generation visible.
- The file is hashed in bounded chunks before decoding and again before activation.
  Frames and embeddings are held only one configured batch at a time.
- Removal clears the active pointer and writes a tombstone. Paused ingest iterators
  check that tombstone before every subsequent commit and activation, so they cannot
  resurrect an asset.
- Search uses finite, nonzero, dimension-checked L2-normalized vectors and exact cosine
  retrieval. Active generation and matched-recording-time predicates are applied as
  LanceDB prefilters. Nearby results whose +/-5 second playback regions overlap are
  deduplicated, with an expanding candidate window used to refill the requested limit.
- UTC-aware ISO-8601 recording instants are normalized to UTC. Unknown recording times
  are excluded when time filters are present.
- Core purge deletes catalog/vector references but refuses `delete_source=True`; source
  media is never deleted by the engine.
- A nonblocking lifetime `FileLock` enforces one open writer. Lake profile metadata
  rejects model fingerprint, dimensions, sampling interval, and format mismatches.

## Verification

Command:

```text
.venv/bin/python -m pytest tests/test_engine.py
```

Result: **11 passed**.

Also verified the four owned files with Ruff check and format check.

The tests use real LanceDB plus deterministic injected reader/embedder fixtures. They
cover ingestion and search, default registration reuse, crash-style iterator resume,
idempotent vector writes, failed replacement visibility, source mutation, removal of a
paused ingest, timestamp/camera filters, unknown recording times, remove/purge source
preservation, query and frame vector validation, profile mismatch, validation errors,
and the lifetime single-writer lock.

## Cross-component note

At handoff, the full repository test run had four server-only failures: FastAPI treated
the auth dependency's `credentials` parameter as a required query parameter and returned
422. The engine tests and other repository tests passed. The server worker also needs to
call `VideoLake.remove()` if cancellation is intended to tombstone a partially registered
asset; merely closing the ingest iterator intentionally leaves it resumable.

## Core review follow-up

The core review findings were addressed in a follow-up pass:

- The model fingerprint and dimensions are pinned when the lake opens. The engine checks
  both immediately before and in a `finally` block after every frame/query embedding
  call, uses only the pinned values for schema validation and stored trace metadata, and
  raises `incompatible_profile` if a mutable adapter drifts.
- UTC milliseconds are calculated using integer `timedelta` fields instead of the
  platform-dependent floating-point `datetime.timestamp()`. This floors pre-epoch
  submillisecond instants consistently with the normalized millisecond string, and
  timezone normalization overflow is reported as `invalid_time`.
- Search deduplication now compares the actual duration-bounded +/-5 second playback
  intervals. Candidates are explicitly processed in cosine-distance rank order, retaining
  the best-scored match from overlapping regions while candidate expansion refills from
  later non-overlapping results.
- A fault-injection test now crashes after the LanceDB merge but before the SQLite
  checkpoint, closes and reopens the lake, and verifies the uncheckpointed batch alone is
  re-inferred, vector IDs remain unique, and the final progress count is correct.

Scoped verification after the follow-up: **15 passed** using
`.venv/bin/python -m pytest tests/test_engine.py`; Ruff check and format check also pass.
