import time

import pytest
from fakes import ColorEmbedder, FixtureReader
from fastapi.testclient import TestClient

from suoku.server.app import Settings, create_app
from suoku.server.worker import Worker
from suoku.types import LakeError

TOKEN = "test-token-" + "x" * 32
HEADERS = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def service(tmp_path):
    app = create_app(Settings(tmp_path, TOKEN, max_upload_bytes=1024))
    with TestClient(app, headers=HEADERS) as client:
        worker = Worker(tmp_path, ColorEmbedder(), reader=FixtureReader())
        try:
            yield client, worker, app.state.jobs
        finally:
            worker.close()


def drain(worker):
    for _ in range(100):
        if not worker.tick():
            return
    pytest.fail("Worker did not drain")


def upload(client):
    response = client.post("/v1/videos?filename=test.mp4&camera_id=front", content=b"fixture")
    assert response.status_code == 202, response.text
    return response.json()


def test_upload_search_range_remove_and_purge(service):
    client, worker, jobs = service
    pending = upload(client)
    assert "payload" not in pending
    drain(worker)
    done = client.get(f"/v1/jobs/{pending['id']}").json()
    asset = done["result"]["asset_id"]
    assert done["state"] == "succeeded", done
    search = client.post("/v1/search", json={"query": "red", "filters": {"camera_id": "front"}})
    assert search.status_code == 202
    drain(worker)
    result = client.get(f"/v1/jobs/{search.json()['id']}").json()
    assert result["state"] == "succeeded", result
    assert result["result"]["matches"][0]["asset_id"] == asset
    playback = client.get(f"/v1/videos/{asset}/content", headers={"Range": "bytes=0-2"})
    assert playback.status_code == 206
    assert playback.content == b"fix"
    assert playback.headers["cache-control"] == "no-store"
    removed = client.delete(f"/v1/videos/{asset}")
    assert removed.status_code == 202
    drain(worker)
    assert worker.lake.search("red") == []
    assert client.get(f"/v1/videos/{asset}/content").status_code == 404
    assert len(list((worker.root / "media").glob("*.mp4"))) == 1
    purged = client.delete(f"/v1/videos/{asset}/purge?delete_media=true")
    drain(worker)
    assert client.get(f"/v1/jobs/{purged.json()['id']}").json()["state"] == "succeeded"
    assert not list((worker.root / "media").glob("*.mp4"))


def test_auth_limits_and_no_arbitrary_paths(service):
    client, worker, _ = service
    assert client.get("/openapi.json", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert (
        client.post(
            "/v1/search", json={"query": "red"}, headers={"Authorization": "Bearer wrong"}
        ).status_code
        == 401
    )
    assert client.post("/v1/videos?filename=../bad.mp4", content=b"x").status_code == 422
    assert client.post("/v1/videos?filename=bad.m3u8", content=b"x").status_code == 415
    assert client.post("/v1/videos?filename=big.mp4", content=b"x" * 1025).status_code == 413
    assert client.post("/v1/videos?filename=empty.mp4", content=b"").status_code == 400
    assert (
        client.post("/v1/search", json={"query": "red", "source": "/etc/passwd"}).status_code == 422
    )
    assert (
        client.post("/v1/search", json={"query": "red", "filters": {"sql": "1=1"}}).status_code
        == 422
    )
    assert client.post("/v1/search", json={"query": " "}).status_code == 422
    assert client.post("/v1/search", json={"query": "red", "limit": 101}).status_code == 422
    assert client.get("/v1/videos/not-an-id/content").status_code == 404
    assert not list((worker.root / "media").iterdir())


def test_cancel_search_priority_and_resume_after_restart(service):
    client, worker, jobs = service
    pending = upload(client)
    worker.tick()  # first batch, not visible yet
    query = client.post("/v1/search", json={"query": "red"}).json()
    worker.tick()  # priority search runs before another ingest batch
    assert jobs.get(query["id"])["state"] == "succeeded"
    assert jobs.get(query["id"])["result"]["matches"] == []
    worker.close()
    restarted = Worker(worker.root, ColorEmbedder(), reader=FixtureReader())
    try:
        drain(restarted)
        assert jobs.get(pending["id"])["state"] == "succeeded"
        other = client.post("/v1/search", json={"query": "red"}).json()
        assert client.post(f"/v1/jobs/{other['id']}/cancel").json()["state"] == "cancelled"
    finally:
        restarted.close()


def test_queue_capacity_and_expiry(service):
    _, _, jobs = service
    first = jobs.submit("search", {"query": "red", "limit": 1}, capacity=1)
    with pytest.raises(LakeError, match="full"):
        jobs.submit("search", {"query": "red", "limit": 1}, capacity=1)
    with jobs.connect() as db:
        db.execute("UPDATE jobs SET created_at=? WHERE id=?", (time.time() - 3601, first["id"]))
    with pytest.raises(LakeError):
        jobs.get(first["id"])
    jobs.expire()
    with jobs.connect() as db:
        assert db.execute("SELECT count(*) FROM jobs").fetchone()[0] == 0


def test_unfinished_upload_has_no_job(tmp_path):
    app = create_app(Settings(tmp_path, TOKEN, max_upload_bytes=2))
    with TestClient(app, headers=HEADERS) as client:
        assert client.post("/v1/videos?filename=x.mp4", content=b"123").status_code == 413
    with app.state.jobs.connect() as db:
        assert db.execute("SELECT count(*) FROM jobs").fetchone()[0] == 0
