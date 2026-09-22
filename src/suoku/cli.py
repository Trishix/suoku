from __future__ import annotations

import argparse
import getpass
import json
import re
import secrets
import sys
import warnings
from datetime import datetime
from pathlib import Path

from .config import DEFAULT_CONFIG, DEFAULT_URL, ConfigError, load_config, write_config
from .errors import LakeError
from .http_client import ClientError, HTTPClient, identifier, validate_url, validate_wait_timeout


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="suoku")
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG,
        help="Config path (default: .suoku/config.env); process environment wins",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    doctor = commands.add_parser(
        "doctor", help="Offline dependency, decoder, permission, and optional model checks"
    )
    doctor.add_argument("--data", type=Path)
    doctor.add_argument("--model", type=Path)
    setup = commands.add_parser(
        "init", help="Create private configuration; never downloads or calls a provider"
    )
    setup.add_argument(
        "--provider", choices=["openai", "anthropic", "gemini", "groq", "openrouter"],
        help="Provider prefix; supports nested upstream model identifiers",
    )
    setup.add_argument("--model", help="Provider/model identifier; required when noninteractive")
    setup.add_argument("--provider-key-stdin", "--key-stdin", action="store_true")
    setup.add_argument(
        "--force",
        action="store_true",
        help="Replace existing config, generating a new service token",
    )
    setup.add_argument("--url", default=DEFAULT_URL)
    prepare = commands.add_parser(
        "prepare-model", help="Explicitly download a pinned SigLIP revision"
    )
    prepare.add_argument("directory", type=Path)
    prepare.add_argument("--revision", required=True)
    server = commands.add_parser("serve", help="Run API only; start worker separately")
    server.add_argument("--data", type=Path, default=Path(".lake"))
    server.add_argument("--host", default="127.0.0.1")
    server.add_argument("--port", type=int, default=8000)
    worker = commands.add_parser(
        "worker", help="Run processing worker, with insights when configured"
    )
    worker.add_argument("--data", type=Path, default=Path(".lake"))
    worker.add_argument("--model", type=Path, required=True)
    worker.add_argument("--device", default="cpu", choices=["cpu", "cuda", "mps"])
    upload = commands.add_parser(
        "upload", help="Stream a video to the service and wait for indexing"
    )
    upload.add_argument("path", type=Path)
    upload.add_argument("--camera")
    upload.add_argument("--recorded-at", help="Recording date/time in ISO 8601 with a timezone")
    ask = commands.add_parser("ask", help="Ask a question using retrieved video evidence")
    ask.add_argument("question")
    ask.add_argument(
        "--candidate-limit",
        type=int,
        choices=range(1, 21),
        default=6,
        metavar="N",
        help="Maximum evidence candidates, from 1 to 20 (default: 6)",
    )
    ask.add_argument(
        "--asset", action="append", help="Restrict to this asset; repeat for multiple assets"
    )
    ask.add_argument("--camera")
    ask.add_argument(
        "--from", dest="start", help="Recording date/time lower bound, ISO 8601 with timezone"
    )
    ask.add_argument(
        "--to", dest="end", help="Recording date/time upper bound, ISO 8601 with timezone"
    )
    analyze = commands.add_parser(
        "analyze", help="Analyze selected evidence from an asset or time window"
    )
    analyze.add_argument("asset")
    analyze.add_argument(
        "--from", dest="start", default="00:00:00", help="Clip-relative HH:MM:SS[.mmm]"
    )
    analyze.add_argument("--to", dest="end", help="Clip-relative HH:MM:SS[.mmm]")
    for command in (ask, analyze):
        recipe = command.add_mutually_exclusive_group()
        recipe.add_argument("--recipe", default="general", help="Recipe ID (default: general)")
        recipe.add_argument(
            "--recipe-file", type=Path, help="Local JSON recipe with a bounded JSON Schema"
        )
    observations = commands.add_parser("observations", help="Read cached observations for an asset")
    observations.add_argument("asset")
    observations.add_argument("--recipe")
    recipes = commands.add_parser("recipes", help="List built-in insight recipes")
    status = commands.add_parser("status", help="Show worker availability and insight readiness")
    for command in (upload, ask, analyze):
        command.add_argument(
            "--no-wait", action="store_true", help="Return the durable job immediately"
        )
        command.add_argument(
            "--wait-timeout",
            type=float,
            default=300,
            help="Maximum polling time in seconds (default: 300)",
        )
    for command in (upload, ask, analyze, observations, recipes, status):
        command.add_argument("--url", help="Service base URL; overrides SUOKU_BASE_URL")
        command.add_argument(
            "--timeout",
            type=float,
            default=30,
            help="HTTP request timeout in seconds, at most 300 (default: 30)",
        )
        command.add_argument(
            "--json", action="store_true", help="Print machine-readable JSON results or errors"
        )
    for command in commands.choices.values():
        command.add_argument(
            "--config",
            type=Path,
            default=argparse.SUPPRESS,
            help="Explicit config path (default: .suoku/config.env); process environment wins",
        )
    return parser


def _initialize(args) -> None:
    model = args.model
    if not model:
        if not sys.stdin.isatty():
            raise ConfigError("Supply --model PROVIDER/MODEL for noninteractive setup.")
        model = input("Vision model (provider/model): ").strip()
    if args.provider and not model.startswith(args.provider + "/"):
        model = args.provider + "/" + model
    if (len(model) > 200 or model.endswith("/") or not re.fullmatch(
            r"(?:openai|anthropic|gemini|groq|openrouter)/[\w./:\-]+", model)):
        raise ConfigError(
            "Supply a supported provider/model identifier, or --provider and --model."
        )
    url = validate_url(args.url)
    if args.provider_key_stdin:
        key = sys.stdin.read(8194)
        if key.endswith("\n"):
            key = key[:-1]
    else:
        if not sys.stdin.isatty():
            raise ConfigError(
                "Use --provider-key-stdin to supply a key without an interactive terminal."
            )
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", getpass.GetPassWarning)
                key = getpass.getpass("Provider API key (hidden): ")
        except getpass.GetPassWarning:
            raise ConfigError(
                "Cannot securely hide keyboard input. Use --provider-key-stdin instead."
            ) from None
    if not key:
        raise ConfigError("A provider API key is required.")
    write_config(
        args.config,
        {
            "SUOKU_API_TOKEN": secrets.token_urlsafe(32),
            "SUOKU_INSIGHT_MODEL": model,
            "SUOKU_PROVIDER_API_KEY": key,
            "SUOKU_BASE_URL": url,
        },
        force=args.force,
    )
    print("Private configuration saved. Next: suoku doctor; start serve and worker separately.")


def _recipe(args):
    if args.recipe_file is None:
        return args.recipe
    try:
        with args.recipe_file.open("r", encoding="utf-8") as stream:
            data = stream.read(65537)
        if len(data) > 65536:
            raise ValueError
        recipe = json.loads(data)
        if not isinstance(recipe, dict):
            raise ValueError
        return recipe
    except (OSError, ValueError, UnicodeError):
        raise ClientError(
            "invalid_recipe",
            "Use --recipe-file with a valid JSON recipe object no larger than 64 KiB.",
        ) from None


def _clip_ms(value: str) -> int:
    match = re.fullmatch(r"(\d{2,}):([0-5]\d):([0-5]\d)(?:\.(\d{1,3}))?", value)
    if match is None:
        raise ClientError("invalid_time", "Clip times must use HH:MM:SS or HH:MM:SS.mmm.")
    hours, minutes, seconds, fraction = match.groups()
    return (int(hours) * 3600 + int(minutes) * 60 + int(seconds)) * 1000 + int(
        (fraction or "0").ljust(3, "0")
    )


def _recording_time(value):
    if value is not None:
        try:
            if datetime.fromisoformat(value.replace("Z", "+00:00")).tzinfo is None:
                raise ValueError
        except ValueError:
            raise ClientError(
                "invalid_time",
                "Recording timestamps must be ISO 8601 with a timezone, such as 2026-01-01T00:00:00Z.",
            ) from None
    return value


def _http_command(args, config):
    if hasattr(args, "wait_timeout"):
        validate_wait_timeout(args.wait_timeout)
    client = HTTPClient(
        args.url or config["SUOKU_BASE_URL"],
        config.get("SUOKU_API_TOKEN", ""),
        timeout=args.timeout,
    )
    if args.command == "upload":
        job = client.upload(
            args.path, camera_id=args.camera, recorded_at=_recording_time(args.recorded_at)
        )
    elif args.command == "ask":
        body = {
            "question": args.question,
            "recipe": _recipe(args),
            "candidate_limit": args.candidate_limit,
        }
        if args.asset:
            body["asset_ids"] = [identifier(asset) for asset in args.asset]
        filters = {
            key: value
            for key, value in {
                "camera_id": args.camera,
                "recorded_after": _recording_time(args.start),
                "recorded_before": _recording_time(args.end),
            }.items()
            if value is not None
        }
        if filters:
            body["filters"] = filters
        job = client.request("POST", "/v1/answers", body=body)
    elif args.command == "analyze":
        body = {
            "asset_id": identifier(args.asset),
            "recipe": _recipe(args),
            "start_ms": _clip_ms(args.start),
        }
        if args.end is not None:
            body["end_ms"] = _clip_ms(args.end)
            if body["end_ms"] <= body["start_ms"]:
                raise ClientError("invalid_time", "The --to clip time must be greater than --from.")
        job = client.request("POST", "/v1/analyses", body=body)
    elif args.command == "observations":
        from urllib.parse import urlencode

        path = "/v1/videos/" + identifier(args.asset) + "/observations"
        if args.recipe:
            path += "?" + urlencode({"recipe": args.recipe})
        return client.request("GET", path)
    else:
        return client.request(
            "GET", "/v1/status" if args.command == "status" else "/v1/insight-recipes"
        )
    if args.no_wait:
        if isinstance(job, dict) and job.get("state") in {"failed", "cancelled"}:
            client.wait(job, timeout=args.wait_timeout)
        return job
    progress = (
        None
        if args.json
        else lambda job_id, state: print(f"Job {job_id}: {state}", file=sys.stderr)
    )
    result = client.wait(job, timeout=args.wait_timeout, progress=progress)
    key = {"ask": "answer", "analyze": "observations"}.get(args.command)
    if key is None:
        return result
    if key not in result or not isinstance(result[key], dict if key == "answer" else list):
        raise ClientError(
            "invalid_result", "The completed job has no expected result. Check the server version."
        )
    return result[key]


def _print_result(result, *, json_output=False):
    if not json_output and isinstance(result, dict) and isinstance(result.get("answer"), str):
        print(result["answer"])
        for citation in result.get("citations", []):
            print(
                f"  Source: {citation['asset_id']} [{citation['start_ms']}–{citation['end_ms']} ms] {citation.get('reason', '')}"
            )
        for limitation in result.get("limitations", []):
            print("  Limitation: " + limitation)
    else:
        print(json.dumps(result, indent=2))


def main():
    parser = _parser()
    args = parser.parse_args()
    try:
        if args.command == "init":
            _initialize(args)
            return
        config = load_config(args.config)
        if args.command == "doctor":
            from .diagnostics import diagnose

            report = diagnose(
                model=args.model, data=args.data, insights=bool(config.get("SUOKU_INSIGHT_MODEL"))
            )
            print(json.dumps(report, indent=2))
            if not report["ok"]:
                raise SystemExit(1)
        elif args.command == "prepare-model":
            from .adapters.siglip import prepare_model

            print(prepare_model(args.directory, revision=args.revision))
        elif args.command == "serve":
            import uvicorn

            from .server.app import Settings, create_app

            token = config.get("SUOKU_API_TOKEN", "")
            uvicorn.run(
                create_app(Settings(args.data, token)),
                host=args.host,
                port=args.port,
                access_log=False,
                limit_concurrency=32,
            )
        elif args.command == "worker":
            from .adapters.siglip import SiglipEmbedder
            from .server.worker import Worker

            provider = None
            model, key = config.get("SUOKU_INSIGHT_MODEL"), config.get("SUOKU_PROVIDER_API_KEY")
            if bool(model) != bool(key):
                raise ConfigError(
                    "Set both SUOKU_INSIGHT_MODEL and SUOKU_PROVIDER_API_KEY, or unset both for offline retrieval."
                )
            if model and key:
                from .providers import LiteLLMProvider

                provider = LiteLLMProvider(model, key)
            Worker(
                args.data, SiglipEmbedder(args.model, device=args.device), provider=provider
            ).run()
        else:
            _print_result(_http_command(args, config), json_output=args.json)
    except KeyboardInterrupt:
        parser.exit(130, "suoku: Interrupted. Submitted jobs may continue on the server.\n")
    except (ConfigError, ClientError, LakeError) as exc:
        if args.command == "doctor":
            print(
                json.dumps(
                    {
                        "ok": False,
                        "checks": [
                            {
                                "name": "configuration",
                                "ok": False,
                                "detail": str(exc),
                                "fix": "Check --config and its permissions; use suoku init to create a private configuration.",
                            }
                        ],
                    }
                )
            )
            parser.exit(1)
        if getattr(args, "json", False):
            print(
                json.dumps(
                    {
                        "error": {
                            "code": getattr(exc, "code", "configuration_error"),
                            "message": str(exc),
                        }
                    }
                )
            )
            parser.exit(1)
        parser.exit(1, f"suoku: {exc}\n")
    except (ValueError, ImportError, OSError, EOFError):
        message = (
            "Command failed. Run suoku doctor and check paths, dependencies, and configuration."
        )
        if args.command == "doctor":
            print(
                json.dumps(
                    {
                        "ok": False,
                        "checks": [
                            {
                                "name": "runtime",
                                "ok": False,
                                "detail": "A diagnostic check could not complete.",
                                "fix": "Check file permissions and reinstall the required Suoku extras.",
                            }
                        ],
                    }
                )
            )
            parser.exit(1)
        if getattr(args, "json", False):
            print(json.dumps({"error": {"code": "command_failed", "message": message}}))
            parser.exit(1)
        parser.exit(1, f"suoku: {message}\n")


if __name__ == "__main__":
    main()
