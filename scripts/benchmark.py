"""Evaluate a prepared local model against labeled media; emits actual JSON measurements."""
import argparse
import json
import platform
import resource
import statistics
import time
from importlib.metadata import version
from pathlib import Path

from semantic_video_lake import VideoLake
from semantic_video_lake.adapters.siglip import SiglipEmbedder

parser = argparse.ArgumentParser()
parser.add_argument("corpus", type=Path)
parser.add_argument("--model", type=Path, required=True)
parser.add_argument("--index", type=Path, required=True)
parser.add_argument("--output", type=Path, required=True)
args = parser.parse_args()
if args.index.exists():
    parser.error("Use a fresh index directory to measure actual ingestion, not cache hits.")
queries = json.loads((args.corpus / "queries.json").read_text())
started = time.perf_counter()
embedder = SiglipEmbedder(args.model)
model_load_seconds = time.perf_counter() - started
latencies = []
hits1 = hits5 = 0
with VideoLake.open(args.index, embedder) as lake:
    started = time.perf_counter()
    assets = {}
    video_ms = 0
    for name in sorted({item["expected_file"] for item in queries}):
        source = (args.corpus / name).resolve()
        if not source.is_relative_to(args.corpus.resolve()):
            parser.error("Corpus paths must stay within the corpus directory.")
        asset = lake.ingest(source)
        assets[name] = asset
        video_ms += lake.status(asset)["duration_ms"]
    indexing_seconds = time.perf_counter() - started
    lake.search(queries[0]["query"])  # warm text inference
    details = []
    for item in queries:
        started = time.perf_counter()
        results = lake.search(item["query"], limit=5)
        latencies.append((time.perf_counter() - started) * 1000)
        relevant = [match.asset_id == assets[item["expected_file"]] and
                    item["start_ms"] <= match.timestamp_ms < item["end_ms"] for match in results]
        hits1 += bool(relevant and relevant[0])
        hits5 += any(relevant)
        details.append({"query": item["query"], "hit_at_1": bool(relevant and relevant[0]), "hit_at_5": any(relevant)})
rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
result = {
    "dataset": str(args.corpus), "query_count": len(queries), "video_seconds": video_ms / 1000,
    "platform": platform.platform(), "processor": platform.processor(),
    "versions": {name: version(name) for name in ["semantic-video-lake", "lancedb", "torch", "transformers"]},
    "model_fingerprint": embedder.fingerprint, "model_load_seconds": model_load_seconds,
    "indexing_seconds": indexing_seconds, "recall_at_1": hits1 / len(queries),
    "recall_at_5": hits5 / len(queries), "search_p50_ms": statistics.median(latencies),
    "search_p95_ms": sorted(latencies)[min(len(latencies) - 1, int(len(latencies) * 0.95))],
    "peak_rss_mib": rss / (1024 * 1024 if platform.system() == "Darwin" else 1024),
    "results": details,
}
args.output.parent.mkdir(parents=True, exist_ok=True)
args.output.write_text(json.dumps(result, indent=2) + "\n")
print(json.dumps({key: value for key, value in result.items() if key != "results"}, indent=2))
