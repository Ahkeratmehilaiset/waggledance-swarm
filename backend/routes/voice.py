"""GET /api/voice/status, POST /api/voice/text, POST /api/voice/audio — stub data."""
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()


@router.get("/api/voice/status")
async def voice_status():
    """Voice interface status — stub returns components unavailable."""
    return {
        "available": False,
        "enabled": False,
        "ready": False,
        "stt_available": False,
        "tts_available": False,
        "chat_connected": False,
    }


@router.post("/api/voice/text")
async def voice_text(request: Request):
    """Voice text pipeline — stub returns mock Finnish response."""
    body = await request.json()
    text = body.get("text", "").strip()
    if not text:
        return JSONResponse({"error": "Empty text"}, status_code=400)
    return {
        "input_text": text,
        "output_text": f"Tämä on stub-vastaus: '{text[:50]}'",
        "audio_base64": "",
        "latency_ms": 0.0,
    }


@router.post("/api/voice/audio")
async def voice_audio(request: Request):
    """Voice audio pipeline — stub returns mock transcription + response."""
    body = await request.json()
    audio_b64 = body.get("audio_base64", "")
    if not audio_b64:
        return JSONResponse({"error": "No audio data"}, status_code=400)
    return {
        "input_text": "(stub-transkriptio)",
        "output_text": "Tämä on stub-vastaus äänisyötteelle.",
        "audio_base64": "",
        "stt_latency_ms": 0.0,
        "chat_latency_ms": 0.0,
        "tts_latency_ms": 0.0,
        "total_latency_ms": 0.0,
        "wake_word_detected": False,
    }
