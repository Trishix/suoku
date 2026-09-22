"""Small, timestamped evidence samples without spatial cropping."""

from __future__ import annotations

import io
import math
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

from PIL import Image

from ..types import LakeError


def _bounded_run(command: list[str], limit: int, timeout: float) -> tuple[bytes, bytes]:
    """Drain both pipes while enforcing an actual read bound and wall deadline."""
    try:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except OSError:
        raise LakeError("decode_failed", "Evidence decoder could not start.") from None
    outputs: list[bytes] = [b"", b""]
    exceeded = threading.Event()
    expired = threading.Event()

    def drain(index, pipe, bound):
        data = pipe.read(bound + 1)
        if len(data) > bound:
            exceeded.set()
            process.kill()
        outputs[index] = data[:bound]

    def expire():
        expired.set()
        if process.poll() is None:
            process.kill()

    threads = [
        threading.Thread(target=drain, args=(0, process.stdout, limit), daemon=True),
        threading.Thread(target=drain, args=(1, process.stderr, 65536), daemon=True),
    ]
    timer = threading.Timer(max(0.000001, timeout), expire)
    timer.daemon = True
    for thread in threads:
        thread.start()
    timer.start()
    try:
        process.wait()
        for thread in threads:
            thread.join()
        if expired.is_set():
            raise LakeError("decode_timeout", "Evidence extraction exceeded its deadline.")
        if exceeded.is_set():
            raise LakeError("decode_failed", "Evidence decoder exceeded its output limit.")
        if process.returncode:
            raise LakeError("decode_failed", "Evidence video could not be decoded.")
        return outputs[0], outputs[1]
    finally:
        timer.cancel()
        if process.poll() is None:
            process.kill()
        process.wait()
        process.stdout.close()
        process.stderr.close()


class EvidenceExtractor:
    """Select nearest decoded frames at the beginning, midpoint, and end.

    Frames must start in [start_ms, end_ms). Short windows repeat the same
    actual frame; empty windows return no evidence. Timestamps are relative to
    the first displayed frame, matching Suoku's ingestion timeline.
    """

    fingerprint = "ffmpeg-evidence-v1:nearest-start-mid-end:768:uncropped"

    def __init__(self, *, ffmpeg="ffmpeg", ffprobe="ffprobe", timeout_seconds=60):
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise LakeError("invalid_config", "Evidence timeout must be positive.")
        self.ffmpeg = shutil.which(ffmpeg)
        self.ffprobe = shutil.which(ffprobe)
        self.timeout_seconds = timeout_seconds
        if not self.ffmpeg or not self.ffprobe:
            raise LakeError(
                "decoder_missing", "Install FFmpeg and ffprobe for evidence extraction."
            )

    def extract(self, path, start_ms: int, end_ms: int):
        from .models import EvidenceFrame

        if (
            type(start_ms) is not int
            or type(end_ms) is not int
            or start_ms < 0
            or end_ms <= start_ms
            or end_ms > 14_400_000
        ):
            raise LakeError("invalid_window", "Evidence window must satisfy 0 <= start < end.")
        try:
            path = Path(path).resolve(strict=True)
            if not path.is_file():
                raise OSError
        except OSError:
            raise LakeError(
                "invalid_media", "Evidence source must be a readable regular file."
            ) from None
        deadline = time.monotonic() + self.timeout_seconds
        common = ["-protocol_whitelist", "file,pipe", "-format_whitelist", "mov,matroska,webm"]
        raw, _ = _bounded_run(
            [
                self.ffprobe,
                "-v",
                "error",
                *common,
                "-select_streams",
                "v:0",
                "-show_frames",
                "-show_entries",
                "frame=best_effort_timestamp_time",
                "-of",
                "csv=p=0",
                str(path),
            ],
            16 * 1024 * 1024,
            deadline - time.monotonic(),
        )
        timestamps = []
        baseline = None
        frame_index = 0
        for line in raw.splitlines():
            # Some decoders append side-data CSV columns or blank lines.
            first = line.split(b",", 1)[0]
            if not first:
                continue
            try:
                timestamp = float(first)
                if not math.isfinite(timestamp):
                    raise ValueError
            except ValueError:
                raise LakeError(
                    "decode_failed", "Video contains an invalid frame timestamp."
                ) from None
            if baseline is None:
                baseline = timestamp
            relative_ms = round((timestamp - baseline) * 1000)
            if start_ms <= relative_ms < end_ms:
                timestamps.append((frame_index, relative_ms))
            frame_index += 1
        if not timestamps:
            return []
        targets = [start_ms, (start_ms + end_ms) / 2, end_ms]
        selected = [min(timestamps, key=lambda item: abs(item[1] - target)) for target in targets]
        unique = sorted({index for index, _ in selected})
        selection = "+".join(f"eq(n,{index})" for index in unique)
        filters = (
            f"setpts=PTS-STARTPTS,select='{selection}',"
            "scale=w='min(768,iw)':h='min(768,ih)':force_original_aspect_ratio=decrease,"
            "setsar=1,showinfo"
        )
        output, diagnostic = _bounded_run(
            [
                self.ffmpeg,
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "info",
                *common,
                "-threads",
                "2",
                "-i",
                str(path),
                "-map",
                "0:v:0",
                "-an",
                "-sn",
                "-dn",
                "-vf",
                filters,
                "-frames:v",
                str(len(unique)),
                "-fps_mode",
                "vfr",
                "-threads",
                "1",
                "-filter_threads",
                "1",
                "-c:v",
                "png",
                "-f",
                "image2pipe",
                "pipe:1",
            ],
            8 * 1024 * 1024,
            deadline - time.monotonic(),
        )
        # PNG IEND is fixed: length=0, type=IEND, CRC=ae426082.
        chunks = output.split(b"\x00\x00\x00\x00IEND\xaeB`\x82")
        actual = [
            round(float(value) * 1000)
            for value in re.findall(rb"\bpts_time:([0-9.eE+\-]+)", diagnostic)
        ]
        if len(chunks) - 1 != len(unique) or len(actual) != len(unique):
            raise LakeError("decode_failed", "Evidence decoder returned incomplete frames.")
        decoded = {}
        for index, chunk, timestamp in zip(unique, chunks, actual, strict=False):
            try:
                with Image.open(io.BytesIO(chunk + b"\x00\x00\x00\x00IEND\xaeB`\x82")) as image:
                    image.load()
                    decoded[index] = EvidenceFrame(timestamp, image.convert("RGB"))
            except (OSError, ValueError):
                raise LakeError("decode_failed", "Evidence image could not be decoded.") from None
        frames = [decoded[index] for index, _ in selected]
        if any(not start_ms <= frame.timestamp_ms < end_ms for frame in frames):
            raise LakeError("decode_failed", "Evidence frame falls outside the requested window.")
        return frames
