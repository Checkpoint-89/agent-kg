# Coreference Resolution

## The gap

The role extraction prompt contains an explicit constraint:

> "Entity names must match the source text exactly."

This means the extraction LLM is instructed to copy entity surface forms verbatim. It has no mechanism for resolving pronouns or definite descriptions to their named antecedents.

Given a passage like:

> "Apple acquired Beats Electronics. The company paid $3 billion in cash."

The relation extracted from the second sentence would have `"The company"` as its agent — not `"Apple"`. In entity resolution, `"The company"` is too generic to cluster with `"Apple"` by normalisation or embedding. It ends up as a distinct, dangling entity in the graph.

This is not an oversight — it is a scope decision. Coreference is a document-level problem, and the current pipeline processes documents in overlapping 1024-token chunks. The LLM may implicitly resolve obvious coreferences within a chunk due to its training, but this is uncontrolled and unverifiable.

---

## Why it matters

The knock-on effects compound:

1. **Duplicate entity nodes** — `"Apple"` and `"The company"` become separate graph nodes, fragmenting the entity's relation graph.
2. **Missed entity resolution** — even if ER ran, `"The company"` has a generic embedding that won't pull it into the `"Apple"` cluster.
3. **Broken graph connectivity** — queries for all relations involving Apple miss the ones where it appeared as a coreferent.
4. **Validation noise** — `"The company"` may trigger generic entity blocklist violations.

---

## How coreference is handled in KG pipelines today

In current knowledge graph ingestion pipelines, coreference is rarely treated as a single, “perfect” upstream step. Instead, teams choose one of a few patterns depending on how much they care about (a) recall vs precision and (b) evidence traceability.

Common patterns, roughly from lowest to highest engineering effort:

1. **Drop coreferent mentions** (production-common baseline)
    - Extract only explicit named entities; ignore pronouns / definite descriptions.
    - This avoids creating confidently-wrong edges, but recall is lower (missed relations).

2. **Prompt-level coreference inside extraction**
    - Ask the extraction model to resolve pronouns / definite descriptions when it can.
    - Works best when antecedent and anaphor are in the same chunk.

3. **Preprocessing coreference as annotations (span-level)**
    - Run a coref model over the full document and emit clusters as span links.
    - Keep the original text as the source of truth; let later steps *use* the annotations.
    - This is a common “production-safe” compromise because it preserves evidence.

4. **Preprocessing coreference as a rewritten text**
    - Generate a `resolved_text` where mentions like “the company / he / it” are replaced.
    - This improves extraction recall quickly, but can break strict “verbatim quote” invariants unless you keep the original text around.

5. **Mention-layer graphs** (high assurance)
    - Ingest mention nodes (doc_id + offsets) and attach `REFERS_TO` edges with confidence.
    - Promote edges to entity-level only after validation / corroboration.

Tooling-wise, the “classic” pipeline choice is Stanford CoreNLP’s coref component; in Python ecosystems, spaCy plugins such as Coreferee / crosslingual-coreference exist. In 2025–2026, a lot of teams also use LLMs for within-document resolution, typically producing either (a) a mapping of spans to antecedents or (b) a `resolved_text` as auxiliary input.

---

## Mitigation strategies for this codebase

### Option A — Prompt-level resolution (cheap, imperfect)

Add an instruction to the role extraction prompt:

> "Before assigning entity names, resolve all pronouns and definite descriptions to their nearest named antecedent within the document. Use the resolved name, not the pronoun."

**Pros**: zero infrastructure cost; works within the existing pipeline.  
**Cons**: unreliable across chunk boundaries (the antecedent may be in a different chunk); depends on LLM consistency; unverifiable without a test suite.

This is the pragmatic default for Phase 2 for intra-chunk coreferences. Cross-chunk coreferences remain unresolved.

### Option B — Pre-processing as annotations (recommended default for correctness)

Run a dedicated coreference resolution step over the **full document** before chunking, but emit a **coreference map** instead of rewriting the original text.

Conceptually:

- Detect mentions and clusters over the full document.
- For each anaphoric mention span, produce a best-guess antecedent span (plus a confidence score).
- Preserve both spans so downstream steps can still quote the original text.

You then have two safe ways to use the result:

1. **Augment extraction prompts**: provide (original_text + coref_map) so the extraction model can choose the resolved entity name while still quoting original evidence.
2. **Post-process extracted relations**: if a role is “the company”, rewrite the role to the antecedent’s surface form, while keeping the original mention string as an attached attribute.

**Pros**: preserves evidence; full-document scope (not chunk scope); lets you apply conservative thresholds (“only rewrite if confidence ≥ X”).  
**Cons**: extra inference step per document; still imperfect; requires careful span bookkeeping.

### Option C — Pre-processing as rewritten text (fast POC, higher risk)

Run a dedicated coreference resolution step over the **full document** before chunking. Replace every anaphor with its resolved named antecedent in the document text. The enriched text is then chunked and fed to extraction.

```
raw document
    → coreference resolution (spaCy coref / second LLM call)
    → enriched document (anaphors replaced with named antecedents)
    → chunk_document(...)
    → extract_raw_relations(...)
```

**Pros**: improves recall; resolution happens at full document scope, not chunk scope; extraction sees fewer pronouns / generic mentions.  
**Cons**:
- Adds a dependency (spaCy `coref` or an extra LLM call per document).
- Increases per-document cost and latency.
- Resolution errors propagate: if the coreference model is wrong, the extraction is wrong.
- LLM-based resolution (second call) adds non-determinism.

Important nuance for this pipeline: because we validate quotes / surface forms, the “resolved” text should be treated as **auxiliary context**, not as the authoritative evidence source. A safe implementation pattern is:

- Keep `raw_text` unchanged for quote extraction and validation.
- Provide `resolved_text` to the extraction model as a helper (or provide inline annotations), but require that any quoted evidence is still taken from `raw_text`.

---

## Cross-chunk coreference: the harder problem

Even with Option B, cross-document coreferences (e.g., "the CEO" referring to someone introduced three documents earlier) remain unresolved. These are a different class of problem — they require graph-level knowledge ("who is the CEO of Apple at this point in time?") rather than document-level resolution.

The phantom mention mechanism in entity resolution partially mitigates this: known graph entities are injected as anchors. If `"The CEO"` has enough semantic overlap with a known entity (from prior batches), entity resolution may pull them together. But this is accidental — it depends on `"The CEO"` and the named entity having similar embeddings, which is domain-dependent.

A robust solution would require a **named entity linking (NEL)** pass: for each generic or ambiguous entity mention, query the graph for candidates and ask the LLM to confirm the link. This is a significant additional component.

---

## Recommendation for Phase 2

1. **Keep Option A** as a baseline: update the extraction prompt to resolve within-chunk corefs when possible.
2. **Add a preprocessing switch** to `DomainConfig` (default off) that enables either:
    - **annotation mode** (Option B), or
    - **rewrite mode** (Option C, POC).
3. **Prefer annotation mode** when evidence traceability matters (most KG use cases). Use rewrite mode when you want a quick recall boost and are comfortable treating the output as “assistive text”.
4. **Monitor coref-related quality** with simple counters:
    - count of exported entity names matching an anaphor/definite-description blocklist ("the company", "he", "she", "they", "it", …)
    - share of relations whose roles were rewritten by coref (coverage)
    - disagreement rate if you do multi-pass LLM resolution (stability)
5. **Defer cross-document linking (NEL)** until it is a measured quality bottleneck.

---

## Relationship to entity resolution

Entity resolution (ER) as currently designed explicitly **excludes** coreference from its scope (see `entity_resolution.md`: "It does not handle coreference (pronouns, 'the company', etc.) — only named mentions"). The two problems are complementary:

- **Coreference resolution**: maps anaphors → named surface forms (pre-extraction or in-extraction).
- **Entity resolution**: maps named surface forms → canonical graph entities (post-extraction).

Coreference should happen upstream so that ER only ever sees named entity strings. The current pipeline has the second layer but not the first.
