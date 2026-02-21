"""Sentence-aware text chunking with token budget and overlap.

Splits a document into chunks of approximately *max_tokens* tokens
without cutting mid-sentence.  Adjacent chunks share an overlap
region so that relations spanning a boundary are captured at least
once.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field

import tiktoken


# ── Constants ───────────────────────────────────────────────────────
_DEFAULT_ENCODING = "cl100k_base"  # GPT-4 / text-embedding-3 family
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


@dataclass(frozen=True)
class Chunk:
    """A contiguous text span from a document."""

    chunk_id: str
    document_id: str
    index: int  # 0-based position within the document
    text: str
    start_char: int  # inclusive offset into the original document
    end_char: int  # exclusive offset
    token_count: int = 0


def generate_chunk_id(document_id: str, index: int, chunk_text: str) -> str:
    """Deterministic chunk ID from document id + chunk index + chunk text.

    Including the chunk text makes the id content-sensitive: changing chunking
    parameters or document text will typically change resulting ids.
    """
    payload = json.dumps(
        {
            "document_id": document_id,
            "chunk_index": index,
            "chunk_text": chunk_text,
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def chunk_document(
    text: str,
    document_id: str,
    *,
    max_tokens: int = 1024,
    overlap_tokens: int = 128,
    encoding_name: str = _DEFAULT_ENCODING,
) -> list[Chunk]:
    """Split *text* into sentence-aware chunks.

    Algorithm:
    1. Split text into sentences.
    2. Greedily accumulate sentences until the token budget is reached.
    3. Emit the chunk and rewind by *overlap_tokens* worth of
       sentences for the next window.

    Args:
        text: Full document text.
        document_id: Identifier of the parent document.
        max_tokens: Soft token budget per chunk (~1024).
        overlap_tokens: Number of trailing tokens to repeat at the
            start of the next chunk.
        encoding_name: ``tiktoken`` encoding name.

    Returns:
        Ordered list of :class:`Chunk` objects.
    """
    if not text.strip():
        return []

    enc = tiktoken.get_encoding(encoding_name)

    # ── 1. Sentence splitting ───────────────────────────────────────
    raw_sentences = _split_sentences(text)

    # Hard-split any sentence that exceeds max_tokens on its own.
    sentences: list[str] = []
    for s in raw_sentences:
        sentences.extend(_hard_split_segment(s, max_tokens, enc))

    sent_tokens = [len(enc.encode(s)) for s in sentences]

    # ── 2. Greedy window with overlap ───────────────────────────────
    chunks: list[Chunk] = []
    idx = 0  # sentence cursor
    chunk_index = 0

    while idx < len(sentences):
        # Accumulate sentences up to max_tokens
        window_sents: list[int] = []  # sentence indices
        window_tokens = 0

        j = idx
        while j < len(sentences):
            cost = sent_tokens[j]
            if window_tokens + cost > max_tokens and window_sents:
                break
            window_sents.append(j)
            window_tokens += cost
            j += 1

        # Build chunk text from the original document (preserving whitespace)
        first_sent_idx = window_sents[0]
        last_sent_idx = window_sents[-1]
        chunk_text = "".join(sentences[first_sent_idx : last_sent_idx + 1])

        # Character offsets — compute from cumulative sentence lengths
        start_char = sum(len(sentences[k]) for k in range(first_sent_idx))
        end_char = start_char + len(chunk_text)

        chunks.append(
            Chunk(
                chunk_id=generate_chunk_id(document_id, chunk_index, chunk_text),
                document_id=document_id,
                index=chunk_index,
                text=chunk_text,
                start_char=start_char,
                end_char=end_char,
                token_count=window_tokens,
            )
        )
        chunk_index += 1

        # ── 3. Rewind for overlap ──────────────────────────────────
        # Walk backwards from the end of the window to find the
        # sentence boundary closest to `overlap_tokens` from the end.
        if j >= len(sentences):
            break  # last chunk — no more text

        overlap_acc = 0
        rewind_to = j  # default: no overlap, continue from j
        for k in reversed(window_sents):
            overlap_acc += sent_tokens[k]
            if overlap_acc >= overlap_tokens:
                rewind_to = k
                break

        idx = rewind_to if rewind_to > idx else j

    return chunks


def _split_sentences(text: str) -> list[str]:
    """Split text into sentence-like segments preserving whitespace.

    Uses a regex split after sentence-ending punctuation (``. ! ?``).
    Keeps trailing whitespace attached to the preceding sentence so
    that ``"".join(segments) == text`` always holds.
    """
    if not text:
        return []

    parts: list[str] = []
    last = 0
    for m in _SENTENCE_SPLIT.finditer(text):
        end = m.end()
        parts.append(text[last:end])
        last = end
    if last < len(text):
        parts.append(text[last:])
    return parts


def _hard_split_segment(
    segment: str,
    max_tokens: int,
    enc: tiktoken.Encoding,
) -> list[str]:
    """Split a single oversized segment into token-bounded pieces.

    Used as a safety net when a "sentence" exceeds *max_tokens*
    (e.g. very long sentences, no punctuation, bullet lists).
    Cuts are placed at whitespace boundaries when possible.

    Invariant: ``"".join(result) == segment``.
    """
    tokens = enc.encode(segment)
    if len(tokens) <= max_tokens:
        return [segment]

    pieces: list[str] = []
    pos = 0  # character cursor

    while pos < len(segment):
        # Decode a max_tokens-sized token window back to text to find
        # the approximate character boundary.
        remaining_tokens = enc.encode(segment[pos:])
        if len(remaining_tokens) <= max_tokens:
            pieces.append(segment[pos:])
            break

        approx_text = enc.decode(remaining_tokens[:max_tokens])
        cut = pos + len(approx_text)

        # Try to snap to the nearest whitespace (look back up to 200 chars).
        snap = segment.rfind(" ", max(pos, cut - 200), cut)
        if snap > pos:
            cut = snap + 1  # include the space with the left piece

        pieces.append(segment[pos:cut])
        pos = cut

    return pieces
