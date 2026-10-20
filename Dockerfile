# syntax=docker/dockerfile:1.7
FROM ghcr.io/astral-sh/uv:0.9.8 AS uv

FROM python:3.12-slim AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev

FROM python:3.12-slim AS runtime
RUN groupadd --system ballast && useradd --system --gid ballast --home /app --shell /usr/sbin/nologin ballast
WORKDIR /app
COPY --from=build --chown=ballast:ballast /app /app
COPY --chown=ballast:ballast alembic.ini ./
COPY --chown=ballast:ballast migrations ./migrations
COPY --chown=ballast:ballast config ./config
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
USER ballast
CMD ["ballast", "--help"]
