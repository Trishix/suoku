# Suoku client for Node.js / TypeScript

A typed, server-side client for the optional Suoku HTTP service. It does not embed Python
or run video models in Node.js. Requires Node.js 22+, a running Suoku API and worker, and
the deployment's service token. Keep that token out of browser bundles and URLs.

This is an alpha. Install from a checkout or a locally built tarball until registry
ownership/publication has been verified:

```bash
# From the Suoku repository root:
npm ci --prefix packages/client
npm run build --prefix packages/client
npm pack ./packages/client
# From your application, install the resulting file using its actual path:
npm install /path/to/suoku/suoku-0.1.0-alpha.1.tgz
```

For repository examples, [examples/client.mjs](../../examples/client.mjs) directly imports
the built `dist/index.js`, so a tarball installation is unnecessary.

```ts
import { VideoLakeClient } from "suoku";

const token = process.env.SUOKU_API_TOKEN;
if (!token) throw new Error("SUOKU_API_TOKEN is required on the server.");
const client = new VideoLakeClient({
  baseUrl: process.env.SUOKU_BASE_URL ?? "http://127.0.0.1:8000",
  token,
});

const status = await client.status();
const recipes = await client.recipes();
const job = await client.upload(new Blob([videoBytes]), {
  filename: "clip.mp4", cameraId: "gate-1",
});
const completed = await client.wait(job, { timeoutMs: 300_000 });
const assetId = completed.result?.asset_id;
if (!assetId) throw new Error("Upload did not return an asset_id.");

// These helpers submit durable jobs and wait for their results.
const answer = await client.ask("What is visible near the gate?", {
  assetIds: [assetId], recipe: "general", timeoutMs: 300_000,
});
console.log(answer.answer, answer.limitations, answer.citations);

const observations = await client.analyze(assetId, {
  recipe: "general", startMs: 0, endMs: 15_000, timeoutMs: 300_000,
});
const cached = await client.observations(assetId, { recipe: "general" });
```

`videoBytes` above represents bytes read by your application; the repository's `.mjs`
example contains the complete file-reading and playback code. A recipe may be a built-in
ID (`general`, `safety`, `warehouse`) or an inline recipe object with a bounded schema.

| Method | Result |
| --- | --- |
| `upload(body, options)` | Ingestion `Job`; call `wait` before using its asset |
| `wait(jobOrId, options)` | Completed `Job`; throws on failure/cancellation/timeout |
| `search(query, options)` | `Match[]` from local retrieval |
| `ask(question, options)` | `InsightAnswer` with `answer`, `citations`, `limitations`, `provider_fingerprint`, `insufficient_evidence` |
| `analyze(assetId, options)` | `Observation[]` |
| `observations(assetId, options)` | Stored `Observation[]`; optional recipe ID filter |
| `status()` | Worker heartbeat and configured insight readiness |
| `recipes()` | Built-in recipe definitions |
| `getJob(id)`, `cancelJob(id)` | Job status/cancellation |
| `content(assetId, options)` | Authenticated `Response`; supports a byte `range` |
| `remove(assetId)`, `purge(assetId, options)` | Lifecycle `Job`; purge media requires explicit `deleteMedia: true` |

The service token authenticates this client; the model provider key stays in the worker.
Keep both secrets out of logs. Use `LakeClientError.code` for service errors, handle aborts
and timeouts, and choose an appropriate wait timeout for video length/provider latency.

Every citation contains `asset_id`, `start_ms`, `end_ms`, `observation_id`, and `reason`.
Use `client.content(citation.asset_id)` to download or proxy authenticated source media,
then seek to `start_ms / 1000` seconds. The response is the source file, not a pre-cut clip.
Stream `response.body` for large files. Your application backend must enforce its own
user authorization before forwarding this stream to a browser.

Answers are based on sampled frames, can miss events, and may be wrong. Valid schemas and
citations are useful review aids, not accuracy guarantees. Reusing cached observations
does not guarantee that repeated answer generation makes no provider calls.

Project setup, data retention, security, and evaluation guidance are in the
[repository](https://github.com/Trishix/suoku). Source code is Apache-2.0.
