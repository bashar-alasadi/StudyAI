"""Bounded, cleanly managed Gemini integration."""

from __future__ import annotations

import logging
import math
import os
import tempfile
import time
from pathlib import Path

logger = logging.getLogger(__name__)


class AIServiceError(RuntimeError):
    def __init__(
        self,
        message: str,
        public_message: str,
        status_code: int = 502,
        *,
        retryable: bool = False,
        code: str = "provider_error",
    ):
        super().__init__(message)
        self.public_message = public_message
        self.status_code = status_code
        self.retryable = retryable
        self.code = code


class AIService:
    def __init__(
        self,
        client,
        model: str,
        file_ready_timeout: int = 120,
        poll_seconds: int = 2,
        sleeper=time.sleep,
    ):
        self.client = client
        self.model = model
        self.file_ready_timeout = file_ready_timeout
        self.poll_seconds = poll_seconds
        self.sleeper = sleeper

    @classmethod
    def from_config(cls, config, *, resolve_provider: bool = True):
        from ..providers import resolve_ai_config

        config = resolve_ai_config(config) if resolve_provider else dict(config)
        if resolve_provider and config.get("AI_PROVIDER") == "openai":
            return OpenAIService.from_config(config)
        api_key = config.get("GEMINI_API_KEY")
        if not api_key:
            raise AIServiceError(
                "GEMINI_API_KEY is missing",
                "خدمة الذكاء الاصطناعي غير مهيأة.",
                503,
                code="missing_api_key",
            )
        try:
            from google import genai
            from google.genai import types
        except ImportError as error:
            raise AIServiceError(
                str(error), "خدمة الذكاء الاصطناعي غير متاحة.", 503, code="sdk_missing"
            ) from error
        timeout_ms = config["GEMINI_REQUEST_TIMEOUT_SECONDS"] * 1000
        client = genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=timeout_ms))
        return cls(
            client,
            config["GEMINI_MODEL"],
            config["GEMINI_FILE_READY_TIMEOUT_SECONDS"],
            config["GEMINI_FILE_POLL_SECONDS"],
        )

    def __enter__(self):
        return self

    def __exit__(self, *_exc_info):
        self.close()

    def close(self) -> None:
        close = getattr(self.client, "close", None)
        if close:
            close()

    def transcribe_path(self, path: Path) -> str:
        remote_file = None
        try:
            remote_file = self.client.files.upload(file=path)
            remote_file = self._wait_until_ready(remote_file)
            return self._generate_text_with_fallback(
                [remote_file, TRANSCRIPTION_PROMPT], "تعذر تفريغ هذا الجزء الآن."
            )
        except AIServiceError:
            raise
        except Exception as error:
            raise self._classify(error, "تعذر تفريغ هذا الجزء الآن.") from error
        finally:
            if remote_file is not None and getattr(remote_file, "name", None):
                try:
                    self.client.files.delete(name=remote_file.name)
                except Exception as cleanup_error:
                    logger.warning(
                        "Gemini remote file cleanup failed: %s", type(cleanup_error).__name__
                    )

    def transcribe_youtube_url(
        self, url: str, duration_seconds: float | None = None, progress_callback=None
    ) -> str:
        """Read long public videos in bounded clips, then return the complete transcript."""
        from google.genai import types

        clip_seconds = 90 * 60
        maximum_clips = (
            min(16, max(1, math.ceil(duration_seconds / clip_seconds)))
            if duration_seconds
            else 6
        )
        transcripts: list[str] = []
        for index in range(maximum_clips):
            start = index * clip_seconds
            end = min((index + 1) * clip_seconds, duration_seconds or math.inf)
            video = types.Part(
                file_data=types.FileData(file_uri=url),
                video_metadata=types.VideoMetadata(
                    start_offset=f"{start}s", end_offset=f"{end:g}s", fps=0.1
                ),
            )
            prompt = (
                f"هذا المقطع الزمني من {start} إلى {end} ثانية. "
                "إذا كان المقطع بعد نهاية الفيديو فأعد فقط [NO_MEDIA]. "
                "وإذا وصل هذا المقطع إلى النهاية الفعلية للفيديو فأضف في آخر سطر "
                "[END_OF_VIDEO]. لا تضف هذه العلامة إذا كان الفيديو مستمرًا بعد هذا المقطع.\n"
                + TRANSCRIPTION_PROMPT
            )
            try:
                text = self._generate_youtube_clip(video, prompt)
            except Exception as error:
                logger.exception(
                    "Gemini YouTube clip failed index=%s start=%s end=%s", index, start, end
                )
                raise self._classify(
                    error, "تعذر على خدمة الذكاء الاصطناعي قراءة فيديو YouTube الآن."
                ) from error
            text, reached_end = self._parse_youtube_clip_result(text)
            if not text:
                break
            transcripts.append(f"[المقطع {index + 1}]\n{text}")
            if progress_callback:
                progress_callback(index + 1, maximum_clips)
            if reached_end:
                break
        if not transcripts:
            raise AIServiceError(
                "YouTube URL returned no media", "لم يُرجع فيديو YouTube محتوى قابلًا للتفريغ."
            )
        return "\n\n".join(transcripts)

    @staticmethod
    def _parse_youtube_clip_result(text: str) -> tuple[str, bool]:
        cleaned = text.strip()
        if cleaned == "[NO_MEDIA]":
            return "", True
        reached_end = "[END_OF_VIDEO]" in cleaned
        return cleaned.replace("[END_OF_VIDEO]", "").strip(), reached_end

    def _generate_youtube_clip(self, video, prompt: str) -> str:
        return self._generate_text_with_fallback(
            [video, prompt], "تعذر قراءة فيديو YouTube."
        )

    def _model_candidates(self) -> list[str]:
        # Use currently supported stable Gemini models. Quotas are enforced per
        # model, so a second stable model can keep a long lecture moving when
        # the configured model's free allowance has been consumed.
        return list(
            dict.fromkeys(
                (
                    self.model,
                    "gemini-3.7-flash",
                    "gemini-3.5-flash",
                    "gemini-3.5-flash-lite",
                    "gemini-3.1-flash-lite",
                )
            )
        )

    def _generate_text_with_fallback(self, contents, public_message: str) -> str:
        last_error: AIServiceError | None = None
        for model in self._model_candidates():
            try:
                response = self.client.models.generate_content(
                    model=model, contents=contents
                )
                return self._response_text(response)
            except Exception as error:
                classified = self._classify(error, public_message)
                last_error = classified
                if not classified.retryable and not self._model_unavailable(error):
                    raise classified from error
                logger.warning(
                    "Gemini model failed model=%s category=%s; trying fallback",
                    model,
                    classified.code,
                )
        if last_error is None:
            raise AIServiceError("No Gemini models configured", "لا يوجد نموذج Gemini متاح.")
        raise last_error

    @staticmethod
    def _model_unavailable(error: Exception) -> bool:
        message = str(error).lower()
        return (
            "model" in message
            and any(
                marker in message
                for marker in ("not_found", "not found", "no longer available", "deprecated")
            )
        )

    def transcribe(self, stream, extension: str) -> str:
        """Compatibility path for the Phase 1 endpoint; chunked jobs use transcribe_path."""
        temporary_path = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=f".{extension}") as temporary:
                while data := stream.read(1024 * 1024):
                    temporary.write(data)
                temporary_path = Path(temporary.name)
            return self.transcribe_path(temporary_path)
        finally:
            if temporary_path:
                try:
                    os.remove(temporary_path)
                except OSError as cleanup_error:
                    logger.warning(
                        "Local AI compatibility file cleanup failed: %s",
                        type(cleanup_error).__name__,
                    )

    def summarize(self, text: str) -> str:
        return self._generate(f"{SUMMARY_PROMPT}\n\nنص المحاضرة:\n{text}")

    def generate_questions(self, text: str) -> str:
        return self._generate(f"{QUESTIONS_PROMPT}\n\nنص المحاضرة:\n{text}")

    def explain_with_examples(self, text: str) -> str:
        return self._generate(f"{EXPLANATION_PROMPT}\n\nمحتوى المحاضرة:\n{text}")

    def count_tokens(self, text: str) -> int:
        last_error: AIServiceError | None = None
        for model in self._model_candidates():
            try:
                response = self.client.models.count_tokens(model=model, contents=text)
                return int(response.total_tokens)
            except Exception as error:
                classified = self._classify(error, "تعذر قياس حجم النص.")
                last_error = classified
                if not classified.retryable and not self._model_unavailable(error):
                    raise classified from error
        if last_error is None:
            raise AIServiceError("No Gemini models configured", "لا يوجد نموذج Gemini متاح.")
        raise last_error

    def _generate(self, prompt: str) -> str:
        try:
            return self._generate_text_with_fallback(prompt, "تعذر إكمال الطلب الآن.")
        except AIServiceError:
            raise
        except Exception as error:
            raise self._classify(error, "تعذر إكمال الطلب الآن.") from error

    def _wait_until_ready(self, remote_file):
        deadline = time.monotonic() + self.file_ready_timeout
        while self._state_name(remote_file) == "PROCESSING":
            if time.monotonic() >= deadline:
                raise AIServiceError(
                    "Gemini file processing timed out",
                    "استغرقت تهيئة الملف وقتًا أطول من المسموح.",
                    retryable=True,
                    code="file_timeout",
                )
            self.sleeper(self.poll_seconds)
            remote_file = self.client.files.get(name=remote_file.name)
        state = self._state_name(remote_file)
        if state in {"FAILED", "ERROR"}:
            raise AIServiceError(
                f"Gemini file entered {state}",
                "رفض مزوّد الذكاء الاصطناعي الملف.",
                code="file_rejected",
            )
        return remote_file

    @staticmethod
    def _state_name(remote_file) -> str | None:
        state = getattr(remote_file, "state", None)
        value = getattr(state, "name", state)
        return str(value).upper().split(".")[-1] if value is not None else None

    @staticmethod
    def _response_text(response) -> str:
        text = (getattr(response, "text", None) or "").strip()
        if not text:
            raise AIServiceError(
                "Empty response from Gemini",
                "لم تُرجع الخدمة نتيجة قابلة للعرض.",
                retryable=True,
                code="empty_response",
            )
        return text

    @staticmethod
    def _classify(error: Exception, public_message: str) -> AIServiceError:
        if isinstance(error, AIServiceError):
            return error
        status = getattr(error, "status_code", None) or getattr(error, "code", None)
        message = str(error).lower()
        quota_exhausted = status == 429 or any(
            marker in message
            for marker in ("resource_exhausted", "quota exceeded", "quota_exceeded")
        )
        if quota_exhausted:
            return AIServiceError(
                f"{type(error).__name__}: {error}",
                "نفدت حصة مزوّد الذكاء الاصطناعي لهذا النموذج. جرّب لاحقًا أو أضف رصيدًا للمزوّد.",
                429,
                retryable=True,
                code="provider_quota",
            )
        retryable = isinstance(error, (TimeoutError, ConnectionError)) or status in {
            408,
            500,
            502,
            503,
            504,
        }
        return AIServiceError(
            f"{type(error).__name__}: {error}",
            public_message,
            502,
            retryable=retryable,
            code="provider_transient" if retryable else "provider_rejected",
        )


TRANSCRIPTION_PROMPT = """فرّغ كل الكلام المسموع في هذا الجزء كاملًا وبدقة، من بدايته إلى نهايته.
لا تلخص، لا تحذف، ولا تضف معلومات. حافظ على اللغة والترتيب، واستخدم فقرات وعلامات ترقيم.
إذا كانت كلمة غير واضحة فاكتب [غير واضح] بدل اختراعها."""
SUMMARY_PROMPT = """لخّص المحاضرة بالعربية في عناوين ونقاط مرتبة. حافظ على المفاهيم والأمثلة
والتعريفات المهمة، ولا تضف معلومات غير موجودة في النص."""
QUESTIONS_PROMPT = """أنشئ أسئلة مراجعة عربية متنوعة من المحاضرة، ثم ضع الإجابات في قسم منفصل.
لا تستخدم معلومات خارج النص."""
EXPLANATION_PROMPT = """أنشئ دليلًا دراسيًا عربيًا يشرح جميع المفاهيم المهمة في المحتوى بوضوح
وتدرج. أضف أمثلة توضيحية جديدة عند فائدتها، لكن ضع أمام كل مثال جديد عبارة [مثال توضيحي مضاف]
حتى لا يختلط بكلام المحاضر. لا تغيّر حقائق المحاضرة، واذكر عند الحاجة أن المثال للتوضيح فقط."""


class OpenAIService:
    """OpenAI provider implementing the same pipeline interface as Gemini."""

    def __init__(self, client, model: str, transcription_model: str):
        self.client = client
        self.model = model
        self.transcription_model = transcription_model

    @classmethod
    def from_config(cls, config):
        api_key = config.get("OPENAI_API_KEY")
        if not api_key:
            raise AIServiceError(
                "OPENAI_API_KEY is missing", "مفتاح OpenAI API غير مضاف في لوحة الإدارة.",
                503, code="missing_api_key",
            )
        try:
            from openai import OpenAI
        except ImportError as error:
            raise AIServiceError(
                str(error), "مكتبة OpenAI غير متاحة.", 503, code="sdk_missing"
            ) from error
        return cls(
            OpenAI(api_key=api_key, timeout=config["GEMINI_REQUEST_TIMEOUT_SECONDS"]),
            config["OPENAI_MODEL"],
            config["OPENAI_TRANSCRIPTION_MODEL"],
        )

    def __enter__(self):
        return self

    def __exit__(self, *_exc_info):
        self.close()

    def close(self):
        self.client.close()

    def transcribe_path(self, path: Path) -> str:
        try:
            with path.open("rb") as audio:
                response = self.client.audio.transcriptions.create(
                    model=self.transcription_model, file=audio,
                    prompt="تفريغ دقيق وكامل مع الحفاظ على لغة المتحدث وترتيب الكلام.",
                )
            text = (getattr(response, "text", None) or "").strip()
            if not text:
                raise AIServiceError("Empty OpenAI transcript", "لم يرجع OpenAI نصًا.")
            return text
        except AIServiceError:
            raise
        except Exception as error:
            raise AIService._classify(error, "تعذر تفريغ الملف عبر OpenAI.") from error

    def transcribe_youtube_url(self, _url: str, progress_callback=None) -> str:
        raise AIServiceError(
            "OpenAI does not accept YouTube video URLs",
            "معالجة رابط YouTube المباشر تحتاج Gemini؛ استخدم OpenAI بعد تنزيل الملف.",
            code="youtube_not_supported",
        )

    def count_tokens(self, text: str) -> int:
        return max(1, len(text) // 4)

    def summarize(self, text: str) -> str:
        return self._generate(f"{SUMMARY_PROMPT}\n\nنص المحاضرة:\n{text}")

    def generate_questions(self, text: str) -> str:
        return self._generate(f"{QUESTIONS_PROMPT}\n\nنص المحاضرة:\n{text}")

    def explain_with_examples(self, text: str) -> str:
        return self._generate(f"{EXPLANATION_PROMPT}\n\nمحتوى المحاضرة:\n{text}")

    def _generate(self, prompt: str) -> str:
        try:
            response = self.client.responses.create(model=self.model, input=prompt)
            text = (response.output_text or "").strip()
            if not text:
                raise AIServiceError("Empty OpenAI response", "لم يرجع OpenAI نتيجة.")
            return text
        except AIServiceError:
            raise
        except Exception as error:
            raise AIService._classify(error, "تعذر إكمال الطلب عبر OpenAI.") from error
