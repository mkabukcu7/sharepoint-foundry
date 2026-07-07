import pytest

fastapi = pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

import src.api.main as api


@pytest.fixture
def client(monkeypatch):
    class FakeAgent:
        def ask(self, message):
            return f"echo: {message}"

    monkeypatch.setattr(api, "get_agent", lambda: FakeAgent())
    return TestClient(api.app)


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_chat_happy_path(client):
    r = client.post("/chat", json={"message": "hello"})
    assert r.status_code == 200
    body = r.json()
    assert body["answer"] == "echo: hello"
    assert body["degraded"] is False


def test_chat_rejects_empty(client):
    r = client.post("/chat", json={"message": "   "})
    assert r.status_code == 400


def test_chat_degrades_on_agent_error(monkeypatch):
    class BoomAgent:
        def ask(self, message):
            raise RuntimeError("backend down")

    monkeypatch.setattr(api, "get_agent", lambda: BoomAgent())
    client = TestClient(api.app)
    r = client.post("/chat", json={"message": "hi"})
    assert r.status_code == 200
    body = r.json()
    assert body["degraded"] is True
    # Never leak the raw error message.
    assert "backend down" not in body["answer"]
