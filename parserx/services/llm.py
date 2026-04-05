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
    ) -> str:
        """Image understanding — tries Responses API then Chat Completions."""
        image_data_url = _encode_image_data_url(image_path)

        if self._api_style != "chat":
            try:
                return self._describe_responses(
                    image_data_url, prompt, context, temperature, max_tokens
                )
            except Exception as exc:
                if self._api_style is None and _is_not_found(exc):
                    log.info("Responses API not available, falling back to Chat Completions")
                    self._api_style = "chat"
                else:
                    raise

        return self._describe_chat(
            image_data_url, prompt, context, temperature, max_tokens
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


def create_llm_service(config: ServiceConfig) -> OpenAICompatibleService:
    """Factory: create LLM service from config."""
    return OpenAICompatibleService(config)


def create_vlm_service(config: ServiceConfig) -> OpenAICompatibleService:
    """Factory: create VLM service from config."""
    return OpenAICompatibleService(config)
