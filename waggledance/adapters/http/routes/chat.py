"""Chat HTTP route -- thin wrapper around ChatService."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
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
OPTIONAL_ROUTE_STAGE_COMPONENTS = {
    "hybrid_retrieval_8_cell": "_hybrid_retrieval",
    "hex_neighbor_assist_7_cell": "_hex_neighbor_assist",
}


def _route_stage_trace_for_ws(
    trace: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    if not trace:
        return []
    return [
        dict(event)
        for event in trace
        if isinstance(event, dict) and isinstance(event.get("stage"), str)
    ]


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
    trace = _route_stage_trace_for_ws(resp.route_stage_trace)
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
            route_stage_trace=r.route_stage_trace,
        )


@router.post("/chat")
async def chat_endpoint(
    request: ChatHttpRequest,
    chat_service=Depends(get_chat_service),
) -> ChatHttpResponse:
    """Handle a chat request.  No business logic here -- delegates entirely."""
    result = await chat_service.handle(request.to_dto())
    resp = ChatHttpResponse.from_result(result)

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
