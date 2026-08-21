FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_ENV=production \
    DATABASE_PATH=/app/instance/studyai.sqlite3 \
    UPLOAD_ROOT=/app/instance/uploads \
    FFMPEG_PATH=ffmpeg \
    FFPROBE_PATH=ffprobe \
    PORT=8000

RUN apt-get update \
    && apt-get install --no-install-recommends -y ffmpeg \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 studyai

WORKDIR /app
COPY requirements.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt

COPY --chown=studyai:studyai . .
RUN mkdir -p /app/instance/uploads && chown -R studyai:studyai /app/instance

USER studyai
EXPOSE 8000
VOLUME ["/app/instance"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.getenv('PORT','8000')+'/health',timeout=3)" || exit 1

CMD ["./deploy/start.sh"]
