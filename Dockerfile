# Multi-stage build: build the Vite calculator, then run Flask with Gunicorn

# ---------- Builder: Node to build static calculator ----------
FROM node:20-alpine AS builder
WORKDIR /app

# Only install node deps first for better caching
COPY package*.json ./
RUN npm ci

# Copy the rest and build
COPY . .
RUN npm run build

# ---------- Runtime: Python with Gunicorn ----------
FROM python:3.11-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# System deps (optional minimal)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
  && rm -rf /var/lib/apt/lists/*

# Python deps
COPY requirements.txt ./
RUN python -m pip install --upgrade pip && pip install -r requirements.txt

# App code
COPY . .

# Bring in built calculator assets
COPY --from=builder /app/static/kostenrechner ./static/kostenrechner

# Railway provides $PORT
CMD ["bash", "-lc", "exec gunicorn app:app -b 0.0.0.0:${PORT}"]

