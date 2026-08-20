"""One low-cost, non-sensitive live Gemini media integration check."""

from __future__ import annotations

import math
import struct
import tempfile
import wave
from pathlib import Path

from studyai import create_app
from studyai.services.ai import AIService


def main() -> None:
    app = create_app({"JOB_QUEUE_MODE": "sync"})
    sample_path = Path(tempfile.gettempdir()) / "studyai-smoke-tone.wav"
    try:
        _write_tone(sample_path)
        with app.app_context(), AIService.from_config(app.config) as service:
            result = service.transcribe_path(sample_path)
        print(f"Gemini media smoke test succeeded; response characters: {len(result)}")
    finally:
        sample_path.unlink(missing_ok=True)


def _write_tone(path: Path) -> None:
    sample_rate = 16000
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        frames = (
            struct.pack("<h", int(5000 * math.sin(2 * math.pi * 440 * index / sample_rate)))
            for index in range(sample_rate)
        )
        output.writeframes(b"".join(frames))


if __name__ == "__main__":
    main()
