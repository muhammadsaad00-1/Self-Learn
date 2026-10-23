# syntax=docker/dockerfile:1
# Stage 1: resolve and install dependencies with uv (build tools stay here).
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS build
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=0
WORKDIR /app
# Dependencies first, project second: code edits don't invalidate the dependency layer cache.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev --no-install-project
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev --no-editable

# Stage 2: minimal runtime — just Python + the venv, running as non-root.
FROM python:3.12-slim-bookworm
RUN apt-get update \
    && apt-get install --no-install-recommends -y poppler-utils tesseract-ocr tesseract-ocr-eng tesseract-ocr-urd \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 app \
    # Model cache mount point, owned by the app user: a named volume mounted here copies
    # this ownership, so the non-root process can download models into it.
    && mkdir /models && chown app:app /models
COPY --from=build --chown=app:app /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
USER app
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"]
CMD ["uvicorn", "agentflow.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
