"""Embedding utilities — batch computation with token-aware chunking."""

from __future__ import annotations

from typing import TYPE_CHECKING

import tiktoken
import numpy as np

if TYPE_CHECKING:
    from openai import OpenAI


# ── Constants ───────────────────────────────────────────────────────
_DEFAULT_MODEL = "text-embedding-3-small"
_MAX_TOKENS_PER_BATCH = 8_192


def _count_tokens(text: str, encoding: tiktoken.Encoding) -> int:
    return len(encoding.encode(text))


def compute_embeddings(
    texts: list[str],
    client: OpenAI,
    model: str = _DEFAULT_MODEL,
    max_tokens_per_batch: int = _MAX_TOKENS_PER_BATCH,
) -> np.ndarray:
    """Embed a list of texts with token-aware batching.

    Splits *texts* into batches that fit within the provider's
    per-request token limit, calls the embedding API, and returns
    a (N, D) numpy array of float32 vectors.

    Args:
        texts: Strings to embed.
        client: An ``openai.OpenAI`` client instance.
        model: Embedding model name.
        max_tokens_per_batch: Soft token budget per API call.

    Returns:
        ``np.ndarray`` of shape ``(len(texts), embedding_dim)``.
    """
    if not texts:
        return np.empty((0, 0), dtype=np.float32)

    encoding = tiktoken.encoding_for_model(model)

    # Build token-aware batches
    batches: list[list[str]] = []
    current_batch: list[str] = []
    current_tokens = 0

    for text in texts:
        n_tokens = _count_tokens(text, encoding)
        if current_batch and current_tokens + n_tokens > max_tokens_per_batch:
            batches.append(current_batch)
            current_batch = []
            current_tokens = 0
        current_batch.append(text)
        current_tokens += n_tokens

    if current_batch:
        batches.append(current_batch)

    # Call API batch by batch
    all_vectors: list[list[float]] = []
    for batch in batches:
        response = client.embeddings.create(input=batch, model=model)
        for item in response.data:
            all_vectors.append(item.embedding)

    return np.array(all_vectors, dtype=np.float32)
