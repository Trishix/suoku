import shutil
import subprocess

import pytest

from suoku.media import FFmpegReader
from suoku.types import LakeError


@pytest.fixture
def video(tmp_path):
    if not shutil.which("ffmpeg"):
        pytest.skip("FFmpeg not installed")
    path = tmp_path / "video ' ; $(test).mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=320x240:rate=10",
            "-t",
            "5",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        check=True,
    )
    return path


def test_real_decode_timestamps_resume_and_hostile_filename(video):
    reader = FFmpegReader()
    info = reader.probe(video)
    assert info.duration_ms == 5000
    frames = list(reader.frames(video, interval_ms=2000))
    assert [frame.timestamp_ms for frame in frames] == [0, 2000, 4000]
    assert all(frame.image.size == (224, 224) for frame in frames)
    resumed = list(reader.frames(video, interval_ms=2000, start_ms=2001))
    assert [frame.timestamp_ms for frame in resumed] == [4000]


def test_invalid_media_and_dimensions(tmp_path, video):
    corrupt = tmp_path / "bad.mp4"
    corrupt.write_bytes(b"not a video")
    with pytest.raises(LakeError, match="Unsupported"):
        FFmpegReader().probe(corrupt)
    with pytest.raises(LakeError):
        FFmpegReader(max_duration_ms=1000).probe(video)


def test_decoder_timeout(video):
    with pytest.raises(LakeError):
        list(FFmpegReader(timeout_seconds=0.0001).frames(video, interval_ms=1))


def test_generator_close_reaps_decoder(video):
    generator = FFmpegReader().frames(video, interval_ms=500)
    assert next(generator).timestamp_ms == 0
    generator.close()
