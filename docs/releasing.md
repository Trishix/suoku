# Building and releasing the alpha

Repository: [Trishix/suoku](https://github.com/Trishix/suoku). Current version spellings:
Python `0.1.0a1`, npm `0.1.0-alpha.1`. These are local artifact versions, not evidence that
the names are registered or published. Documentation uses source installation until a
maintainer verifies registry ownership and installs the intended published artifacts.

Run and record the checks below for the release you are preparing. Do not copy results from a
different machine, model, provider account, or commit; release evidence must describe the exact
artifact and environment that was tested.

## Reproducible local artifacts

From a clean checkout with Python 3.11+, Node.js 22+, and FFmpeg/ffprobe:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[server,insights,dev]'
python -m pytest -q
python -m ruff check src tests scripts examples
python scripts/export_openapi.py
npm ci --prefix packages/client
npm run generate --prefix packages/client
npm test --prefix packages/client
python -m build
npm pack ./packages/client
```

Inspect the wheel, source archive, and npm tarball contents. Install the built wheel into
a fresh environment and the tarball into a fresh Node project; do not allow an editable
checkout or `PYTHONPATH` to mask missing package files. Check import, CLI help, and client
exports there. Include the client README and license, but exclude secrets, models, indexes,
private media, dependency trees, and local output. The licensed demo and its attribution
must stay together wherever the sample is distributed.

Review regenerated OpenAPI/client types with `git diff`. Test the sample upload → ask →
analyze → observations → citation playback flow. Record whether this was a fake-provider
pipeline check or an actual provider/model call. Do not merge those claims.

## Optional model and provider checks

Prepare the pinned local model explicitly, install the `local` extra, and run the opt-in
SigLIP smoke test with `SUOKU_TEST_MODEL`. Verify model/dependency licenses and scan the
actual resolved artifacts. For each advertised provider, use an authorized key and an
available image/structured-output model to test analysis, answers, no-evidence behavior,
malformed output, and readable errors. Record exact model IDs and date. Do not run paid
calls automatically in ordinary CI or claim a provider was tested from a mock alone.

The [evaluation guide](evaluation.md) defines what a semantic result requires. Hardware,
memory, network behavior, spending limits, and container restrictions must be checked on
the intended host. An unrun checklist is not a security audit or performance result.

## Container deployment

Prepare `.models/siglip` and provide `SUOKU_API_TOKEN` through your deployment's secret
manager or environment. The default profile preserves the worker's offline network mode:

```bash
docker compose up --build
```

For cloud insights, explicitly provide `SUOKU_INSIGHT_MODEL` and
`SUOKU_PROVIDER_API_KEY` to the worker and select the insights override:

```bash
docker compose -f compose.yaml -f compose.insights.yaml up --build
```

The override enables outbound networking for provider calls; media frame data leaves the
host. Review [SECURITY.md](../SECURITY.md) and the provider's current data-handling terms.
Keep the API bound to loopback unless a reviewed reverse proxy provides TLS and application
authorization. API and worker share one data directory; the worker owns model processing.
Compose stores that data in the named `suoku-data` volume mounted at `/data`; native CLI
examples use `.lake`. Back up the correct storage location for the deployment. The named
volume also avoids assuming that a host bind mount is writable by the container's user.

The CLI's protected config uses literal shell-style quoting. It is not automatically
exported to Docker Compose. To use it without evaluating it in a shell, the following
Python launcher passes only the configuration parser's values into the child environment:

```bash
python - <<'PY'
import os
import subprocess
from suoku.config import load_config

subprocess.run(
    ["docker", "compose", "-f", "compose.yaml", "-f", "compose.insights.yaml", "up", "--build"],
    env={**os.environ, **load_config()},
    check=True,
)
PY
```

## Publication gates

- [ ] Confirm Python/npm names and maintainer ownership; match versions and release notes.
- [ ] Verify clean-environment installs, CI, artifact contents, source/sample licenses, and checksums.
- [ ] Record tested providers/models and all untested limitations honestly.
- [ ] Run real-model/sample and Linux container checks on documented infrastructure.
- [ ] Review dependency scan results and update the supported dependency range if necessary.
- [ ] Enable and test the repository's private vulnerability reporting route; confirm maintainer coverage.
- [ ] Configure trusted publishing/provenance with least privilege; keep registry secrets out of Git.
- [ ] Obtain explicit publication authorization, then publish and verify registry installs.
- [ ] Add the release tag/date and artifact links only after those artifacts exist.

Building a wheel or npm tarball does not perform any of the external publication steps.
