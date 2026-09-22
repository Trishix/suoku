# Suoku documentation

Suoku is an alpha developer tool, so the documentation separates what the software does
today from ideas that may be useful later. Start with the path that matches how you plan
to use it:

| Goal | Read |
| --- | --- |
| Install it and run a first local sample | [Getting started](../README.md#run-the-sample) |
| Embed it in a Node.js/TypeScript web application | [Web-app integration](web-app-integration.md) |
| Call it from another language | [HTTP API](api.md) |
| Use the Python API directly | [Python API](../examples/insights.py) and [contracts](contracts.md) |
| Configure providers and custom schemas | [Recipes and providers](recipes.md) |
| Understand storage and process boundaries | [Architecture](architecture.md) |
| Diagnose setup or runtime failures | [Troubleshooting](troubleshooting.md) |
| Understand tokens, environment, and model setup | [Configuration](configuration.md) |
| Deploy or publish a release | [Release checklist](releasing.md) |
| Evaluate answer quality | [Evaluation guide](evaluation.md) |

## Scope of this alpha

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
