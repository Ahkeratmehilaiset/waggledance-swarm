# ── Stage 1: Build React dashboard ─────────────────────────────
FROM node:20-slim AS dashboard-build

WORKDIR /dashboard
COPY dashboard/package.json dashboard/package-lock.json* ./
RUN npm ci --ignore-scripts
COPY dashboard/ ./
RUN npm run build

# ── Stage 2: Python runtime ───────────────────────────────────
FROM python:3.13-slim

WORKDIR /app

# System deps + Voikko Finnish morphological analyzer
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl git \
    libvoikko1 voikko-fi && \
    rm -rf /var/lib/apt/lists/*

# Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App code
COPY . .

# Copy pre-built dashboard (overwrites source)
COPY --from=dashboard-build /dashboard/dist /app/dashboard/dist

# Create data dirs
RUN mkdir -p data/chroma_db logs

EXPOSE 8000

# Ollama runs outside container — connect via OLLAMA_HOST
ENV OLLAMA_HOST=http://host.docker.internal:11434
ENV PYTHONUTF8=1
ENV PYTHONUNBUFFERED=1

HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD curl -f http://localhost:8000/health || exit 1

# Legacy entrypoint; new runtime: python -m waggledance.adapters.cli.start_runtime
CMD ["python", "main.py"]
