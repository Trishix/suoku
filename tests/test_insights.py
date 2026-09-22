import dataclasses
import json

import pytest
from fakes import ColorEmbedder, FixtureReader
from PIL import Image

from suoku import LakeError, VideoLake
from suoku.insights import InsightEngine, InsightRecipe
from suoku.insights.models import EvidenceFrame


class Evidence:
    fingerprint = "fake-evidence-v1"

    def extract(self, path, start_ms, end_ms):
        return [EvidenceFrame(t, Image.new("RGB", (32, 18), "red"))
                for t in [start_ms, (start_ms + end_ms) // 2, end_ms - 1]]


class Provider:
    fingerprint = "fake-provider-v1"

    def __init__(self):
        self.calls = 0
        self.bad_citation = False

    def analyze_images(self, images, prompt, schema):
        self.calls += 1
        return {"summary": "A red scene", "payload": {"scene": "red", "entities": [],
                "activities": [], "limitations": ["Sampled frames only"]}}

    def reason(self, question, evidence, schema):
        return {"answer": "A red scene was visible.", "citations": [{
            "observation_id": "invented" if self.bad_citation else evidence[0]["id"],
            "reason": "Visible red scene"}], "limitations": [], "insufficient_evidence": False}


@pytest.fixture
def insights(tmp_path):
    source = tmp_path / "video.mp4"
    source.write_bytes(b"fixture")
    with VideoLake.open(tmp_path / "index", ColorEmbedder(), reader=FixtureReader()) as lake:
        asset = lake.ingest(source)
        provider = Provider()
        engine = InsightEngine(lake, provider, extractor=Evidence())
        yield engine, lake, provider, asset, source


def test_answer_cites_real_evidence_and_reuses_cache(insights):
    engine, _, provider, asset, _ = insights
    first = engine.ask("red", asset_ids=[asset], candidate_limit=1)
    assert first.citations[0].asset_id == asset
    assert first.citations[0].start_ms == 0
    assert first.citations[0].end_ms == 10000
    assert first.limitations
    engine.ask("red", asset_ids=[asset], candidate_limit=1)
    assert provider.calls == 1
    provider.fingerprint = "fake-provider-v2"
    engine.ask("red", asset_ids=[asset], candidate_limit=1)
    assert provider.calls == 2
    assert len(engine.observations(asset)) == 1


def test_source_changes_cannot_use_old_observations(insights):
    engine, lake, provider, asset, source = insights
    engine.ask("red", candidate_limit=1)
    source.write_bytes(b"changed")
    with pytest.raises(LakeError) as error:
        engine.ask("red", candidate_limit=1)
    assert error.value.code == "source_changed"
    assert provider.calls == 1
    lake.ingest(source)
    engine.ask("red", candidate_limit=1)
    assert provider.calls == 2


def test_observation_read_hides_changed_or_missing_source(insights):
    from suoku.insights.store import InsightStore
    engine, lake, _, asset, source = insights
    engine.analyze(asset, end_ms=10000)
    assert len(engine.observations(asset)) == 1
    source.write_bytes(b"changed-without-ingestion")
    assert engine.observations(asset) == []
    assert InsightStore.read_visible(lake.path, asset) == []
    source.unlink()
    assert InsightStore.read_visible(lake.path, asset) == []


def test_removed_and_purged_assets_have_no_observations(insights):
    engine, lake, _, asset, _ = insights
    engine.analyze(asset)
    assert len(engine.observations(asset)) == 2
    lake.remove(asset)
    assert engine.observations(asset) == []
    assert engine.ask("red").insufficient_evidence
    lake.purge(asset)
    import sqlite3
    with sqlite3.connect(lake.path / "insights.sqlite3") as db:
        assert db.execute("SELECT count(*) FROM observations").fetchone()[0] == 0


def test_invalid_citation_is_not_returned(insights):
    engine, _, provider, _, _ = insights
    provider.bad_citation = True
    with pytest.raises(LakeError) as error:
        engine.ask("red", candidate_limit=1)
    assert error.value.code == "invalid_evidence"


def test_cancelled_work_makes_no_calls(insights):
    engine, _, provider, asset, _ = insights
    with pytest.raises(LakeError) as error:
        engine.analyze(asset, cancelled=lambda: True)
    assert error.value.code == "cancelled"
    assert provider.calls == 0


def test_asset_filter_is_applied_before_ranking(insights):
    engine, lake, _, asset, source = insights
    other = lake.ingest(source, camera_id="other")
    result = engine.ask("red", asset_ids=[other], candidate_limit=1)
    assert result.citations[0].asset_id == other
    assert result.citations[0].asset_id != asset


def test_bad_schema_rejected_without_network():
    for schema in [
        {"type": "object", "properties": {"x": {"$ref": "https://example.com/schema"}}},
        {"type": "object", "properties": {"x": {"type": "string", "pattern": "(a+)+$"}}},
        {"type": "object", "$defs": {"x": {"$ref": "#/$defs/x"}},
         "properties": {"x": {"$ref": "#/$defs/x"}}},
    ]:
        with pytest.raises(LakeError):
            InsightRecipe("custom", "1", "Custom", "", "Describe", schema)


def test_pydantic_schema_and_fingerprints():
    from pydantic import BaseModel

    class Item(BaseModel):
        name: str

    class Payload(BaseModel):
        items: list[Item]

    recipe = InsightRecipe.from_pydantic(Payload, id="items", prompt="List visible objects")
    assert recipe.schema["properties"]["items"]["items"]["type"] == "object"
    other = dataclasses.replace(recipe, prompt="Only list visible red objects")
    assert other.fingerprint != recipe.fingerprint
    assert json.loads(json.dumps(dataclasses.asdict(recipe)))["id"] == "items"
