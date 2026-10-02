FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    OUTPUT_DIR=/output \
    DATA_DIR=/data \
    HOST=0.0.0.0 \
    PORT=8000

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir --no-deps . && rm -rf /app/src

# compose.yaml overrides this with the syncthing user's UID/GID from .env.
USER 1000:1000

EXPOSE 8000
HEALTHCHECK --interval=1m --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4)" || exit 1

ENTRYPOINT ["transcript-extractor"]
# Runs the web app. `docker compose run --rm yt-transcripts <url>` replaces this
# with URLs and saves them from the command line instead.
CMD ["serve"]
