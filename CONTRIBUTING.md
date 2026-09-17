# Contributing

Use Python 3.11+ and Node 22+. Install FFmpeg/ffprobe, then:

```sh
python -m venv .venv
.venv/bin/pip install -e '.[server,dev]'
.venv/bin/python -m pytest
.venv/bin/python -m ruff check src tests scripts
.venv/bin/python scripts/export_openapi.py
cd packages/client
npm ci
npm run generate
npm test
```

Tests use deterministic color embeddings for pipeline behavior, not semantic claims. `SUOKU_TEST_MODEL=/absolute/model/path` enables the real SigLIP test after explicit setup. Keep real customer footage and models out of Git.

Change public API models first, regenerate OpenAPI and TypeScript types, then update the transport/client tests. Preserve model fingerprints and generation activation semantics; include a fault-recovery regression for persistence changes.

Release preparation: verify dependency and model licenses, run the Linux isolation tests and the real-model benchmark on documented hardware, build wheel/sdist and npm tarball, scan dependencies, and configure trusted publishing and provenance. Do not store publishing tokens in this repository. No automatic publishing workflow is enabled yet because the package/repository identities and registries have not been registered.
