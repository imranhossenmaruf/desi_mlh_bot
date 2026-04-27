# Force Railway to build this as a pure Python project, bypassing the
# Replit pnpm monorepo wrapper at the repo root that was tripping up
# Railpack's auto-detection.

FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# System packages occasionally needed by tgcrypto / native wheels.
RUN apt-get update \
 && apt-get install -y --no-install-recommends gcc build-essential \
 && rm -rf /var/lib/apt/lists/*

# Install Python deps first (better layer caching).
COPY bot_repo/requirements.txt /app/bot_repo/requirements.txt
RUN pip install --upgrade pip \
 && pip install -r /app/bot_repo/requirements.txt

# Copy the actual bot source.
COPY bot_repo /app/bot_repo

WORKDIR /app/bot_repo
CMD ["python", "main.py"]
