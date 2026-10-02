FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    OUTPUT_DIR=/output

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY transcript_extractor ./transcript_extractor

# compose.yaml overrides this with the syncthing user's UID/GID from .env.
USER 1000:1000

ENTRYPOINT ["python", "-m", "transcript_extractor"]
