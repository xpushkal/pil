# syntax=docker/dockerfile:1.7

# ---- builder ---------------------------------------------------------------
# Use uv to resolve and install deps into a virtualenv, then bake ML models in
# a separate layer so they live inside the final image (no first-run downloads).
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

# Build tools needed by tiktoken / argon2-cffi / asyncpg wheels on slim.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
        ca-certificates \
        git \
    && rm -rf /var/lib/apt/lists/*

# Install uv
COPY --from=ghcr.io/astral-sh/uv:0.5.4 /uv /usr/local/bin/uv

WORKDIR /build

COPY pyproject.toml ./
# uv.lock is created on first install; we generate it here.
RUN uv venv /opt/venv \
    && . /opt/venv/bin/activate \
    && uv pip install --python /opt/venv/bin/python -e ".[dev]"

# Bake spaCy + sentence-transformers models into the image.
# This is the long step; later sprints rely on these being present at startup.
ENV HF_HOME=/opt/models/hf \
    SENTENCE_TRANSFORMERS_HOME=/opt/models/st \
    TRANSFORMERS_OFFLINE=0
RUN /opt/venv/bin/python -m spacy download en_core_web_lg \
    && /opt/venv/bin/python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2')"

# ---- runtime ---------------------------------------------------------------
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:${PATH}" \
    HF_HOME=/opt/models/hf \
    SENTENCE_TRANSFORMERS_HOME=/opt/models/st \
    TRANSFORMERS_OFFLINE=1 \
    HF_HUB_OFFLINE=1

# Minimal runtime deps. libgomp1 is needed by torch CPU wheels.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libgomp1 \
        curl \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 1001 pil \
    && useradd  --system --uid 1001 --gid pil --home /home/pil --shell /bin/bash pil \
    && mkdir -p /home/pil /app /opt/venv /opt/models \
    && chown -R pil:pil /home/pil /app /opt/venv /opt/models

COPY --from=builder --chown=pil:pil /opt/venv /opt/venv
COPY --from=builder --chown=pil:pil /opt/models /opt/models

WORKDIR /app
COPY --chown=pil:pil app ./app
COPY --chown=pil:pil config ./config
COPY --chown=pil:pil alembic.ini ./alembic.ini

USER pil

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/health/live || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
