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

# Keep the existing CI dependency profile for this image. It is not an
# exact-lock restore or proof that optional features are installed; Chroma
# requires the explicit [chroma] extra described in the Docker quickstart.
# A switch to requirements.lock.txt needs separate Linux closure/build proof.
COPY requirements-ci.txt .
# Bootstrap a fixed installer before application dependency resolution.
# GHSA-qwm4-qh6w-59xr is fixed in pip 26.2.0; pin the verified patch release.
# The && chain stops the build if the installer upgrade fails.
RUN python -m pip install --no-cache-dir --upgrade pip==26.2.1 && \
    python -m pip install --no-cache-dir -r requirements-ci.txt

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
