"""Tests for OpenAI-compatible LLM/VLM service configuration forwarding."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from parserx.config.schema import ServiceConfig
from parserx.services.llm import OpenAICompatibleService


class _FakeResponseStream:
    def __init__(self, deltas: list[str]):
        self._events = [
            SimpleNamespace(type="response.output_text.delta", delta=delta)
            for delta in deltas
        ]

    def __enter__(self):
        return iter(self._events)

    def __exit__(self, exc_type, exc, tb):
        return False


class _FakeResponsesAPI:
    def __init__(self):
        self.calls: list[dict] = []
        self.raise_not_found = False

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.raise_not_found:
            raise RuntimeError("404 Not Found")
        return _FakeResponseStream(["hello", " world"])


class _FakeChatCompletionsAPI:
    def __init__(self):
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        message = SimpleNamespace(content="chat answer")
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class _FakeOpenAIClient:
    instances: list["_FakeOpenAIClient"] = []

    def __init__(self, **kwargs):
        self.init_kwargs = kwargs
        self.responses = _FakeResponsesAPI()
        self.chat = SimpleNamespace(completions=_FakeChatCompletionsAPI())
        self.__class__.instances.append(self)


def _make_service(monkeypatch, **config_overrides) -> tuple[OpenAICompatibleService, _FakeOpenAIClient]:
    monkeypatch.setattr("parserx.services.llm.OpenAI", _FakeOpenAIClient)
    _FakeOpenAIClient.instances.clear()
    config = ServiceConfig(
        endpoint="https://example.invalid/v1",
        api_key="test-key",
        model="test-model",
        **config_overrides,
    )
    service = OpenAICompatibleService(config)
    return service, _FakeOpenAIClient.instances[-1]


def test_chat_api_style_forwards_extra_body(monkeypatch):
    service, client = _make_service(
        monkeypatch,
        api_style="chat",
        extra_body={"enable_thinking": True},
    )

    result = service.complete("system", "user")

    assert result == "chat answer"
    assert client.responses.calls == []
    assert client.chat.completions.calls[0]["extra_body"] == {"enable_thinking": True}


def test_responses_api_style_forwards_extra_body(monkeypatch):
    service, client = _make_service(
        monkeypatch,
        api_style="responses",
        extra_body={"enable_thinking": False},
    )

    result = service.complete("system", "user")

    assert result == "hello world"
    assert client.chat.completions.calls == []
    assert client.responses.calls[0]["extra_body"] == {"enable_thinking": False}


def test_auto_api_style_falls_back_to_chat_on_404(monkeypatch):
    service, client = _make_service(
        monkeypatch,
        api_style="auto",
        extra_body={"enable_thinking": True},
    )
    client.responses.raise_not_found = True

    result = service.complete("system", "user")

    assert result == "chat answer"
    assert len(client.responses.calls) == 1
    assert client.chat.completions.calls[0]["extra_body"] == {"enable_thinking": True}


def test_describe_image_forwards_extra_body_to_chat(monkeypatch, tmp_path: Path):
    service, client = _make_service(
        monkeypatch,
        api_style="chat",
        extra_body={"enable_thinking": False},
    )
    image_path = tmp_path / "sample.png"
    image_path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        b"\x00\x00\x00\rIHDR"
        b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00"
        b"\x90wS\xde"
        b"\x00\x00\x00\x0cIDATx\x9cc```\x00\x00\x00\x04\x00\x01"
        b"\x0b\xe7\x02\x9d"
        b"\x00\x00\x00\x00IEND\xaeB`\x82"
    )

    result = service.describe_image(image_path, "Describe image")

    assert result == "chat answer"
    assert client.chat.completions.calls[0]["extra_body"] == {"enable_thinking": False}
