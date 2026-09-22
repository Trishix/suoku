"""Create an original, redistributable toy corpus and 50 labeled visual queries.

This exercises color retrieval, not actions, object detection or CCTV accuracy.
"""
import argparse
import json
import subprocess
from pathlib import Path

COLORS = ["red", "blue", "green", "yellow", "orange", "purple", "white", "black", "pink", "brown"]
PROMPTS = ["a solid {color} background", "a {color} screen", "the color {color}", "an entirely {color} image", "a plain {color} surface"]

parser = argparse.ArgumentParser()
parser.add_argument("directory", type=Path)
args = parser.parse_args()
# The generated corpus is synthetic and redistributable, making local smoke tests
# reproducible without shipping private or copyrighted surveillance footage.
args.directory.mkdir(parents=True, exist_ok=True)
queries = []
for color in COLORS:
    path = args.directory / f"{color}.mp4"
    if not path.exists():
        subprocess.run([
            "ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i",
            f"color=c={color}:s=320x240:r=10", "-t", "5", "-c:v", "libx264",
            "-pix_fmt", "yuv420p", str(path),
        ], check=True)
    for prompt in PROMPTS:
        queries.append({"query": prompt.format(color=color), "expected_file": path.name,
                        "start_ms": 0, "end_ms": 5000})
(args.directory / "queries.json").write_text(json.dumps(queries, indent=2) + "\n")
print(f"Created 10 synthetic videos and {len(queries)} queries; no real-world accuracy claim.")
