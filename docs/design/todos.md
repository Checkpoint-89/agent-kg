# Design TODOs

**Status:** Working notes  
**Date:** 2026-02-20

---

## Backlog

- Review `Relation` computed fields: clarify how `generic` / `specific` (and related text fields) are built, what invariants they should satisfy, and whether they should be derived vs persisted.
- Entity resolution: consider introducing an explicit mention-level embedding text (e.g. `mention_to_embed` / `_Mention.embed_text`) that can include role/relation context, instead of reusing `Entity.to_embed` (canonical) for clustering.
- Pipeline: QC (`_run_qc`) currently only logs/counts flags; discuss how to handle quality issues (e.g., block export, trigger re-extraction, or surface as a report without action).
- Pipeline: role filling (`_fill_roles_batch`) currently fetches `GraphContext` once from `documents[0].text` and reuses it for all relations in the batch; discuss whether context should be retrieved per document (or per relation) to avoid cross-document bias.
- Embeddings persistence: for nodes that have an embedding vector, also persist the exact `to_embed`/`embed_text` that was embedded (plus embedding model + version). Goal: avoid recomputing embed inputs, enable auditability/debugging, and support re-embedding strategies.
- Review property vs node modeling: when a concept should remain a node property versus becoming a dependent node (identity, multiplicity, provenance, evolution over time, and query patterns).
- Revisit the structural constraint requiring `Agent` + `Theme`: document the rationale, when it fails (e.g., stative / attribution / non-event relations), and possible v2 alternatives (configurable required roles, per-relation-type constraints, or discourse-claim modeling).
