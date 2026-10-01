"""Pluggable LLM/VLM service abstraction.

Supports OpenAI-compatible API endpoints with two API styles:
- Responses API (client.responses.create) — preferred; used by the official endpoint
- Chat Completions API (client.chat.completions.create) — fallback

Auto-detects which API to use, or can be configured explicitly.

Reasoning models (gpt-5.6-*, gpt-6-*, o-series) reject some classic request
parameters (``temperature``, ``max_tokens``) and take ``reasoning.effort``.
Rather than keeping a per-model capability table, every request goes through
``_create``: when the backend answers 400 "Unsupported parameter/value", the
offending parameter is dropped (or renamed, for ``max_tokens`` →
``max_completion_tokens``), remembered for the lifetime of the service, and
the request is retried once.  The same config therefore works for both
generations of models.

Transport retries belong to the scheduling layer (``ServiceGateway``): the SDK
is built with ``max_retries=0``.  Every network send is reported through
``attempt_hook`` and the token usage of every answer through ``usage_hook``.
"""

from __future__ import annotations

import base64
import json
import logging
import mimetypes
import re
from pathlib import Path
from typing import Any, Callable, Protocol

import httpx2
from openai import OpenAI

from parserx.config.schema import ServiceConfig, effort_for

log = logging.getLogger(__name__)

# An answer cut at its output budget (reasoning included: a reasoning model may think the whole budget away and write
# nothing) is asked once more with this budget — the most every configured model accepts (probed: glm-5.3-flashx
# 131 072; gpt-6-luna, gpt-6-sol, deepseek-flash at least 393 216).  Billed per token generated, so it costs only what is
# used.  Cut again: ``OutputTruncated``, a failure, never an empty or half answer taken as whole.
TRUNCATED_RETRY_TOKENS = 131072


class OutputTruncated(RuntimeError):
    """The model stopped at its output budget, at the largest budget too: no complete answer."""


class LLMService(Protocol):
    """Protocol for LLM text completion."""

    def complete(
        self,
        system: str,
        user: str,
        *,
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> str: ...


class VLMService(Protocol):
    """Protocol for VLM image understanding."""

    def describe_image(
        self,
        image_path: Path,
        prompt: str,
        *,
        context: str = "",
        temperature: float = 0.1,
        max_tokens: int = 8192,
        structured_output_mode: str = "off",
        json_schema: dict[str, Any] | None = None,
        json_schema_name: str = "parserx_image_description",
    ) -> str: ...

    def describe_images(
        self,
        image_paths: list[Path],
        prompt: str,
        *,
        context: str = "",
        temperature: float = 0.1,
        max_tokens: int = 8192,
    ) -> str: ...


# Parameter the backend names in a 400, e.g.
#   "Unsupported parameter: 'temperature' is not supported with this model."
#   "Unsupported value: 'minimal' is not supported with the 'gpt-6-luna' model."
_UNSUPPORTED_RE = re.compile(r"Unsupported (parameter|value): '([^']+)'")

# Parameters renamed rather than dropped when rejected.
_PARAM_RENAMES = {"max_tokens": "max_completion_tokens"}

_MAX_PARAM_RETRIES = 4


class OpenAICompatibleService:
    """LLM/VLM service supporting both Responses API and Chat Completions API.

    Tries Responses API first.  Falls back to Chat Completions API if the
    Responses API returns 404.
    """

    def __init__(self, config: ServiceConfig):
        self._config = config
        # Override the User-Agent when configured — some OpenAI-compatible
        # proxies front a WAF (e.g. Cloudflare) that 403-blocks the stock
        # openai-python User-Agent. An empty user_agent keeps the SDK default.
        default_headers = (
            {"User-Agent": config.user_agent} if config.user_agent else None
        )
        self._client = OpenAI(
            api_key=config.api_key or "no-key",
            base_url=config.endpoint or None,
            timeout=config.timeout,
            max_retries=0,  # the gateway retries and counts attempts (guide §8.2)
            default_headers=default_headers,
        )
        self._model = config.model
        # None = auto-detect, "responses" or "chat"
        self._api_style: str | None = None if config.api_style == "auto" else config.api_style
        # Request parameters this backend has rejected (learned from 400s).
        self._unsupported: set[str] = set()
        if config.send_temperature is False:
            self._unsupported.add("temperature")
        # Called once per network send; set by the gateway.
        self.attempt_hook: Callable[[], None] | None = None
        # Called with (model, input, cached_input, output) tokens per answer; set by the gateway.
        self.usage_hook: Callable[[str, int, int, int], None] | None = None

    # ── Public API ───────────────────────────────────────────────────────

    def complete(
        self,
        system: str,
        user: str,
        *,
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> str:
        """Text completion — tries Responses API then Chat Completions."""
        full_prompt = f"{system}\n\n{user}" if system else user

        if self._api_style != "chat":
            try:
                return self._complete_responses(full_prompt, temperature, max_tokens)
            except Exception as exc:
                if self._api_style is None and _is_not_found(exc):
                    log.info("Responses API not available, falling back to Chat Completions")
                    self._api_style = "chat"
                else:
                    raise

        return self._complete_chat(system, user, temperature, max_tokens)

    def describe_image(
        self,
        image_path: Path,
        prompt: str,
        *,
        context: str = "",
        temperature: float = 0.1,
        max_tokens: int = 8192,
        structured_output_mode: str = "off",
        json_schema: dict[str, Any] | None = None,
        json_schema_name: str = "parserx_image_description",
    ) -> str:
        """Image understanding with optional structured-output constraints."""
        image_data_url = _encode_image_data_url(image_path)
        asked = prompt
        for mode in _structured_output_modes(structured_output_mode, has_schema=bool(json_schema),
                                             strongest=self._config.structured_output):
            # a mode weaker than json_schema does not carry the schema: the model is shown it in the prompt (Q105)
            prompt = asked if mode == "json_schema" or not json_schema else asked + _schema_note(json_schema)
            try:
                if self._api_style != "chat":
                    try:
                        return self._describe_responses(
                            image_data_url,
                            prompt,
                            context,
                            temperature,
                            max_tokens,
                            structured_output_mode=mode,
                            json_schema=json_schema,
                            json_schema_name=json_schema_name,
                        )
                    except Exception as exc:
                        if self._api_style is None and _is_not_found(exc):
                            log.info("Responses API not available, falling back to Chat Completions")
                            self._api_style = "chat"
                        elif mode != "off" and _is_structured_output_unsupported(exc):
                            log.info("Responses structured output mode %s unsupported; retrying with a weaker constraint", mode)
                            continue
                        else:
                            raise

                return self._describe_chat(
                    image_data_url,
                    prompt,
                    context,
                    temperature,
                    max_tokens,
                    structured_output_mode=mode,
                    json_schema=json_schema,
                    json_schema_name=json_schema_name,
                )
            except Exception as exc:
                if mode != "off" and _is_structured_output_unsupported(exc):
                    log.info("Chat structured output mode %s unsupported; retrying with a weaker constraint", mode)
                    continue
                raise

        return self._describe_chat(
            image_data_url,
            asked + _schema_note(json_schema) if json_schema else asked,
            context,
            temperature,
            max_tokens,
            structured_output_mode="off",
            json_schema=None,
            json_schema_name=json_schema_name,
        )

    def describe_images(
        self,
        image_paths: list[Path],
        prompt: str,
        *,
        context: str = "",
        temperature: float = 0.1,
        max_tokens: int = 8192,
    ) -> str:
        """Multi-image understanding — sends all images in a single request."""
        image_data_urls = [_encode_image_data_url(p) for p in image_paths]

        if self._api_style != "chat":
            try:
                return self._describe_images_responses(
                    image_data_urls, prompt, context, temperature, max_tokens,
                )
            except Exception as exc:
                if self._api_style is None and _is_not_found(exc):
                    log.info("Responses API not available, falling back to Chat Completions")
                    self._api_style = "chat"
                else:
                    raise

        return self._describe_images_chat(
            image_data_urls, prompt, context, temperature, max_tokens,
        )

    # ── Request assembly ─────────────────────────────────────────────────

    def _generation_kwargs(
        self, api_style: str, temperature: float, max_tokens: int,
    ) -> dict[str, Any]:
        """Sampling/budget/reasoning parameters, minus those the backend rejects."""
        kwargs: dict[str, Any] = {}
        if "temperature" not in self._unsupported:
            kwargs["temperature"] = temperature

        budget = max(max_tokens, self._config.min_output_tokens)
        if api_style == "responses":
            kwargs["max_output_tokens"] = budget
        else:
            key = _PARAM_RENAMES["max_tokens"] if "max_tokens" in self._unsupported else "max_tokens"
            kwargs[key] = budget

        effort = effort_for(self._config.reasoning_effort, self._config.efforts)  # what the model accepts (Q100)
        if effort:
            if api_style == "responses" and "reasoning" not in self._unsupported:
                kwargs["reasoning"] = {"effort": effort}
            elif api_style == "chat" and "reasoning_effort" not in self._unsupported:
                kwargs["reasoning_effort"] = effort

        kwargs.update(self._extra_request_kwargs())
        return kwargs

    def _create(self, api: Any, kwargs: dict[str, Any]) -> Any:
        """Call ``api.create(**kwargs)``, shedding parameters the backend rejects.

        A rejected parameter is remembered in ``self._unsupported`` so later
        requests never send it again.
        """
        for _ in range(_MAX_PARAM_RETRIES):
            self._note_attempt()
            try:
                return api.create(**kwargs)
            except Exception as exc:
                param = _unsupported_param(exc, kwargs)
                if param is None:
                    raise
                self._unsupported.add(param)
                value = kwargs.pop(param)
                renamed = _PARAM_RENAMES.get(param)
                if renamed:
                    kwargs[renamed] = value
                log.info(
                    "%s rejects %r; %s and retrying",
                    self._model, param,
                    f"sending {renamed!r} instead" if renamed else "dropping it",
                )
        self._note_attempt()
        return api.create(**kwargs)

    def _note_attempt(self) -> None:
        if self.attempt_hook is not None:
            self.attempt_hook()

    def _report_usage(self, usage: Any) -> None:
        """Forward Responses (input/output_tokens) or Chat (prompt/completion_tokens) usage."""
        if self.usage_hook is None or usage is None:
            return
        input_tokens = getattr(usage, "input_tokens", None)
        if input_tokens is None:
            input_tokens = getattr(usage, "prompt_tokens", 0)
        output_tokens = getattr(usage, "output_tokens", None)
        if output_tokens is None:
            output_tokens = getattr(usage, "completion_tokens", 0)
        details = getattr(usage, "input_tokens_details", None) or getattr(usage, "prompt_tokens_details", None)
        cached = getattr(details, "cached_tokens", 0) if details is not None else 0
        self.usage_hook(self._model, int(input_tokens or 0), int(cached or 0), int(output_tokens or 0))

    def _extra_request_kwargs(self) -> dict[str, Any]:
        if not self._config.extra_body:
            return {}
        return {"extra_body": dict(self._config.extra_body)}

    # ── Responses API ────────────────────────────────────────────────────

    def _responses_stream(self, content: Any, temperature: float, max_tokens: int, **extra: Any) -> str:
        kwargs: dict[str, Any] = {
            "model": self._model,
            "input": [{"role": "user", "content": content}],
            "stream": True,
            # a stalled stream fails after the idle limit (retried by the gateway), not after the full timeout
            "timeout": httpx2.Timeout(self._config.timeout, read=self._config.stream_idle_timeout),
            **extra,
            **self._generation_kwargs("responses", temperature, max_tokens),
        }
        tokens: list[str] = []
        usage = None
        cut = False
        with self._create(self._client.responses, kwargs) as stream:
            for event in stream:
                kind = getattr(event, "type", "")
                if kind == "response.output_text.delta":
                    tokens.append(event.delta)
                elif kind in ("response.completed", "response.incomplete"):
                    response = getattr(event, "response", None)
                    usage = getattr(response, "usage", None)
                    details = getattr(response, "incomplete_details", None)
                    cut = kind == "response.incomplete" and getattr(details, "reason", "") == "max_output_tokens"
        self._report_usage(usage)
        if cut:
            return self._responses_stream(content, temperature, self._larger_budget(max_tokens), **extra)

        text = "".join(tokens).strip()
        if self._api_style is None:
            self._api_style = "responses"
        return _strip_code_fences(text)

    def _complete_responses(
        self, prompt: str, temperature: float, max_tokens: int
    ) -> str:
        return self._responses_stream(prompt, temperature, max_tokens)

    def _describe_responses(
        self,
        image_data_url: str,
        prompt: str,
        context: str,
        temperature: float,
        max_tokens: int,
        *,
        structured_output_mode: str,
        json_schema: dict[str, Any] | None,
        json_schema_name: str,
    ) -> str:
        content: list[dict[str, Any]] = []
        if context:
            content.append({"type": "input_text", "text": context})
        content.append({"type": "input_text", "text": prompt})
        content.append({"type": "input_image", "image_url": image_data_url})
        return self._responses_stream(
            content, temperature, max_tokens,
            **_structured_output_kwargs(
                api_style="responses",
                mode=structured_output_mode,
                json_schema=json_schema,
                json_schema_name=json_schema_name,
            ),
        )

    def _describe_images_responses(
        self,
        image_data_urls: list[str],
        prompt: str,
        context: str,
        temperature: float,
        max_tokens: int,
    ) -> str:
        content: list[dict[str, Any]] = []
        if context:
            content.append({"type": "input_text", "text": context})
        content.append({"type": "input_text", "text": prompt})
        for url in image_data_urls:
            content.append({"type": "input_image", "image_url": url})
        return self._responses_stream(content, temperature, max_tokens)

    # ── Chat Completions API ─────────────────────────────────────────────

    def _chat(self, messages: list[dict[str, Any]], temperature: float, max_tokens: int, **extra: Any) -> str:
        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            **extra,
            **self._generation_kwargs("chat", temperature, max_tokens),
        }
        response = self._create(self._client.chat.completions, kwargs)
        self._report_usage(getattr(response, "usage", None))
        if self._api_style is None:
            self._api_style = "chat"
        choice = response.choices[0]
        if getattr(choice, "finish_reason", None) == "length":
            return self._chat(messages, temperature, self._larger_budget(max_tokens), **extra)
        return choice.message.content or ""

    def _larger_budget(self, max_tokens: int) -> int:
        """The budget to ask an answer cut at *max_tokens* again with (``TRUNCATED_RETRY_TOKENS``); cut at that one:
        ``OutputTruncated``."""
        if max(max_tokens, self._config.min_output_tokens) >= TRUNCATED_RETRY_TOKENS:
            raise OutputTruncated(f"{self._model}: the answer stopped at its output budget of {max_tokens} tokens")
        log.info("%s: answer cut at %d tokens; asked again with %d", self._model, max_tokens, TRUNCATED_RETRY_TOKENS)
        return TRUNCATED_RETRY_TOKENS

    def _complete_chat(
        self, system: str, user: str, temperature: float, max_tokens: int
    ) -> str:
        messages: list[dict[str, Any]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": user})
        return self._chat(messages, temperature, max_tokens)

    def _describe_chat(
        self,
        image_data_url: str,
        prompt: str,
        context: str,
        temperature: float,
        max_tokens: int,
        *,
        structured_output_mode: str,
        json_schema: dict[str, Any] | None,
        json_schema_name: str,
    ) -> str:
        content: list[dict[str, Any]] = []
        if context:
            content.append({"type": "text", "text": context})
        content.append({"type": "text", "text": prompt})
        content.append({"type": "image_url", "image_url": {"url": image_data_url}})
        return self._chat(
            [{"role": "user", "content": content}], temperature, max_tokens,
            **_structured_output_kwargs(
                api_style="chat",
                mode=structured_output_mode,
                json_schema=json_schema,
                json_schema_name=json_schema_name,
            ),
        )

    def _describe_images_chat(
        self,
        image_data_urls: list[str],
        prompt: str,
        context: str,
        temperature: float,
        max_tokens: int,
    ) -> str:
        content: list[dict[str, Any]] = []
        if context:
            content.append({"type": "text", "text": context})
        content.append({"type": "text", "text": prompt})
        for url in image_data_urls:
            content.append({"type": "image_url", "image_url": {"url": url}})
        return self._chat([{"role": "user", "content": content}], temperature, max_tokens)


# ── Helpers ─────────────────────────────────────────────────────────────


def _unsupported_param(exc: Exception, kwargs: dict[str, Any]) -> str | None:
    """Name the request parameter a 400 complains about, or None.

    ``Unsupported parameter: 'x'`` maps to ``x`` (``'reasoning.effort'`` →
    ``reasoning``).  ``Unsupported value: 'v'`` is attributed to the
    reasoning parameter when ``v`` is the configured effort.
    """
    match = _UNSUPPORTED_RE.search(str(exc))
    if not match:
        return None
    kind, name = match.group(1), match.group(2)
    if kind == "value":
        effort = kwargs.get("reasoning_effort") or (kwargs.get("reasoning") or {}).get("effort")
        if name != effort:
            return None
        name = "reasoning" if "reasoning" in kwargs else "reasoning_effort"
    name = name.split(".", 1)[0]
    return name if name in kwargs else None


def _encode_image_data_url(image_path: Path) -> str:
    """Encode image as data URL for API consumption."""
    mime_type = mimetypes.guess_type(str(image_path))[0] or "image/png"
    data = base64.b64encode(image_path.read_bytes()).decode("utf-8")
    return f"data:{mime_type};base64,{data}"


def _strip_code_fences(text: str) -> str:
    """Remove markdown code fences from LLM response."""
    if text.startswith("```"):
        first_nl = text.find("\n")
        text = text[first_nl + 1:] if first_nl >= 0 else ""
        if text.endswith("```"):
            text = text[:-3]
    return text.strip()


def _is_not_found(exc: Exception) -> bool:
    """Check if exception is a 404 Not Found error."""
    exc_str = str(exc)
    return "404" in exc_str or "Not Found" in exc_str


_STRUCTURED = ("json_schema", "json_object", "off")


def _schema_note(schema: dict[str, Any]) -> str:
    """The schema, for a model whose structured output does not carry it (Q105)."""
    return ("\n\n只回答一个 JSON 对象，严格符合下面的 JSON Schema（字段名、嵌套结构、取值范围都照此，不要加说明或代码围栏）：\n"
            + json.dumps(schema, ensure_ascii=False))


def _structured_output_modes(requested_mode: str, *, has_schema: bool, strongest: str | None = None) -> tuple[str, ...]:
    """Return a strongest-to-weakest structured-output fallback chain, starting no stronger than what the model
    honours (``strongest``, from its ``models`` entry, Q105)."""
    if requested_mode == "json_schema" and has_schema:
        chain = _STRUCTURED
    elif requested_mode == "json_object":
        chain = _STRUCTURED[1:]
    else:
        return ("off",)
    return chain[chain.index(strongest):] if strongest in chain else chain


def _structured_output_kwargs(
    *,
    api_style: str,
    mode: str,
    json_schema: dict[str, Any] | None,
    json_schema_name: str,
) -> dict[str, Any]:
    """Build API-native structured-output parameters for OpenAI-compatible backends."""
    if mode == "off":
        return {}

    if api_style == "responses":
        if mode == "json_schema" and json_schema:
            return {
                "text": {
                    "format": {
                        "type": "json_schema",
                        "name": json_schema_name,
                        "schema": json_schema,
                        "strict": True,
                    }
                }
            }
        if mode == "json_object":
            return {"text": {"format": {"type": "json_object"}}}
        return {}

    if mode == "json_schema" and json_schema:
        return {
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": json_schema_name,
                    "schema": json_schema,
                    "strict": True,
                },
            }
        }
    if mode == "json_object":
        return {"response_format": {"type": "json_object"}}
    return {}


def _is_structured_output_unsupported(exc: Exception) -> bool:
    """Best-effort detection for providers that reject structured-output params."""
    message = str(exc).lower()
    indicators = (
        "response_format",
        "json_schema",
        "json_object",
        "text.format",
        "structured output",
        "structured outputs",
        "unsupported",
        "not support",
        "unknown parameter",
        "invalid parameter",
        "extra inputs are not permitted",
    )
    return any(indicator in message for indicator in indicators)


def create_vlm_service(config: ServiceConfig) -> OpenAICompatibleService:
    """Factory: create VLM service from config."""
    return OpenAICompatibleService(config)
