# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

from waggledance.adapters.http.routes.hologram import _redact_secrets


def test_redact_keeps_token_metrics_and_nonsecret_identifiers() -> None:
    payload = {
        "token_count": 42,
        "tokenization_method": "bpe",
        "broken_thresholds": {"warning": 0.7},
        "forgotten_solver_id": "solver-1",
        "nested": [{"output_token_count": 12}],
    }

    assert _redact_secrets(payload) == payload


def test_redact_drops_secret_key_shapes() -> None:
    payload = {
        "api_key": "secret",
        "api_key_value": "secret",
        "authorization": "Bearer secret",
        "user_password_hash": "hash",
        "secret_message_id": "secret-id",
        "access_token": "token",
        "token_value": "token",
        "public_metric": 12,
        "nested": {
            "refresh_token": "token",
            "visible": "ok",
        },
    }

    redacted = _redact_secrets(payload)

    assert redacted == {
        "public_metric": 12,
        "nested": {
            "visible": "ok",
        },
    }
