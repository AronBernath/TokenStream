FROM node:22-slim AS frontend

WORKDIR /app/services/dev-ui/frontend
RUN chown -R node:node /app

COPY --chown=node:node services/dev-ui/frontend/package*.json ./
USER node
RUN npm ci

COPY --chown=node:node services/dev-ui/frontend ./
RUN npm run build

FROM python:3.12-slim

ARG APP_UID=1000
ARG APP_GID=1000

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY services/dev-ui/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY --chown=${APP_UID}:${APP_GID} services/dev-ui/main.py .
COPY --chown=${APP_UID}:${APP_GID} --from=frontend /app/services/dev-ui/frontend/dist ./frontend/dist
COPY --chown=${APP_UID}:${APP_GID} packages/config_auth ./config_auth
COPY --chown=${APP_UID}:${APP_GID} services/common /app/common

RUN groupadd --gid "${APP_GID}" app \
    && useradd --uid "${APP_UID}" --gid "${APP_GID}" --create-home --home-dir /home/app --shell /usr/sbin/nologin app \
    && mkdir -p /data/config-auth /runtime /tmp \
    && chown -R app:app /app /data /runtime /home/app \
    && chmod 1777 /tmp

ENV PYTHONPATH=/app/common
EXPOSE 8010
USER app
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8010"]
