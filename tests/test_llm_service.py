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


def _reject_unsupported(kwargs: dict, rejected: set[str]) -> None:
    for name in rejected:
        if name in kwargs:
            raise RuntimeError(
                f"Error code: 400 - Unsupported parameter: '{name}' is not supported with this model."
            )


class _FakeResponsesAPI:
    def __init__(self):
        self.calls: list[dict] = []
        self.raise_not_found = False
        self.rejected: set[str] = set()

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.raise_not_found:
            raise RuntimeError("404 Not Found")
        _reject_unsupported(kwargs, self.rejected)
        return _FakeResponseStream(["hello", " world"])


class _FakeChatCompletionsAPI:
    def __init__(self):
        self.calls: list[dict] = []
        self.rejected: set[str] = set()

    def create(self, **kwargs):
        self.calls.append(kwargs)
        _reject_unsupported(kwargs, self.rejected)
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


def test_describe_image_forwards_json_schema_to_chat(monkeypatch, tmp_path: Path):
    service, client = _make_service(monkeypatch, api_style="chat")
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

    schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["summary"],
        "properties": {"summary": {"type": "string"}},
    }
    service.describe_image(
        image_path,
        "Describe image",
        structured_output_mode="json_schema",
        json_schema=schema,
        json_schema_name="demo_schema",
    )

    response_format = client.chat.completions.calls[0]["response_format"]
    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["name"] == "demo_schema"
    assert response_format["json_schema"]["schema"] == schema
    assert response_format["json_schema"]["strict"] is True


def test_a_model_without_json_schema_is_shown_the_schema_in_the_prompt(monkeypatch, tmp_path: Path):
    # Q105: GLM and DeepSeek honour json_object only; told "answer in JSON" without the schema, GLM fitted none of
    # 66 figure descriptions to it
    image_path = tmp_path / "sample.png"
    image_path.write_bytes(b"\x89PNG\r\n\x1a\n")
    schema = {"type": "object", "required": ["summary"], "properties": {"summary": {"type": "string"}}}

    def text_of(call):
        return " ".join(part["text"] for part in call["messages"][0]["content"] if part["type"] == "text")

    service, client = _make_service(monkeypatch, api_style="chat", structured_output="json_object")
    service.describe_image(image_path, "Describe image", structured_output_mode="json_schema", json_schema=schema)
    call = client.chat.completions.calls[0]
    assert call["response_format"] == {"type": "json_object"} and '"summary"' in text_of(call)
    service, client = _make_service(monkeypatch, api_style="chat")  # json_schema honoured: the prompt as written
    service.describe_image(image_path, "Describe image", structured_output_mode="json_schema", json_schema=schema)
    assert text_of(client.chat.completions.calls[0]) == "Describe image"


def test_describe_image_forwards_json_schema_to_responses(monkeypatch, tmp_path: Path):
    service, client = _make_service(monkeypatch, api_style="responses")
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

    schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["summary"],
        "properties": {"summary": {"type": "string"}},
    }
    service.describe_image(
        image_path,
        "Describe image",
        structured_output_mode="json_schema",
        json_schema=schema,
        json_schema_name="demo_schema",
    )

    text_config = client.responses.calls[0]["text"]
    assert text_config["format"]["type"] == "json_schema"
    assert text_config["format"]["name"] == "demo_schema"
    assert text_config["format"]["schema"] == schema
    assert text_config["format"]["strict"] is True


def test_rejected_temperature_is_dropped_and_remembered(monkeypatch):
    service, client = _make_service(monkeypatch, api_style="responses")
    client.responses.rejected = {"temperature"}

    assert service.complete("system", "user", temperature=0.0) == "hello world"
    assert service.complete("system", "user", temperature=0.0) == "hello world"

    calls = client.responses.calls
    assert len(calls) == 3  # rejected, retried, then never sent again
    assert "temperature" in calls[0] and "temperature" not in calls[1] and "temperature" not in calls[2]


def test_reasoning_effort_forwarded_per_api_style(monkeypatch):
    service, client = _make_service(monkeypatch, api_style="responses", reasoning_effort="none")
    service.complete("system", "user")
    assert client.responses.calls[0]["reasoning"] == {"effort": "none"}

    service, client = _make_service(monkeypatch, api_style="chat", reasoning_effort="low")
    service.complete("system", "user")
    assert client.chat.completions.calls[0]["reasoning_effort"] == "low"


def test_the_effort_sent_is_the_nearest_the_model_accepts(monkeypatch):
    # Q100: GLM takes low / high / max only; a task's "none" is sent as "low", not rejected request by request
    service, client = _make_service(monkeypatch, api_style="chat", reasoning_effort="none", efforts=["low", "high", "max"])
    service.complete("system", "user")
    assert client.chat.completions.calls[0]["reasoning_effort"] == "low"


def test_structured_output_starts_at_what_the_model_honours():
    from parserx.services.llm import _structured_output_modes

    assert _structured_output_modes("json_schema", has_schema=True) == ("json_schema", "json_object", "off")
    assert _structured_output_modes("json_schema", has_schema=True, strongest="json_object") == ("json_object", "off")
    assert _structured_output_modes("json_object", has_schema=False, strongest="off") == ("off",)
    assert _structured_output_modes("off", has_schema=True, strongest="json_schema") == ("off",)


def test_rejected_reasoning_value_drops_reasoning(monkeypatch):
    service, client = _make_service(monkeypatch, api_style="responses", reasoning_effort="minimal")

    def create(**kwargs):
        client.responses.calls.append(kwargs)
        if "reasoning" in kwargs:
            raise RuntimeError("Error code: 400 - Unsupported value: 'minimal' is not supported with the 'x' model.")
        return _FakeResponseStream(["ok"])

    client.responses.create = create
    assert service.complete("system", "user") == "ok"
    assert "reasoning" not in client.responses.calls[-1]


def test_min_output_tokens_floor_and_chat_token_param_rename(monkeypatch):
    service, client = _make_service(monkeypatch, api_style="responses", min_output_tokens=1024)
    service.complete("system", "user", max_tokens=64)
    assert client.responses.calls[0]["max_output_tokens"] == 1024

    service, client = _make_service(monkeypatch, api_style="chat")
    client.chat.completions.rejected = {"max_tokens"}
    service.complete("system", "user", max_tokens=64)
    assert client.chat.completions.calls[-1]["max_completion_tokens"] == 64
    assert "max_tokens" not in client.chat.completions.calls[-1]


def test_usage_is_reported_for_responses_and_chat(monkeypatch):
    service, client = _make_service(monkeypatch)
    usage = SimpleNamespace(input_tokens=120, output_tokens=30,
                            input_tokens_details=SimpleNamespace(cached_tokens=100))
    completed = SimpleNamespace(type="response.completed", response=SimpleNamespace(usage=usage))

    class _Stream(_FakeResponseStream):
        def __init__(self):
            super().__init__(["ok"])
            self._events.append(completed)

    client.responses.create = lambda **kw: _Stream()
    chat_usage = SimpleNamespace(prompt_tokens=50, completion_tokens=5,
                                 prompt_tokens_details=SimpleNamespace(cached_tokens=0))
    message = SimpleNamespace(content="chat answer")
    client.chat.completions.create = lambda **kw: SimpleNamespace(
        choices=[SimpleNamespace(message=message)], usage=chat_usage)

    reports = []
    service.usage_hook = lambda *args: reports.append(args)
    assert service.complete("s", "u") == "ok"
    service._api_style = "chat"
    assert service.complete("s", "u") == "chat answer"
    assert reports == [("test-model", 120, 100, 30), ("test-model", 50, 0, 5)]


def test_sdk_never_retries(monkeypatch):
    _, client = _make_service(monkeypatch)
    assert client.init_kwargs["max_retries"] == 0


def test_a_streamed_answer_that_stalls_times_out_early(monkeypatch):
    # guide §8.2: a stalled stream is a transport failure, found after the idle limit rather than the full timeout
    service, client = _make_service(monkeypatch, api_style="responses", timeout=180, stream_idle_timeout=45)
    service.complete("s", "u")
    timeout = client.responses.calls[0]["timeout"]
    assert (timeout.read, timeout.connect) == (45, 180)



def test_a_chat_answer_cut_at_its_budget_is_asked_again_with_the_largest(monkeypatch):
    from parserx.services.llm import TRUNCATED_RETRY_TOKENS

    service, client = _make_service(monkeypatch, api_style="chat")
    answers = [("", "length"), ("the whole answer", "stop")]

    def create(**kw):
        client.chat.completions.calls.append(kw)
        text, finish = answers.pop(0)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text), finish_reason=finish)])

    client.chat.completions.create = create
    assert service.complete("s", "u", max_tokens=4096) == "the whole answer"
    budgets = [c.get("max_tokens") or c.get("max_completion_tokens") for c in client.chat.completions.calls]
    assert budgets == [4096, TRUNCATED_RETRY_TOKENS]


def test_an_answer_cut_twice_is_a_failure_not_an_empty_answer(monkeypatch):
    import pytest

    from parserx.services.llm import OutputTruncated

    service, client = _make_service(monkeypatch, api_style="chat")
    client.chat.completions.create = lambda **kw: SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="half"), finish_reason="length")])
    with pytest.raises(OutputTruncated):
        service.complete("s", "u", max_tokens=4096)


def test_a_responses_answer_left_incomplete_at_its_budget_is_asked_again(monkeypatch):
    from parserx.services.llm import TRUNCATED_RETRY_TOKENS

    service, client = _make_service(monkeypatch)
    incomplete = SimpleNamespace(type="response.incomplete", response=SimpleNamespace(
        usage=None, incomplete_details=SimpleNamespace(reason="max_output_tokens")))
    streams = []

    class _Cut(_FakeResponseStream):
        def __init__(self):
            super().__init__(["par"])
            self._events.append(incomplete)

    def create(**kw):
        client.responses.calls.append(kw)
        streams.append(kw["max_output_tokens"])
        return _Cut() if len(streams) == 1 else _FakeResponseStream(["whole"])

    client.responses.create = create
    assert service.complete("s", "u", max_tokens=4096) == "whole"
    assert streams == [4096, TRUNCATED_RETRY_TOKENS]
