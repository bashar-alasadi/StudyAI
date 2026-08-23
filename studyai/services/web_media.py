"""Safely acquire public web media for the regular lecture pipeline."""

from __future__ import annotations

import ipaddress
import logging
import shutil
import socket
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from flask import current_app

from .uploads import ALLOWED_EXTENSIONS

logger = logging.getLogger(__name__)


class WebMediaError(RuntimeError):
    code = "web_media_failed"

    def __init__(self, public_message: str):
        super().__init__(public_message)
        self.public_message = public_message


def validate_source_url(url: str) -> str:
    value = url.strip()
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username:
        raise WebMediaError("أدخل رابطًا عامًا صحيحًا يبدأ بـ http أو https.")
    if parsed.port and parsed.port not in {80, 443}:
        raise WebMediaError("منفذ الرابط غير مسموح.")
    _require_public_host(parsed.hostname)
    return value


def download_web_media(url: str, work_dir: Path) -> tuple[Path, str]:
    url = validate_source_url(url)
    work_dir.mkdir(parents=True, exist_ok=True)
    if _is_youtube(urlsplit(url).hostname or ""):
        return _download_youtube(url, work_dir)
    return _download_direct(url, work_dir)


def _download_youtube(url: str, work_dir: Path) -> tuple[Path, str]:
    try:
        import yt_dlp

        output = str(work_dir / "source.%(ext)s")
        options = {
            # Speech does not benefit from a very high bitrate. Keeping the audio small
            # makes multi-hour lectures much more reliable on constrained hosting.
            "format": "bestaudio[abr<=96]/worstaudio/bestaudio/best",
            "outtmpl": output,
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "max_filesize": current_app.config["MAX_UPLOAD_SIZE_BYTES"],
            "socket_timeout": current_app.config["WEB_DOWNLOAD_TIMEOUT_SECONDS"],
            "continuedl": True,
            "retries": 10,
            "fragment_retries": 10,
            "extractor_retries": 5,
            "file_access_retries": 5,
            "concurrent_fragment_downloads": 1,
            "http_chunk_size": 10 * 1024 * 1024,
        }
        with yt_dlp.YoutubeDL(options) as downloader:
            info = downloader.extract_info(url, download=True)
            path = Path(downloader.prepare_filename(info))
        if not path.is_file():
            matches = list(work_dir.glob("source.*"))
            path = matches[0] if len(matches) == 1 else path
        _check_download(path)
        title = str(info.get("title") or "youtube-lecture")[:220]
        return path, f"{title}{path.suffix.lower()}"
    except WebMediaError:
        raise
    except Exception as error:
        logger.exception("YouTube download failed: %s", type(error).__name__)
        raise WebMediaError("تعذر تنزيل فيديو YouTube. تحقق من الرابط وإتاحة الفيديو.") from error


def fetch_youtube_transcript(url: str, segment_seconds: int = 1200):
    """Return complete caption chunks when a hosting IP is blocked by YouTube media."""
    video_id = _youtube_video_id(url)
    if not video_id:
        raise WebMediaError("تعذر تحديد معرّف فيديو YouTube من الرابط.")
    try:
        from youtube_transcript_api import YouTubeTranscriptApi

        transcript_list = YouTubeTranscriptApi().list(video_id)
        try:
            transcript = transcript_list.find_manually_created_transcript(["ar", "en"])
        except Exception:
            transcript = transcript_list.find_generated_transcript(["ar", "en"])
        entries = list(transcript.fetch())
        if not entries:
            raise WebMediaError("لا يحتوي فيديو YouTube على نص أو ترجمة متاحة.")
        chunks: list[tuple[float, float, str]] = []
        current: list[str] = []
        start = float(entries[0].start)
        end = start
        for entry in entries:
            entry_start = float(entry.start)
            entry_end = entry_start + float(entry.duration)
            if current and entry_start - start >= segment_seconds:
                chunks.append((start, end, " ".join(current).strip()))
                current, start = [], entry_start
            text = str(entry.text).replace("\n", " ").strip()
            if text:
                current.append(text)
            end = entry_end
        if current:
            chunks.append((start, end, " ".join(current).strip()))
        return chunks, end
    except WebMediaError:
        raise
    except Exception as error:
        logger.exception("YouTube transcript fallback failed: %s", type(error).__name__)
        raise WebMediaError(
            "حظر YouTube التنزيل من الخادم، ولا يتوفر لهذا الفيديو نص بديل قابل للمعالجة."
        ) from error


def _youtube_video_id(url: str) -> str | None:
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()
    if host == "youtu.be":
        return parsed.path.strip("/").split("/")[0] or None
    if _is_youtube(host):
        if parsed.path == "/watch":
            return parse_qs(parsed.query).get("v", [None])[0]
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) >= 2 and parts[0] in {"embed", "shorts", "live"}:
            return parts[1]
    return None


def _download_direct(url: str, work_dir: Path) -> tuple[Path, str]:
    parsed = urlsplit(url)
    suffix = Path(unquote(parsed.path)).suffix.lower()
    if suffix.lstrip(".") not in ALLOWED_EXTENSIONS:
        raise WebMediaError("الرابط المباشر يجب أن ينتهي بصيغة صوت أو فيديو مدعومة.")
    destination = work_dir / f"source{suffix}"
    maximum = current_app.config["MAX_UPLOAD_SIZE_BYTES"]
    request = urllib.request.Request(url, headers={"User-Agent": "StudyAI/1.0"})
    try:
        with _safe_opener().open(
            request, timeout=current_app.config["WEB_DOWNLOAD_TIMEOUT_SECONDS"]
        ) as response, destination.open("wb") as target:
            declared = int(response.headers.get("Content-Length", "0") or 0)
            if declared > maximum:
                raise WebMediaError("حجم الملف في الرابط يتجاوز الحد المسموح.")
            written = 0
            while chunk := response.read(1024 * 1024):
                written += len(chunk)
                if written > maximum:
                    raise WebMediaError("حجم الملف في الرابط يتجاوز الحد المسموح.")
                target.write(chunk)
        _check_download(destination)
        return destination, Path(unquote(parsed.path)).name[:255]
    except WebMediaError:
        destination.unlink(missing_ok=True)
        raise
    except (OSError, urllib.error.URLError) as error:
        destination.unlink(missing_ok=True)
        raise WebMediaError("تعذر تنزيل ملف الوسائط من الرابط.") from error


class _PublicRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_source_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _safe_opener():
    return urllib.request.build_opener(_PublicRedirectHandler())


def _require_public_host(hostname: str) -> None:
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(hostname, None)}
    except socket.gaierror as error:
        raise WebMediaError("تعذر الوصول إلى عنوان الرابط.") from error
    if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
        raise WebMediaError("لا يُسمح بروابط الشبكات المحلية أو الخاصة.")


def _is_youtube(hostname: str) -> bool:
    host = hostname.rstrip(".").lower()
    return host == "youtu.be" or host == "youtube.com" or host.endswith(".youtube.com")


def _check_download(path: Path) -> None:
    maximum = current_app.config["MAX_UPLOAD_SIZE_BYTES"]
    if not path.is_file() or path.stat().st_size <= 0:
        raise WebMediaError("لم يحتوِ الرابط على ملف وسائط صالح.")
    if path.stat().st_size > maximum:
        path.unlink(missing_ok=True)
        raise WebMediaError("حجم الملف في الرابط يتجاوز الحد المسموح.")
    if path.suffix.lower().lstrip(".") not in ALLOWED_EXTENSIONS:
        path.unlink(missing_ok=True)
        raise WebMediaError("صيغة الوسائط في الرابط غير مدعومة.")


def cleanup_download_dir(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)
