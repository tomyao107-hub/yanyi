# syntax=docker/dockerfile:1.7

FROM node:22-bookworm-slim AS frontend-build
WORKDIR /build/frontend

COPY frontend/package.json frontend/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm \
    npm ci

COPY frontend/ ./
RUN npm run build


FROM python:3.12-slim-bookworm AS python-build
ENV PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /build

COPY pyproject.toml requirements.lock README.md ./
COPY backend/ ./backend/
RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip wheel --no-deps --wheel-dir /build/dist .


FROM python:3.12-slim-bookworm AS python-dependencies
ENV VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN python -m venv "$VIRTUAL_ENV"
WORKDIR /build

COPY requirements.lock ./
COPY --from=python-build /build/dist/ ./dist/
RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip install --requirement requirements.lock \
    && python -m pip install --no-deps ./dist/*.whl


FROM caddy:2.10.2-alpine AS caddy-runtime
USER root
RUN mkdir -p /data/caddy /config/caddy \
    && chown -R 1000:1000 /data /config
USER 1000:1000


FROM python:3.12-slim-bookworm AS app-runtime

ARG APP_UID=10001
ARG APP_GID=10001
RUN groupadd --gid "$APP_GID" trans \
    && useradd --uid "$APP_UID" --gid "$APP_GID" \
      --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin trans

ENV VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONFAULTHANDLER=1 \
    HOME=/tmp \
    XDG_CACHE_HOME=/tmp/.cache \
    TRANS_HOST=0.0.0.0 \
    TRANS_PORT=8000 \
    TRANS_STATE_DIR=/var/lib/trans \
    TRANS_DATA_DIR=/var/lib/trans \
    TRANS_UPLOAD_DIR=/var/lib/trans/uploads \
    TRANS_EXPORT_DIR=/var/lib/trans/exports \
    TRANS_TEMP_DIR=/var/lib/trans/tmp \
    TRANS_BACKUP_DIR=/var/lib/trans/backups \
    TRANS_DATABASE_URL=sqlite:////var/lib/trans/trans.db \
    TRANS_FRONTEND_DIST=/app/frontend/dist \
    TRANS_MAX_UPLOAD_MB=250

WORKDIR /app
COPY --from=python-dependencies /opt/venv /opt/venv
COPY backend/alembic.ini ./backend/alembic.ini
COPY backend/alembic/ ./backend/alembic/
COPY settings.toml ./settings.toml
COPY --from=frontend-build /build/frontend/dist ./frontend/dist/
COPY --chmod=0555 deploy/entrypoint.sh /usr/local/bin/trans-entrypoint

RUN mkdir -p /var/lib/trans/uploads /var/lib/trans/exports \
      /var/lib/trans/tmp /var/lib/trans/backups \
    && chown -R "$APP_UID:$APP_GID" /var/lib/trans \
    && chmod -R u=rwX,g=,o= /var/lib/trans

USER trans:trans
EXPOSE 8000
ENTRYPOINT ["/usr/local/bin/trans-entrypoint"]
