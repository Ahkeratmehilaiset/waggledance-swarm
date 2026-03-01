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

# Create data dirs
RUN mkdir -p data/chroma_db logs

# Ports: 8000 = API, 5173 = React dev server (optional)
EXPOSE 8000 5173

# Ollama runs outside container — connect via OLLAMA_HOST
ENV OLLAMA_HOST=http://host.docker.internal:11434
ENV PYTHONUTF8=1
ENV PYTHONUNBUFFERED=1

CMD ["python", "main.py"]
