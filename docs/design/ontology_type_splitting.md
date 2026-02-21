# Ontology Type Splitting

## The blind spot

The pipeline's drift signal and `_collect_candidates` filter share a structural limitation: they only detect **absence** — relations that don't fit any existing type. They are completely blind to **intra-type divergence** — relations that fit an existing type too well while actually representing two distinct semantic sub-kinds.

Consider `EMPLOYS`. New documents introduce contractor relationships alongside the full-time employment already in the graph. Both map closely to `EMPLOYS` in embedding space, so:
- They pass the embedding similarity filter in `_collect_candidates` → not forwarded to the Arbiter.
- They don't raise drift — mean similarity to `EMPLOYS` is high.
- They are exported as `EMPLOYS`, the bimodality is invisible.

The ontology silently becomes less expressive without any signal being raised.

---

## Why this is hard to detect per-batch

The per-batch pipeline only sees the current window of documents. A single batch may contain only one sub-kind, or a mix too small to make the bimodality statistically visible. The signal only becomes reliable when looking at the **accumulated distribution** of relations mapped to each type across the full graph.

This is fundamentally a **post-ingestion, graph-level analysis** — not something the per-batch pipeline can do.

---

## Detection approach

For each relation type $T$ in the ontology, retrieve all graph relations whose type label is $T$ and embed them. Run agglomerative clustering on those embeddings. If the resulting clusters are well-separated (silhouette score above a threshold), raise $T$ as a **split candidate**.

More formally, for a type with $N$ mapped relations:

$$s(T) = \text{silhouette\_score}(\text{embed}(\text{relations}_T), \text{agglomerative\_clusters})$$

If $s(T) \geq \theta_{split}$ and the number of clusters $k \geq 2$, surface $T$ as a split candidate with the $k$ proposed sub-types.

This scan should run periodically (e.g., after every M documents ingested, or triggered manually) — not on every batch.

---

## What the Arbiter would need

The current Arbiter has three tools: `accept_type`, `merge_with_existing`, `reject_type`. Type splitting requires a new tool:

```
split_type(
    existing_label: str,
    sub_types: list[{label, definition}],
    reasoning: str
)
```

The Arbiter would receive: the existing type, its current definition, representative examples from each proposed sub-cluster, and the clustering evidence. It decides whether the split is semantically meaningful or an artefact of surface variation.

If confirmed, `apply_arbiter_decisions` would need to:
1. Create the new sub-type labels in the ontology.
2. Optionally: re-tag relations already in the graph (requires a graph migration step).
3. Deprecate or keep the parent type (policy decision — see below).

---

## The re-tagging problem

This is the hardest part. Relations already exported under `EMPLOYS` don't automatically become `EMPLOYS_FULLTIME` or `EMPLOYS_CONTRACTOR`. Three options:

| Option | Description | Cost | Risk |
|--------|-------------|------|------|
| **Lazy** | Keep old relations as `EMPLOYS`; only new ingestion uses the split types | Zero | Graph becomes heterogeneous — same concept, two labels |
| **Re-embed + re-tag** | Query all `EMPLOYS` relations, re-classify using the new sub-type definitions via LLM | Medium | LLM inconsistency; expensive at scale |
| **Full re-ingestion** | Re-run the pipeline on the source documents with the updated ontology | High | Clean but requires access to original documents |

The lazy option is the pragmatic default for Phase 2 but should be treated as temporary. The graph should record a `deprecated_at` version on the parent type so queries can filter or merge as needed.

---

## Relationship to the discovery phase

The ontology discovery phase (first run, no seed) and type splitting are complementary:

- **Discovery phase**: shapes the initial ontology from scratch, before any data is in the graph.
- **Type splitting**: refines the ontology as the graph grows and intra-type divergence accumulates.

Together they form the two ends of **ontology lifecycle management**: birth and refinement. The Arbiter's incremental governance (per-batch) handles the middle — new types arriving one by one.

---

## Open questions

1. **Trigger policy**: scan after every M documents? On manual request only? After major drift events?
2. **Minimum cluster size**: a split into a cluster of 2 and a cluster of 500 is probably noise; enforce a minimum fraction per sub-cluster.
3. **Parent type fate**: deprecate `EMPLOYS` and force migration, or keep it as a supertype for backward compatibility?
4. **Entity types**: the same bimodality problem applies to entity types, but is likely rarer in practice. Include in the same scan or handle separately?
