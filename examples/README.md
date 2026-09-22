# Run the examples

Start from the repository root. Follow [the main setup](../README.md) to install
`.[local,server,insights]`, prepare SigLIP, and run `suoku init` with your model and key.
The bundled [15-second animation](media/big-buck-bunny-15s.mp4) has
[attribution and license information](media/ATTRIBUTION.md). Its checksum can be checked
with `shasum -a 256 -c SHA256SUMS` from `examples/media`.

Provider calls use your key and may incur charges. Outputs are not fixtures: they depend
on your provider/model and can be incorrect. The sample is animated footage, not a
real-world event-detection evaluation.

## Python: no service required

```bash
python examples/insights.py --model .models/siglip
```

[insights.py](insights.py) reads the protected config, ingests the sample into
`.example-lake/index`, performs on-demand Q&A, analyzes a bounded window, and lists cached
observations. `InsightEngine` returns dataclasses; use `dataclasses.asdict` for JSON.
The printed playback filename and citation interval can be opened in a local video player.
Run again to exercise reuse of ingestion/observations; answer reasoning can still make a
provider call. Use `--help` for a custom video, question, index, or config path.

## Node.js: use the separate API and worker

Start these in separate terminals:

```bash
suoku serve --data .lake
```

```bash
suoku worker --data .lake --model .models/siglip --device cpu
```

With Node.js 22+ and the Python virtual environment still activated:

```bash
npm ci --prefix packages/client
npm run build --prefix packages/client
node examples/client.mjs
```

[client.mjs](client.mjs) imports `../packages/client/dist/index.js`, so no published npm
package is needed. It checks status and recipes, uploads/waits, asks, analyzes, lists
observations, and downloads authenticated citation media to `.example-output`. It prints
the local filename and start/end seconds. Repeated executions upload a new managed asset.

If `SUOKU_API_TOKEN` is not already in the environment, the example calls the Python
configuration helper without a shell. Override its Python executable with `SUOKU_PYTHON`
or config path with `SUOKU_CONFIG`. Neither the config nor token is printed. In applications,
provide a backend secret through your deployment environment instead.

## HTTP contract

The API requires `Authorization: Bearer ...` on every route except `/healthz`. Use a trusted
backend to add that header. The following bodies illustrate requests after upload has
returned an `asset_id`; strings in uppercase are placeholders.

| Request | Body or purpose |
| --- | --- |
| `POST /v1/videos?filename=clip.mp4&camera_id=demo` | Raw video bytes, `Content-Type: application/octet-stream`; returns ingestion job |
| `POST /v1/search` | `{"query":"a rabbit outdoors","limit":5,"filters":{"camera_id":"demo"}}` |
| `POST /v1/answers` | `{"question":"What animal is visible?","recipe":"general","asset_ids":["ASSET_ID"],"candidate_limit":6}` |
| `POST /v1/analyses` | `{"asset_id":"ASSET_ID","recipe":"general","start_ms":0,"end_ms":15000}` |
| `GET /v1/jobs/JOB_ID` | Poll `state`; read `result.answer` or `result.observations` on success |
| `POST /v1/jobs/JOB_ID/cancel` | Request cancellation |
| `GET /v1/videos/ASSET_ID/observations?recipe=general` | Read stored observations |
| `GET /v1/insight-recipes` | Inspect built-in recipe definitions |
| `GET /v1/status` | Read worker heartbeat and insight configuration readiness |
| `GET /v1/videos/ASSET_ID/content` | Authenticated media response; accepts byte `Range` |

Job-creating requests return `202`. A successful HTTP submission does not mean processing
has finished. Poll until `succeeded`, `failed`, or `cancelled`; handle reported errors and
timeouts. Status reports configured readiness and a recent worker heartbeat, not a live
test of provider credentials or semantic accuracy.

For a complete HTTP example using only the Python standard library after initialization:

```bash
python examples/http_status.py
```

This reads protected configuration and calls status/recipes without exposing secrets in
shell arguments. The TypeScript example exercises the full upload-to-playback HTTP flow.
For browsers, proxy media through your application backend and enforce your user/session
authorization there. A bare `/content` URL cannot carry the required bearer header, and
embedding the service token in a browser or query string gives away deployment access.

Custom recipe example: [visible-objects.json](recipes/visible-objects.json). Usage and
schema limits are documented in [recipes](../docs/recipes.md). Evaluation and pilot
acceptance belong in [the evaluation guide](../docs/evaluation.md).
