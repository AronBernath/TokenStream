FROM python:3.11-slim

ARG APP_UID=1000
ARG APP_GID=1000

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        tesseract-ocr \
        tesseract-ocr-eng \
        tesseract-ocr-hun \
    && rm -rf /var/lib/apt/lists/*

# Copy shared common package and this service
COPY --chown=${APP_UID}:${APP_GID} services/common /app/common
COPY services/ingestion_worker/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY --chown=${APP_UID}:${APP_GID} services/ingestion_worker/worker ./worker

RUN groupadd --gid "${APP_GID}" app \
    && useradd --uid "${APP_UID}" --gid "${APP_GID}" --create-home --home-dir /home/app --shell /usr/sbin/nologin app \
    && mkdir -p /data /tmp \
    && chown -R app:app /app /data /home/app \
    && chmod 1777 /tmp

ENV PYTHONPATH=/app/common

EXPOSE 8002
USER app
CMD ["uvicorn", "worker.server:app", "--host", "0.0.0.0", "--port", "8002"]
