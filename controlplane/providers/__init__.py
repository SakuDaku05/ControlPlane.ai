from __future__ import annotations

from ..config import settings
from ..schemas import Provider
from .anthropic_provider import AnthropicWireFormat
from .base import ContentSource, StreamChunk, UpstreamContentSource, UpstreamError, WireFormat
from .mock_provider import MockContentSource, list_scenarios, scenario_id_from_model
from .openai_provider import OpenAIWireFormat

_wire_formats: dict[str, WireFormat] = {
    Provider.OPENAI.value: OpenAIWireFormat(),
    Provider.ANTHROPIC.value: AnthropicWireFormat(),
}

_mock_source = MockContentSource()


def get_wire_format(provider: str) -> WireFormat:
    return _wire_formats[provider]


def get_content_source(provider: str, model: str) -> ContentSource:
    """Route to the offline demo provider whenever the model name is
    `mock:<scenario>` (or CONTROLPLANE_DEMO_MODE forces it for unknown
    models with no API key configured); otherwise call the real upstream."""
    if model.startswith("mock:"):
        return _mock_source

    wire = get_wire_format(provider)
    api_key = settings.openai_api_key if provider == Provider.OPENAI.value else settings.anthropic_api_key
    if not api_key and settings.demo_mode:
        # No key configured, but demo mode is on: fail soft into the mock
        # provider rather than erroring the whole request, so the sidecar
        # is still explorable out of the box.
        return _mock_source
    return UpstreamContentSource(wire, api_key)


__all__ = [
    "ContentSource",
    "StreamChunk",
    "UpstreamContentSource",
    "UpstreamError",
    "WireFormat",
    "MockContentSource",
    "get_wire_format",
    "get_content_source",
    "list_scenarios",
    "scenario_id_from_model",
]
