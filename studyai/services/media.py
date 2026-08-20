"""Safe FFprobe/FFmpeg media inspection, normalization, and segmentation."""

from __future__ import annotations

import json
import math
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


class MediaError(RuntimeError):
    pass


@dataclass(frozen=True)
class MediaInfo:
    duration_seconds: float
    media_type: str
    format_name: str


@dataclass(frozen=True)
class SegmentFile:
    index: int
    path: Path
    start_seconds: float
    end_seconds: float


class MediaService:
    def __init__(self, ffmpeg: str = "ffmpeg", ffprobe: str = "ffprobe", runner=None):
        self.ffmpeg = ffmpeg
        self.ffprobe = ffprobe
        self.runner = runner or subprocess.run

    def check_dependencies(self) -> None:
        for command in (self.ffmpeg, self.ffprobe):
            if Path(command).is_absolute():
                available = Path(command).is_file()
            else:
                available = shutil.which(command) is not None
            if not available:
                raise MediaError(f"Required media command is unavailable: {command}")

    def inspect(self, source: Path) -> MediaInfo:
        if not source.is_file() or source.stat().st_size == 0:
            raise MediaError("Media file is empty or missing")
        result = self._run([
            self.ffprobe, "-v", "error", "-show_entries",
            "format=duration,format_name:stream=codec_type", "-of", "json", str(source),
        ])
        try:
            payload = json.loads(result.stdout)
            duration = float(payload["format"]["duration"])
            stream_types = {stream["codec_type"] for stream in payload.get("streams", [])}
            format_name = payload["format"]["format_name"]
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise MediaError("ffprobe returned invalid media metadata") from error
        if duration <= 0 or "audio" not in stream_types:
            raise MediaError("File has no processable audio stream")
        return MediaInfo(duration, "video" if "video" in stream_types else "audio", format_name)

    def normalize_audio(self, source: Path, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        self._run([
            self.ffmpeg, "-nostdin", "-y", "-i", str(source), "-vn", "-ac", "1", "-ar",
            "16000", "-c:a", "flac", str(destination),
        ])
        if not destination.is_file() or destination.stat().st_size == 0:
            raise MediaError("FFmpeg did not create normalized audio")

    def segment_audio(
        self, source: Path, destination_dir: Path, duration: float, segment_seconds: int,
        overlap_seconds: int,
    ) -> list[SegmentFile]:
        if segment_seconds <= 0 or overlap_seconds < 0 or overlap_seconds >= segment_seconds:
            raise ValueError("Invalid segment or overlap duration")
        destination_dir.mkdir(parents=True, exist_ok=True)
        count = math.ceil(duration / segment_seconds)
        segments = []
        for index in range(count):
            start = index * segment_seconds
            overlap = overlap_seconds if index < count - 1 else 0
            end = min(duration, (index + 1) * segment_seconds + overlap)
            path = destination_dir / f"segment-{index:05d}.flac"
            self._run([
                self.ffmpeg, "-nostdin", "-y", "-ss", str(start), "-i", str(source),
                "-t", str(end - start), "-ac", "1", "-ar", "16000", "-c:a", "flac", str(path),
            ])
            if not path.is_file() or path.stat().st_size == 0:
                raise MediaError(f"FFmpeg failed to create segment {index}")
            segments.append(SegmentFile(index, path, start, end))
        return segments

    def _run(self, arguments: list[str]):
        try:
            return self.runner(arguments, check=True, capture_output=True, text=True, timeout=3600)
        except (OSError, subprocess.SubprocessError) as error:
            raise MediaError(f"Media command failed: {arguments[0]}") from error
