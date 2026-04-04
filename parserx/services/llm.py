"""Pluggable LLM/VLM service abstraction.

Supports any OpenAI-compatible API endpoint. Models and endpoints are
configured via parserx.yaml or environment variables — never hardcoded.
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
    """LLM/VLM service using OpenAI-compatible API.

    Works with OpenAI, Anthropic (via proxy), local Ollama, vLLM,
    or any server implementing the OpenAI chat completions API.
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

    def complete(
        self,
        system: str,
        user: str,
        *,
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> str:
        """Text completion via chat API."""
        messages: list[dict[str, Any]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": user})

        response = self._client.chat.completions.create(
            model=self._model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return response.choices[0].message.content or ""

    def describe_image(
        self,
        image_path: Path,
        prompt: str,
        *,
        context: str = "",
        temperature: float = 0.1,
        max_tokens: int = 8192,
    ) -> str:
        """Image understanding via vision API."""
        image_data = base64.b64encode(image_path.read_bytes()).decode("utf-8")
        mime_type = mimetypes.guess_type(str(image_path))[0] or "image/png"

        content: list[dict[str, Any]] = []
        if context:
            content.append({"type": "text", "text": context})
        content.append({"type": "text", "text": prompt})
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:{mime_type};base64,{image_data}"},
        })

        response = self._client.chat.completions.create(
            model=self._model,
            messages=[{"role": "user", "content": content}],
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return response.choices[0].message.content or ""


def create_llm_service(config: ServiceConfig) -> OpenAICompatibleService:
    """Factory: create LLM service from config."""
    return OpenAICompatibleService(config)


def create_vlm_service(config: ServiceConfig) -> OpenAICompatibleService:
    """Factory: create VLM service from config."""
    return OpenAICompatibleService(config)
