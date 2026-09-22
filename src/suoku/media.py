"""Bounded, subprocess-based media decoding. This is not an OS sandbox."""

from __future__ import annotations

import json
import math
import queue
import re
import shutil
import subprocess
import threading
from collections.abc import Iterator
from pathlib import Path

from PIL import Image

from .types import Frame, LakeError, MediaInfo

# Decoder subprocesses are bounded for output and time, but callers must still treat
# them as trusted local utilities rather than a complete security sandbox.


class FFmpegReader:
    def __init__(
        self,
        *,
        ffmpeg: str = "ffmpeg",
        ffprobe: str = "ffprobe",
        max_duration_ms: int = 14_400_000,
        timeout_seconds: float = 3600,
    ):
        self.ffmpeg = shutil.which(ffmpeg)
        self.ffprobe = shutil.which(ffprobe)
        self.max_duration_ms = max_duration_ms
        self.timeout_seconds = timeout_seconds
        if not self.ffmpeg or not self.ffprobe:
            raise LakeError("decoder_missing", "Install FFmpeg and ffprobe before ingesting media.")

    @staticmethod
    def _path(path: Path) -> str:
        path = path.resolve(strict=True)
        if not path.is_file():
            raise LakeError("invalid_media", "Source must be a regular file.")
        return str(path)

    def probe(self, path: Path) -> MediaInfo:
        command = [
            self.ffprobe,
            "-v",
            "error",
            "-protocol_whitelist",
            "file,pipe",
            "-format_whitelist",
            "mov,matroska,webm",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=codec_name,width,height,duration:format=duration,format_name",
            "-of",
            "json",
            self._path(path),
        ]
        # ffprobe is subject to the same outer container limits as decoding.
        try:
            result = subprocess.run(command, capture_output=True, timeout=30, check=True)
            if len(result.stdout) > 65_536:
                raise ValueError("oversized probe response")
            data = json.loads(result.stdout)
            stream = data["streams"][0]
            container = data["format"]["format_name"].split(",")
            codec = stream["codec_name"]
            if not (
                ("mov" in container and codec == "h264")
                or ("matroska" in container and codec in {"vp8", "vp9"})
            ):
                raise ValueError("unsupported codec/container")
            duration = float(data["format"].get("duration", stream.get("duration", "nan")))
            width, height = int(stream["width"]), int(stream["height"])
            if not math.isfinite(duration) or not 0 < duration * 1000 <= self.max_duration_ms:
                raise ValueError("invalid duration")
            if min(width, height) <= 0 or max(width, height) > 4096 or min(width, height) > 2160:
                raise ValueError("invalid dimensions")
            return MediaInfo(math.ceil(duration * 1000), width, height, codec)
        except (subprocess.SubprocessError, ValueError, KeyError, IndexError, TypeError) as exc:
            raise LakeError("invalid_media", "Unsupported, malformed, or oversized video.") from exc

    def frames(self, path: Path, *, interval_ms: int, start_ms: int = 0) -> Iterator[Frame]:
        if interval_ms < 1 or start_ms < 0:
            raise LakeError("invalid_config", "Sampling interval must be positive.")
        # Selection precedes the resume cutoff: timestamps are identical after a restart.
        filters = (
            "setpts=PTS-STARTPTS,"
            f"select='isnan(prev_selected_t)+gte(t-prev_selected_t,{interval_ms / 1000})',"
            f"select='gte(t,{start_ms / 1000})',"
            "scale=224:224:force_original_aspect_ratio=increase,crop=224:224,showinfo"
        )
        command = [
            self.ffmpeg,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "info",
            "-protocol_whitelist",
            "file,pipe",
            "-format_whitelist",
            "mov,matroska,webm",
            "-threads",
            "2",
            "-i",
            self._path(path),
            "-map",
            "0:v:0",
            "-an",
            "-sn",
            "-dn",
            "-vf",
            filters,
            "-fps_mode",
            "vfr",
            "-threads",
            "1",
            "-filter_threads",
            "1",
            "-pix_fmt",
            "rgb24",
            "-f",
            "rawvideo",
            "pipe:1",
        ]
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        expired = threading.Event()

        def expire():
            expired.set()
            if process.poll() is None:
                process.kill()

        timer = threading.Timer(self.timeout_seconds, expire)
        timer.daemon = True
        timestamps: queue.Queue[int] = queue.Queue(maxsize=64)
        stop = threading.Event()

        def drain_stderr():
            assert process.stderr is not None
            # readline's explicit bound prevents attacker-controlled log lines accumulating.
            while not stop.is_set():
                line = process.stderr.readline(8192)
                if not line:
                    return
                match = re.search(rb"\bpts_time:([0-9.eE+\-]+)", line)
                if match:
                    try:
                        value = float(match[1])
                        if not math.isfinite(value):
                            continue
                        while not stop.is_set():
                            try:
                                timestamps.put(round(value * 1000), timeout=0.1)
                                break
                            except queue.Full:
                                continue
                    except ValueError:
                        continue

        reader = threading.Thread(target=drain_stderr, daemon=True)
        reader.start()
        timer.start()
        try:
            assert process.stdout is not None
            size = 224 * 224 * 3
            while True:
                raw = process.stdout.read(size)
                if not raw:
                    break
                if len(raw) != size:
                    raise LakeError("decode_failed", "Decoder returned an incomplete frame.")
                try:
                    timestamp = timestamps.get(timeout=10)
                except queue.Empty as exc:
                    raise LakeError("decode_failed", "Decoder omitted a frame timestamp.") from exc
                yield Frame(timestamp, Image.frombytes("RGB", (224, 224), raw))
            code = process.wait(timeout=5)
            if expired.is_set():
                raise LakeError("decode_timeout", "Video decoding exceeded its deadline.")
            if code:
                raise LakeError("decode_failed", "Video decoding failed.")
        finally:
            stop.set()
            timer.cancel()
            if process.poll() is None:
                process.kill()
            process.wait()
            reader.join(timeout=2)
            process.stdout.close()
            process.stderr.close()
