"""Use case: handle an incoming chat message."""

from waggledance.application.dto.chat_dto import ChatRequest, ChatResult
from waggledance.application.services.chat_service import ChatService


async def handle_chat(req: ChatRequest, chat_service: ChatService) -> ChatResult:
    """Process a chat request through the full pipeline."""
    return await chat_service.handle(req)
