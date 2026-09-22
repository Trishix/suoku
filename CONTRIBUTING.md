# Contributing

Suoku is an alpha. Small, reproducible changes with clear evidence are welcome through
[GitHub issues and pull requests](https://github.com/Trishix/suoku). Do not attach private
footage, API keys, protected configuration, or a security exploit to a public issue.
See [SECURITY.md](SECURITY.md) for vulnerability reporting.

## Local development

Use Python 3.11+, Node.js 22+, and FFmpeg/ffprobe:

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
```

Local SigLIP testing also needs the `local` extra and an explicitly prepared model.
Set `SUOKU_TEST_MODEL=/absolute/path/to/prepared/model` to enable its smoke test. Standard
tests use generated media and deterministic fake models; passing them establishes pipeline
behavior, not semantic accuracy. Never make ordinary tests download models or call paid APIs.

## Changes and review

Describe the concrete problem, resulting behavior, and relevant verification. Preserve
source/model/recipe fingerprints and generation activation semantics. Persistence changes
need recovery coverage; cache changes need invalidation coverage. Provider behavior should
be tested with fakes, including malformed output, provider errors, and secret redaction.

For API changes, update Python request/response models, export OpenAPI, regenerate client
types, and update client behavior and examples together. Keep the CLI, Python API, HTTP
API, and TypeScript client consistent. Add migration notes when stored data or a public
contract changes.

Custom recipes should describe visible evidence and uncertainty. Do not describe a recipe
as a validated safety/compliance detector without a relevant evaluation. Add only media
you have permission to redistribute, with attribution, source, changes, and checksum.
Keep customer footage, downloaded models, secrets, indexes, and generated benchmark data
out of Git.

Before a release, follow [the release checklist](docs/releasing.md). Building artifacts
does not authorize or imply publication; registry ownership, support contact, security
reporting, licenses, and release verification must be checked separately.
