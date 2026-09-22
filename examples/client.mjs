/** Node.js 22+ example. Run API and worker first; then `node examples/client.mjs`. */
import { execFileSync } from "node:child_process";
import { createWriteStream } from "node:fs";
import { mkdir, readFile } from "node:fs/promises";
import { resolve } from "node:path";
import { Readable } from "node:stream";
import { pipeline } from "node:stream/promises";
import { fileURLToPath } from "node:url";
import { VideoLakeClient } from "../packages/client/dist/index.js";

// Keep the service token in the local process. Browser code should call an application
// backend instead of exposing this deployment-wide credential.

function configuration() {
  if (process.env.SUOKU_API_TOKEN) return process.env;
  // Reuse Suoku's parser, without shell evaluation or printing the secret mapping.
  // Activate .venv first, or set SUOKU_PYTHON to its Python executable.
  const result = execFileSync(process.env.SUOKU_PYTHON ?? "python", [
    "-c",
    "import json,sys; from pathlib import Path; from suoku.config import load_config; " +
      "print(json.dumps(load_config(Path(sys.argv[1]))))",
    process.env.SUOKU_CONFIG ?? ".suoku/config.env",
  ], { encoding: "utf8", stdio: ["ignore", "pipe", "inherit"] });
  return JSON.parse(result);
}

const config = configuration();
const client = new VideoLakeClient({
  baseUrl: config.SUOKU_BASE_URL ?? "http://127.0.0.1:8000",
  token: config.SUOKU_API_TOKEN ?? "",
});
const status = await client.status();
console.log("Service status:", status);
if (!status.worker_available || !status.insights_ready) {
  throw new Error("Start a worker with the prepared model and insight provider configuration.");
}
console.log("Recipes:", (await client.recipes()).map(recipe => recipe.id));

const sample = fileURLToPath(new URL("./media/big-buck-bunny-15s.mp4", import.meta.url));
const job = await client.upload(new Blob([await readFile(sample)]), {
  filename: "big-buck-bunny-15s.mp4",
  cameraId: "demo",
});
const ingested = await client.wait(job, { timeoutMs: 300_000 });
const assetId = ingested.result?.asset_id;
if (!assetId) throw new Error("Ingestion did not return an asset_id.");

// ask returns an InsightAnswer, not a job or a list of search matches.
const answer = await client.ask("What animal is visible outdoors?", {
  assetIds: [assetId], recipe: "general", timeoutMs: 300_000,
});
console.log(JSON.stringify(answer, null, 2));
const observations = await client.analyze(assetId, {
  recipe: "general", startMs: 0, endMs: 15_000, timeoutMs: 300_000,
});
console.log("Analysis:", JSON.stringify(observations, null, 2));
console.log("Stored observations:", await client.observations(assetId, { recipe: "general" }));

const citation = answer.citations[0];
if (citation) {
  // Save one authenticated response locally. Never embed the bearer token in a URL.
  // Production browser applications can proxy this stream through their own backend.
  const response = await client.content(citation.asset_id);
  if (!response.body) throw new Error("Media response has no body.");
  const outputDirectory = resolve(".example-output");
  await mkdir(outputDirectory, { recursive: true });
  const output = resolve(outputDirectory, `${citation.asset_id}.mp4`);
  await pipeline(Readable.fromWeb(response.body), createWriteStream(output, { mode: 0o600 }));
  console.log(`Play ${output} from ${citation.start_ms / 1000}s to ${citation.end_ms / 1000}s`);
} else {
  console.log("No cited clip is available; review the answer limitations.");
}
