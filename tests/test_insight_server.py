"""Authenticated HTTP/worker integration tests for insight jobs and deletion."""

import threading
import time

from fakes import ColorEmbedder, FixtureReader
from fastapi.testclient import TestClient
from test_insights import Evidence, Provider

from suoku.server.app import Settings, create_app
from suoku.server.worker import Worker

TOKEN = "test-token-" + "x" * 32
HEADERS = {"Authorization": f"Bearer {TOKEN}"}


def test_insight_jobs_cache_observations_and_removal(tmp_path):
    app = create_app(Settings(tmp_path, TOKEN))
    provider = Provider()
    worker = Worker(tmp_path, ColorEmbedder(), reader=FixtureReader(),
                    provider=provider, extractor=Evidence())
    try:
        with TestClient(app, headers=HEADERS) as client:
            assert client.get("/v1/status").json()["insights_ready"]
            assert len(client.get("/v1/insight-recipes").json()) == 3
            pending = client.post("/v1/videos?filename=test.mp4", content=b"fixture").json()
            while worker.tick():
                pass
            asset = client.get(f"/v1/jobs/{pending['id']}").json()["result"]["asset_id"]
            jobs = []
            for _ in range(2):
                response = client.post("/v1/answers", json={"question": "red",
                    "asset_ids": [asset], "candidate_limit": 1})
                assert response.status_code == 202, response.text
                jobs.append(response.json()["id"])
                worker.tick()
                result = client.get(f"/v1/jobs/{jobs[-1]}").json()
                assert result["state"] == "succeeded", result
                assert result["result"]["answer"]["citations"][0]["asset_id"] == asset
            assert provider.calls == 1
            assert len(client.get(f"/v1/videos/{asset}/observations").json()) == 1
            analysis = client.post("/v1/analyses", json={"asset_id": asset}).json()
            worker.tick()
            assert len(client.get(f"/v1/jobs/{analysis['id']}").json()["result"]["observations"]) == 2
            client.delete(f"/v1/videos/{asset}")
            worker.tick()
            assert client.get(f"/v1/videos/{asset}/observations").json() == []
            assert client.get(f"/v1/jobs/{jobs[0]}").json()["result"] is None
    finally:
        worker.close()


def test_status_does_not_confuse_api_and_worker_readiness(tmp_path):
    app = create_app(Settings(tmp_path, TOKEN))
    with TestClient(app) as client:
        assert client.get("/healthz").status_code == 200
        assert client.get("/v1/status").status_code == 401
        assert client.get("/v1/insight-recipes").status_code == 401
        assert not client.get("/v1/status", headers=HEADERS).json()["worker_available"]


def test_purge_invalidates_answers_even_without_citations(tmp_path):
    class UncertainProvider(Provider):
        def reason(self, question, evidence, schema):
            return {"answer": "A red scene is visible, but the cause is unclear.",
                    "citations": [], "limitations": ["No cause established."],
                    "insufficient_evidence": True}

    worker = Worker(tmp_path, ColorEmbedder(), reader=FixtureReader(),
                    provider=UncertainProvider(), extractor=Evidence())
    try:
        with TestClient(create_app(Settings(tmp_path, TOKEN)), headers=HEADERS) as client:
            ingest = client.post("/v1/videos?filename=test.mp4", content=b"fixture").json()
            while worker.tick():
                pass
            asset = client.get(f"/v1/jobs/{ingest['id']}").json()["result"]["asset_id"]
            answer = client.post("/v1/answers", json={"question": "red",
                "asset_ids": [asset], "candidate_limit": 1}).json()
            worker.tick()
            before = client.get(f"/v1/jobs/{answer['id']}").json()
            assert before["state"] == "succeeded"
            assert before["result"]["answer"]["citations"] == []
            purge = client.delete(f"/v1/videos/{asset}/purge?delete_media=true")
            assert purge.status_code == 202, purge.text
            worker.tick()
            after = client.get(f"/v1/jobs/{answer['id']}").json()
            assert after["result"] is None
            assert after["state"] == "failed"
            assert after["error"]["code"] == "source_removed"
    finally:
        worker.close()


def test_model_key_cannot_enter_job_requests(tmp_path):
    app = create_app(Settings(tmp_path, TOKEN))
    with TestClient(app, headers=HEADERS) as client:
        for extra in [{"api_key": "secret"}, {"model": "arbitrary/model"}]:
            result = client.post("/v1/answers", json={"question": "red", **extra})
            assert result.status_code == 422
            assert "secret" not in result.text
        assert client.post("/v1/answers", json={"question": " "}).status_code == 422
        assert client.post("/v1/answers", json={"question": "x", "candidate_limit": 21}).status_code == 422


def test_worker_heartbeat_runs_during_blocked_processing(tmp_path):
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    failures = []

    class SlowProvider(Provider):
        def analyze_images(self, *args):
            entered.set()
            assert release.wait(10)
            return super().analyze_images(*args)

    def run():
        worker = None
        try:
            worker = Worker(tmp_path, ColorEmbedder(), reader=FixtureReader(),
                            provider=SlowProvider(), extractor=Evidence())
            source = tmp_path / "test.mp4"
            source.write_bytes(b"fixture")
            asset = worker.lake.ingest(source)
            worker.jobs.submit("answer", {"question": "red", "asset_ids": [asset], "candidate_limit": 1})
            worker.tick()
        except BaseException as exc:
            failures.append(exc)
        finally:
            if worker:
                worker.close()
            finished.set()

    from suoku.server.jobs import Jobs
    thread = threading.Thread(target=run)
    thread.start()
    try:
        assert entered.wait(5)
        jobs = Jobs(tmp_path)
        first = jobs.worker_status()["last_heartbeat"]
        deadline = time.monotonic() + 6
        while jobs.worker_status()["last_heartbeat"] <= first and time.monotonic() < deadline:
            finished.wait(0.05)
        assert jobs.worker_status()["last_heartbeat"] > first
        assert jobs.worker_status()["insights_ready"]
    finally:
        release.set()
        thread.join(10)
    assert not failures
    assert not jobs.worker_status()["worker_available"]
