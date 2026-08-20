from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from studyai.services.media import MediaError, MediaService


def test_inspects_audio_and_video(tmp_path):
    source = tmp_path / "lecture.mp4"
    source.write_bytes(b"media")

    def runner(_arguments, **_kwargs):
        return SimpleNamespace(stdout=json.dumps({
            "format": {"duration": "42.5", "format_name": "mov,mp4"},
            "streams": [{"codec_type": "video"}, {"codec_type": "audio"}],
        }))

    info = MediaService(runner=runner).inspect(source)
    assert info.duration_seconds == 42.5
    assert info.media_type == "video"


def test_rejects_corrupt_or_silent_media(tmp_path):
    source = tmp_path / "fake.mp3"
    source.write_bytes(b"not audio")
    service = MediaService(runner=lambda *_args, **_kwargs: SimpleNamespace(
        stdout='{"format":{"duration":"1","format_name":"data"},"streams":[]}'
    ))
    with pytest.raises(MediaError, match="audio stream"):
        service.inspect(source)


def test_normalizes_and_segments_with_overlap(tmp_path):
    source = tmp_path / "lecture.mp4"
    source.write_bytes(b"media")
    calls = []

    def runner(arguments, **_kwargs):
        calls.append(arguments)
        destination = Path(arguments[-1])
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"flac")
        return SimpleNamespace(stdout="")

    service = MediaService(runner=runner)
    normalized = tmp_path / "normalized.flac"
    service.normalize_audio(source, normalized)
    segments = service.segment_audio(normalized, tmp_path / "segments", 65, 30, 5)
    assert [(item.index, item.start_seconds, item.end_seconds) for item in segments] == [
        (0, 0, 35), (1, 30, 65), (2, 60, 65),
    ]
    assert all("-nostdin" in call for call in calls)
    assert all(isinstance(argument, str) for call in calls for argument in call)


def test_ffmpeg_failure_is_safe(tmp_path):
    source = tmp_path / "lecture.mp3"
    source.write_bytes(b"media")

    def failing_runner(*_args, **_kwargs):
        raise subprocess.TimeoutExpired("ffmpeg", 1)

    with pytest.raises(MediaError, match="Media command failed"):
        MediaService(runner=failing_runner).normalize_audio(source, tmp_path / "out.flac")
