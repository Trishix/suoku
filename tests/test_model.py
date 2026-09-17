import json
import os

import numpy as np
import pytest
from PIL import Image

from suoku.adapters.siglip import SiglipEmbedder, prepare_model
from suoku.types import Frame, LakeError


def test_model_requires_pinned_revision_and_manifest(tmp_path):
    with pytest.raises(LakeError, match="immutable"):
        prepare_model(tmp_path / "weights", revision="main")
    with pytest.raises(LakeError, match="pinned"):
        SiglipEmbedder(tmp_path)
    (tmp_path / "suoku-manifest.json").write_text(
        json.dumps(
            {
                "model": "google/siglip-base-patch16-224",
                "revision": "a" * 40,
                "files": {"../escape": "deadbeef"},
            }
        )
    )
    with pytest.raises(LakeError, match="pinned"):
        SiglipEmbedder(tmp_path)


@pytest.mark.skipif(
    not os.environ.get("SUOKU_TEST_MODEL"), reason="Set SUOKU_TEST_MODEL to prepared local weights"
)
def test_real_siglip_local_inference():
    model = SiglipEmbedder(os.environ["SUOKU_TEST_MODEL"])
    frames = [Frame(0, Image.new("RGB", (224, 224), color)) for color in ["red", "blue"]]
    vectors = model.embed_frames(frames)
    query = model.embed_query("a solid red background")
    assert vectors.shape == (2, 768)
    assert query.shape == (768,)
    assert np.isfinite(vectors).all() and np.isfinite(query).all()
    normalized = vectors / np.linalg.norm(vectors, axis=1, keepdims=True)
    assert (normalized @ query).argmax() == 0
