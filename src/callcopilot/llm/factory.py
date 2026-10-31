from __future__ import annotations

import httpx

from .client import LLMClient
from .config import LLMConfig


def create_client(config: LLMConfig, transport: httpx.BaseTransport | None = None) -> LLMClient:
    """The single place that maps config -> provider adapter. Add a new provider here only."""
    if config.provider == "fake":
        from .fake import FakeLLM
        return FakeLLM()
    if config.provider == "anthropic":
        from .anthropic_adapter import AnthropicAdapter
        return AnthropicAdapter(config, transport)
    from .openai_adapter import OpenAIAdapter
    return OpenAIAdapter(config, transport)
