# Security policy

This is an alpha, single-tenant, single-host library and optional service. Do not expose it to hostile public uploads without reviewing and validating your isolation boundary. Direct Python usage is not sandboxed. No audited security guarantee is made.

The HTTP service requires a random bearer token and never accepts arbitrary server paths, URLs, SQL, model names, or decoder flags. Keep its token in a server-side environment, not browser JavaScript. Bind to loopback by default; use TLS and your application's authorization when exposing it remotely. All holders of the token have full access to the deployment. CORS is disabled; no multitenant isolation is implemented.

Use the provided API/worker containers for isolation: non-root users, no-new-privileges, dropped capabilities, read-only rootfs, resource limits. The worker has no network. The worker can read the collection's media and write its index; its boundary is the whole single-tenant deployment, not a per-upload sandbox. Keep the default container seccomp profile enabled, never mount the Docker socket or host home, and patch FFmpeg/PyTorch/LanceDB and the host runtime regularly. Containers share a kernel and do not prevent every exploit.

Model setup is explicit and pins a commit. The local adapter validates the downloaded manifest and hashes, uses Safetensors, disables remote code, and loads local files only. The manifest verifies accidental modification; someone who can replace both model and manifest is already inside the trusted filesystem boundary. Custom adapters run trusted Python and are not sandboxed.

Uploaded video can exhaust disk/CPU/memory. Configure quotas, timeouts and container resource limits for the deployment. Probe and decode use a protocol allowlist, but that alone is not a sandbox. Proxy limits should be at least as strict as the application. The worker serializes model use. SQLite files belong on local storage, not network filesystems.

Search queries/results expire after an hour, provided the worker runs; expired search records are also hidden by the API immediately. Deletion removes retrieval access but does not erase backups, SQLite free pages, old Lance versions, external originals, or exported clips. The explicit purge endpoint can delete managed uploaded media; it never deletes files outside the managed directory. Embeddings and hashes are sensitive. Enable storage encryption at the host/volume layer; application-level encryption at rest is not implemented.

No analytics or cloud model requests occur in the default inference path. Package installation and explicit model preparation require network access.

For a private vulnerability report, use the hosting repository's private vulnerability reporting feature once enabled. Do not include real private footage, credentials, or exploitable payloads in a public issue. A maintainer contact and supported-version policy must be configured before a public release.

