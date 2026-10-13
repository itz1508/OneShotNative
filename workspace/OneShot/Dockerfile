# syntax=docker/dockerfile:1.7
#
# OneShot System — reproducible image built from the SAME `uv.lock` that local
# clean installs use.
#
# `pip install --no-cache-dir .` is deliberately NOT used: it ignores `uv.lock`
# and cannot be proven to install the reviewed versions. The build context is the
# Python project root (`workspace/OneShot/`), which is where `uv.lock` lives.

# --------------------------------------------------------------------------- #
# Build: resolve the environment from the reviewed lock only.
# --------------------------------------------------------------------------- #
FROM ghcr.io/astral-sh/uv:0.9.26-python3.11-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON=3.11 \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Dependency layer first, from the lock alone, so it caches across code changes.
# `--no-install-project` is correct here: pyproject sets `package = false`, so the
# project itself is never installed — only its pinned dependencies.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project --extra test

COPY pyproject.toml uv.lock ./
COPY systems ./systems
COPY tests ./tests

# Finalise against the same lock. `--locked` fails the build if the lock is stale.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --extra test

# Prove the image runs the target interpreter before it ships.
RUN /app/.venv/bin/python -c "import sys; assert sys.version_info[:2] == (3, 11), sys.version"

# --------------------------------------------------------------------------- #
# Runtime: the same 3.11 interpreter, an unprivileged user, no build tooling.
# --------------------------------------------------------------------------- #
FROM python:3.11-slim-bookworm AS runtime

RUN groupadd --system --gid 1001 oneshot \
 && useradd --system --uid 1001 --gid oneshot --create-home --home-dir /home/oneshot oneshot

WORKDIR /app
COPY --from=builder --chown=oneshot:oneshot /app /app

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    ONESHOT_HOST=0.0.0.0 \
    ONESHOT_PORT=8787

USER oneshot
EXPOSE 8787

HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=5 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8787/health')"

# `systems.api` exposes `app` at module scope (there is no `create_app` factory).
CMD ["uvicorn", "systems.api:app", "--host", "0.0.0.0", "--port", "8787"]
