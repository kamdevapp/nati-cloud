FROM python:3.13-slim

# ffmpeg: transkodowanie i pobieranie; nodejs: silnik JS wymagany przez yt-dlp dla YouTube
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg nodejs \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv/nati
COPY pyproject.toml ./
COPY app ./app
RUN pip install --no-cache-dir .

RUN useradd --create-home --uid 1000 nati && mkdir -p /data && chown nati /data
USER nati

ENV NATI_DATA_DIR=/data \
    NATI_MUSIC_DIRS=/music \
    PYTHONUNBUFFERED=1

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/info')"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers"]
