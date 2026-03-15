"""HTTP smoke tests for the new runtime (stub mode).

Tests R-3, R-4 acceptance criteria from WORK_ORDER.md.
Uses FastAPI TestClient — no actual server needed.
"""

import asyncio
import unittest


class TestStubSmoke(unittest.TestCase):
    """Stub-mode smoke tests via TestClient."""

    @classmethod
    def setUpClass(cls):
        from waggledance.adapters.config.settings_loader import WaggleSettings
        from waggledance.bootstrap.container import Container

        settings = WaggleSettings.from_env()
        cls.container = Container(settings=settings, stub=True)
        cls.app = cls.container.build_app()

        from starlette.testclient import TestClient

        cls.client = TestClient(cls.app, raise_server_exceptions=False)

    def test_stub_health(self):
        """GET /health -> 200 in stub mode."""
        resp = self.client.get("/health")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "ok")

    def test_stub_ready(self):
        """GET /ready -> 200 in stub mode (all stubs report ready)."""
        resp = self.client.get("/ready")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["ready"])

    def test_stub_chat(self):
        """POST /api/chat -> 200 with response in stub mode."""
        resp = self.client.post(
            "/api/chat",
            json={"query": "Hello world"},
            headers={"Authorization": f"Bearer {self.container._settings.api_key}"},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("response", data)
        self.assertTrue(len(data["response"]) > 0)

    def test_stub_chat_finnish(self):
        """POST /api/chat with Finnish text -> response without crash."""
        resp = self.client.post(
            "/api/chat",
            json={"query": "Mikä on mehiläisten hoitokynnys?"},
            headers={"Authorization": f"Bearer {self.container._settings.api_key}"},
        )
        self.assertEqual(resp.status_code, 200)

    def test_health_no_auth_required(self):
        """GET /health should work without auth token."""
        resp = self.client.get("/health")
        self.assertEqual(resp.status_code, 200)


class TestNonStubContainer(unittest.TestCase):
    """Non-stub Container validation (no HTTP calls — just construction)."""

    def test_non_stub_uses_chroma_memory_repository(self):
        """Container(stub=False).memory_repository is ChromaMemoryRepository (R-4)."""
        from waggledance.adapters.config.settings_loader import WaggleSettings
        from waggledance.bootstrap.container import Container

        settings = WaggleSettings.from_env()
        container = Container(settings=settings, stub=False)
        repo = container.memory_repository
        class_name = type(repo).__name__
        self.assertEqual(class_name, "ChromaMemoryRepository")

    def test_non_stub_uses_ollama_adapter(self):
        """Container(stub=False).llm is OllamaAdapter."""
        from waggledance.adapters.config.settings_loader import WaggleSettings
        from waggledance.bootstrap.container import Container

        settings = WaggleSettings.from_env()
        container = Container(settings=settings, stub=False)
        llm = container.llm
        class_name = type(llm).__name__
        self.assertEqual(class_name, "OllamaAdapter")

    def test_stub_uses_stub_llm(self):
        """Container(stub=True).llm is StubLLMAdapter."""
        from waggledance.adapters.config.settings_loader import WaggleSettings
        from waggledance.bootstrap.container import Container

        settings = WaggleSettings.from_env()
        container = Container(settings=settings, stub=True)
        llm = container.llm
        class_name = type(llm).__name__
        self.assertEqual(class_name, "StubLLMAdapter")


if __name__ == "__main__":
    unittest.main()
