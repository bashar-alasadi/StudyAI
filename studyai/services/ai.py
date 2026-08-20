"""Gemini integration isolated from HTTP and UI concerns."""

from __future__ import annotations

import os
import tempfile


class AIServiceError(RuntimeError):
    def __init__(self, message: str, public_message: str, status_code: int = 502):
        super().__init__(message)
        self.public_message = public_message
        self.status_code = status_code


class AIService:
    def __init__(self, client, model: str):
        self.client = client
        self.model = model

    @classmethod
    def from_config(cls, config):
        api_key = config.get("GEMINI_API_KEY")
        if not api_key:
            raise AIServiceError(
                "GEMINI_API_KEY is missing", "خدمة الذكاء الاصطناعي غير مهيأة.", 503
            )
        try:
            from google import genai
        except ImportError as error:
            raise AIServiceError(str(error), "خدمة الذكاء الاصطناعي غير متاحة.", 503) from error
        return cls(genai.Client(api_key=api_key), config["GEMINI_MODEL"])

    def transcribe(self, stream, extension: str) -> str:
        temp_path = None
        remote_file = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=f".{extension}") as temporary:
                stream.seek(0)
                while chunk := stream.read(1024 * 1024):
                    temporary.write(chunk)
                temp_path = temporary.name
            remote_file = self.client.files.upload(file=temp_path)
            response = self.client.models.generate_content(
                model=self.model,
                contents=[remote_file, TRANSCRIPTION_PROMPT],
            )
            return self._response_text(response)
        except AIServiceError:
            raise
        except Exception as error:
            raise AIServiceError(str(error), "تعذر تحويل الملف الآن. حاول لاحقًا.") from error
        finally:
            if remote_file is not None and getattr(remote_file, "name", None):
                try:
                    self.client.files.delete(name=remote_file.name)
                except Exception:
                    pass
            if temp_path:
                try:
                    os.remove(temp_path)
                except OSError:
                    pass

    def summarize(self, text: str) -> str:
        return self._generate(f"{SUMMARY_PROMPT}\n\nنص المحاضرة:\n{text}")

    def generate_questions(self, text: str) -> str:
        return self._generate(f"{QUESTIONS_PROMPT}\n\nنص المحاضرة:\n{text}")

    def _generate(self, prompt: str) -> str:
        try:
            response = self.client.models.generate_content(model=self.model, contents=prompt)
            return self._response_text(response)
        except AIServiceError:
            raise
        except Exception as error:
            raise AIServiceError(str(error), "تعذر إكمال الطلب الآن. حاول لاحقًا.") from error

    @staticmethod
    def _response_text(response) -> str:
        text = (getattr(response, "text", None) or "").strip()
        if not text:
            raise AIServiceError("Empty response from Gemini", "لم تُرجع الخدمة نتيجة قابلة للعرض.")
        return text


TRANSCRIPTION_PROMPT = """فرّغ هذه المحاضرة الصوتية كاملة وبدقة. لا تلخص ولا تخترع كلمات غير واضحة.
حافظ على لغة التسجيل وترتيب المحتوى، واستخدم علامات ترقيم وفقرات واضحة."""
SUMMARY_PROMPT = """لخّص المحاضرة بالعربية في عناوين ونقاط مرتبة. حافظ على المفاهيم والأمثلة
والتعريفات المهمة، ولا تضف معلومات غير موجودة في النص."""
QUESTIONS_PROMPT = """أنشئ مجموعة متنوعة من أسئلة المراجعة العربية من المحاضرة، تشمل أسئلة قصيرة
واختيارًا من متعدد، ثم ضع الإجابات في قسم منفصل في النهاية. لا تستخدم معلومات خارج النص."""
