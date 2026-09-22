# Web-application integration

The recommended integration is a backend-for-frontend: your application authenticates its
own users, calls Suoku with the deployment token, and returns only the assets and citations
that the current user is allowed to see.

```text
browser ──your session──▶ application backend ──Suoku token──▶ Suoku API
```

Suoku's token is deployment-wide in this alpha. It is not a user token and does not provide
per-user permissions. Your backend must enforce tenancy, asset ownership, rate limits, and
audit policy.

## Node.js/TypeScript setup

Until npm publication is enabled, build and install the local tarball:

```bash
cd /path/to/suoku
npm ci --prefix packages/client
npm run build --prefix packages/client
cd /path/to/your-app
npm install /path/to/suoku/packages/client/suoku-0.1.0-alpha.1.tgz
```

For maximum npm compatibility, run the pack command from the package directory:

```bash
cd /path/to/suoku/packages/client
npm pack --dry-run
```

The package requires Node.js 22 or newer. The client is dependency-light and does not run
Python, FFmpeg, or a model; those remain in the Suoku service deployment.

## Server-side example

```ts
import { readFile } from "node:fs/promises";
import { VideoLakeClient } from "suoku";

const suoku = new VideoLakeClient({
  baseUrl: process.env.SUOKU_BASE_URL ?? "http://127.0.0.1:8000",
  token: process.env.SUOKU_API_TOKEN!,
});

export async function inspectVideo(path: string) {
  const bytes = await readFile(path);
  const upload = await suoku.upload(new Blob([bytes]), {
    filename: "upload.mp4",
    cameraId: "front-door",
  });
  const indexed = await suoku.wait(upload, { timeoutMs: 300_000 });
  const assetId = indexed.result?.asset_id;
  if (!assetId) throw new Error("Suoku ingestion returned no asset ID");

  const answer = await suoku.ask("What is visible near the entrance?", {
    assetIds: [assetId],
    recipe: "general",
    timeoutMs: 300_000,
  });

  return {
    assetId,
    answer: answer.answer,
    citations: answer.citations,
    limitations: answer.limitations,
  };
}
```

Use `client.content(citation.asset_id)` only after checking that the citation belongs to
the current application user. The response is the source file, not a generated clip. Seek
to `citation.start_ms` and stop at `citation.end_ms` in your own video player or media
proxy. The bearer token must not appear in a browser URL.

## Uploading from a browser

Do not send the Suoku token from browser JavaScript. Instead:

1. The browser uploads to your backend using your normal session.
2. Your backend checks file type, size, user authorization, and quota.
3. Your backend streams the file to `POST /v1/videos` with the Suoku bearer token.
4. Your backend stores the returned job/asset mapping in your application database.
5. The browser polls your backend, not Suoku directly.

This also lets you replace the local service with a queued or remote deployment later
without changing your browser contract.

## Error and timeout handling

Treat upload, analysis, and answer calls as asynchronous jobs. Handle these states:

- `queued`: accepted but not started
- `running`: worker is processing
- `succeeded`: read `result`
- `failed`: inspect safe `error.code` and `error.message`
- `cancelled`: the job was explicitly cancelled

Use a timeout appropriate for file size, decoder work, and provider latency. Retry only
safe submission failures; avoid blindly resubmitting an accepted upload because it may
create duplicate media. Use `getJob` to recover after a client disconnect.

## Python web applications

A Python application can either use the same HTTP API or embed the local engine. HTTP is
recommended when the application already has a web server and authentication layer. Direct
Python use is suitable for controlled scripts and trusted server processes:

```python
from suoku import VideoLake
from suoku.adapters.siglip import SiglipEmbedder
from suoku.insights import InsightEngine
from suoku.providers import LiteLLMProvider

lake = VideoLake.open(".app-lake/index", SiglipEmbedder(".models/siglip"))
provider = LiteLLMProvider("openai/gpt-4o-mini", api_key="read-from-secret-manager")
insights = InsightEngine(lake, provider)
asset_id = lake.ingest("clip.mp4")
answer = insights.ask("What is visible?", asset_ids=[asset_id])
```

Direct embedding runs trusted Python and shares the process with your application. It does
not provide the API's process separation or token boundary.
