# Drift Management

## The problem

The pipeline runs in two modes: **full** (re-negotiates the ontology) and **fast** (trusts the existing ontology and anchors extraction to it). The fast path is cheaper but requires a valid assumption: that the current ontology still describes what the documents contain. When that assumption breaks, the fast path produces low-quality, unreliable extractions. Drift management is the mechanism that detects when this happens and forces a full re-run.

---

## Two independent signals

### Signal 1 — Document count (staleness)

`OntologySchema` tracks `documents_since_last_negotiation`. Before each batch `_process_async` checks:

```python
if self._ontology.is_stale(config.ontology_staleness_threshold):
    return await self._full_pipeline(documents)
```

This is a **scheduled** trigger — regardless of content, after N documents the system forces a full re-negotiation. It costs nothing (no embeddings) and guards against slow, unnoticed drift that never produces a single sufficiently drifted batch.

### Signal 2 — Semantic drift (fast path only)

Inside the fast path, *after* extraction has already run, `_should_negotiate_by_drift` computes how well the extracted relations are covered by the current ontology:

$$\text{drift} = 1 - \frac{1}{N} \sum_{i=1}^{N} \max_{j} \cos(\vec{r}_i, \vec{t}_j)$$

where $\vec{r}_i$ are embeddings of the extracted relations and $\vec{t}_j$ are embeddings of the ontology's **relation types** (not entity types). For each extracted relation, find the closest known type. Average those max similarities. Drift = 1 minus that average.

- **Drift ≈ 0** — every extracted relation maps closely to a known type → ontology valid.
- **Drift ≈ 1** — extracted relations are semantically distant from all known types → ontology no longer covers the content.

If drift ≥ 0.25 (mean similarity < 0.75), the fast path **aborts and relaunches `_full_pipeline`** on the same documents.

---

## Why only relation types, not entity types?

Both sides of the drift formula are relation-scoped:
- `rel_texts` = `[r.to_embed for r in extracted_relations]`
- `type_texts` = `[f"{t.label}: {t.definition}" for t in ontology.relation_types]`

Relations are the more semantically loaded signal — they carry verb, roles, and relational context. Entity labels tend to be more stable across topic shifts. The implicit assumption is that relation-level drift is a sufficient proxy for overall ontological mismatch.

---

## Why not just run the Arbiter on the fast-path extractions?

The fast path runs **with** `ontology=self._ontology` and `with_context=True`. The extraction LLM is anchored to the existing type vocabulary. When drift is high, this anchoring degrades the extractions themselves:

- The model may force novel relations into the closest existing label rather than expressing a genuinely new type.
- Relations that don't fit any known type may be missed or under-extracted.

Running the Arbiter on top of these extractions would update the ontology, but the **relations exported to the graph for this batch would still be the degraded fast-path extractions** — typed through the wrong ontological lens.

The full pipeline relaunch solves both problems simultaneously:
1. Re-extracts **without** ontology anchoring → the LLM expresses what it actually sees.
2. The Arbiter updates the ontology based on those clean extractions.
3. The batch is exported with correctly typed relations aligned to the updated ontology.

The drift signal is therefore not just "the ontology needs new types" — it is "the **extraction step needs to be redone**".

---

## Where the ontology actually changes

Regardless of which path triggers the full pipeline, the ontology is updated in exactly one place: `apply_arbiter_decisions`, called at the end of `_run_arbiter`:

```python
self._ontology = apply_arbiter_decisions(session.decisions, self._ontology)
```

`_collect_candidates` fed the Arbiter with the genuinely novel types (filtered through label normalisation and embedding similarity). The Arbiter accepted, merged, or rejected each one. `apply_arbiter_decisions` materialises those decisions into a new versioned `OntologySchema`.

---

## Two signals, two failure modes

| | Staleness counter | Drift score |
|---|---|---|
| **What it catches** | Gradual stagnation | Sudden topic shift |
| **When it fires** | Before extraction | After extraction |
| **Cost** | Zero | One embedding batch |
| **Downside** | Fires even if content hasn't changed | Requires extraction to have already run |

They are complementary: staleness is the **scheduled maintenance** trigger; drift is the **emergency** trigger.

---

## Performance note

Ontology type embeddings are cached in `_cached_ontology_type_embeddings` and only recomputed when the type list changes. Repeated fast-path drift checks across successive batches only embed the extracted relations, not the whole ontology — keeping the marginal cost of drift detection to a single batch embedding call.
