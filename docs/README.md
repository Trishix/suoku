# Suoku contributor documentation

This directory contains the documentation needed to understand, modify, test, secure, and
release Suoku. The repository is contributor-focused: implementation scratch plans and one-off
release verification notes are intentionally not kept here. Start with [CONTRIBUTING.md](../CONTRIBUTING.md),
then use the guide that matches the area you are changing:

| Contributor task | Read |
| --- | --- |
| Set up a development environment | [Contributing](../CONTRIBUTING.md) and [configuration](configuration.md) |
| Understand ownership and data flow | [Architecture](architecture.md) and [contracts](contracts.md) |
| Change or review HTTP behavior | [HTTP API](api.md) and the generated [OpenAPI schema](../schema/openapi.json) |
| Maintain the TypeScript client | [Web-app integration](web-app-integration.md) and [client README](../packages/client/README.md) |
| Change recipes or providers | [Recipes and providers](recipes.md) |
| Diagnose local or CI failures | [Troubleshooting](troubleshooting.md) |
| Run quality and semantic evaluation | [Evaluation guide](evaluation.md) |
| Prepare a release | [Release checklist](releasing.md) |
| Report a vulnerability | [Security policy](../SECURITY.md) |

## What contributors should preserve

The current release accepts archived MP4, MOV, and WebM uploads. It performs local visual
retrieval, selects a small number of timestamped frames, sends selected evidence to one
configured vision/structured-output model, validates the result, and returns observations
or answers with citations.

It does not provide live camera ingestion, audio transcription, identity recognition,
multi-tenant authorization, alert delivery, automated actions, a hosted control plane, or
a semantic video codec. Those are separate product and research problems.

## Documentation conventions

- Times are integer milliseconds relative to the first displayed video timestamp.
- `asset_id` and job IDs are 32-character lowercase hexadecimal identifiers.
- A service token authenticates the whole local deployment; application users need an
  authorization layer in the developer's own backend.
- “Citation” means the answer points to sampled source evidence. It does not prove that
  the answer is correct or exhaustive.
- Examples use a local service and fake providers where appropriate. They are not model
  quality benchmarks.

## Documentation change rule

When a public behavior changes, update the relevant guide, example, generated OpenAPI/client
types, and tests in the same pull request. Keep operational notes in the issue or pull request
discussion rather than adding temporary plans or dated verification reports to the repository.
