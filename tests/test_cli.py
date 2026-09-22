import hashlib
import io
import json
import os
import stat
import subprocess
import sys
import urllib.request
from http.client import IncompleteRead
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

import pytest

from suoku import cli


def run_cli(monkeypatch, *args):
    monkeypatch.setattr(sys, "argv", ["suoku", *map(str, args)])
    cli.main()


def initialize(monkeypatch, tmp_path, key="sk-private-key"):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "stdin", io.StringIO(key + "\n"))
    run_cli(
        monkeypatch,
        "init",
        "--provider",
        "openai",
        "--model",
        "vision-test",
        "--provider-key-stdin",
    )


def test_init_keeps_secrets_private_and_does_not_overwrite(monkeypatch, tmp_path, capsys):
    initialize(monkeypatch, tmp_path)
    config = tmp_path / ".suoku/config.env"
    before = config.read_bytes()
    assert stat.S_IMODE(config.stat().st_mode) == 0o600
    assert stat.S_IMODE(config.parent.stat().st_mode) == 0o700
    assert ".suoku/" in (tmp_path / ".gitignore").read_text().splitlines()
    assert "sk-private-key" not in capsys.readouterr().out
    with pytest.raises(SystemExit) as error:
        initialize(monkeypatch, tmp_path, "replacement-secret")
    assert error.value.code == 1
    assert config.read_bytes() == before
    monkeypatch.setattr(sys, "stdin", io.StringIO("replacement-secret\n"))
    run_cli(monkeypatch, "init", "--model", "openai/vision-test", "--provider-key-stdin", "--force")
    assert "replacement-secret" in config.read_text()
    assert stat.S_IMODE(config.stat().st_mode) == 0o600


def test_config_loading_is_literal_explicit_and_environment_wins(monkeypatch, tmp_path):
    secret = "sk-$NOT_EXPANDED-$(touch danger)-'\""
    initialize(monkeypatch, tmp_path, secret)
    from suoku.config import load_config

    before = dict(os.environ)
    loaded = load_config(tmp_path / ".suoku/config.env", environ={"SUOKU_API_TOKEN": "override"})
    assert loaded["SUOKU_PROVIDER_API_KEY"] == secret
    assert loaded["SUOKU_API_TOKEN"] == "override"
    assert loaded["SUOKU_INSIGHT_MODEL"] == "openai/vision-test"
    assert loaded["SUOKU_BASE_URL"] == "http://127.0.0.1:8000"
    assert dict(os.environ) == before
    assert not (tmp_path / "danger").exists()


@pytest.mark.parametrize("target", ["directory", "config", "gitignore"])
def test_init_rejects_symlinks_without_touching_targets(monkeypatch, tmp_path, target):
    outside = tmp_path / "outside"
    outside.mkdir()
    protected = outside / "existing"
    protected.write_text("keep this")
    if target == "directory":
        (tmp_path / ".suoku").symlink_to(outside, target_is_directory=True)
    elif target == "config":
        (tmp_path / ".suoku").mkdir()
        (tmp_path / ".suoku/config.env").symlink_to(protected)
    else:
        (tmp_path / ".gitignore").symlink_to(protected)
    with pytest.raises(SystemExit) as error:
        initialize(monkeypatch, tmp_path)
    assert error.value.code == 1
    assert protected.read_text() == "keep this"
    assert not (outside / "config.env").exists()


def test_init_rejects_multiline_key_without_persisting_it(monkeypatch, tmp_path, capsys):
    with pytest.raises(SystemExit) as error:
        initialize(monkeypatch, tmp_path, "secret\nSUOKU_API_TOKEN=injected")
    assert error.value.code == 1
    assert not (tmp_path / ".suoku/config.env").exists()
    assert "secret" not in capsys.readouterr().err


@pytest.mark.parametrize("provider,model,expected", [
    ("openrouter", "google/vision-test", "openrouter/google/vision-test"),
    ("groq", "meta-llama/vision-test", "groq/meta-llama/vision-test"),
    ("openrouter", "openai/vision-test", "openrouter/openai/vision-test"),
    ("openai", "openai/vision-test", "openai/vision-test"),
])
def test_init_qualifies_nested_provider_models(monkeypatch, tmp_path, provider, model, expected):
    from suoku.config import load_config

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "stdin", io.StringIO("fixture-key\n"))
    run_cli(monkeypatch, "init", "--provider", provider, "--model", model, "--key-stdin")
    assert load_config(environ={})["SUOKU_INSIGHT_MODEL"] == expected


@pytest.mark.parametrize("model", ["unknown/vision", "openai/", "openai/model?api_key=secret"])
def test_init_rejects_unsupported_routes_before_prompt(monkeypatch, tmp_path, model, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "stdin", io.StringIO("fixture-key\n"))
    with pytest.raises(SystemExit) as caught:
        run_cli(monkeypatch, "init", "--model", model, "--key-stdin")
    assert caught.value.code != 0
    assert not (tmp_path / ".suoku/config.env").exists()
    assert "secret" not in capsys.readouterr().err


def test_doctor_reports_missing_dependencies_without_importing_model(monkeypatch, tmp_path, capsys):
    import importlib.util

    original = importlib.util.find_spec
    monkeypatch.setattr(
        importlib.util, "find_spec", lambda name: None if name == "lancedb" else original(name)
    )
    model_imported = "transformers" in sys.modules
    with pytest.raises(SystemExit) as error:
        run_cli(monkeypatch, "doctor", "--data", tmp_path)
    assert error.value.code == 1
    report = json.loads(capsys.readouterr().out)
    missing = next(check for check in report["checks"] if check["name"] == "dependency:lancedb")
    assert missing["ok"] is False
    assert "install" in missing["fix"]
    assert ("transformers" in sys.modules) == model_imported


def test_doctor_hashes_local_model_without_loading_or_downloading(monkeypatch, tmp_path, capsys):
    model = tmp_path / "model"
    model.mkdir()
    weights = model / "weights.safetensors"
    weights.write_bytes(b"changed weights")
    (model / "suoku-manifest.json").write_text(
        json.dumps(
            {
                "model": "google/siglip-base-patch16-224",
                "revision": "a" * 40,
                "files": {"weights.safetensors": hashlib.sha256(b"original weights").hexdigest()},
            }
        )
    )
    with pytest.raises(SystemExit) as error:
        run_cli(monkeypatch, "doctor", "--model", model, "--data", tmp_path)
    assert error.value.code == 1
    report = json.loads(capsys.readouterr().out)
    integrity = next(check for check in report["checks"] if check["name"] == "model_integrity")
    assert not integrity["ok"]
    assert "prepare-model" in integrity["fix"]


@pytest.fixture
def transport(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SUOKU_API_TOKEN", "t" * 32)
    monkeypatch.setenv("SUOKU_BASE_URL", "http://localhost:8000")
    seen = []
    responses = []

    class Transport:
        def open(self, request, *, timeout):
            assert 0 < timeout <= 300
            body = request.data
            streamed = body is not None and not isinstance(body, bytes)
            if streamed:
                body = b"".join(body)
            seen.append(
                {
                    "method": request.get_method(),
                    "url": request.full_url,
                    "headers": dict(request.header_items()),
                    "body": body,
                    "streamed": streamed,
                }
            )
            response = responses.pop(0)
            if isinstance(response, Exception):
                raise response
            return io.BytesIO(json.dumps(response).encode())

    monkeypatch.setattr(urllib.request, "build_opener", lambda *handlers: Transport())
    return responses, seen


def job(state, *, result=None, error=None):
    return {
        "id": "b" * 32,
        "kind": "answer",
        "state": state,
        "progress": 100 if state == "succeeded" else 0,
        "result": result,
        "error": error,
        "created_at": 1.0,
        "updated_at": 1.0,
    }


def test_http_status_uses_explicit_url_and_environment_token(monkeypatch, transport, capsys):
    responses, seen = transport
    responses.append(
        {"worker_available": True, "insights_ready": True, "insight_model": "test/vision"}
    )
    run_cli(monkeypatch, "status", "--url", "https://example.test", "--json")
    assert json.loads(capsys.readouterr().out)["insights_ready"]
    assert seen[0]["url"] == "https://example.test/v1/status"
    assert seen[0]["headers"]["Authorization"] == "Bearer " + "t" * 32


def test_upload_streams_file_and_waits_for_durable_ingestion(
    monkeypatch, transport, tmp_path, capsys
):
    responses, seen = transport
    source = tmp_path / "camera clip.mp4"
    source.write_bytes(b"video" * 400000)
    responses.extend([job("queued"), job("succeeded", result={"asset_id": "a" * 32})])
    run_cli(monkeypatch, "upload", source, "--camera", "entrance", "--json")
    assert json.loads(capsys.readouterr().out)["asset_id"] == "a" * 32
    assert seen[0]["streamed"]
    assert len(seen[0]["body"]) == 2000000
    assert seen[0]["headers"]["Content-length"] == "2000000"
    assert parse_qs(urlsplit(seen[0]["url"]).query) == {
        "filename": ["camera clip.mp4"],
        "camera_id": ["entrance"],
    }
    assert seen[1]["url"].endswith("/v1/jobs/" + "b" * 32)


def test_ask_filters_and_inline_recipe_reach_answer_endpoint(
    monkeypatch, transport, tmp_path, capsys
):
    responses, seen = transport
    recipe = {
        "id": "custom",
        "version": "1",
        "name": "Custom",
        "description": "Test",
        "prompt": "Describe",
        "schema": {"type": "object"},
    }
    file = tmp_path / "recipe.json"
    file.write_text(json.dumps(recipe))
    answer = {
        "answer": "A person arrived.",
        "citations": [],
        "limitations": ["Sampled frames"],
        "insufficient_evidence": False,
    }
    responses.append(job("succeeded", result={"answer": answer}))
    run_cli(
        monkeypatch,
        "ask",
        "Who arrived?",
        "--recipe-file",
        file,
        "--asset",
        "a" * 32,
        "--camera",
        "entrance",
        "--from",
        "2026-01-01T00:00:00Z",
        "--to",
        "2026-01-02T00:00:00Z",
        "--json",
    )
    assert json.loads(capsys.readouterr().out) == answer
    assert seen[0]["url"].endswith("/v1/answers")
    assert json.loads(seen[0]["body"]) == {
        "question": "Who arrived?",
        "candidate_limit": 6,
        "recipe": recipe,
        "asset_ids": ["a" * 32],
        "filters": {
            "camera_id": "entrance",
            "recorded_after": "2026-01-01T00:00:00Z",
            "recorded_before": "2026-01-02T00:00:00Z",
        },
    }


def test_analyze_converts_clip_times_to_milliseconds(monkeypatch, transport, capsys):
    responses, seen = transport
    responses.append(job("succeeded", result={"observations": [{"summary": "Door opened"}]}))
    run_cli(
        monkeypatch,
        "analyze",
        "a" * 32,
        "--from",
        "00:01:02.345",
        "--to",
        "00:02:00",
        "--recipe",
        "safety",
        "--json",
    )
    assert json.loads(capsys.readouterr().out) == [{"summary": "Door opened"}]
    assert json.loads(seen[0]["body"]) == {
        "asset_id": "a" * 32,
        "start_ms": 62345,
        "end_ms": 120000,
        "recipe": "safety",
    }


@pytest.mark.parametrize(
    "command,endpoint",
    [
        ("recipes", "/v1/insight-recipes"),
        ("observations", "/v1/videos/" + "a" * 32 + "/observations"),
    ],
)
def test_read_commands_accept_list_responses(monkeypatch, transport, capsys, command, endpoint):
    responses, seen = transport
    responses.append([{"id": "item"}])
    args = [command] + (["a" * 32] if command == "observations" else [])
    run_cli(monkeypatch, *args, "--json")
    assert json.loads(capsys.readouterr().out) == [{"id": "item"}]
    assert seen[0]["url"].endswith(endpoint)


@pytest.mark.parametrize("state", ["failed", "cancelled"])
def test_wait_reports_terminal_errors_without_echoing_server_secrets(
    monkeypatch, transport, capsys, state
):
    responses, seen = transport
    responses.extend(
        [
            job("queued"),
            job(state, error={"code": "provider_error", "message": "sk-exposed-by-server"}),
        ]
    )
    with pytest.raises(SystemExit) as error:
        run_cli(monkeypatch, "ask", "What happened?", "--json")
    assert error.value.code == 1
    output = capsys.readouterr()
    assert "sk-exposed-by-server" not in output.out + output.err
    assert json.loads(output.out)["error"]["code"] == "job_" + state
    assert len(seen) == 2


def test_no_wait_returns_the_durable_job(monkeypatch, transport, capsys):
    responses, seen = transport
    responses.append(job("queued"))
    run_cli(monkeypatch, "ask", "What happened?", "--no-wait", "--json")
    assert json.loads(capsys.readouterr().out)["state"] == "queued"
    assert len(seen) == 1


def test_http_errors_are_actionable_and_do_not_reveal_response_body(monkeypatch, transport, capsys):
    responses, seen = transport
    responses.append(
        HTTPError("http://localhost", 401, "sk-server-secret", {}, io.BytesIO(b"sk-body-secret"))
    )
    with pytest.raises(SystemExit) as error:
        run_cli(monkeypatch, "status")
    assert error.value.code == 1
    output = capsys.readouterr()
    assert "SUOKU_API_TOKEN" in output.err
    assert "sk-" not in output.err


@pytest.mark.parametrize(
    "url",
    [
        "https://user:secret@example.test",
        "http://example.test/?token=secret",
        "file:///tmp/service",
        "http://example.test/#secret",
    ],
)
def test_http_rejects_urls_that_embed_credentials_or_non_http_schemes(
    monkeypatch, transport, capsys, url
):
    with pytest.raises(SystemExit) as error:
        run_cli(monkeypatch, "status", "--url", url)
    assert error.value.code == 1
    assert transport[1] == []
    assert "secret" not in capsys.readouterr().err


def test_client_disables_redirects(monkeypatch):
    from suoku.http_client import HTTPClient

    client = HTTPClient("https://example.test", "t" * 32)
    redirects = [
        handler
        for handler in client.opener.handlers
        if isinstance(handler, urllib.request.HTTPRedirectHandler)
    ]
    assert len(redirects) == 1
    request = urllib.request.Request(
        "https://example.test/v1/status", headers={"Authorization": "Bearer secret"}
    )
    assert (
        redirects[0].redirect_request(request, None, 302, "Moved", {}, "https://other.test/")
        is None
    )


def test_prepare_model_preserves_actionable_public_error(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as error:
        run_cli(monkeypatch, "prepare-model", tmp_path / "model", "--revision", "main")
    assert error.value.code == 1
    assert "immutable" in capsys.readouterr().err


def test_wait_timeout_is_checked_before_submitting_billable_job(monkeypatch, transport, capsys):
    responses, seen = transport
    responses.append(job("queued"))
    with pytest.raises(SystemExit) as error:
        run_cli(monkeypatch, "ask", "What happened?", "--wait-timeout", "0", "--json")
    assert error.value.code == 1
    assert seen == []
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "invalid_timeout"


def test_polling_deadline_leaves_job_running_and_returns_identifier(monkeypatch, transport, capsys):
    responses, seen = transport
    responses.extend([job("queued"), job("running")])
    with pytest.raises(SystemExit) as error:
        run_cli(monkeypatch, "ask", "What happened?", "--wait-timeout", "0.01", "--json")
    assert error.value.code == 1
    failure = json.loads(capsys.readouterr().out)["error"]
    assert failure["code"] == "job_timeout"
    assert "b" * 32 in failure["message"]
    assert all(request["method"] != "DELETE" for request in seen)


def test_config_rejects_symlinks_in_ancestor_directories(monkeypatch, tmp_path):
    from suoku.config import ConfigError, load_config

    initialize(monkeypatch, tmp_path)
    (tmp_path / "alias").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ConfigError, match="symbolic"):
        load_config(tmp_path / "alias/.suoku/config.env")


def test_doctor_always_emits_json_for_configuration_failure(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    config = tmp_path / "unsafe.env"
    config.write_text("SUOKU_API_TOKEN=secret\n")
    config.chmod(0o644)
    with pytest.raises(SystemExit) as error:
        run_cli(monkeypatch, "doctor", "--config", config)
    assert error.value.code == 1
    report = json.loads(capsys.readouterr().out)
    assert not report["ok"]
    assert report["checks"][0]["name"] == "configuration"
    assert "secret" not in str(report)


def test_doctor_starts_even_when_base_dependencies_cannot_import(tmp_path):
    script = """
import importlib.abc
import sys
class MissingDependencies(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'lancedb', 'numpy', 'PIL', 'filelock', 'torch', 'transformers', 'litellm'}:
            raise ModuleNotFoundError(fullname)
sys.meta_path.insert(0, MissingDependencies())
from suoku.cli import main
sys.argv = ['suoku', 'doctor']
main()
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=15,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")},
    )
    assert result.returncode == 1
    report = json.loads(result.stdout)
    checks = {item["name"]: item for item in report["checks"]}
    assert not checks["dependency:lancedb"]["ok"]
    assert not checks["dependency:numpy"]["ok"]
    assert "Traceback" not in result.stderr


def test_config_load_rejects_public_permissions_and_duplicate_base_url(monkeypatch, tmp_path):
    from suoku.config import ConfigError, load_config

    initialize(monkeypatch, tmp_path)
    target = tmp_path / ".suoku/config.env"
    target.chmod(0o644)
    with pytest.raises(ConfigError, match="private"):
        load_config(target)
    target.chmod(0o600)
    with target.open("a") as stream:
        stream.write("SUOKU_BASE_URL=http://other.example\n")
    with pytest.raises(ConfigError):
        load_config(target)


def test_init_preserves_gitignore_content_and_reasserts_config_exclusion(monkeypatch, tmp_path):
    original = "node_modules/\n.suoku/\n!.suoku/\n"
    (tmp_path / ".gitignore").write_text(original)
    initialize(monkeypatch, tmp_path)
    content = (tmp_path / ".gitignore").read_text()
    assert content.startswith(original)
    result = subprocess.run(
        [
            "git",
            "-c",
            "core.excludesFile=/dev/null",
            "check-ignore",
            "--no-index",
            ".suoku/config.env",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    # check-ignore needs a repository; test its real decision in a temporary repository.
    if result.returncode == 128:
        subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True, capture_output=True)
        result = subprocess.run(
            [
                "git",
                "-c",
                "core.excludesFile=/dev/null",
                "check-ignore",
                "--no-index",
                ".suoku/config.env",
            ],
            cwd=tmp_path,
            capture_output=True,
            text=True,
        )
    assert result.returncode == 0


def test_invalid_polled_job_has_safe_error_instead_of_traceback(monkeypatch, transport, capsys):
    responses, seen = transport
    responses.extend([job("queued"), ["unexpected shape"]])
    with pytest.raises(SystemExit) as error:
        run_cli(monkeypatch, "ask", "What happened?", "--json")
    assert error.value.code == 1
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "invalid_job"


def test_incomplete_http_response_is_reported_without_raw_bytes(monkeypatch, transport, capsys):
    responses, seen = transport
    responses.append(IncompleteRead(b"sk-partial-response-secret", 100))
    with pytest.raises(SystemExit) as error:
        run_cli(monkeypatch, "status", "--json")
    assert error.value.code == 1
    output = capsys.readouterr()
    assert "sk-partial" not in output.out + output.err
    assert json.loads(output.out)["error"]["code"] == "connection_failed"


def test_no_wait_terminal_failure_does_not_echo_server_error(monkeypatch, transport, capsys):
    responses, seen = transport
    responses.append(
        job("failed", error={"code": "provider_error", "message": "sk-exposed-by-server"})
    )
    with pytest.raises(SystemExit) as error:
        run_cli(monkeypatch, "ask", "What happened?", "--no-wait", "--json")
    assert error.value.code == 1
    output = capsys.readouterr()
    assert "sk-exposed" not in output.out + output.err
    assert json.loads(output.out)["error"]["code"] == "job_failed"


def test_hidden_prompt_never_falls_back_to_echoing(monkeypatch, tmp_path, capsys):
    import warnings

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)

    def unsafe_prompt(prompt):
        warnings.warn("Cannot control echo on the terminal.", cli.getpass.GetPassWarning)
        return "would-have-been-echoed"

    monkeypatch.setattr(cli.getpass, "getpass", unsafe_prompt)
    with pytest.raises(SystemExit) as error:
        run_cli(monkeypatch, "init", "--model", "openai/test")
    assert error.value.code == 1
    assert not (tmp_path / ".suoku/config.env").exists()
    assert "--provider-key-stdin" in capsys.readouterr().err


def test_ask_candidate_limit_bounds_provider_work(monkeypatch, transport, capsys):
    responses, seen = transport
    responses.append(job("queued"))
    run_cli(monkeypatch, "ask", "What happened?", "--candidate-limit", "2", "--no-wait", "--json")
    assert json.loads(seen[0]["body"])["candidate_limit"] == 2
    with pytest.raises(SystemExit):
        run_cli(monkeypatch, "ask", "What happened?", "--candidate-limit", "21", "--no-wait")
    assert len(seen) == 1


def test_doctor_checks_jsonschema_when_insights_are_configured(monkeypatch, tmp_path, capsys):
    import importlib.util

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SUOKU_INSIGHT_MODEL", "openai/test")
    original = importlib.util.find_spec
    monkeypatch.setattr(
        importlib.util, "find_spec", lambda name: None if name == "jsonschema" else original(name)
    )
    with pytest.raises(SystemExit) as error:
        run_cli(monkeypatch, "doctor")
    assert error.value.code == 1
    checks = {item["name"]: item for item in json.loads(capsys.readouterr().out)["checks"]}
    assert not checks["dependency:jsonschema"]["ok"]
