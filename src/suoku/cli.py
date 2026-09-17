from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import sqlite3
from pathlib import Path

from .types import LakeError


def main():
    parser = argparse.ArgumentParser(prog="suoku")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser(
        "doctor", help="Print runtime and decoder availability; never downloads models"
    )
    prepare = commands.add_parser(
        "prepare-model", help="Explicitly download a pinned SigLIP revision"
    )
    prepare.add_argument("directory", type=Path)
    prepare.add_argument("--revision", required=True)
    server = commands.add_parser("serve", help="Run API only; start worker separately")
    server.add_argument("--data", type=Path, default=Path(".lake"))
    server.add_argument("--host", default="127.0.0.1")
    server.add_argument("--port", type=int, default=8000)
    worker = commands.add_parser("worker", help="Run offline processing worker")
    worker.add_argument("--data", type=Path, default=Path(".lake"))
    worker.add_argument("--model", type=Path, required=True)
    worker.add_argument("--device", default="cpu", choices=["cpu", "cuda", "mps"])
    args = parser.parse_args()
    try:
        if args.command == "doctor":
            print(
                json.dumps(
                    {
                        "python": platform.python_version(),
                        "platform": platform.platform(),
                        "sqlite": sqlite3.sqlite_version,
                        "ffmpeg": shutil.which("ffmpeg"),
                        "ffprobe": shutil.which("ffprobe"),
                        "cwd_writable": os.access(Path.cwd(), os.W_OK),
                        "formats": ["MP4/MOV H.264", "WebM VP8/VP9"],
                    },
                    indent=2,
                )
            )
        elif args.command == "prepare-model":
            from .adapters.siglip import prepare_model

            print(prepare_model(args.directory, revision=args.revision))
        elif args.command == "serve":
            import uvicorn

            from .server.app import Settings, create_app

            token = os.environ.get("SUOKU_API_TOKEN", "")
            uvicorn.run(
                create_app(Settings(args.data, token)),
                host=args.host,
                port=args.port,
                access_log=False,
                limit_concurrency=32,
            )
        else:
            from .adapters.siglip import SiglipEmbedder
            from .server.worker import Worker

            Worker(args.data, SiglipEmbedder(args.model, device=args.device)).run()
    except KeyboardInterrupt:
        pass
    except (LakeError, ValueError, ImportError, OSError) as exc:
        parser.exit(1, f"suoku: {exc}\n")


if __name__ == "__main__":
    main()
