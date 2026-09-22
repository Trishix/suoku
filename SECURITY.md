# Security policy

This alpha is a single-tenant, single-host library and optional service. It has not received
an independent security audit. Direct Python use and custom adapters run trusted code;
they are not sandboxed. Review and test the isolation boundary before accepting hostile uploads.

## Credentials and access

Every HTTP route except `/healthz` requires the deployment's bearer token. The service
binds to loopback by default. Token holders have access to the entire deployment; there
is no per-user authorization or multitenant isolation. Use TLS and your application's
authorization layer for remote access. Keep tokens on a trusted backend and proxy
authenticated media to browsers; do not put tokens in browser bundles or media URLs.

`suoku init` writes `.suoku/config.env` with restrictive permissions and generates a
random service token. Protect that file, use a secret manager for deployments, and keep
it out of commits, logs, screenshots, and issue reports. Process environment overrides
the configuration file. Rotate both the service token and the provider key after suspected
disclosure, then restart the affected processes. Environment variables are visible to
other sufficiently privileged processes on the host.

The provider key belongs to the worker/operator. HTTP clients cannot supply arbitrary
provider models, API bases, server file paths, URLs, SQL, or decoder flags. User questions,
recipe text, video text, and model responses remain untrusted data. Schema validation and
citation checks limit output shape; they do not establish that a statement is true or
fully prevent prompt injection. Do not execute model output as code or give it authority
to control safety equipment.

## Media, models, and network access

The supplied containers use non-root users, dropped capabilities, no-new-privileges,
read-only root filesystems, and resource limits. The base worker has no network access.
The explicit insights Compose override enables outbound requests so the configured
provider can receive selected evidence frames and question/evidence text. Check that
provider's retention, processing region, terms, and account settings before using private
footage. Suoku does not control provider-side retention. Keep credentials out of the API
container when only the worker needs them.

Containers share the host kernel and are not a complete defense. The worker can read all
collection media and write its index; it is not isolated per upload. Keep the default
seccomp profile, avoid mounting the Docker socket or host home, and patch FFmpeg,
PyTorch, LanceDB, LiteLLM, containers, and the host. Apply appropriate disk, memory, CPU,
upload, and provider spending limits. Use local storage for SQLite.

Model preparation is explicit and pins a revision. The local SigLIP adapter checks its
manifest and hashes, uses Safetensors, disables remote code, and loads local files. A
manifest cannot protect against an attacker able to replace both the files and manifest.
Custom adapters are trusted Python. Use only models and plugins you have reviewed.

## Retention and deletion

Media, embeddings, hashes, observations, questions, answers, and citations can reveal
sensitive information. Protect the index and job database alongside the media. Use host
or volume encryption and restricted backups; application-level encryption at rest is
not implemented. Insight observations are persistent cached data, not ephemeral frames.

Removing an asset hides its retrieval access and invalidates access to its observations.
It also clears all saved answer-job results in this single-host alpha: citations may omit
sources used in reasoning, so deletion cannot safely depend on the cited subset alone.
Rerun questions against the remaining assets; unrelated observation caches are preserved.
Explicit purge can remove managed uploaded media and derived records. Direct Python
ingestion never deletes external originals. Neither operation erases backups, exports,
SQLite free pages, historical storage versions, provider records, or screenshots. Maintain
and test an appropriate backup, retention, and secure-erasure policy for your environment.
Completed query jobs have limited retention; worker maintenance requires a running worker.

Local retrieval does not require provider calls. Package installation, explicit model
preparation, and enabled cloud insights require network access. Audit your dependency and
provider configuration before making broader claims about telemetry or data residency.

## Reporting and support

Use [GitHub private vulnerability reporting](https://github.com/Trishix/suoku/security/advisories/new)
if it is enabled for the repository. If that form is unavailable, open a public issue
asking for a private reporting channel without including sensitive details or exploit
material. Enabling and verifying the private channel is a release gate, not a claim
that it is already active.

The current alpha development line is the only target for fixes; there is no guaranteed
response time, long-term support, or backport policy. Maintainers must confirm a working
private reporting route before publishing a public release.
