import json
import time

import pytest
from test_insights import insights as insights

from suoku import LakeError, SearchFilters
from suoku.server.jobs import Jobs


def test_cache_invalidation_on_recipe_and_extractor_changes(insights):
    from dataclasses import replace

    from suoku.insights.recipes import resolve_recipe
    engine, _, provider, asset, _ = insights
    engine.analyze(asset, end_ms=10000)
    revised = replace(resolve_recipe("general"), version="2")
    engine.analyze(asset, end_ms=10000, recipe=revised)
    assert provider.calls == 2
    engine.extractor.fingerprint = "new-extraction-settings"
    engine.analyze(asset, end_ms=10000, recipe=revised)
    assert provider.calls == 3


def test_time_filter_bounds_all_exported_evidence(insights):
    engine, lake, _, _, source = insights
    asset = lake.ingest(source, recorded_at="2026-01-01T00:00:00Z")
    answer = engine.ask("red", asset_ids=[asset], filters=SearchFilters(
        recorded_after="2026-01-01T00:00:02Z", recorded_before="2026-01-01T00:00:04Z"))
    assert answer.citations
    for observation in engine.observations(asset):
        assert all(2000 <= timestamp <= 4000 for timestamp in observation.evidence_timestamps)


def test_cancel_between_windows_keeps_only_completed_cache(insights):
    engine, _, provider, asset, _ = insights
    with pytest.raises(LakeError) as exc:
        engine.analyze(asset, cancelled=lambda: provider.calls > 0)
    assert exc.value.code == "cancelled"
    assert not engine.observations(asset)


def test_running_insight_job_does_not_expire(tmp_path):
    jobs = Jobs(tmp_path)
    pending = jobs.submit("analysis", {"asset_id": "a" * 32})
    jobs.claim()
    with jobs.connect() as db:
        db.execute("UPDATE jobs SET created_at=? WHERE id=?", (time.time() - 4000, pending["id"]))
    jobs.expire()
    assert jobs.get(pending["id"])["state"] == "running"
    jobs.finish(pending["id"], result={"observations": []})
    assert jobs.get(pending["id"])["state"] == "succeeded"
    with jobs.connect() as db:
        db.execute("UPDATE jobs SET updated_at=? WHERE id=?", (time.time() - 4000, pending["id"]))
    with pytest.raises(LakeError):
        jobs.get(pending["id"])
    jobs.expire()
    with jobs.connect() as db:
        assert db.execute("SELECT count(*) FROM jobs").fetchone()[0] == 0


def test_removal_clears_answer_snapshots_but_preserves_unrelated_analyses(tmp_path):
    jobs = Jobs(tmp_path)
    removed, unrelated = "a" * 32, "b" * 32
    answer = jobs.submit("answer", {"question": "compare scenes"})
    jobs.finish(answer["id"], result={"answer": {"answer": "Both scenes share context.",
        "citations": [{"asset_id": unrelated}]}})
    analysis = jobs.submit("analysis", {"asset_id": unrelated})
    jobs.finish(analysis["id"], result={"observations": [{"asset_id": unrelated}]})
    jobs.invalidate_insights(removed)
    assert jobs.get(answer["id"])["result"] is None
    assert jobs.get(analysis["id"])["state"] == "succeeded"


def test_legacy_database_is_upgraded_additively(tmp_path):
    import sqlite3
    with sqlite3.connect(tmp_path / "jobs.sqlite3") as db:
        db.executescript("""CREATE TABLE jobs (
            id TEXT PRIMARY KEY,kind TEXT,state TEXT,payload TEXT,progress INTEGER DEFAULT 0,
            result TEXT,error TEXT,cancel INTEGER DEFAULT 0,created_at REAL,updated_at REAL);
            CREATE TABLE uploads(id TEXT PRIMARY KEY,suffix TEXT,size INTEGER,state TEXT);
        """)
        db.execute("INSERT INTO jobs(id,kind,state,payload,created_at,updated_at) "
                   "VALUES(?, 'ingest','queued',?,?,?)", ("a"*32,json.dumps({}), time.time(),time.time()))
    jobs = Jobs(tmp_path)
    assert jobs.get("a" * 32)["state"] == "queued"
    assert not jobs.worker_status()["worker_available"]
