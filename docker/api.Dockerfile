
# syntax=docker/dockerfile:1
ARG UV_VERSION=0.8.0
FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv

# Stage 1: install dependencies and build the Python environment.
FROM python:3.15-slim-bookworm AS builder
COPY --from=uv /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable

# Stage 2: final lightweight image.
FROM python:3.15-slim-bookworm

RUN useradd --create-home --uid 1000 app
WORKDIR /app

COPY --from=builder --chown=app:app /app/.venv /app/.venv

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PROJECT_ROOT=/app

USER app
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"]

CMD ["uvicorn", "mlops.api:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
