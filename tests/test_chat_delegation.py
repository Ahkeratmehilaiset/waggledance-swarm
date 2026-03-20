"""Tests for chat delegation module."""
import pytest
from core.chat_delegation import AgentDelegator


class TestResponseValidation:
    def test_valid_response(self):
        assert AgentDelegator._is_valid_response("Mehiläiset ovat hyönteisiä.") is True

    def test_empty_response_invalid(self):
        assert AgentDelegator._is_valid_response("") is False
        assert AgentDelegator._is_valid_response("   ") is False

    def test_short_response_invalid(self):
        assert AgentDelegator._is_valid_response("ok") is False

    def test_error_markers_invalid(self):
        assert AgentDelegator._is_valid_response("[LLM-virhe: timeout]") is False
        assert AgentDelegator._is_valid_response("[Ollama ei vastaa]") is False

    def test_normal_response_valid(self):
        assert AgentDelegator._is_valid_response(
            "Pesän lämpötila on noin 35 astetta.") is True
