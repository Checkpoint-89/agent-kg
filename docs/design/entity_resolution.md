# Entity Resolution

## The problem

Extraction produces entity mentions, not canonical entities. The same real-world entity can appear under many surface forms across documents ("OpenAI", "Open AI", "OpenAI Inc."), while a single surface form can hide distinct entities ("Apple" the company vs "Apple" the fruit). Entity resolution is the process of **collapsing mentions that refer to the same entity** and **preventing spurious merges** of homonyms.

## Design principles

### 1. Cost escalation — cheap first, expensive last

Resolution is structured as three stages of increasing cost:

| Stage | Method | Cost | Role |
|-------|--------|------|------|
| 1 | Normalised string equality | Negligible | Bulk grouping |
| 2 | Embedding cosine clustering | Medium (API call) | Surface-form bridging |
| 3 | LLM arbitration | High (LLM call) | Homonym guard |

Expensive stages only run on the residue that cheaper stages could not resolve deterministically. This keeps the typical cost close to Stage 1 and only pays for LLM calls when genuine ambiguity exists.

### 2. Operate on groups, not individual mentions

Stage 1 produces **groups of mentions** that share the same normalised `(label, name)` key. Stage 2 then clusters those **groups** (one representative embedding per group), not individual mentions. This means:
- The number of vectors to embed is bounded by the number of distinct surface forms, not the total mention count.
- Stage 2 clusters are always composed of Stage-1 groups, so any merge decision propagates cleanly to all mentions within each group.

### 3. Mutation in place

Entities are mutable Python objects referenced directly inside `Relation` objects. Resolution rewrites the `name`, `label`, and `definition` fields of non-canonical entities directly on those objects. There is no separate "resolution table" to join at query time — after resolution, all downstream stages (graph building, export) see canonical entities transparently.

### 4. Known entity anchors (phantoms) — cross-batch consistency

Entities already present in the graph (from prior batches) are converted into **phantom mentions** before resolution begins. A phantom is a `_Mention` object fabricated from a graph entity's `{name, label, definition}` — it has no source document, no relation (`relation_index=-1`), and the flag `is_known=True`.

Phantoms participate in the pipeline exactly like real mentions:
- They receive a **normalised key** (`norm_key`) and join Stage-1 groups. If a new mention normalises to the same key as a phantom, they land in the same group immediately.
- They receive an **embedding** (`embed_text`) and are included in the Stage-2 clustering matrix. If a new mention's embedding is close enough to a phantom's, their Stage-1 groups get merged into the same Stage-2 cluster.

However, phantoms are **write-protected throughout**:
- `_apply_merge` explicitly skips them (`if m.is_known: continue`) — their `name`, `label`, and `definition` are never overwritten.
- `_pick_canonical` gives them unconditional priority (`confidence=1.0`, known wins over unknown) — so when a new mention lands in the same cluster as a phantom, the phantom's identity becomes the canonical, not the other way around.

The net effect: a new mention that is close enough (string or embedding) to an existing graph entity is silently rewritten to that entity's canonical form without any LLM call. Cross-batch identity is enforced structurally, not by re-running resolution over the full graph.

#### How phantoms are retrieved

The graph can contain millions of entities; injecting all of them as phantoms is not feasible. Instead, `Pipeline._resolve_entities` performs a **targeted vector retrieval** before building the phantom set:

1. Every unique mention in the current batch is embedded (`name | label | definition`).
2. Each embedding is used as a query against the Neo4j `entity_embeddings` vector index (`find_similar_entities`), returning the top-K nearest graph entities per query.
3. Results are deduplicated across all queries — the union becomes the phantom set.

This means only graph entities that are semantically close to at least one mention in the current batch are injected. The retrieval is self-calibrating: the query vectors come directly from the mentions being resolved, so the anchors fetched are precisely those that could plausibly conflict or match.

#### Why top-K with K > 1

For a mention with a single true canonical in the graph, top-1 would be sufficient: if the nearest phantom falls within the clustering threshold it merges, otherwise it doesn't. Retrieving further neighbours changes nothing in that case.

K > 1 is useful for **homonym disambiguation**. Consider a new mention "Apple" that is close to both "Apple Inc" (ORGANIZATION) and "Apple" (FRUIT) in the graph. With K=1 only the nearest is retrieved and the competing alternative is invisible. With K=10 both land in the phantom set, both end up in the same Stage-2 cluster as the new mention, and Stage 3 (LLM) gets to arbitrate between the two competing graph identities rather than blindly merging with the closest one.

In short: top-1 is enough for recall of the true canonical; K > 1 exists to surface competing candidates so the homonym guard has the full picture.

### 5. LLM as a last-resort homonym guard, not a decision engine

Stage 3 is deliberately narrow: it only activates when a Stage-2 cluster contains **more than one distinct (label, name) pair** — i.e., when the embeddings are close but the surface forms differ. Its sole job is to answer: "do these similar-looking mentions refer to the same thing?" When the LLM says no, the cluster is split back into its Stage-1 sub-groups. When it says yes, it also picks the canonical form.

Keeping the LLM role narrow means: (a) fewer LLM calls, (b) the LLM never introduces new surface forms — it only selects among existing ones, and (c) Stage 1 and 2 results are never degraded by LLM errors.

## Stage-by-stage walkthrough

### Stage 1 — Deterministic normalisation

All mentions are normalised: lowercase, accent-stripped, punctuation removed, whitespace collapsed. Mentions are grouped by their `(normalised_label, normalised_name)` key. Mentions in the same group are **already resolved** — they are trivially the same entity. No model calls needed.

### Stage 2 — Embedding clustering

One representative text is selected per Stage-1 group (`name | label | definition` of the first mention). All representatives are embedded in a single batch call. Agglomerative clustering with a cosine distance threshold groups Stage-1 groups together. Clusters with only one Stage-1 group pass through untouched (they are either single-form or already handled). Multi-group clusters become **merge candidates** for Stage 3.

The distance threshold (`entity_resolution_similarity_threshold`) controls the aggressiveness of bridging: too low and aliases are missed; too high and homonyms merge.

### Stage 3 — LLM arbitration

For each multi-group cluster, a formatted prompt is sent to the LLM containing each distinct mention's name, label, definition, role, and the relation description it appeared in. The LLM returns a `MergeDecision`: `should_merge`, `canonical_name`, `canonical_label`, `canonical_definition`, `reasoning`.

If `should_merge` is False, the cluster is split back into Stage-1 sub-groups and each is resolved independently via confidence heuristic. No merge occurs.

If `should_merge` is True, `_apply_merge` rewrites all non-known mentions in the cluster to the canonical form and records the original names as `aliases` in entity metadata.

## Canonical selection priority

When no LLM decision is in play (Stage 1 singleton, or Stage 2 with LLM disabled):

1. **Known graph entities win unconditionally** — they represent the established canonical.
2. Among new mentions, the entity with the **highest extraction confidence** wins.

## What resolution does not do

- It does not persist to the graph — it only rewrites in-memory `Relation` objects.
- It does not resolve relations themselves, only entity mentions within them.
- It does not deduplicate relations (a separate step, if needed).
- It does not handle coreference (pronouns, "the company", etc.) — only named mentions.
