import type { components } from "./schema.js";

export type Job = components["schemas"]["JobBody"];
export type Match = components["schemas"]["MatchBody"];
export type SearchRequest = components["schemas"]["SearchRequest"];
export type Filters = components["schemas"]["Filters"];
export type Recipe = components["schemas"]["RecipeBody"];
export type Observation = components["schemas"]["Observation"];
export type InsightAnswer = components["schemas"]["InsightAnswer"];
export type Status = components["schemas"]["StatusBody"];

/** Millisecond ranges and recipes accepted by the analysis endpoint. */
export interface AnalysisOptions {
  recipe?: components["schemas"]["AnalysisRequest"]["recipe"];
  startMs?: components["schemas"]["AnalysisRequest"]["start_ms"];
  endMs?: components["schemas"]["AnalysisRequest"]["end_ms"];
  signal?: AbortSignal;
}

export interface AnswerOptions {
  recipe?: components["schemas"]["AnswerRequest"]["recipe"];
  assetIds?: components["schemas"]["AnswerRequest"]["asset_ids"];
  filters?: components["schemas"]["AnswerRequest"]["filters"];
  candidateLimit?: components["schemas"]["AnswerRequest"]["candidate_limit"];
  signal?: AbortSignal;
}

export class LakeClientError extends Error {
  constructor(public readonly code: string, message: string, public readonly status?: number) {
    super(message);
    this.name = "LakeClientError";
  }
}

export interface WaitOptions {
  signal?: AbortSignal;
  timeoutMs?: number;
  pollMs?: number;
}

function identifier(id: string): string {
  if (!/^[a-f0-9]{32}$/.test(id)) throw new LakeClientError("invalid_id", "Invalid job or asset identifier.");
  return id;
}

function sleep(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    signal.throwIfAborted();
    const abort = () => { clearTimeout(timer); reject(signal.reason); };
    const timer = setTimeout(() => { signal.removeEventListener("abort", abort); resolve(); }, ms);
    signal.addEventListener("abort", abort, { once: true });
  });
}

/** Server-side client: keep the service token out of browser bundles. */
export class VideoLakeClient {
  private readonly base: string;
  private readonly token: string;

  constructor(options: { baseUrl: string; token: string }) {
    const url = new URL(options.baseUrl);
    if (!new Set(["http:", "https:"]).has(url.protocol) || url.username || url.password || url.search || url.hash) {
      throw new LakeClientError("invalid_url", "Supply an HTTP(S) service URL without credentials, query, or fragment.");
    }
    if (!options.token) throw new LakeClientError("invalid_token", "An API token is required.");
    this.base = url.href.replace(/\/$/, "");
    this.token = options.token;
  }

  private async request<T>(path: string, init: RequestInit = {}): Promise<T> {
    const headers = new Headers(init.headers);
    headers.set("Authorization", `Bearer ${this.token}`);
    const response = await fetch(this.base + path, {
      ...init, headers, redirect: "error",
      signal: init.signal ?? AbortSignal.timeout(30_000),
    });
    if (!response.ok) {
      const body = await response.json().catch(() => ({})) as { error?: { code: string; message: string }; detail?: unknown };
      throw new LakeClientError(body.error?.code ?? "http_error", body.error?.message ?? `Service returned HTTP ${response.status}.`, response.status);
    }
    return await response.json() as T;
  }

  upload(body: Blob | ReadableStream<Uint8Array>, options: {
    filename: string; cameraId?: string; recordedAt?: string; signal?: AbortSignal;
  }): Promise<Job> {
    const query = new URLSearchParams({ filename: options.filename });
    if (options.cameraId !== undefined) query.set("camera_id", options.cameraId);
    if (options.recordedAt !== undefined) query.set("recorded_at", options.recordedAt);
    const init: RequestInit & { duplex?: "half" } = {
      method: "POST", body, signal: options.signal ?? AbortSignal.timeout(300_000),
      headers: { "Content-Type": "application/octet-stream" },
    };
    if (body instanceof ReadableStream) init.duplex = "half";
    return this.request(`/v1/videos?${query}`, init);
  }

  submitSearch(query: string, options: { limit?: number; filters?: Filters; signal?: AbortSignal } = {}): Promise<Job> {
    return this.request("/v1/search", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query, limit: options.limit ?? 10, filters: options.filters ?? {} }), signal: options.signal,
    });
  }

  status(options: { signal?: AbortSignal } = {}): Promise<Status> {
    return this.request("/v1/status", { signal: options.signal });
  }

  recipes(options: { signal?: AbortSignal } = {}): Promise<Recipe[]> {
    return this.request("/v1/insight-recipes", { signal: options.signal });
  }

  submitAnalysis(assetId: string, options: AnalysisOptions = {}): Promise<Job> {
    const body: components["schemas"]["AnalysisRequest"] = {
      asset_id: identifier(assetId), recipe: options.recipe ?? "general",
      start_ms: options.startMs ?? 0, end_ms: options.endMs,
    };
    return this.request("/v1/analyses", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body), signal: options.signal,
    });
  }

  async analyze(assetId: string, options: AnalysisOptions & WaitOptions = {}): Promise<Observation[]> {
    const job = await this.submitAnalysis(assetId, options);
    const completed = await this.wait(job, options);
    if (!completed.result?.observations) {
      throw new LakeClientError("invalid_response", "Analysis job returned no observations result.");
    }
    return completed.result.observations;
  }

  submitAnswer(question: string, options: AnswerOptions = {}): Promise<Job> {
    const body: components["schemas"]["AnswerRequest"] = {
      question, recipe: options.recipe ?? "general", asset_ids: options.assetIds?.map(identifier),
      filters: options.filters ?? {}, candidate_limit: options.candidateLimit ?? 6,
    };
    return this.request("/v1/answers", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body), signal: options.signal,
    });
  }

  async ask(question: string, options: AnswerOptions & WaitOptions = {}): Promise<InsightAnswer> {
    const job = await this.submitAnswer(question, options);
    const completed = await this.wait(job, options);
    if (!completed.result?.answer) {
      throw new LakeClientError("invalid_response", "Answer job returned no answer result.");
    }
    return completed.result.answer;
  }

  observations(assetId: string, options: { recipe?: string; signal?: AbortSignal } = {}): Promise<Observation[]> {
    const query = new URLSearchParams();
    if (options.recipe !== undefined) query.set("recipe", options.recipe);
    return this.request(`/v1/videos/${identifier(assetId)}/observations${query.size ? `?${query}` : ""}`, {
      signal: options.signal,
    });
  }

  getJob(id: string, signal?: AbortSignal): Promise<Job> {
    return this.request(`/v1/jobs/${identifier(id)}`, { signal });
  }

  cancelJob(id: string): Promise<Job> {
    return this.request(`/v1/jobs/${identifier(id)}/cancel`, { method: "POST" });
  }

  remove(id: string): Promise<Job> {
    return this.request(`/v1/videos/${identifier(id)}`, { method: "DELETE" });
  }

  purge(id: string, options: { deleteMedia?: boolean } = {}): Promise<Job> {
    return this.request(`/v1/videos/${identifier(id)}/purge?delete_media=${options.deleteMedia ?? false}`, { method: "DELETE" });
  }

  async wait(job: Job | string, options: WaitOptions = {}): Promise<Job> {
    const id = typeof job === "string" ? job : job.id;
    const timeoutMs = options.timeoutMs ?? 60_000;
    const pollMs = options.pollMs ?? 250;
    if (!Number.isInteger(timeoutMs) || timeoutMs <= 0 || !Number.isInteger(pollMs) || pollMs < 10) {
      throw new LakeClientError("invalid_options", "Timeout must be positive; polling interval must be at least 10ms.");
    }
    const deadline = AbortSignal.timeout(timeoutMs);
    const signal = options.signal ? AbortSignal.any([options.signal, deadline]) : deadline;
    while (true) {
      signal.throwIfAborted();
      const current = await this.getJob(id, signal);
      if (current.state === "succeeded") return current;
      if (current.state === "failed" || current.state === "cancelled") {
        throw new LakeClientError(current.error?.code ?? current.state, current.error?.message ?? `Job ${current.state}.`);
      }
      await sleep(pollMs, signal);
    }
  }

  async search(query: string, options: WaitOptions & { limit?: number; filters?: Filters } = {}): Promise<Match[]> {
    const job = await this.submitSearch(query, options);
    const completed = await this.wait(job, options);
    return completed.result?.matches ?? [];
  }

  /** Authenticated response; stream response.body instead of buffering large media. */
  async content(id: string, options: { range?: string; signal?: AbortSignal } = {}): Promise<Response> {
    const headers = new Headers({ Authorization: `Bearer ${this.token}` });
    if (options.range) headers.set("Range", options.range);
    const response = await fetch(`${this.base}/v1/videos/${identifier(id)}/content`, {
      headers, redirect: "error", signal: options.signal ?? AbortSignal.timeout(30_000),
    });
    if (!response.ok) throw new LakeClientError("http_error", `Media returned HTTP ${response.status}.`, response.status);
    return response;
  }
}
