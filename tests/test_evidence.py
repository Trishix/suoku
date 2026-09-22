import shutil
import subprocess
import sys

import pytest

from suoku.types import LakeError


@pytest.fixture
def variable_video(tmp_path):
    if not shutil.which("ffmpeg"):
        pytest.skip("FFmpeg not installed")
    path = tmp_path / "video ' ; $(ignored).mp4"
    # Actual display timestamps: 0, .1, .4, .9, 1.7 seconds.
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=1200x600:rate=10",
            "-vf",
            "select='eq(n,0)+eq(n,1)+eq(n,4)+eq(n,9)+eq(n,17)'",
            "-frames:v",
            "5",
            "-fps_mode",
            "vfr",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        check=True,
        timeout=30,
    )
    return path


def test_extract_real_vfr_frames_in_window_and_preserve_aspect(variable_video):
    from suoku.insights.evidence import EvidenceExtractor

    frames = EvidenceExtractor().extract(variable_video, 200, 1700)
    assert [frame.timestamp_ms for frame in frames] == [400, 900, 900]
    assert all(frame.image.size == (768, 384) for frame in frames)
    # The testsrc left and right edges survive (center-cropping would lose them).
    assert frames[0].image.getpixel((0, 190)) != frames[0].image.getpixel((767, 190))


def test_no_frame_in_window_returns_no_evidence(variable_video):
    from suoku.insights.evidence import EvidenceExtractor

    assert EvidenceExtractor().extract(variable_video, 200, 300) == []


def test_short_window_repeats_actual_frame_without_inventing_timestamps(variable_video):
    from suoku.insights.evidence import EvidenceExtractor

    frames = EvidenceExtractor().extract(variable_video, 1, 200)
    assert [frame.timestamp_ms for frame in frames] == [100, 100, 100]


def test_evidence_rejects_invalid_window_and_decoder_failure(variable_video, tmp_path):
    from suoku.insights.evidence import EvidenceExtractor

    with pytest.raises(LakeError, match="window"):
        EvidenceExtractor().extract(variable_video, -1, 100)
    with pytest.raises(LakeError):
        EvidenceExtractor(timeout_seconds=0.00001).extract(variable_video, 0, 1000)
    corrupt = tmp_path / "corrupt.mp4"
    corrupt.write_bytes(b"not a video")
    with pytest.raises(LakeError):
        EvidenceExtractor().extract(corrupt, 0, 1000)


def test_decoder_output_bound_terminates_excessive_output():
    from suoku.insights.evidence import _bounded_run

    with pytest.raises(LakeError, match="output limit"):
        _bounded_run([sys.executable, "-c", "print('x' * 200000)"], 1024, 3)
