"""Chat HTTP route -- thin wrapper around ChatService."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel

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


class ChatHttpRequest(BaseModel):
    """Pydantic model for incoming chat HTTP requests."""

    query: str
    language: str = "auto"
    profile: str = "HOME"
    user_id: str | None = None
    session_id: str | None = None
    context_turns: int = 5

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
    return ChatHttpResponse.from_result(result)
