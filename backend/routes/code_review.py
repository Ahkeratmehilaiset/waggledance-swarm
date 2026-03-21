"""GET /api/code-review — Code self-review suggestions for dashboard."""
import json
from pathlib import Path
from fastapi import APIRouter

router = APIRouter()

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_SUGGESTIONS_FILE = _PROJECT_ROOT / "data" / "code_suggestions.jsonl"


def _load_suggestions(limit: int = 10) -> list[dict]:
    """Load recent code suggestions from jsonl file."""
    if not _SUGGESTIONS_FILE.exists():
        return []
    try:
        rows = []
        with open(_SUGGESTIONS_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
        # Return most recent, up to limit
        return rows[-limit:][::-1]
    except Exception:
        return []


@router.get("/api/code-review")
async def code_review_status():
    """Return code self-review status and recent suggestions."""
    suggestions = _load_suggestions()
    return {
        "enabled": True,
        "suggestions_count": len(suggestions),
        "recent_suggestions": suggestions,
        "last_run": suggestions[0].get("timestamp") if suggestions else None,
    }


@router.get("/api/code-review/suggestions")
async def code_review_suggestions(limit: int = 20):
    """Return code suggestions list."""
    suggestions = _load_suggestions(limit=limit)
    return {
        "count": len(suggestions),
        "suggestions": suggestions,
    }
