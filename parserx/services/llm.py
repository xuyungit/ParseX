"""Pluggable LLM/VLM service abstraction.

Supports OpenAI-compatible API endpoints with two API styles:
- Responses API (client.responses.create) — used by legacy pipeline's endpoint
- Chat Completions API (client.chat.completions.create) — standard OpenAI

Auto-detects which API to use, or can be configured explicitly.
"""

from __future__ import annotations

import base64
import logging
import mimetypes
from pathlib import Path
from typing import Any, Protocol

from openai import OpenAI

from parserx.config.schema import ServiceConfig

log = logging.getLogger(__name__)


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


class OpenAICompatibleService:
    """LLM/VLM service supporting both Responses API and Chat Completions API.

    Tries Responses API first (as used by legacy pipeline's endpoint).
    Falls back to Chat Completions API if Responses API returns 404.
    """

    def __init__(self, config: ServiceConfig):
        self._config = config
        self._client = OpenAI(
            api_key=config.api_key or "no-key",
            base_url=config.endpoint or None,
            timeout=config.timeout,
            max_retries=config.max_retries,
        )
        self._model = config.model
        # None = auto-detect, "responses" or "chat"
        self._api_style: str | None = None if config.api_style == "auto" else config.api_style

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
        for mode in _structured_output_modes(structured_output_mode, has_schema=bool(json_schema)):
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
            prompt,
            context,
            temperature,
            max_tokens,
            structured_output_mode="off",
            json_schema=None,
            json_schema_name=json_schema_name,
        )

    # ── Responses API (legacy pipeline style) ────────────────────────────────

    def _complete_responses(
        self, prompt: str, temperature: float, max_tokens: int
    ) -> str:
        tokens: list[str] = []
        with self._client.responses.create(
            model=self._model,
            input=[{"role": "user", "content": prompt}],
            temperature=temperature,
            max_output_tokens=max_tokens,
            stream=True,
            **self._extra_request_kwargs(),
        ) as stream:
            for event in stream:
                if getattr(event, "type", "") == "response.output_text.delta":
                    tokens.append(event.delta)

        text = "".join(tokens).strip()
        if self._api_style is None:
            self._api_style = "responses"
        return _strip_code_fences(text)

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

        tokens: list[str] = []
        with self._client.responses.create(
            model=self._model,
            input=[{"role": "user", "content": content}],
            temperature=temperature,
            max_output_tokens=max_tokens,
            stream=True,
            **_structured_output_kwargs(
                api_style="responses",
                mode=structured_output_mode,
                json_schema=json_schema,
                json_schema_name=json_schema_name,
            ),
            **self._extra_request_kwargs(),
        ) as stream:
            for event in stream:
                if getattr(event, "type", "") == "response.output_text.delta":
                    tokens.append(event.delta)

        text = "".join(tokens).strip()
        if self._api_style is None:
            self._api_style = "responses"
        return _strip_code_fences(text)

    # ── Chat Completions API (standard OpenAI) ──────────────────────────

    def _complete_chat(
        self, system: str, user: str, temperature: float, max_tokens: int
    ) -> str:
        messages: list[dict[str, Any]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": user})

        response = self._client.chat.completions.create(
            model=self._model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            **self._extra_request_kwargs(),
        )
        if self._api_style is None:
            self._api_style = "chat"
        return response.choices[0].message.content or ""

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
        content.append({
            "type": "image_url",
            "image_url": {"url": image_data_url},
        })

        response = self._client.chat.completions.create(
            model=self._model,
            messages=[{"role": "user", "content": content}],
            temperature=temperature,
            max_tokens=max_tokens,
            **_structured_output_kwargs(
                api_style="chat",
                mode=structured_output_mode,
                json_schema=json_schema,
                json_schema_name=json_schema_name,
            ),
            **self._extra_request_kwargs(),
        )
        if self._api_style is None:
            self._api_style = "chat"
        return response.choices[0].message.content or ""

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

        tokens: list[str] = []
        with self._client.responses.create(
            model=self._model,
            input=[{"role": "user", "content": content}],
            temperature=temperature,
            max_output_tokens=max_tokens,
            stream=True,
            **self._extra_request_kwargs(),
        ) as stream:
            for event in stream:
                if getattr(event, "type", "") == "response.output_text.delta":
                    tokens.append(event.delta)

        text = "".join(tokens).strip()
        if self._api_style is None:
            self._api_style = "responses"
        return _strip_code_fences(text)

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
            content.append({
                "type": "image_url",
                "image_url": {"url": url},
            })

        response = self._client.chat.completions.create(
            model=self._model,
            messages=[{"role": "user", "content": content}],
            temperature=temperature,
            max_tokens=max_tokens,
            **self._extra_request_kwargs(),
        )
        if self._api_style is None:
            self._api_style = "chat"
        return response.choices[0].message.content or ""

    def _extra_request_kwargs(self) -> dict[str, Any]:
        if not self._config.extra_body:
            return {}
        return {"extra_body": dict(self._config.extra_body)}


# ── Helpers ─────────────────────────────────────────────────────────────


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


def _structured_output_modes(requested_mode: str, *, has_schema: bool) -> tuple[str, ...]:
    """Return a strongest-to-weakest structured-output fallback chain."""
    if requested_mode == "json_schema" and has_schema:
        return ("json_schema", "json_object", "off")
    if requested_mode == "json_object":
        return ("json_object", "off")
    return ("off",)


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


def create_llm_service(config: ServiceConfig) -> OpenAICompatibleService:
    """Factory: create LLM service from config."""
    return OpenAICompatibleService(config)


def create_vlm_service(config: ServiceConfig) -> OpenAICompatibleService:
    """Factory: create VLM service from config."""
    return OpenAICompatibleService(config)
