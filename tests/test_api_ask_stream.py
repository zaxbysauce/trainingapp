"""Regression tests for issue #51: /ask/stream lifecycle, cancellation, and
API contract surface (health, CORS, loopback default).

These live under tests/ (collected by CI) — the original /ask/stream tests in
root-level test_api_endpoints.py were never collected because pytest.ini's
testpaths excludes the repo root, which is why the unawaited-coroutine bug
survived. See scripts/check_test_collection.py for the class guardrail.
"""

import asyncio
import inspect
import json
from unittest.mock import MagicMock

import httpx
import pytest
from fastapi.testclient import TestClient

import api_server
from api_server import CANCELLED_ANSWER, app
from llm_interface import QueryCancelled

pytestmark = pytest.mark.unit


class _Result:
    question = "What is Python?"
    answer = "A programming language."
    sources = ["doc1.txt"]
    context_length = 100
    inference_time = 0.5


def _parse_sse(raw: str):
    """Parse SSE into (event, payload) tuples, tolerating CRLF separators."""
    events = []
    for block in raw.replace("\r\n", "\n").split("\n\n"):
        block = block.strip()
        if not block:
            continue
        name, data = "message", None
        for line in block.splitlines():
            if line.startswith("event:"):
                name = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                data = line.split(":", 1)[1].strip()
        if data is not None:
            try:
                data = json.loads(data)
            except (ValueError, TypeError):
                pass
        events.append((name, data))
    return events


def _stream_with(engine, question="What is Python?"):
    """POST /ask/stream via ASGITransport and return parsed SSE events."""
    import api_server

    old = api_server.engine
    api_server.engine = engine
    try:

        async def _run():
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                resp = await client.post("/ask/stream", json={"question": question})
                return resp

        return asyncio.run(_run())
    finally:
        api_server.engine = old


def test_ask_stream_streams_tokens_and_single_done_terminal():
    class Engine:
        llm = MagicMock()

        def query(
            self,
            question,
            n_results=6,
            stream_callback=None,
            conversation_history=None,
            cancellation_event=None,
        ):
            if stream_callback:
                for tok in ("Hello ", "streaming ", "world"):
                    stream_callback(tok)
            return _Result()

    resp = _stream_with(Engine())
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")

    events = _parse_sse(resp.text)
    tokens = [p for e, p in events if isinstance(p, dict) and "token" in p]
    assert len(tokens) == 3

    terminals = [
        p
        for e, p in events
        if e == "error" or (isinstance(p, dict) and ("done" in p or "error" in p))
    ]
    assert len(terminals) == 1
    done = terminals[0]
    # Renderer done-detection contract (web_ui streaming.ts): sources AND
    # context_length must both be present.
    assert done.get("done") is True
    assert done["sources"] == ["doc1.txt"]
    assert done["context_length"] == 100
    assert done["inference_time"] == 0.5
    assert not any(
        e == "error" or (isinstance(p, dict) and "error" in p) for e, p in events
    )


def test_ask_stream_cancelled_reaches_renderer_done_terminal():
    class Engine:
        llm = MagicMock()

        def query(self, *args, **kwargs):
            cb = kwargs.get("stream_callback")
            if cb:
                cb("partial ")
            raise QueryCancelled()

    resp = _stream_with(Engine())
    assert resp.status_code == 200

    events = _parse_sse(resp.text)
    terminals = [
        p for e, p in events if isinstance(p, dict) and ("done" in p or "error" in p)
    ]
    assert len(terminals) == 1
    done = terminals[0]
    assert done.get("done") is True
    assert done.get("cancelled") is True
    assert "sources" in done
    # Regression: the cancelled payload must satisfy the renderer contract
    # (context_length present) or the UI never reaches its done path.
    assert "context_length" in done
    assert not any(e == "error" for e, _ in events)


def test_ask_stream_real_engine_style_cancel_emits_cancelled_done():
    """Regression (PRR-007): rag_engine swallows QueryCancelled and returns a
    sentinel result (answer == CANCELLED_ANSWER) WITHOUT setting
    cancellation_event, so the server must detect the cancellation from the
    result on the success path and emit the renderer-detectable
    cancelled-done terminal. The stub mirrors the real engine exactly."""

    class Engine:
        llm = MagicMock()

        def query(
            self,
            question,
            n_results=6,
            stream_callback=None,
            conversation_history=None,
            cancellation_event=None,
        ):
            assert cancellation_event is not None
            if stream_callback:
                stream_callback("partial ")
            # Real-engine shape: sentinel answer, empty sources, event NOT set.
            result = _Result()
            result.answer = CANCELLED_ANSWER
            result.sources = []
            result.context_length = 0
            return result

    resp = _stream_with(Engine())
    assert resp.status_code == 200

    events = _parse_sse(resp.text)
    terminals = [
        p for e, p in events if isinstance(p, dict) and ("done" in p or "error" in p)
    ]
    assert len(terminals) == 1
    done = terminals[0]
    assert done.get("done") is True
    assert done.get("cancelled") is True
    assert done["sources"] == []
    assert done["context_length"] == 0
    assert not any(e == "error" for e, _ in events)


def test_ask_stream_engine_error_yields_exactly_one_error_terminal():
    class Engine:
        llm = MagicMock()

        def query(self, *args, **kwargs):
            raise RuntimeError("boom")

    resp = _stream_with(Engine())
    assert resp.status_code == 200

    events = _parse_sse(resp.text)
    terminals = [
        p for e, p in events if e == "error" or (isinstance(p, dict) and "error" in p)
    ]
    assert len(terminals) == 1
    assert "error" in terminals[0]
    assert not any(isinstance(p, dict) and "token" in p for _, p in events)


def test_health_endpoint_reports_engine_readiness():
    with patch_engine(None):
        client = TestClient(app)
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok", "engine_ready": False}

    with patch_engine(MagicMock()):
        client = TestClient(app)
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["engine_ready"] is True


class patch_engine:
    """Context manager swapping api_server.engine."""

    def __init__(self, value):
        self.value = value
        self.old = None

    def __enter__(self):
        self.old = api_server.engine
        api_server.engine = self.value
        return self

    def __exit__(self, *exc):
        api_server.engine = self.old
        return False


def test_cors_preflight_allows_put_settings():
    client = TestClient(app)
    preflight = client.options(
        "/settings",
        headers={
            "Origin": "http://localhost",
            "Access-Control-Request-Method": "PUT",
        },
    )
    assert preflight.status_code in (200, 204)
    allow = preflight.headers.get("access-control-allow-methods", "")
    assert (
        "PUT" in allow.upper()
    ), f"PUT /settings preflight denied — allow-methods: {allow!r}"


def test_api_host_defaults_to_loopback():
    from api_server import main

    source = inspect.getsource(main)
    assert (
        '"API_HOST", "127.0.0.1"' in source
    ), "API_HOST default must be loopback (127.0.0.1), not 0.0.0.0"
