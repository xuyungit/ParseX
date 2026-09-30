"""The P0 service call: one page image with its data, one JSON answer, how it ended.

The service layer (``services/llm.py``) returns only the answer's text; the probe must also tell a truncated answer
(finish reason ``length``, a Responses answer ``incomplete``), an empty one and a timeout apart (execution plan
§3.3).  ``ProbeService`` is the same service with one method that keeps them: the request is assembled by the
service's own parts (generation parameters, structured output from the model's entry, the schema written into the
prompt for a model weaker than json_schema — Q105, Q139), sent through the ``ServiceGateway`` (response cache,
transport retries, metering and cost), and answered as a JSON-able record, so an offline replay gives the same
record.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx2

from parserx.cache import ResponseCache, request_key, service_identity
from parserx.config.schema import ServiceConfig, effort_for
from parserx.scheduling import ServiceGateway
from parserx.scheduling.meter import RequestMeter
from parserx.scheduling.usage import PriceTable
from parserx.services.llm import (OpenAICompatibleService, _encode_image_data_url, _schema_note,
                                  _structured_output_kwargs)


def sha256(text: str | bytes) -> str:
    return hashlib.sha256(text.encode("utf-8") if isinstance(text, str) else text).hexdigest()


class ProbeService(OpenAICompatibleService):
    def ask(self, image: Path | list[Path], prompt: str, context: str, *, schema: dict, max_tokens: int) -> dict:
        """{"text", "finish", "usage", "seconds", "structured_output", "api"}; *image* one page image or several."""
        mode = self._config.structured_output or "json_schema"
        asked = prompt if mode == "json_schema" else prompt + _schema_note(schema)
        urls = [_encode_image_data_url(i) for i in (image if isinstance(image, list) else [image])]
        api = self._config.api_style if self._config.api_style in ("responses", "chat") else "responses"
        started = time.monotonic()
        if api == "responses":
            content = [{"type": "input_text", "text": context}, {"type": "input_text", "text": asked},
                       *({"type": "input_image", "image_url": url} for url in urls)]
            text, finish, usage = self._responses(content, max_tokens, _structured_output_kwargs(
                api_style="responses", mode=mode, json_schema=schema, json_schema_name="p0_allocation"))
        else:
            content = [{"type": "text", "text": context}, {"type": "text", "text": asked},
                       *({"type": "image_url", "image_url": {"url": url}} for url in urls)]
            text, finish, usage = self._chat_once(content, max_tokens, _structured_output_kwargs(
                api_style="chat", mode=mode, json_schema=schema, json_schema_name="p0_allocation"))
        return {"text": text, "finish": finish, "usage": usage, "seconds": round(time.monotonic() - started, 2),
                "structured_output": mode, "api": api}

    def _responses(self, content: list, max_tokens: int, extra: dict) -> tuple[str, str, dict]:
        kwargs: dict[str, Any] = {
            "model": self._model, "input": [{"role": "user", "content": content}], "stream": True,
            "timeout": httpx2.Timeout(self._config.timeout, read=self._config.stream_idle_timeout),
            **extra, **self._generation_kwargs("responses", 0.0, max_tokens)}
        tokens: list[str] = []
        finish, usage = "unknown", None
        with self._create(self._client.responses, kwargs) as stream:
            for event in stream:
                kind = getattr(event, "type", "")
                if kind == "response.output_text.delta":
                    tokens.append(event.delta)
                elif kind in ("response.completed", "response.incomplete", "response.failed"):
                    response = getattr(event, "response", None)
                    usage = getattr(response, "usage", None)
                    details = getattr(response, "incomplete_details", None)
                    finish = {"response.completed": "stop", "response.failed": "failed"}.get(
                        kind, f"incomplete:{getattr(details, 'reason', '') or ''}")
        self._report_usage(usage)
        return "".join(tokens).strip(), finish, _usage(usage)

    def _chat_once(self, content: list, max_tokens: int, extra: dict) -> tuple[str, str, dict]:
        kwargs: dict[str, Any] = {"model": self._model, "messages": [{"role": "user", "content": content}],
                                  **extra, **self._generation_kwargs("chat", 0.0, max_tokens)}
        response = self._create(self._client.chat.completions, kwargs)
        usage = getattr(response, "usage", None)
        self._report_usage(usage)
        choice = response.choices[0]
        return (choice.message.content or "").strip(), str(choice.finish_reason or "unknown"), _usage(usage)


def _usage(usage: Any) -> dict:
    if usage is None:
        return {}
    get = lambda obj, *names: next((getattr(obj, n) for n in names if getattr(obj, n, None) is not None), 0)  # noqa: E731
    details_in = getattr(usage, "input_tokens_details", None) or getattr(usage, "prompt_tokens_details", None)
    details_out = getattr(usage, "output_tokens_details", None) or getattr(usage, "completion_tokens_details", None)
    return {"input": int(get(usage, "input_tokens", "prompt_tokens") or 0),
            "cached": int(get(details_in, "cached_tokens") or 0) if details_in is not None else 0,
            "output": int(get(usage, "output_tokens", "completion_tokens") or 0),
            "reasoning": int(get(details_out, "reasoning_tokens") or 0) if details_out is not None else 0}


@dataclass
class Caller:
    """One model at one effort, through its own gateway on the run's cache."""

    name: str  # the run configuration's name, e.g. "luna-low-r1"
    config: ServiceConfig
    cache: ResponseCache
    prices: PriceTable
    meter: RequestMeter = field(default_factory=RequestMeter)

    def __post_init__(self) -> None:
        self.service = ProbeService(self.config)
        self.gateway = ServiceGateway(self.meter, self.cache, prices=self.prices)
        self.service.attempt_hook = self.gateway.record_send
        self.service.usage_hook = self.gateway.record_usage

    def identity(self) -> dict:
        return {**service_identity(self.config),
                "effort_sent": effort_for(self.config.reasoning_effort, self.config.efforts),
                "structured_output": self.config.structured_output or "json_schema"}

    def ask(self, image: Path | list[Path], prompt: str, context: str, *, schema: dict, max_tokens: int, run: int,
            round_: int, feedback: str = "", method: str = "p0_allocate") -> dict:
        """The record of one request (a cache hit returns the recorded one); failed attempts are listed.  One image
        keys the request as P0 did (its recorded answers replay); several are keyed in order."""
        shown = ({"file_sha256": sha256(image.read_bytes())} if not isinstance(image, list)
                 else [{"file_sha256": sha256(i.read_bytes())} for i in image])
        material = {"method": method, **self.identity(),
                    "args": {"image": shown, "prompt": sha256(prompt),
                             "context": sha256(context + feedback), "schema": sha256(repr(schema)),
                             "max_tokens": max_tokens, "run": run, "round": round_}}
        attempts: list[dict] = []
        hit = self.cache.readable and self.cache.path("vlm", request_key("vlm", material)).exists()

        def fetch() -> dict:
            try:
                return self.service.ask(image, prompt, context + feedback, schema=schema, max_tokens=max_tokens)
            except Exception as exc:  # noqa: BLE001 - recorded, then the gateway decides
                attempts.append({"error": f"{type(exc).__name__}: {str(exc)[:300]}", "timeout": _timeout(exc)})
                raise

        started = time.monotonic()
        try:
            record = self.gateway.call("vlm", material, fetch)
        except Exception as exc:  # noqa: BLE001 - a request that never answered is a result too
            return {"text": "", "finish": "error", "usage": {}, "seconds": round(time.monotonic() - started, 2),
                    "error": f"{type(exc).__name__}: {str(exc)[:300]}", "failed_attempts": attempts,
                    "cache_hit": False, "usd": None}
        cost = self.prices.cost(self.config.model, input_tokens=record["usage"].get("input", 0),
                                cached_input_tokens=record["usage"].get("cached", 0),
                                output_tokens=record["usage"].get("output", 0)) if record.get("usage") else None
        return {**record, "failed_attempts": attempts, "cache_hit": hit, "usd": cost}


def _timeout(exc: BaseException) -> bool:
    name = type(exc).__name__.lower()
    return "timeout" in name or "timed out" in str(exc).lower()
