# Troubleshooting

Run the diagnostic command first:

```bash
suoku doctor --data .lake --model .models/siglip
```

It checks dependencies, FFmpeg/ffprobe, writable storage, and the pinned model manifest
without loading the model or making a provider request.

## `suoku: command not found`

Activate the virtual environment or invoke the module directly:

```bash
source .venv/bin/activate
python -m suoku.cli --help
```

For editable development installs, run `pip install -e '.[server,insights,dev]'` from the
repository root.

## Missing FFmpeg or ffprobe

Install both executables and confirm they are on `PATH`:

```bash
ffmpeg -version
ffprobe -version
```

Evidence extraction cannot run without them. The Docker worker installs FFmpeg in the image.

## `worker_available` is false

The API is alive, but no worker heartbeat has been observed recently. Start the worker in a
second terminal using the same data directory:

```bash
suoku worker --data .lake --model .models/siglip --device cpu
```

With Compose, check `docker compose logs worker`. Do not start an API and worker against
different data directories.

## `insights_ready` is false

The worker is running without an insight provider. Confirm that the worker process can read
`SUOKU_INSIGHT_MODEL` and `SUOKU_PROVIDER_API_KEY`, or that its deployment environment
contains them. Restart the worker after changing configuration. A ready status does not
prove the key is accepted by the provider; the first insight request may still return an
authentication or unsupported-model error.

## Provider errors

Use a fully qualified route such as `openai/model-name`, `anthropic/model-name`,
`gemini/model-name`, `groq/model-name`, or `openrouter/provider/model-name`. The selected
model must support both image input and structured JSON Schema output. There is no universal
default model. Check the provider's current model documentation, account permissions, and
data-retention terms.

Never put a provider key in an HTTP request body or command argument. Use `suoku init
--provider-key-stdin` for automation or a deployment secret manager.

## Upload succeeds but analysis says `not_ready`

Uploading creates a job; it does not synchronously index the video. Poll until the ingest
job is `succeeded`, then submit analysis or an answer. The TypeScript `wait()` helper and CLI
wait by default. If a job fails, inspect its safe `error.code` and fix the original issue
before resubmitting.

## `source_changed`

Direct Python ingestion records a content hash. If the original file changes after ingest,
Suoku refuses to reuse observations. Re-ingest the changed file, or restore the original
bytes. This prevents citations from pointing to different media than the one analyzed.

## Empty or weak answers

Suoku samples frames and retrieves only a bounded candidate set. Try a short clip, a more
specific question, an appropriate recipe, or a larger `candidate_limit` (up to 20). An
insufficient-evidence answer is expected behavior when retrieval or sampled frames do not
support the question. It is not a provider failure.

## Custom recipe rejected

Recipes must be JSON objects using the documented bounded schema subset. Objects should set
`additionalProperties: false`; arrays should define typed `items`; recursive references,
remote `$ref` URLs, regex patterns, and unrestricted maps are rejected. See [recipes](recipes.md).

Pydantic schemas are converted for strict provider transport separately from local validation.
Avoid recursive, arbitrary, or heavily constrained models.

## npm packaging fails with `package.json ENOENT`

Run npm from the client directory:

```bash
cd packages/client
npm pack --dry-run
```

Some npm versions do not apply `--prefix` correctly to `npm pack`. The CI workflow uses a
step-level `working-directory: packages/client` for this reason.

## Docker or Compose issues

Validate configuration before starting containers:

```bash
docker compose config --quiet
docker compose -f compose.yaml -f compose.insights.yaml config --quiet
```

The base worker has no network. Use the insights override only when provider egress is
intentional. Compose uses a named `suoku-data` volume; do not assume it is the same as a
native `.lake` directory.
