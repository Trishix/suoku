# Suoku

An open-source semantic layer for archived video: retrieve relevant moments locally,
inspect selected frames with your model provider, and get structured observations and
answers with source timestamps.

Suoku combines local SigLIP retrieval with optional bring-your-own-key visual reasoning
through LiteLLM. Python, CLI, HTTP, and TypeScript expose the same workflow. Original
media stays in your storage; enabling insights sends selected frames and question/evidence
text to your configured provider. Provider charges and data policies apply.

This is an alpha for experimentation. It samples frames, can miss events, and can produce
incorrect answers. Safety and warehouse recipes are starting points for evaluation, not
validated detectors or compliance assessments. Live streams, audio understanding, face
recognition, and autonomous alerts are outside this release.

Start with short archived clips. Evidence extraction currently scans the source for each
selected window; long recordings may be slow or reach the decoder deadline. Long-video
throughput and live-provider accuracy are not yet benchmarked.

## Run the sample

Requirements: Python 3.11+, FFmpeg/ffprobe, and a vision-capable provider model and API key.
Node.js 22+ is needed only for the TypeScript client. Install from this checkout; registry
publication is not assumed.

```bash
git clone https://github.com/Trishix/suoku.git
cd suoku
python -m venv .venv
source .venv/bin/activate
pip install -e '.[local,server,insights]'

# Explicit one-time local retrieval model download, pinned to a revision.
suoku prepare-model .models/siglip \
  --revision 7fd15f0689c79d79e38b1c2e2e2370a7bf2761ed

# Replace YOUR_VISION_MODEL_ID with a model available in your provider account.
# The API key is requested without terminal echo.
suoku init --provider openai --model YOUR_VISION_MODEL_ID
suoku doctor --model .models/siglip
```

`init` writes `.suoku/config.env` with restrictive permissions and generates a service
token. Commands load that file explicitly; process environment variables take precedence.
Keep it out of Git. Provider options are `openai`, `anthropic`, `gemini`, `groq`, and
`openrouter`; model identifiers are configurable. See [provider setup and recipes](docs/recipes.md).

Start the API and the worker in separate terminals, from this directory with the virtual
environment activated:

```bash
# Terminal 1: authenticated API, bound to loopback by default.
suoku serve --data .lake
```

```bash
# Terminal 2: local retrieval plus the configured optional insight provider.
suoku worker --data .lake --model .models/siglip --device cpu
```

```bash
# Terminal 3: status, upload, and questions. Upload waits for ingestion by default.
suoku status
suoku upload examples/media/big-buck-bunny-15s.mp4 --camera demo
# Use the asset_id printed by upload in the commands below.
suoku ask "What animal is visible outdoors?" --asset ASSET_ID
suoku analyze ASSET_ID --recipe general --from 00:00:00 --to 00:00:15
suoku observations ASSET_ID
```

The [included sample](examples/media/ATTRIBUTION.md) is an attributed excerpt from
Blender Foundation's *Big Buck Bunny*, licensed CC BY 3.0. It demonstrates the workflow;
it is not a real-world surveillance or safety dataset.

## Use it in code

There are two integration modes:

- Use `VideoLake` and `InsightEngine` directly in a trusted Python process when you want
  local control and do not need an HTTP boundary.
- Run `suoku serve` and `suoku worker`, then call the authenticated service from your
  application backend with the TypeScript client or any HTTP client. This is the recommended
  web-application shape; keep `SUOKU_API_TOKEN` and provider keys out of browser code.

The [web-app integration guide](docs/web-app-integration.md) includes a backend example,
browser upload pattern, authorization guidance, and job/error handling.

The [complete Python example](examples/insights.py) ingests the sample, asks a question,
runs a recipe, prints dataclass results as JSON, and displays local playback references:

```bash
python examples/insights.py --model .models/siglip
```

It uses `InsightEngine(lake, provider)`, `ask(...)`, `analyze(...)`, and
`observations(...)`. Python use opens its own `.example-lake/index`; it does not need
the HTTP service. The core `VideoLake` retrieval API also works without a cloud provider.

For Node.js, build the local typed client and run the [complete client example](examples/client.mjs)
against the API and worker:

```bash
npm ci --prefix packages/client
npm run build --prefix packages/client
node examples/client.mjs
```

`VideoLakeClient` supports upload, search, jobs, recipes, analysis, observations, answers,
status, and authenticated media playback. The example imports the built client directly,
loads configuration through Python when necessary, and saves a playable local file for
the first citation. Keep the service token server-side; use your application backend to
proxy authorized playback to a browser. See the [client reference](packages/client/README.md).

## How it works

Ingestion samples archived video into local embeddings. `ask` retrieves candidate windows,
extracts bounded evidence frames, validates the model's recipe output, and reasons over
those observations. Citations identify an asset and a millisecond playback interval.
They link evidence for review; they do not prove the answer is correct.

Observations are cached by source content, window, extraction settings, recipe, provider,
and prompt fingerprints. Reusing an observation avoids its repeated vision call; answer
reasoning can still call the provider. Changing a model, recipe, source, or relevant
fingerprint requires fresh observations. A provider can change an unversioned model alias
without changing its identifier, so use versioned identifiers where available.

The service stores managed uploads under `.lake/media`, vectors and observation cache
under `.lake/index` (including `insights.sqlite3`), and durable jobs in `.lake/jobs.sqlite3`.
These paths describe native CLI use; Compose uses a named data volume. Direct Python ingestion
references external files and never deletes the originals. Removal and purge have
different retention effects; see [SECURITY.md](SECURITY.md).

The base `compose.yaml` runs an offline worker. Cloud insights require the explicit
`compose.insights.yaml` override and provider credentials; see [deployment notes](docs/releasing.md).

## Alpha resources

- [Documentation index](docs/README.md)
- [Architecture and data flow](docs/architecture.md)
- [Web-app integration](docs/web-app-integration.md)
- [HTTP API reference](docs/api.md)
- [Troubleshooting](docs/troubleshooting.md)
- [Configuration reference](docs/configuration.md)
- [Recipes, provider setup, and bounded custom schemas](docs/recipes.md)
- [Runnable examples and authenticated HTTP calls](examples/README.md)
- [Evaluation and pilot checklist](docs/evaluation.md)
- [Checks run on this alpha candidate](docs/verification.md)
- [Contributing](CONTRIBUTING.md), [security](SECURITY.md), [release checks](docs/releasing.md), and [changelog](CHANGELOG.md)

Suoku source is Apache-2.0. The bundled sample has its own attribution and license.
