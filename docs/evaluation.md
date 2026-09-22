# Evaluation and pilot checklist

No semantic accuracy, latency, cloud cost, safety-detection, or production-scale results
are claimed for this alpha. Fill in measurements from your own authorized test data.
The bundled animation demonstrates plumbing; the generated color benchmark measures
simple retrieval behavior. Neither establishes CCTV, warehouse, or workplace-safety accuracy.

## Define a test before tuning

Choose one narrow task and write what would count as a supported answer. Record camera
placement, resolution, lighting, compression, occlusion, clip duration, and relevant object
size. Separate training/tuning clips from held-out evaluation clips. Include empty scenes,
ambiguous scenes, near misses, events outside sampled windows, and questions that should
receive an insufficient-evidence response.

Create a manifest of approved clips and human-reviewed intervals. Have a second reviewer
check ambiguous labels. Keep people, locations, and identities out of labels unless they
are needed, authorized, and appropriate to the task. Do not label an inferred intention
or regulatory conclusion as directly observed ground truth.

Record the Suoku commit, dependency versions, OS/hardware, FFmpeg version, retrieval model
revision/fingerprint, sample interval, recipe JSON and fingerprint, provider model/version,
candidate limit, and evidence settings. Note whether model aliases or provider routing can
change between runs. Never include keys or private media in public result artifacts.

## Measure each stage

| Measure | How to record it | Alpha result |
| --- | --- | --- |
| Retrieval Recall@1 / Recall@5 | Fraction of queries whose labeled evidence interval appears in the top results | Not measured on real footage |
| Grounded observation precision | Human-supported payload statements / reviewed statements | Not measured |
| Event recall | Labeled relevant events with supported observations / all relevant events | Not measured |
| Citation validity | Asset/window resolves, overlaps labeled evidence, and supports the statement | Not measured semantically |
| Abstention behavior | Correct abstentions and unsupported answers on no-evidence queries | Not measured |
| Runtime | Model load, ingest throughput, ask p50/p95, analysis latency, peak RAM/disk | Hardware-specific; not measured |
| Provider cost | Calls, actual billed usage, retries, cost per clip/question, warm-cache savings | Provider/account-specific; not measured |
| Recovery | Job behavior after cancellation/restart, stale worker, source change, and provider failure | Verify in the target deployment |

Separate a technically valid citation from one that actually supports the answer. Review
the cited footage rather than scoring only text similarity. Report sample counts and
uncertainty, per-condition failures, and negatives; a single aggregate score can conceal
camera-specific failures.

Measure cold and warm runs separately. Repeating `ask` may reuse observations but still
call the model for reasoning. Count repair/retry calls and provider errors when recording
latency and cost. Set provider account spending limits before running larger evaluations.

The existing `scripts/make_benchmark.py` and `scripts/benchmark.py` exercise synthetic
color retrieval. Use their command help to select paths/model options. Keep those results
separate from any human-reviewed semantic evaluation.

## Pilot acceptance

- [ ] Authorized footage, retention period, storage location, and provider handling agreed.
- [ ] Narrow question/recipe scope and false-positive/false-negative costs documented.
- [ ] Held-out footage covers actual cameras and adverse conditions.
- [ ] Human reviewer checks cited playback and abstentions; no automated consequential action.
- [ ] Numerical acceptance thresholds agreed **before** reading test results.
- [ ] Model/recipe versions and reproducible environment recorded.
- [ ] Budget, rate limits, worker health, cancellation, and restart behavior tested.
- [ ] Source replacement, removal, purge, export, and backup retention verified.
- [ ] Service token stays on the backend; remote transport/auth and container isolation reviewed.
- [ ] A responsible operator, rollback path, and incident/reporting route are assigned.

The safety recipe is illustrative. A pilot using it does not establish legal compliance,
replace a trained safety professional, or justify safety-critical automated decisions.
Publish only measured results and describe the evaluation population and limits.
