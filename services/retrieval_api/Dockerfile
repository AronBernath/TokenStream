FROM python:3.11-slim AS base

ARG APP_UID=1000
ARG APP_GID=1000

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Copy shared common package and this service
COPY --chown=${APP_UID}:${APP_GID} services/common /app/common
COPY services/retrieval_api/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY --chown=${APP_UID}:${APP_GID} services/retrieval_api/app ./app

RUN groupadd --gid "${APP_GID}" app \
    && useradd --uid "${APP_UID}" --gid "${APP_GID}" --create-home --home-dir /home/app --shell /usr/sbin/nologin app \
    && mkdir -p /data /tmp \
    && chown -R app:app /app /data /home/app \
    && chmod 1777 /tmp

ENV PYTHONPATH=/app:/app/common

EXPOSE 8000
USER app
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

FROM base AS reranker

# Optional local reranking target. The default runtime target below intentionally
# avoids torch/sentence-transformers so the base retrieval image stays small.
USER root
COPY services/retrieval_api/requirements-reranker.txt .
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu \
    && pip install --no-cache-dir -r requirements-reranker.txt
USER app

FROM base AS runtime
