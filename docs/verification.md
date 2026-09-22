# Alpha verification — 2026-09-22

This records local checks for the `0.1.0a1` / `0.1.0-alpha.1` candidate. It is not a
semantic-accuracy benchmark, security certification, or published-release announcement.

Host: macOS arm64, Python 3.13.2, Node.js 26.5.0, locally installed FFmpeg/ffprobe.
The optional provider adapter was tested with LiteLLM 1.102.0. CI also defines Python
3.11/3.12 and Node.js 22 jobs on Linux/macOS; those remote jobs were not run here.

## Checks performed

| Check | Observed result |
| --- | --- |
| Full Python suite with prepared SigLIP weights | 128 passed; 5 live-provider tests skipped |
| Full Python suite against the installed wheel from outside the checkout, without `PYTHONPATH` | 127 passed; 1 local-model and 5 live-provider tests skipped |
| TypeScript client and CLI against live loopback HTTP service | 2 integration tests passed |
| Ruff, whitespace checks | Passed |
| OpenAPI and TypeScript type regeneration | No drift |
| Python wheel/source archive and npm tarball builds | Passed |
| Installed Python package | Imports resolved to `site-packages`; CLI help, protected initialization, and doctor passed |
| Installed npm tarball | Client and error exports, including insight methods, loaded successfully |
| Bundled sample checksum | Passed |

Python tests cover schema validation, cache invalidation, changed/missing sources, removal
and purge, uncited answer dependencies, cancellation, worker heartbeat, secret-safe errors,
protected config, real timestamped FFmpeg frames, and authenticated endpoints. The actual
LiteLLM/OpenAI SDK serialization path is tested using an offline HTTP mock, including
nested Pydantic models and optional nullable fields. No real credentials are used there.

The end-to-end service test uses real HTTP, SQLite, LanceDB, media upload/playback, and
FFmpeg evidence extraction, with deterministic fake embeddings and insight responses.
Separately, prepared pinned SigLIP weights loaded and indexed/searched the licensed sample.
The Python and Node examples were exercised with real media and fake insight providers.
These checks demonstrate pipeline operation, not correctness of model-generated insights.

Independent review identified three issues that were fixed and regression-tested: stale
observation reads after source changes, strict provider schema compatibility, and retained
answers after removal of uncited evidence. A follow-up review found no remaining important
blockers in those fixes. Dependency deprecation warnings remain in the test output.

Artifact inspection checked for private configuration, model weights, local indexes,
virtual environments, Git internals, and dependency directories. None were included in
the candidate artifacts. The source archive includes the licensed sample and attribution;
the wheel contains the library and package metadata, not that demo media.

## Not verified or performed

- Live OpenAI, Anthropic, Gemini, Groq, or OpenRouter requests: five opt-in tests skipped.
  Validate an available image/structured-output model with an authorized key before
  advertising deployment-specific compatibility or costs.
- Docker/Compose runtime: no Docker executable was available on this host. CI includes
  configuration validation, but Linux image builds, startup, volume ownership, resource
  limits, and worker network isolation/egress still need a target-host smoke test.
- Long-recording throughput, event recall, answer accuracy, or real safety/warehouse
  performance. See the [evaluation guide](evaluation.md).
- A dependency vulnerability audit or independent penetration test.
- Registry ownership, package publication, release tags, GitHub visibility changes, or
  enabling/verifying private vulnerability reporting. Nothing was pushed or published.

Follow the [release checklist](releasing.md) for these remaining external checks. Built
artifacts are local validation outputs; they do not establish that registry packages exist.
