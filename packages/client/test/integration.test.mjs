import { test } from "node:test";
import assert from "node:assert/strict";
import { spawn, execFileSync } from "node:child_process";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { resolve, join } from "node:path";
import { createInterface } from "node:readline";
import { once } from "node:events";
import { VideoLakeClient, LakeClientError } from "../dist/index.js";

test("built npm client against real HTTP API, FFmpeg, worker and LanceDB", { timeout: 60_000 }, async (t) => {
  const root = resolve("../..");
  const directory = await mkdtemp(join(tmpdir(), "suoku-sdk-"));
  const python = process.env.SUOKU_TEST_PYTHON ?? join(root, ".venv/bin/python");
  const child = spawn(python, [join(root, "tests/http_fixture.py"), join(directory, "lake")], { cwd: root });
  let errors = "";
  child.stderr.on("data", (chunk) => { errors = (errors + chunk).slice(-16_384); });
  t.after(async () => {
    child.kill("SIGTERM");
    if (child.exitCode === null) await once(child, "exit");
    await rm(directory, { recursive: true, force: true });
  });
  const lines = createInterface({ input: child.stdout });
  const [line] = await Promise.race([
    once(lines, "line"),
    once(child, "exit").then(() => { throw new Error(`Fixture exited: ${errors}`); }),
  ]);
  const { url, token } = JSON.parse(line);
  for (let tries = 0; tries < 100; tries++) {
    try { if ((await fetch(`${url}/healthz`)).ok) break; } catch { /* not listening yet */ }
    await new Promise(r => setTimeout(r, 50));
  }
  const client = new VideoLakeClient({ baseUrl: url, token });
  const video = join(directory, "red.mp4");
  execFileSync("ffmpeg", ["-v", "error", "-f", "lavfi", "-i", "color=c=red:s=320x240:r=10", "-t", "3", "-c:v", "libx264", "-pix_fmt", "yuv420p", video]);
  const bytes = await readFile(video);
  const job = await client.upload(new Blob([bytes]), { filename: "red.mp4", cameraId: "gate" });
  const ingested = await client.wait(job, { timeoutMs: 30_000, pollMs: 50 });
  const asset = ingested.result.asset_id;
  const matches = await client.search("red", { filters: { camera_id: "gate" }, pollMs: 50 });
  assert.equal(matches[0].asset_id, asset);
  assert.ok([0, 2000].includes(matches[0].timestamp_ms));
  const playback = await client.content(asset, { range: "bytes=0-9" });
  assert.equal(playback.status, 206);
  assert.equal((await playback.arrayBuffer()).byteLength, 10);
  const unauthenticated = new VideoLakeClient({ baseUrl: url, token: "wrong" });
  await assert.rejects(() => unauthenticated.submitSearch("red"), e => e instanceof LakeClientError && e.status === 401);
  await client.wait(await client.remove(asset), { pollMs: 50 });
  assert.deepEqual(await client.search("red", { pollMs: 50 }), []);
  await client.wait(await client.purge(asset, { deleteMedia: true }), { pollMs: 50 });
  assert.equal(errors, "");
});

test("client validates URL, identifiers and polling options", async () => {
  assert.throws(() => new VideoLakeClient({ baseUrl: "file:///tmp", token: "a" }), LakeClientError);
  const client = new VideoLakeClient({ baseUrl: "http://127.0.0.1:9", token: "a" });
  assert.throws(() => client.getJob("../private"), LakeClientError);
  await assert.rejects(() => client.wait("a".repeat(32), { pollMs: -1 }), LakeClientError);
  const controller = new AbortController();
  controller.abort();
  await assert.rejects(() => client.wait("a".repeat(32), { signal: controller.signal }), { name: "AbortError" });
});
