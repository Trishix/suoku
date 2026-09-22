# Provider setup and observation recipes

Retrieval uses local embeddings. Insights are optional paid model calls with the operator's
key, one configured provider/model per deployment. The provider receives selected images
for observation generation and question/evidence text for answer generation. The alpha
accepts these LiteLLM routes:

| Provider | `SUOKU_INSIGHT_MODEL` form | Provider integration reference |
| --- | --- | --- |
| OpenAI | `openai/YOUR_MODEL_ID` | [LiteLLM OpenAI](https://docs.litellm.ai/docs/providers/openai) |
| Anthropic | `anthropic/YOUR_MODEL_ID` | [LiteLLM Anthropic](https://docs.litellm.ai/docs/providers/anthropic) |
| Google Gemini | `gemini/YOUR_MODEL_ID` | [LiteLLM Gemini](https://docs.litellm.ai/docs/providers/gemini) |
| Groq | `groq/YOUR_MODEL_ID` | [LiteLLM Groq](https://docs.litellm.ai/docs/providers/groq) |
| OpenRouter | `openrouter/UPSTREAM/YOUR_MODEL_ID` | [LiteLLM OpenRouter](https://docs.litellm.ai/docs/providers/openrouter) |

Replace placeholders with an available model that supports **images and structured JSON
Schema output**. A provider integration does not imply every model on that provider is
compatible. LiteLLM's capability metadata and the provider's current account/model access
must agree; an unrecognized model fails explicitly. There is no fixed default model and
no claim that an old model identifier remains available. Versioned model identifiers are
preferable to aliases for reproducible cache behavior. Live tests on all five providers
remain an opt-in release check, not a demonstrated benchmark.

```bash
# Interactive hidden key prompt; install .[insights] first.
suoku init --provider anthropic --model YOUR_MODEL_ID
# The fully prefixed form is also supported:
suoku init --model openrouter/UPSTREAM/YOUR_MODEL_ID
```

These are alternative initializations, not commands to run sequentially. Existing config
is protected from accidental replacement; deliberate replacement uses `--force`. For
automation, pipe a secret manager's output to `suoku init --model PROVIDER/MODEL
--provider-key-stdin`. Never put the key in command arguments. The resulting private
`.suoku/config.env` includes `SUOKU_INSIGHT_MODEL`, `SUOKU_PROVIDER_API_KEY`,
`SUOKU_API_TOKEN`, and `SUOKU_BASE_URL`. Commands accept `--config PATH`; process environment
overrides that file. Restart the worker after changing its model/key.

## Built-in recipes

Run `suoku recipes` or call `GET /v1/insight-recipes` to inspect the current prompts and
schemas. Each observation stores its recipe ID and fingerprint, provider fingerprint,
source hash, window, prompt version, and actual evidence timestamps.

| Recipe | Payload fields | Intended use and limits |
| --- | --- | --- |
| `general` | `scene`, `entities`, `activities`, `limitations` | Describe visible scenes and entities; no identity or intent inference. |
| `safety` | `ppe`, `potential_hazards`, `activities`, `limitations` | Illustrative visible equipment/conditions; not a compliance or risk assessment. |
| `warehouse` | `vehicles`, `dock_activity`, `handling`, `limitations` | Visible logistics context; not validated tracking, counts, or event detection. |

For footage you are authorized to process:

```bash
suoku analyze ASSET_ID --recipe safety --from 00:01:00 --to 00:01:15
suoku ask "What loading activity is visible?" --recipe warehouse --asset ASSET_ID
suoku observations ASSET_ID --recipe warehouse
```

The bundled animation is suitable for `general` and custom visible-object examples. It
cannot validate safety or warehouse performance. Recipe names do not add a temporal
tracking model: all claims must remain bounded by the sampled evidence.

## Custom JSON Schema

[visible-objects.json](../examples/recipes/visible-objects.json) is a complete recipe with
bounded arrays, string limits, enums, required properties, and `additionalProperties: false`:

```bash
suoku analyze ASSET_ID --recipe-file examples/recipes/visible-objects.json
suoku ask "Which animals are visible?" --asset ASSET_ID \
  --recipe-file examples/recipes/visible-objects.json
suoku observations ASSET_ID --recipe visible-objects
```

The same recipe object works in Python and as an inline `recipe` in HTTP/TypeScript:

```python
import json
from pathlib import Path
from suoku.insights import InsightRecipe

recipe = InsightRecipe(**json.loads(Path("examples/recipes/visible-objects.json").read_text()))
observations = insights.analyze(asset_id, recipe=recipe)
```

Custom recipes are supplied with each analysis/question; they are not registered in the
built-in recipe catalog. Increment the recipe's version when changing its meaning. Cache
fingerprints also include prompt and schema content, so editing either invalidates reuse
even if you forget to change the version. Filtering observations by ID can return more
than one recipe version; inspect `recipe_fingerprint` when comparing records.

Recipe payloads must be JSON objects. The accepted schema subset is intentionally bounded:

- JSON Schema draft 2020-12; primitive types, object `properties`, `required`,
  `additionalProperties`, array `items`, `enum`, and `const`.
- `minimum`, `maximum`, `exclusiveMinimum`, `exclusiveMaximum`, `minLength`, `maxLength`,
  `minItems`, and `maxItems`; annotations `title`, `description`, and `default`.
- Up to eight `anyOf` alternatives, plus acyclic root `$defs` references of the form
  `#/$defs/Name`, with simple alphanumeric, underscore, hyphen, or period names.
- A serialized schema limit of 32 KiB, traversal limits of 2,048 nodes and 16 levels,
  and a 64 KiB provider payload limit. Keep actual schemas substantially smaller.

Remote references, recursive references, regular-expression patterns, `allOf`, `oneOf`,
conditionals, and other unsupported keywords are rejected. Suoku does not fetch schema
URLs. Add explicit item/string bounds to keep outputs useful and affordable. Provider
structured-output implementations may impose additional restrictions on accepted schemas.

Pydantic v2 models can be converted with
`InsightRecipe.from_pydantic(Model, id="example", prompt="Describe visible evidence.")`.
Only exports fitting the subset above are accepted; recursive models, regex-constrained
strings, and unrestricted arbitrary schemas are not supported merely because Pydantic
can emit them.

The LiteLLM adapter builds a separate schema for strict provider output: it sets
`additionalProperties: false` on every object and requires every declared property,
including nested fields and fields with defaults. It removes `default` annotations
from that transport schema and represents typed `const` values as single-value enums.
The original recipe schema remains unchanged and validates the returned data locally.
Existing nullability is preserved: `Optional[str] = None` can return `null`, while
`count: int = 7` must return an integer. The adapter does not add `null` or fill in
default values. Use nullable fields explicitly when an observation may be unknown.

Strict provider transport requires objects with declared `properties`, typed fields
(or typed `anyOf` alternatives), and arrays with typed `items`. Explicitly open objects
(`additionalProperties: true`), arbitrary dictionaries using a schema in
`additionalProperties`, untyped or boolean subschemas, and a root `anyOf` are rejected
with `unsupported_schema` before any provider call. Some schemas accepted by local
validation therefore require a different custom provider or a more explicit recipe.

## Evidence and cache semantics

`ask` retrieves candidate windows (default maximum six), observes the selected frames on
demand, and reasons over those observations. `analyze` explicitly observes an asset/window.
Explicit analysis divides its interval into ten-second windows and accepts at most 100
windows per request; split longer archives into bounded requests.
The evidence extractor uses three actual timestamped frames per window, preserves aspect
ratio, and bounds the longest side to 768 pixels. Small objects, occlusion, events between
frames, and long sequences can be missed. Review `limitations` and `insufficient_evidence`.

Observations are cached by source, window, extractor, recipe, provider, and prompt identity.
Identical requests can reuse those observations; generating an answer can still require
a provider call. Deleting or replacing an asset prevents stale evidence from being served.
Do not interpret schema validation, cache hits, or valid citations as proof of correctness.
