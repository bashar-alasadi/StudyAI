"""Exercise StudyAI's real FFprobe/FFmpeg workflow with generated media."""

from __future__ import annotations

import argparse
import math
import struct
import tempfile
import wave
from pathlib import Path

from studyai.services.media import MediaService


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ffmpeg", default="ffmpeg")
    parser.add_argument("--ffprobe", default="ffprobe")
    args = parser.parse_args()
    service = MediaService(args.ffmpeg, args.ffprobe)

    with tempfile.TemporaryDirectory(prefix="studyai-native-media-") as temporary:
        root = Path(temporary)
        source = root / "محاضرة-تجريبية.wav"
        normalized = root / "normalized.flac"
        segments_dir = root / "segments"
        _write_tone(source, duration_seconds=5)

        service.check_dependencies()
        source_info = service.inspect(source)
        service.normalize_audio(source, normalized)
        normalized_info = service.inspect(normalized)
        segments = service.segment_audio(
            normalized,
            segments_dir,
            normalized_info.duration_seconds,
            segment_seconds=2,
            overlap_seconds=1,
        )

        assert source_info.media_type == "audio"
        assert 4.9 <= source_info.duration_seconds <= 5.1
        assert len(segments) == 3
        assert [segment.index for segment in segments] == [0, 1, 2]
        assert [(segment.start_seconds, segment.end_seconds) for segment in segments] == [
            (0, 3),
            (2, 5),
            (4, 5),
        ]
        assert all(service.inspect(segment.path).duration_seconds > 0 for segment in segments)
        print(
            "Native media smoke succeeded: "
            f"duration={source_info.duration_seconds:.3f}s segments={len(segments)}"
        )


def _write_tone(path: Path, duration_seconds: int) -> None:
    sample_rate = 16000
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        frames = (
            struct.pack("<h", int(4000 * math.sin(2 * math.pi * 440 * i / sample_rate)))
            for i in range(sample_rate * duration_seconds)
        )
        output.writeframes(b"".join(frames))


if __name__ == "__main__":
    main()
