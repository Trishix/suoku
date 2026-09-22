# Suoku

[![CI](https://github.com/Trishix/suoku/actions/workflows/ci.yml/badge.svg)](https://github.com/Trishix/suoku/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)

Suoku is an open-source semantic layer for archived video. It combines local visual search
with optional bring-your-own-key (BYOK) vision models so applications can ask questions,
create structured observations, and receive timestamped evidence citations.

Suoku is currently an alpha for developers building and evaluating video-understanding
workflows. It is not a hosted SaaS, surveillance product, compliance detector, or autonomous
operations system.

## Why use Suoku?

Raw video is difficult for an application to search, reason over, and review. Suoku provides
the missing application layer:

- Local retrieval finds likely moments before any provider request.
- Selected frames are converted into validated structured observations.
- Questions return answers with source asset IDs and millisecond time ranges.
- Provider credentials remain with the worker; applications send only a Suoku service token.
- Python, CLI, authenticated HTTP, and typed TypeScript interfaces expose the same workflow.
- Observation caching avoids repeating the same vision analysis when source and recipe
  fingerprints have not changed.
- Built-in general, safety, and warehouse recipes provide inspectable starting points.

The current release processes archived MP4, MOV, and WebM files. It does not include live
camera ingestion, audio understanding, face recognition, autonomous alerts, multi-tenant
authorization, or automated actions.

## How it works

```text
Your backend ──Bearer service token──▶ Suoku API ──durable jobs──▶ Suoku worker
                                                                    │
                          media + local retrieval ◀─────────────────┘
                                                                    │ selected frames/text
                                                                    ▼
                                                        configured vision provider
```

The API accepts uploads and queues work. The worker owns FFmpeg, the local retrieval model,
the observation cache, and the optional provider adapter. A provider receives selected frames
and question/evidence text, not the complete source video. Answers state that evidence is
sampled and can be incomplete.

For storage and process boundaries, see [Architecture](docs/architecture.md).

## Quick start

### Requirements

- Python 3.11 or newer
- FFmpeg and `ffprobe`
- A vision model that supports image input and structured JSON output
- Node.js 22 or newer only if using the TypeScript client

### Install from a checkout

```bash
git clone https://github.com/Trishix/suoku.git
cd suoku
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[local,server,insights]'
```

Prepare the pinned local retrieval model. This is the explicit network/download step:

```bash
suoku prepare-model .models/siglip \
  --revision 7fd15f0689c79d79e38b1c2e2e2370a7bf2761ed
suoku doctor --model .models/siglip
```

Initialize the deployment. The command generates `SUOKU_API_TOKEN`, asks for the provider
key without echoing it, and writes a protected `.suoku/config.env` file. Do not commit that
file or copy its values into browser code.

```bash
suoku init --provider openai --model YOUR_VISION_MODEL_ID
```

Supported provider prefixes are `openai`, `anthropic`, `gemini`, `groq`, and `openrouter`.
There is no universal default model; use a model available to your account that supports
both image input and structured output. See [configuration](docs/configuration.md) and
[provider setup](docs/recipes.md).

Start the API and worker in separate terminals:

```bash
suoku serve --data .lake
suoku worker --data .lake --model .models/siglip --device cpu
```

In a third terminal, upload and ask a question:

```bash
suoku status
suoku upload examples/media/big-buck-bunny-15s.mp4 --camera demo
suoku ask "What animal is visible outdoors?" --asset ASSET_ID
suoku analyze ASSET_ID --recipe general --from 00:00:00 --to 00:00:15
suoku observations ASSET_ID
```

The bundled sample is an attributed *Big Buck Bunny* excerpt under its own CC BY 3.0
license. See [sample attribution](examples/media/ATTRIBUTION.md).

## Use the Python API

Direct Python use is useful for trusted scripts and applications that want to own the local
engine process. It does not provide the HTTP process boundary or per-user authorization.

```python
from suoku import VideoLake
from suoku.adapters.siglip import SiglipEmbedder
from suoku.config import load_config
from suoku.insights import InsightEngine
from suoku.providers import LiteLLMProvider

config = load_config()
lake = VideoLake.open(".app-lake/index", SiglipEmbedder(".models/siglip"))
provider = LiteLLMProvider(
    config["SUOKU_INSIGHT_MODEL"],
    config["SUOKU_PROVIDER_API_KEY"],
)
insights = InsightEngine(lake, provider)

asset_id = lake.ingest("clip.mp4")
answer = insights.ask("What is visible near the entrance?", asset_ids=[asset_id])
print(answer.answer)
for citation in answer.citations:
    print(citation.asset_id, citation.start_ms, citation.end_ms)

lake.close()
```

For a complete runnable example, see [examples/insights.py](examples/insights.py).

## Use it from a web application

Run Suoku as a backend service and call it from your own server. Do not put the Suoku
service token or provider key in browser JavaScript. Your backend should authenticate users,
check asset ownership, proxy authorized media, and enforce application-specific quotas.

The local TypeScript client is built and installed from this checkout until registry
publication is configured:

```bash
cd packages/client
npm ci
npm run build
npm pack --dry-run
cd ../..
npm install ./packages/client/suoku-0.1.0-alpha.1.tgz
```

Example server-side usage:

```ts
import { readFile } from "node:fs/promises";
import { VideoLakeClient } from "suoku";

const suoku = new VideoLakeClient({
  baseUrl: process.env.SUOKU_BASE_URL ?? "http://127.0.0.1:8000",
  token: process.env.SUOKU_API_TOKEN!,
});

const upload = await suoku.upload(new Blob([await readFile("clip.mp4")]), {
  filename: "clip.mp4",
  cameraId: "entrance",
});
const indexed = await suoku.wait(upload, { timeoutMs: 300_000 });
const assetId = indexed.result?.asset_id;
if (!assetId) throw new Error("Upload did not return an asset ID");

const answer = await suoku.ask("What is visible near the entrance?", {
  assetIds: [assetId],
  recipe: "general",
  timeoutMs: 300_000,
});

console.log(answer.answer, answer.citations, answer.limitations);
```

For upload authorization, job handling, media playback, and error behavior, read the
[web-app integration guide](docs/web-app-integration.md). The complete endpoint reference
is in the [HTTP API documentation](docs/api.md).

## Repository layout

```text
src/suoku/engine.py          local ingestion and retrieval
src/suoku/insights/          recipes, evidence, cache, and grounded answers
src/suoku/providers.py       isolated BYOK provider adapter
src/suoku/server/            authenticated API, durable jobs, and worker
src/suoku/cli.py             operator setup and command-line workflows
packages/client/             typed TypeScript client
tests/                       unit, integration, security, and provider-contract tests
docs/                        contributor-facing architecture and maintenance guides
examples/                    runnable examples and licensed sample media
```

## Development and contribution

Set up the contributor environment:

```bash
python -m pip install -e '.[server,insights,dev]'
python -m pytest -q
python -m ruff check src tests scripts examples
python scripts/export_openapi.py
cd packages/client
npm ci
npm run generate
npm test
```

The test suite uses deterministic fixtures and does not make paid provider calls by default.
Local model and live-provider tests are explicit opt-ins. Public API changes should update
the Python models, OpenAPI schema, TypeScript client, examples, and tests together.

Read [CONTRIBUTING.md](CONTRIBUTING.md) before opening a pull request. It explains code
ownership, review expectations, persistence changes, provider testing, and documentation
updates.

## Help and documentation

- [Contributor documentation index](docs/README.md)
- [Architecture](docs/architecture.md)
- [Configuration](docs/configuration.md)
- [Recipes and providers](docs/recipes.md)
- [HTTP API](docs/api.md)
- [Web-app integration](docs/web-app-integration.md)
- [Troubleshooting](docs/troubleshooting.md)
- [Evaluation guide](docs/evaluation.md)
- [Security policy](SECURITY.md)
- [Release checklist](docs/releasing.md)

For bugs and feature discussions, use [GitHub Issues](https://github.com/Trishix/suoku/issues).
Do not post private footage, credentials, or vulnerability details in a public issue; follow
the [security policy](SECURITY.md) instead.

## Maintainer and license

Suoku is maintained by [Trishix](https://github.com/Trishix). Contributions are welcome
through issues and pull requests. See [CONTRIBUTING.md](CONTRIBUTING.md) for project rules.

The source code is available under the Apache-2.0 license; see [LICENSE](LICENSE). The
bundled sample media has separate attribution and licensing terms documented beside it.
