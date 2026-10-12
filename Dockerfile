FROM node:22-slim AS ui
WORKDIR /ui
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build


FROM python:3.13-slim

# git is not optional: vouch reads diffs from the repo under review.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN pip install --no-cache-dir ".[api,agent]"

# Served at / by FastAPI, so the whole product is on one port.
COPY --from=ui /ui/dist/ ./frontend/dist/
ENV VOUCH_UI_DIR=/app/frontend/dist

# The repo under review mounts here; vouch never writes to it.
VOLUME /workspace
ENV VOUCH_REPO_ROOT=/workspace

EXPOSE 8741
CMD ["uvicorn", "vouch.api.app:app", "--host", "0.0.0.0", "--port", "8741"]
