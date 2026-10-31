"""Read-only Q4 UTC helpers and the existing output privacy guard."""
import datetime as dt
from tools import bridge_mind_workspace as ws

def parse_utc(text):
    try:
        value = dt.datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return None
    return value.astimezone(dt.timezone.utc) if value.tzinfo is not None else None


def utc_text(value):
    return value.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
