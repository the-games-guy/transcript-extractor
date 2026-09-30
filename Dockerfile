FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    VAULT_DIR=/vault DATA_DIR=/data HOST=0.0.0.0 PORT=8000

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir . && mkdir -p /vault /data

EXPOSE 8000
VOLUME ["/vault", "/data"]
HEALTHCHECK --interval=1m --timeout=5s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz')" || exit 1
CMD ["transcript-extractor"]
