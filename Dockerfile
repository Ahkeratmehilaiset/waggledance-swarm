# WaggleDance Swarm AI — single-stage Python image.
#
# The React dashboard (dashboard/) was archived in commit c15349d
# ("chore: archive React dashboard and remove /api/auth/token endpoints");
# the live UI is now served as a static HTML file (web/hologram-brain-v6.html)
# rendered by waggledance/adapters/http/routes/hologram.py. No node build
# stage needed.

FROM python:3.13-slim

WORKDIR /app

# System deps + Voikko Finnish morphological analyzer
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl git \
    libvoikko1 voikko-fi && \
    rm -rf /var/lib/apt/lists/*

# Python deps (pinned versions for reproducible builds). Full lock file,
# includes faiss-cpu and playwright — use requirements-ci.txt instead for a
# minimal deployment without hybrid retrieval or e2e browser testing.
COPY requirements.lock.txt .
RUN pip install --no-cache-dir -r requirements.lock.txt

# App code
COPY . .

# Create data dirs
RUN mkdir -p data/chroma_db logs

EXPOSE 8000

# Ollama runs outside container — connect via OLLAMA_HOST
ENV OLLAMA_HOST=http://host.docker.internal:11434
ENV PYTHONUTF8=1
ENV PYTHONUNBUFFERED=1

HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD curl -f http://localhost:8000/health || exit 1

CMD ["python", "start_waggledance.py"]
