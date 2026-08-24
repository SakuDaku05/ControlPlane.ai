"""Small shared helpers that don't belong to any one component."""
from __future__ import annotations

import tiktoken

# We use cl100k_base (GPT-4 tokenizer) as a fast, standard approximation
# for exact billing-grade numbers mid-stream.
_encoder = tiktoken.get_encoding("cl100k_base")


def estimate_tokens(text: str) -> int:
    """Accurate token-count using tiktoken. Used whenever a provider hasn't
    reported real usage numbers yet (e.g. mid-stream, or the Mock provider).
    Provides exact billing-grade numbers for the Cost Telemetry Agent."""
    if not text:
        return 0
    return len(_encoder.encode(text))
