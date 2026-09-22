import { test } from "node:test";
import assert from "node:assert/strict";
import { spawn, execFileSync } from "node:child_process";
import { mkdtemp, readFile, readdir, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { resolve, join } from "node:path";
import { createInterface } from "node:readline";
import { once } from "node:events";
import { VideoLakeClient, LakeClientError } from "../dist/index.js";

test("built npm client against real HTTP API, FFmpeg evidence, worker and LanceDB", { timeout: 60_000 }, async (t) => {
  const root = resolve("../..");
  const directory = await mkdtemp(join(tmpdir(), "suoku-sdk-"));
  const python = process.env.SUOKU_TEST_PYTHON ?? join(root, ".venv/bin/python");
  const child = spawn(python, [join(root, "tests/http_fixture.py"), join(directory, "lake")], {
    cwd: root,
    env: { ...process.env, PYTHONPATH: join(root, "src") },
  });
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
  const status = await client.status();
  assert.equal(status.worker_available, true);
  assert.equal(status.insights_ready, true);
  assert.equal(status.insight_model, "test/color-insights");
  assert.ok(status.last_heartbeat > 0);
  const recipes = await client.recipes();
  assert.deepEqual(recipes.map(recipe => recipe.id).sort(), ["general", "safety", "warehouse"]);
  assert.equal(recipes[0].schema.type, "object");
  const video = join(directory, "red.mp4");
  execFileSync("ffmpeg", ["-v", "error", "-f", "lavfi", "-i", "color=c=red:s=320x240:r=10", "-t", "3", "-c:v", "libx264", "-pix_fmt", "yuv420p", video]);
  const bytes = await readFile(video);
  // Exercise the actual Python CLI transport against this same live service.
  const cli = (...args) => JSON.parse(execFileSync(python, ["-m", "suoku.cli", ...args,
    "--url", url, "--json"], {
    cwd: root,
    env: { ...process.env, PYTHONPATH: join(root, "src"), SUOKU_API_TOKEN: token },
    encoding: "utf8", timeout: 30_000,
  }));
  assert.equal(cli("status").insights_ready, true);
  assert.equal(cli("recipes").length, 3);
  const cliAsset = cli("upload", video, "--camera", "cli-demo").asset_id;
  assert.equal(cli("ask", "red", "--asset", cliAsset).citations[0].asset_id, cliAsset);
  assert.equal(cli("analyze", cliAsset, "--from", "00:00:00", "--to", "00:00:03").length, 1);
  assert.equal(cli("observations", cliAsset).length, 1);
  await client.wait(await client.purge(cliAsset, { deleteMedia: true }), { pollMs: 50 });
  const job = await client.upload(new Blob([bytes]), { filename: "red.mp4", cameraId: "gate" });
  const ingested = await client.wait(job, { timeoutMs: 30_000, pollMs: 50 });
  const asset = ingested.result.asset_id;
  const matches = await client.search("red", { filters: { camera_id: "gate" }, pollMs: 50 });
  assert.equal(matches[0].asset_id, asset);
  assert.ok([0, 2000].includes(matches[0].timestamp_ms));
  const playback = await client.content(asset, { range: "bytes=0-9" });
  assert.equal(playback.status, 206);
  assert.equal((await playback.arrayBuffer()).byteLength, 10);

  const analysisJob = await client.submitAnalysis(asset, { recipe: "general" });
  assert.equal(analysisJob.kind, "analysis");
  const analyzed = await client.wait(analysisJob, { pollMs: 50 });
  const observations = analyzed.result.observations;
  assert.equal(observations.length, 1);
  assert.equal(observations[0].asset_id, asset);
  assert.equal(observations[0].payload.scene, "red");
  assert.deepEqual(observations[0].evidence_timestamps, [0, 1500, 2900]);
  assert.equal(observations[0].provider_fingerprint, "test-only-color-insights-v1");
  assert.deepEqual(await client.analyze(asset, { pollMs: 50 }), observations);
  assert.deepEqual(await client.observations(asset, { recipe: "general" }), observations);

  const answerJob = await client.submitAnswer("red", {
    assetIds: [asset], filters: { camera_id: "gate" }, candidateLimit: 1,
  });
  assert.equal(answerJob.kind, "answer");
  const answered = await client.wait(answerJob, { pollMs: 50 });
  const answer = answered.result.answer;
  assert.equal(answer.insufficient_evidence, false);
  assert.equal(answer.citations[0].observation_id, observations[0].id);
  assert.equal(answer.citations[0].asset_id, asset);
  assert.equal(answer.citations[0].start_ms, 0);
  assert.equal(answer.citations[0].end_ms, 3000);
  assert.ok(answer.limitations.length > 0);
  const repeated = await client.ask("red", { assetIds: [asset], candidateLimit: 1, pollMs: 50 });
  assert.deepEqual(repeated, answer);
  // The fake's call sequence is embedded in its summary: equality verifies a
  // cache hit rather than merely deterministic IDs after another provider call.
  assert.deepEqual(await client.observations(asset), observations);
  const empty = await client.ask("red", { filters: { camera_id: "missing" }, pollMs: 50 });
  assert.equal(empty.insufficient_evidence, true);
  assert.deepEqual(empty.citations, []);

  const customRecipe = {
    id: "frame-details", version: "1", name: "Frame details", description: "Integration fixture",
    prompt: "Describe sampled colors and actual frame timestamps.",
    schema: {
      type: "object", additionalProperties: false,
      required: ["color", "frame_timestamps", "aspect_ratio"],
      properties: {
        color: { type: "string" },
        frame_timestamps: { type: "array", items: { type: "integer" } },
        aspect_ratio: { type: "number" },
      },
    },
  };
  const custom = await client.analyze(asset, {
    recipe: customRecipe, startMs: 500, endMs: 2500, pollMs: 50,
  });
  assert.equal(custom[0].recipe_id, "frame-details");
  assert.deepEqual(custom[0].payload, {
    color: "red", frame_timestamps: [500, 1500, 2400], aspect_ratio: 4 / 3,
  });
  assert.notEqual(custom[0].id, observations[0].id);
  assert.deepEqual(await client.observations(asset, { recipe: "frame-details" }), custom);
  assert.deepEqual(await client.observations(asset, { recipe: "unknown" }), []);
  const customAnswer = await client.ask("red", {
    recipe: customRecipe, assetIds: [asset], candidateLimit: 1, pollMs: 50,
  });
  assert.equal(customAnswer.citations[0].asset_id, asset);

  const unauthenticated = new VideoLakeClient({ baseUrl: url, token: "wrong" });
  await assert.rejects(() => unauthenticated.submitSearch("red"), e => e instanceof LakeClientError && e.status === 401);
  for (const request of [
    () => unauthenticated.status(), () => unauthenticated.recipes(),
    () => unauthenticated.submitAnalysis(asset), () => unauthenticated.submitAnswer("red"),
    () => unauthenticated.observations(asset),
  ]) {
    await assert.rejects(request, e => e instanceof LakeClientError && e.status === 401);
  }
  await client.wait(await client.remove(asset), { pollMs: 50 });
  assert.deepEqual(await client.search("red", { pollMs: 50 }), []);
  assert.deepEqual(await client.observations(asset), []);
  assert.equal((await client.ask("red", { assetIds: [asset], pollMs: 50 })).insufficient_evidence, true);
  for (const id of [analysisJob.id, answerJob.id]) {
    const hidden = await client.getJob(id);
    assert.equal(hidden.result, null);
    assert.equal(hidden.error.code, "source_removed");
  }
  await assert.rejects(() => client.content(asset), e => e instanceof LakeClientError && e.status === 404);
  await client.wait(await client.purge(asset, { deleteMedia: true }), { pollMs: 50 });
  assert.deepEqual(await client.observations(asset), []);
  assert.deepEqual(await readdir(join(directory, "lake/media")), []);
  assert.equal(errors, "");
});

test("client validates URL, identifiers and polling options", async () => {
  assert.throws(() => new VideoLakeClient({ baseUrl: "file:///tmp", token: "a" }), LakeClientError);
  const client = new VideoLakeClient({ baseUrl: "http://127.0.0.1:9", token: "a" });
  assert.throws(() => client.getJob("../private"), LakeClientError);
  assert.throws(() => client.submitAnalysis("../private"), LakeClientError);
  assert.throws(() => client.observations("../private"), LakeClientError);
  assert.throws(() => client.submitAnswer("red", { assetIds: ["../private"] }), LakeClientError);
  await assert.rejects(() => client.wait("a".repeat(32), { pollMs: -1 }), LakeClientError);
  const controller = new AbortController();
  controller.abort();
  await assert.rejects(() => client.wait("a".repeat(32), { signal: controller.signal }), { name: "AbortError" });
  for (const request of [
    () => client.status({ signal: controller.signal }),
    () => client.recipes({ signal: controller.signal }),
    () => client.analyze("a".repeat(32), { signal: controller.signal }),
    () => client.ask("red", { signal: controller.signal }),
    () => client.observations("a".repeat(32), { signal: controller.signal }),
  ]) {
    await assert.rejects(request, { name: "AbortError" });
  }
});
