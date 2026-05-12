"""Shared dashboard route state.

Kept outside route modules so hologram state synthesis does not import the
compatibility dashboard route just to count WebSocket clients.
"""

from __future__ import annotations


_ws_clients: set[object] = set()


def websocket_client_count() -> int:
    return len(_ws_clients)
