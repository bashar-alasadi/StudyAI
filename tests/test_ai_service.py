from pathlib import Path
from types import SimpleNamespace

import pytest

from studyai.services.ai import (
    AIService,
    AIServiceError,
    OpenAIService,
    transcription_prompt,
)


class FilesAPI:
    def __init__(self, states=("ACTIVE",), delete_error=None, upload_error=None):
        self.states = list(states)
        self.delete_error = delete_error
        self.upload_error = upload_error
        self.deleted = []
        self.get_calls = 0

    def upload(self, **_kwargs):
        if self.upload_error:
            raise self.upload_error
        return SimpleNamespace(name="files/test", state=self.states[0])

    def get(self, **_kwargs):
        self.get_calls += 1
        return SimpleNamespace(name="files/test", state=self.states[self.get_calls])

    def delete(self, *, name):
        if self.delete_error:
            raise self.delete_error
        self.deleted.append(name)


class ModelsAPI:
    def __init__(self, text="نص كامل"):
        self.text = text

    def generate_content(self, **_kwargs):
        return SimpleNamespace(text=self.text)

    def count_tokens(self, **_kwargs):
        return SimpleNamespace(total_tokens=123)


class Client:
    def __init__(self, files=None, models=None):
        self.files = files or FilesAPI()
        self.models = models or ModelsAPI()
        self.closed = False

    def close(self):
        self.closed = True


def test_upload_wait_generation_cleanup_and_client_close(tmp_path):
    client = Client(files=FilesAPI(("PROCESSING", "ACTIVE")))
    path = tmp_path / "segment.flac"
    path.write_bytes(b"audio")
    service = AIService(client, "model", sleeper=lambda _seconds: None)
    with service:
        assert service.transcribe_path(path) == "نص كامل"
    assert client.files.get_calls == 1
    assert client.files.deleted == ["files/test"]
    assert client.closed is True


def test_processing_timeout_still_deletes_remote_file(tmp_path):
    client = Client(files=FilesAPI(("PROCESSING",)))
    path = tmp_path / "segment.flac"
    path.write_bytes(b"audio")
    service = AIService(client, "model", file_ready_timeout=0, sleeper=lambda _seconds: None)
    with pytest.raises(AIServiceError) as captured:
        service.transcribe_path(path)
    assert captured.value.retryable is True
    assert captured.value.code == "file_timeout"
    assert client.files.deleted == ["files/test"]


def test_upload_failure_is_classified_without_cleanup(tmp_path):
    client = Client(files=FilesAPI(upload_error=TimeoutError("network timeout")))
    with pytest.raises(AIServiceError) as captured:
        AIService(client, "model").transcribe_path(Path(tmp_path / "missing.flac"))
    assert captured.value.retryable is True
    assert client.files.deleted == []


def test_empty_response_and_remote_delete_failure_are_controlled(tmp_path, caplog):
    files = FilesAPI(delete_error=RuntimeError("delete failed"))
    client = Client(files=files, models=ModelsAPI(""))
    path = tmp_path / "segment.flac"
    path.write_bytes(b"audio")
    with pytest.raises(AIServiceError, match="Empty response"):
        AIService(client, "model").transcribe_path(path)
    assert "cleanup failed" in caplog.text


def test_token_count_and_generation():
    service = AIService(Client(), "model")
    assert service.count_tokens("محاضرة") == 123
    assert service.summarize("محاضرة كاملة") == "نص كامل"
    assert service.generate_questions("محاضرة كاملة") == "نص كامل"


def test_verbatim_prompt_preserves_mixed_languages_and_forbids_rewriting():
    prompt = transcription_prompt(True)
    assert "العربي يبقى عربيًا" in prompt
    assert "الإنجليزي يبقى إنجليزيًا" in prompt
    assert "لا تترجم" in prompt
    assert "دون تلخيص أو حذف أو إضافة أو شرح أو تصحيح أو إعادة صياغة" in prompt
    assert "المصطلحات" in prompt


def test_readable_mode_is_distinct_from_verbatim_mode():
    assert transcription_prompt(False) != transcription_prompt(True)
    assert "يمكن حذف التردد اللفظي" in transcription_prompt(False)


def test_youtube_model_fallback_uses_supported_stable_model():
    class QuotaError(RuntimeError):
        status_code = 429

    class FallbackModels:
        def __init__(self):
            self.calls = []

        def generate_content(self, *, model, **_kwargs):
            self.calls.append(model)
            if model == "gemini-3.6-flash":
                raise QuotaError("RESOURCE_EXHAUSTED: quota exceeded")
            return SimpleNamespace(text="تفريغ المقطع")

    models = FallbackModels()
    service = AIService(Client(models=models), "gemini-3.6-flash")
    assert service._generate_youtube_clip(None, "prompt") == "تفريغ المقطع"
    assert models.calls == ["gemini-3.6-flash", "gemini-3.7-flash"]


def test_quota_error_has_clear_public_message():
    class QuotaError(RuntimeError):
        status_code = 429

    classified = AIService._classify(QuotaError("RESOURCE_EXHAUSTED"), "generic")
    assert classified.code == "provider_quota"
    assert classified.retryable is True
    assert "نفدت حصة" in classified.public_message


def test_existing_ai_service_error_is_not_masked():
    original = AIServiceError("quota", "رسالة واضحة", 429, retryable=True, code="quota")
    assert AIService._classify(original, "generic") is original


def test_youtube_end_marker_is_removed_and_stops_extra_clip():
    text, reached_end = AIService._parse_youtube_clip_result(
        "آخر كلام في المحاضرة.\n[END_OF_VIDEO]"
    )
    assert text == "آخر كلام في المحاضرة."
    assert reached_end is True
    assert AIService._parse_youtube_clip_result("[NO_MEDIA]") == ("", True)


def test_verbatim_youtube_transcript_has_no_generated_segment_heading():
    service = AIService(Client(), "model")
    service._generate_youtube_clip = lambda _video, _prompt: (
        "مصطلح API يبقى كما نُطق.\n[END_OF_VIDEO]"
    )
    transcript = service.transcribe_youtube_url(
        "https://youtu.be/example", duration_seconds=60, verbatim=True
    )
    assert transcript == "مصطلح API يبقى كما نُطق."
    assert "[المقطع" not in transcript


def test_internal_error_after_successful_youtube_clips_is_recognized_as_end():
    error = AIServiceError(
        "ServerError: 500 INTERNAL server error",
        "تعذر قراءة الفيديو.",
        retryable=True,
        code="provider_transient",
    )
    assert AIService._looks_like_youtube_end_failure(error) is True
    quota = AIServiceError(
        "429 RESOURCE_EXHAUSTED", "نفدت الحصة.", retryable=True, code="provider_quota"
    )
    assert AIService._looks_like_youtube_end_failure(quota) is False


def test_openai_provider_transcribes_and_generates(tmp_path):
    class Transcriptions:
        def create(self, **_kwargs):
            return SimpleNamespace(text="تفريغ OpenAI كامل")

    class Responses:
        def create(self, **_kwargs):
            return SimpleNamespace(output_text="نتيجة OpenAI")

    client = SimpleNamespace(
        audio=SimpleNamespace(transcriptions=Transcriptions()),
        responses=Responses(),
        close=lambda: None,
    )
    path = tmp_path / "lecture.mp3"
    path.write_bytes(b"audio")
    service = OpenAIService(client, "gpt-test", "gpt-transcribe")
    assert service.transcribe_path(path) == "تفريغ OpenAI كامل"
    assert service.summarize("نص") == "نتيجة OpenAI"
    assert service.generate_questions("نص") == "نتيجة OpenAI"
