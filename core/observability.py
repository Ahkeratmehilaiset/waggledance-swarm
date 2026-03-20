"""Prometheus metrics for WaggleDance runtime.

Usage:
    from core.observability import CHAT_REQUESTS, CHAT_LATENCY
    CHAT_REQUESTS.labels(method="memory_fast", language="fi", agent_id="beekeeper").inc()
    with CHAT_LATENCY.time():
        response = await do_chat(...)
"""
from prometheus_client import Counter, Histogram, Gauge, Info

CHAT_REQUESTS = Counter(
    'waggle_chat_requests_total',
    'Total chat requests processed',
    ['method', 'language', 'agent_id'])

CHAT_LATENCY = Histogram(
    'waggle_chat_latency_seconds',
    'Chat request latency in seconds',
    buckets=[0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0])

MEMORY_STORE_SIZE = Gauge(
    'waggle_memory_store_size',
    'Number of items in memory store',
    ['collection'])

MICROMODEL_TRAINING = Counter(
    'waggle_micromodel_training_total',
    'Micromodel training runs completed',
    ['version', 'status'])

HALLUCINATION_DETECTED = Counter(
    'waggle_hallucination_detected_total',
    'Number of suspicious answers detected by hallucination checker')

ACTIVE_AGENTS = Gauge(
    'waggle_active_agents',
    'Number of currently active agents in the swarm')

OLLAMA_ERRORS = Counter(
    'waggle_ollama_errors_total',
    'Ollama API connection errors')

BUILD_INFO = Info(
    'waggle_build',
    'WaggleDance build information')
