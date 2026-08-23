"""Bounded, cleanly managed Gemini integration."""

from __future__ import annotations

import logging
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
    def from_config(cls, config):
        from ..providers import resolve_ai_config

        config = resolve_ai_config(config)
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
            response = self.client.models.generate_content(
                model=self.model, contents=[remote_file, TRANSCRIPTION_PROMPT]
            )
            return self._response_text(response)
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

    def transcribe_youtube_url(self, url: str) -> str:
        """Ask Gemini to read a public YouTube video without downloading it on our host."""
        try:
            from google.genai import types

            video = types.Part(file_data=types.FileData(file_uri=url))
            response = self.client.models.generate_content(
                model=self.model,
                contents=[video, TRANSCRIPTION_PROMPT],
            )
            return self._response_text(response)
        except AIServiceError:
            raise
        except Exception as error:
            raise self._classify(
                error, "تعذر على خدمة الذكاء الاصطناعي قراءة فيديو YouTube الآن."
            ) from error

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
        try:
            response = self.client.models.count_tokens(model=self.model, contents=text)
            return int(response.total_tokens)
        except Exception as error:
            raise self._classify(error, "تعذر قياس حجم النص.") from error

    def _generate(self, prompt: str) -> str:
        try:
            response = self.client.models.generate_content(model=self.model, contents=prompt)
            return self._response_text(response)
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
        status = getattr(error, "status_code", None) or getattr(error, "code", None)
        retryable = isinstance(error, (TimeoutError, ConnectionError)) or status in {
            408,
            429,
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
