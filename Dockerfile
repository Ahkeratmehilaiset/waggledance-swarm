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

# Python deps. Phase 16F uses requirements-ci.txt (the cross-platform CI
# subset already proven on GitHub Actions Linux runners) instead of
# requirements.lock.txt. The lock file was generated against a Windows + cu118
# CUDA torch environment and pins Windows-only / Linux-incompatible packages
# (pywin32, triton-windows, torch==2.7.1+cu118, hard-pinned nvidia-cuda-* libs)
# whose conflict resolution against linux/amd64 PyPI wheels is not solvable
# without a substantial lock-file rewrite. requirements-ci.txt is what the CI
# uses and is therefore the documented Linux-portable install for this image.
# This intentionally drops faiss-cpu, playwright, unsloth, xformers — none of
# which are required by the autonomy proof scripts or the targeted smoke tests
# the v3.8.0 stable gate exercises (autonomy uses SQLite control plane only).
COPY requirements-ci.txt .
RUN pip install --no-cache-dir -r requirements-ci.txt

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

# Canonical entrypoint (v3.12.0): align Dockerfile CMD with the
# docker-compose command and the pyproject [project.scripts] entry,
# all of which delegate to waggledance.adapters.cli.start_runtime:main.
# start_waggledance.py is retained as a dev-convenience wrapper but
# the production-shape entrypoint is the python -m form.
CMD ["python", "-m", "waggledance.adapters.cli.start_runtime"]
