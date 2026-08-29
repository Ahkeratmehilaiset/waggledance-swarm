"""Chat HTTP route -- thin wrapper around ChatService."""

from threading import Lock
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, field_validator, model_validator

from waggledance.adapters.http.deps import get_chat_service

try:
    from waggledance.application.dto.chat_dto import ChatRequest, ChatResult
except ImportError:
    from dataclasses import dataclass

    @dataclass
    class ChatRequest:
        """Minimal fallback for import isolation."""

        query: str
        language: str = "auto"
        profile: str = "HOME"
        user_id: str | None = None
        session_id: str | None = None
        context_turns: int = 5

    @dataclass
    class ChatResult:
        """Minimal fallback for import isolation."""

        response: str
        language: str
        source: str
        confidence: float
        latency_ms: float
        agent_id: str | None
        round_table: bool
        cached: bool
        route_stage_trace: list[dict[str, Any]] | None = None


router = APIRouter()


# Maximum query length (characters).  Prevents OOM / DoS via
# oversized payloads that block the LLM for minutes.
MAX_QUERY_LENGTH = 10_000
CHAT_ROUTE_STAGE_ORDER = (
    "language_detection",
    "hot_cache",
    "memory_context",
    "route_selection",
    "deterministic_solver",
    "hybrid_retrieval_8_cell",
    "hex_neighbor_assist_7_cell",
    "orchestrator_llm_fallback",
)
HEX_BACKED_ROUTE_STAGE_NAMES = (
    "hybrid_retrieval_8_cell",
    "hex_neighbor_assist_7_cell",
)
HEX_STAGE_COVERAGE_STATES = ("entered", "disabled")
# Resolution stages eligible to be the FIRST stage that SERVES a query, in
# pipeline order. Pre-processing stages (language_detection, memory_context,
# route_selection) cannot serve and are excluded. Measurement only: the
# hex_neighbor_assist_7_cell bucket is the honeycomb-first-served signal.
FIRST_SERVED_HOP_STAGES = (
    "hot_cache",
    "deterministic_solver",
    "hybrid_retrieval_8_cell",
    "hex_neighbor_assist_7_cell",
    "orchestrator_llm_fallback",
)
OPTIONAL_ROUTE_STAGE_COMPONENTS = {
    "hybrid_retrieval_8_cell": "_hybrid_retrieval",
    "hex_neighbor_assist_7_cell": "_hex_neighbor_assist",
}
ROUTE_STAGE_TRACE_ALLOWED_FIELDS = {
    "language_detection": {"explicit_hint", "detected_language"},
    "hot_cache": {"hit"},
    "memory_context": {"limit", "result_count", "memory_score"},
    "route_selection": {"route_type", "solver_intent", "memory_score"},
    "deterministic_solver": {"intent", "answered"},
    "hybrid_retrieval_8_cell": {
        "enabled",
        "authoritative",
        "answered",
        "retrieval_mode",
        "hit_count",
        "cell_id",
    },
    "hex_neighbor_assist_7_cell": {
        "enabled",
        "answered",
        "confidence",
        "source",
        "error",
    },
    "orchestrator_llm_fallback": {
        "route_type",
        "source",
        "confidence",
        "round_table_used",
    },
}

ROUTE_STAGE_LATENCY_BUCKETS_MS: tuple[float, ...] = (
    50.0,
    100.0,
    250.0,
    500.0,
    1000.0,
    2500.0,
    5000.0,
    10000.0,
)

ROUTE_STAGE_LATENCY_BUCKET_LABELS: tuple[str, ...] = (
    "50",
    "100",
    "250",
    "500",
    "1000",
    "2500",
    "5000",
    "10000",
    "+Inf",
)


def _first_served_route_hop(
    trace: list[dict[str, Any]] | None,
) -> str | None:
    """Return the first route stage that SERVED the query, in trace order.

    A stage "serves" when: ``hot_cache`` hit, ``deterministic_solver`` or
    ``hex_neighbor_assist_7_cell`` answered, authoritative
    ``hybrid_retrieval_8_cell`` answered, or the terminal
    ``orchestrator_llm_fallback`` is reached. Pre-processing stages cannot
    serve. Strict bool checks only (no truthy coercion). Read-only: derives a
    label from the already-sanitized trace; changes no behavior.
    """
    if not trace:
        return None
    for event in trace:
        if not isinstance(event, dict):
            continue
        stage = event.get("stage")
        if stage not in FIRST_SERVED_HOP_STAGES:
            continue
        if stage == "hot_cache":
            served = event.get("hit") is True
        elif stage == "hybrid_retrieval_8_cell":
            served = (
                event.get("answered") is True
                and event.get("authoritative") is True
            )
        elif stage == "orchestrator_llm_fallback":
            served = True
        else:
            served = event.get("answered") is True
        if served:
            return stage
    return None


class RouteStageRuntimeMetrics:
    """In-memory counters for sanitized chat route-stage observations."""

    def __init__(self, stage_order: tuple[str, ...] = CHAT_ROUTE_STAGE_ORDER):
        self._stage_order = tuple(stage_order)
        self._allowed_stages = set(self._stage_order)
        self._observations_total = {
            stage: 0 for stage in self._stage_order
        }
        self._request_latency_ms_total = {
            stage: 0.0 for stage in self._stage_order
        }
        self._request_latency_ms_buckets = {
            stage: {label: 0 for label in ROUTE_STAGE_LATENCY_BUCKET_LABELS}
            for stage in self._stage_order
        }
        self._hex_stage_coverage_total = {
            stage: {state: 0 for state in HEX_STAGE_COVERAGE_STATES}
            for stage in HEX_BACKED_ROUTE_STAGE_NAMES
        }
        self._first_served_hop_total = {
            stage: 0 for stage in FIRST_SERVED_HOP_STAGES
        }
        self._lock = Lock()

    def record(
        self,
        trace: list[dict[str, Any]] | None,
        request_latency_ms: float,
        disabled_route_stages: list[str] | None = None,
    ) -> None:
        """Record one sanitized route trace without storing payload fields."""

        if not trace:
            return
        try:
            latency_ms = float(request_latency_ms)
        except (TypeError, ValueError):
            latency_ms = 0.0
        if latency_ms < 0:
            latency_ms = 0.0

        observed: list[str] = []
        seen: set[str] = set()
        for event in trace:
            if not isinstance(event, dict):
                continue
            stage = event.get("stage")
            if (
                isinstance(stage, str)
                and stage in self._allowed_stages
                and stage not in seen
            ):
                observed.append(stage)
                seen.add(stage)

        if not observed:
            return
        disabled_hex_stages = {
            stage
            for stage in disabled_route_stages or []
            if isinstance(stage, str) and stage in HEX_BACKED_ROUTE_STAGE_NAMES
        }
        observed_set = set(observed)
        first_served_hop = _first_served_route_hop(trace)
        with self._lock:
            for stage in observed:
                self._observations_total[stage] += 1
                self._request_latency_ms_total[stage] += latency_ms
                for upper_bound, label in zip(
                    ROUTE_STAGE_LATENCY_BUCKETS_MS,
                    ROUTE_STAGE_LATENCY_BUCKET_LABELS[:-1],
                    strict=True,
                ):
                    if latency_ms <= upper_bound:
                        self._request_latency_ms_buckets[stage][label] += 1
                self._request_latency_ms_buckets[stage]["+Inf"] += 1
            for stage in HEX_BACKED_ROUTE_STAGE_NAMES:
                if stage in observed_set:
                    self._hex_stage_coverage_total[stage]["entered"] += 1
                elif stage in disabled_hex_stages:
                    self._hex_stage_coverage_total[stage]["disabled"] += 1
            if first_served_hop is not None:
                self._first_served_hop_total[first_served_hop] += 1

    def snapshot(self) -> dict[str, dict[str, float] | list[str]]:
        with self._lock:
            return {
                "stage_order": list(self._stage_order),
                "observations_total": {
                    stage: float(value)
                    for stage, value in self._observations_total.items()
                },
                "request_latency_ms_total": dict(
                    self._request_latency_ms_total
                ),
                "request_latency_ms_bucket_labels": list(
                    ROUTE_STAGE_LATENCY_BUCKET_LABELS
                ),
                "request_latency_ms_buckets": {
                    stage: {
                        label: float(value)
                        for label, value in buckets.items()
                    }
                    for stage, buckets in self._request_latency_ms_buckets.items()
                },
                "hex_stage_coverage_total": {
                    stage: {
                        state: float(value)
                        for state, value in counts.items()
                    }
                    for stage, counts in self._hex_stage_coverage_total.items()
                },
                "first_served_hop_total": {
                    stage: float(value)
                    for stage, value in self._first_served_hop_total.items()
                },
            }


def _route_stage_value_is_json_scalar(value: Any) -> bool:
    return value is None or isinstance(value, (bool, int, float, str))


def _sanitize_route_stage_event(event: dict[str, Any]) -> dict[str, Any] | None:
    stage = event.get("stage")
    if not isinstance(stage, str) or stage not in ROUTE_STAGE_TRACE_ALLOWED_FIELDS:
        return None
    sanitized: dict[str, Any] = {"stage": stage}
    for key in ROUTE_STAGE_TRACE_ALLOWED_FIELDS.get(stage, set()):
        if key not in event:
            continue
        value = event[key]
        if not _route_stage_value_is_json_scalar(value):
            continue
        if key == "detected_language" and value not in {"en", "fi", "custom"}:
            value = "custom"
        sanitized[key] = value
    return sanitized


def _sanitize_route_stage_trace(
    trace: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    if not trace:
        return []
    sanitized: list[dict[str, Any]] = []
    for event in trace:
        if not isinstance(event, dict):
            continue
        safe_event = _sanitize_route_stage_event(event)
        if safe_event is not None:
            sanitized.append(safe_event)
    return sanitized


def _route_stage_component_enabled(chat_service: Any, attr: str) -> bool:
    component = getattr(chat_service, attr, None)
    if component is None:
        return False
    return bool(getattr(component, "enabled", False))


def _route_stage_labels(
    trace: list[dict[str, Any]],
    chat_service: Any,
) -> list[dict[str, str]]:
    observed = {event["stage"] for event in trace}
    disabled = {
        stage
        for stage, attr in OPTIONAL_ROUTE_STAGE_COMPONENTS.items()
        if not _route_stage_component_enabled(chat_service, attr)
    }
    labels: list[dict[str, str]] = []
    added: set[str] = set()

    for stage in CHAT_ROUTE_STAGE_ORDER:
        if stage in observed:
            labels.append({
                "stage": stage,
                "status": "observed",
                "label": "observed",
            })
            added.add(stage)
        elif stage in disabled:
            labels.append({
                "stage": stage,
                "status": "disabled",
                "label": "disabled:runtime_config",
            })
            added.add(stage)

    for event in trace:
        stage = event["stage"]
        if stage not in added:
            labels.append({
                "stage": stage,
                "status": "observed",
                "label": "observed",
            })
            added.add(stage)

    return labels


def _build_chat_route_ws_event(
    resp: "ChatHttpResponse",
    chat_service: Any,
) -> dict[str, Any]:
    trace = _sanitize_route_stage_trace(resp.route_stage_trace)
    labels = _route_stage_labels(trace, chat_service)
    disabled_route_stages = [
        item["stage"] for item in labels if item["status"] == "disabled"
    ]
    return {
        "type": "chat_route",
        "data": {
            "source": resp.source,
            "confidence": resp.confidence,
            "agent_id": resp.agent_id,
            "route_stage_trace": trace,
            "route_stage_labels": labels,
            "disabled_route_stages": disabled_route_stages,
        },
    }


def _route_stage_runtime_metrics(container: Any) -> RouteStageRuntimeMetrics:
    metrics = getattr(container, "route_stage_runtime_metrics", None)
    if metrics is None:
        metrics = RouteStageRuntimeMetrics()
        setattr(container, "route_stage_runtime_metrics", metrics)
    return metrics


def _record_route_stage_runtime_metrics(
    container: Any,
    trace: list[dict[str, Any]] | None,
    request_latency_ms: float,
    disabled_route_stages: list[str] | None = None,
) -> None:
    try:
        metrics = _route_stage_runtime_metrics(container)
        metrics.record(trace, request_latency_ms, disabled_route_stages)
    except Exception:
        pass


class ChatHttpRequest(BaseModel):
    """Pydantic model for incoming chat HTTP requests."""

    query: str
    language: str = "auto"
    profile: str = "HOME"
    user_id: str | None = None
    session_id: str | None = None
    context_turns: int = 5

    @model_validator(mode="before")
    @classmethod
    def accept_message_alias(cls, data):
        """Accept ``message`` as a backwards-compat alias for ``query``.

        Many OpenAI-compatible clients send ``{"message": "..."}``. Rather
        than returning a cryptic 422, we silently rename it to ``query`` so
        the endpoint is ergonomic for those clients. If both fields are
        present, the explicit ``query`` wins.
        """
        if isinstance(data, dict):
            if "query" not in data and "message" in data:
                data = {**data, "query": data["message"]}
        return data

    @field_validator("query")
    @classmethod
    def query_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError(
                "query must be a non-empty string "
                "(hint: send {'query': '...'} or {'message': '...'})"
            )
        return v

    @field_validator("query")
    @classmethod
    def query_not_too_long(cls, v: str) -> str:
        if len(v) > MAX_QUERY_LENGTH:
            raise ValueError(
                f"query exceeds maximum length of {MAX_QUERY_LENGTH} characters"
            )
        return v

    def to_dto(self) -> ChatRequest:
        """Convert Pydantic model to application-layer DTO."""
        return ChatRequest(
            query=self.query,
            language=self.language,
            profile=self.profile,
            user_id=self.user_id,
            session_id=self.session_id,
            context_turns=self.context_turns,
        )


class ChatHttpResponse(BaseModel):
    """Pydantic model for outgoing chat HTTP responses."""

    response: str
    source: str
    confidence: float
    latency_ms: float
    cached: bool
    language: str = "en"
    agent_id: str | None = None
    round_table: bool = False
    route_stage_trace: list[dict[str, Any]] | None = None

    @classmethod
    def from_result(cls, r: ChatResult) -> "ChatHttpResponse":
        """Convert application-layer ChatResult to HTTP response model."""
        return cls(
            response=r.response,
            source=r.source,
            confidence=r.confidence,
            latency_ms=r.latency_ms,
            cached=r.cached,
            language=r.language,
            agent_id=r.agent_id,
            round_table=r.round_table,
            route_stage_trace=_sanitize_route_stage_trace(r.route_stage_trace),
        )


@router.post("/chat")
async def chat_endpoint(
    request: ChatHttpRequest,
    http_request: Request,
    chat_service=Depends(get_chat_service),
) -> ChatHttpResponse:
    """Handle a chat request.  No business logic here -- delegates entirely."""
    result = await chat_service.handle(request.to_dto())
    resp = ChatHttpResponse.from_result(result)
    route_stage_labels = _route_stage_labels(resp.route_stage_trace, chat_service)
    disabled_route_stages = [
        item["stage"] for item in route_stage_labels if item["status"] == "disabled"
    ]
    _record_route_stage_runtime_metrics(
        getattr(http_request.app.state, "container", None),
        resp.route_stage_trace,
        resp.latency_ms,
        disabled_route_stages,
    )

    # Broadcast chat_route event to WS clients (fire-and-forget)
    try:
        from waggledance.adapters.http.routes.compat_dashboard import broadcast_ws
        import asyncio
        asyncio.ensure_future(
            broadcast_ws(_build_chat_route_ws_event(resp, chat_service))
        )
    except Exception:
        pass  # WS broadcast is best-effort

    return resp
