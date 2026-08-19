FROM python:3.11-slim

ARG APP_UID=1000
ARG APP_GID=1000

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Copy shared common package and this service
COPY --chown=${APP_UID}:${APP_GID} services/common /app/common
COPY services/orchestrator_api/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY --chown=${APP_UID}:${APP_GID} services/orchestrator_api/app ./app

RUN groupadd --gid "${APP_GID}" app \
    && useradd --uid "${APP_UID}" --gid "${APP_GID}" --create-home --home-dir /home/app --shell /usr/sbin/nologin app \
    && mkdir -p /runtime /tmp \
    && chown -R app:app /app /runtime /home/app \
    && chmod 1777 /tmp

ENV PYTHONPATH=/app:/app/common

EXPOSE 8004
USER app
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8004"]
