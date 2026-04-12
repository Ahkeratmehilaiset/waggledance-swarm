"""Chat HTTP route -- thin wrapper around ChatService."""

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


router = APIRouter()


# Maximum query length (characters).  Prevents OOM / DoS via
# oversized payloads that block the LLM for minutes.
MAX_QUERY_LENGTH = 10_000


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
        asyncio.ensure_future(broadcast_ws({
            "type": "chat_route",
            "data": {
                "source": resp.source,
                "confidence": resp.confidence,
                "agent_id": resp.agent_id,
            },
        }))
    except Exception:
        pass  # WS broadcast is best-effort

    return resp
