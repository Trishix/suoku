"""Run local retrieval and optional paid visual insights on the attributed sample.

From the repository root, after model preparation and `suoku init`:
    python examples/insights.py --model .models/siglip
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from suoku import VideoLake
from suoku.adapters.siglip import SiglipEmbedder
from suoku.config import load_config
from suoku.insights import InsightEngine
from suoku.providers import LiteLLMProvider

# This example intentionally uses the same public engine API as an application would;
# its generated lake is disposable and kept outside the repository's main .lake.


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, required=True, help="Prepared local SigLIP directory")
    parser.add_argument("--config", type=Path, default=Path(".suoku/config.env"))
    parser.add_argument("--index", type=Path, default=Path(".example-lake/index"))
    parser.add_argument("--video", type=Path, default=Path(__file__).parent / "media/big-buck-bunny-15s.mp4")
    parser.add_argument("--question", default="What animal is visible outdoors?")
    args = parser.parse_args()

    config = load_config(args.config)
    model = config.get("SUOKU_INSIGHT_MODEL", "")
    key = config.get("SUOKU_PROVIDER_API_KEY", "")
    if not model or not key:
        parser.error("Run suoku init or set SUOKU_INSIGHT_MODEL and SUOKU_PROVIDER_API_KEY.")

    source = args.video.resolve(strict=True)
    provider = LiteLLMProvider(model=model, api_key=key)
    embedder = SiglipEmbedder(args.model)
    with VideoLake.open(args.index, embedder) as lake:
        asset_id = lake.ingest(source, camera_id="demo")
        insights = InsightEngine(lake, provider)

        # ask retrieves evidence on demand, so there is no required full-video analysis.
        answer = insights.ask(args.question, asset_ids=[asset_id], recipe="general")
        print(json.dumps({"asset_id": asset_id, "answer": asdict(answer)}, indent=2))

        # Explicit analysis is useful when an application needs structured records.
        end_ms = min(15_000, int(lake.status(asset_id)["duration_ms"]))
        observations = insights.analyze(asset_id, recipe="general", start_ms=0, end_ms=end_ms)
        print(json.dumps({"observations": [asdict(item) for item in observations]}, indent=2))
        cached = insights.observations(asset_id, recipe="general")
        print(f"Stored general observations: {len(cached)}")

        # Local playback needs no HTTP token. Seek to these offsets in a local player.
        for citation in answer.citations:
            print(json.dumps({
                "asset_id": citation.asset_id,
                "playback_file": str(source),
                "start_seconds": citation.start_ms / 1000,
                "end_seconds": citation.end_ms / 1000,
                "reason": citation.reason,
            }))
        if answer.insufficient_evidence:
            print("The model reported insufficient evidence. Review its limitations.")


if __name__ == "__main__":
    main()
