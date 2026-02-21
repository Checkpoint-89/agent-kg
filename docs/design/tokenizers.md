# Design Note — Tokenizers and Token Counting (Multi-supplier)

**Status:** Problem analysis / v2 consideration  
**Date:** 2026-02-20

---

## Context (v1)

In v1 we use `tiktoken` (see `encoding_for_model()` / `cl100k_base`) to:

- estimate token counts for chunking and overlap budgets
- batch embedding requests within provider token limits

This works because v1 is OpenAI-centric (OpenAI model names + OpenAI tokenizers).

---

## Problem

In a multi-supplier setup (OpenAI / Anthropic / Gemini / open-source models), **"token" is not a universal unit**:

- different providers use different tokenizers and vocabularies
- model name → tokenizer mapping differs per provider
- limits are provider/model-specific (and can change)

So a single `tiktoken`-based token count is:

- correct for OpenAI tokenizers
- at best an approximation elsewhere

This creates two risks:

1. **Budget risk:** inaccurate counts → over-limit requests or overly conservative batching.
2. **Semantic coupling:** code that assumes OpenAI tokenizer semantics becomes harder to generalize.

---

## What we actually need from "tokenization"

There are two distinct needs:

1. **Budgeting** (engineering constraint)
   - split/chunk/batch so requests stay under limits
   - needs to be conservative and stable

2. **Exact accounting** (optional)
   - for observability, audits, and precise limit enforcement
   - needs the provider/model’s true tokenizer

In most pipelines, budgeting is the critical need; exact accounting is a quality-of-life upgrade.

---

## Desired v2 shape (conceptual)

### 1) Abstract token counting behind a provider-aware interface

Instead of importing `tiktoken` directly everywhere, introduce a conceptual interface:

- `TokenCounter.count(text, model, provider) -> int`

Implementation choices can vary by provider:

- OpenAI: `tiktoken.encoding_for_model(model)`
- Open-source (HF): `transformers` tokenizer for the specific model
- Providers without public tokenizers: conservative heuristic (e.g., characters/4) with safety margin

### 2) Make provider/model a first-class input

Avoid code paths that infer tokenization from only `model: str`.

A multi-supplier stack should carry:

- `provider` (openai / anthropic / google / local)
- `model` (provider-specific identifier)

### 3) Prefer conservative budgeting when exact tokenizer is unknown

When exact counting is unavailable, enforce budgets with a margin:

- `estimated_tokens * safety_factor <= limit`

and keep observability so we can tune the safety factor.

---

## Design constraints and notes

- Token budgets are *provider concerns*, not KG semantics.
- Chunking quality should not depend on a specific provider tokenizer.
- Token counting should be centralized to avoid fragmented assumptions.

---

## Open questions

1. Which suppliers are in scope (OpenAI only vs OpenAI + Anthropic + HF)?
2. Do we require exact tokenizers for all suppliers, or accept conservative heuristics?
3. Should the chunker budget be expressed in tokens, characters, or both (tokens preferred when exact)?
