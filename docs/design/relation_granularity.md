# Relation Granularity

## The problem

Extracting relations (or triples) from text forces a **granularity choice**: how much meaning from the original sentence should one relation carry?

This is one of the most studied failure modes in knowledge graph construction. OpenIE systems, NER+RE pipelines, and LLM-based "text → triples" approaches all face it. The symptom varies, but the root cause is always the same: natural language encodes information at a density and flexibility that a flat (subject, predicate, object) schema cannot faithfully represent without losing something.

The tension has two poles:

| Pole | What happens | Consequence |
|------|-------------|-------------|
| **Too faithful to the sentence** | Each relation keeps every modifier, qualifier, and nested clause from the source text. Predicates are long, unique strings. | Near-zero reuse across the graph. Millions of near-unique predicate labels → sparse, disconnected, unqueryable graph. |
| **Too abstract** | Relations are aggressively normalized into a small set of generic types. Modifiers are discarded. | Semantic collapse: distinct sub-kinds are flattened into one label. The graph looks tidy but misrepresents the data. |

Most KG construction systems sit closer to the first pole (they over-generate surface-level facts). The hard part is moving toward the second pole *without crossing it*.

---

## Where we sit

Our pipeline is designed to avoid the "predicate explosion" pole. Extracted relations are **typed against an ontology** (not kept as raw surface predicates), entities go through **entity resolution**, and an **Arbiter** governs ontology evolution. This puts us structurally ahead of raw OpenIE / naive LLM-triple pipelines.

But we still have the granularity problem — it just shows up differently.

### 1. Intra-type collapse (too coarse)

When two semantically distinct relation kinds are close enough in embedding space, both pass the candidates filter and get mapped to the same ontology type. The ontology silently loses expressiveness.

This is the **type splitting** problem documented in [ontology_type_splitting.md](ontology_type_splitting.md).

Example: `EMPLOYS` absorbs both full-time employment and contractor relationships without any signal being raised.

### 2. Over-proliferation of types (too fine)

If the Arbiter accepts too many candidate types, or if seed types are overly specific, the ontology accumulates rare, under-used types with weak connectivity. The graph resembles the "predicate explosion" problem, just wearing ontology labels instead of raw text predicates.

This is partially mitigated by the `merge_with_existing` Arbiter tool, but there is no current mechanism to detect that the ontology has become too fine-grained (no "merge scan" analogous to the proposed split scan).

### 3. Compound-relation loss (n-ary / event structure)

A sentence like *"Apple acquired Beats Electronics for $3 billion in cash on May 28, 2014"* carries at least four pieces of information: the acquisition itself, the price, the payment method, and the date. If the extraction compresses this into one binary or ternary relation, the extra structure is either:

- lost entirely,
- stuffed into a free-text `properties` field (not queryable as first-class graph structure), or
- split into multiple relations that may or may not be linked.

Our reified relation model (relations are nodes, roles are edges — see [reification_and_event_semantics.md](reification_and_event_semantics.md)) handles this better than a flat triple model: the relation node can carry multiple role edges (Agent, Theme) plus property edges (Price, Date). But the extraction prompt and the downstream pipeline must **actually decompose** the sentence into these atomic components. If it doesn't, the relation node becomes a "fat node" — structurally reified but semantically still a monolithic sentence paraphrase.

### 4. Coreference-induced fragmentation

When coreferent mentions ("the company", "it") are not resolved, they create dangling generic entities that reduce graph connectivity regardless of how well relations are typed. This is orthogonal to relation granularity but compounds its effects.

Documented in [coreference_resolution.md](coreference_resolution.md).

---

## The paper's framing and how it maps to ours

The paper referenced in the design discussion proposes a three-stage hierarchical extraction:

| Paper stage | What it does | Our equivalent |
|-------------|-------------|----------------|
| **1. Rich triples** | Extract full sentence meaning as detailed triples (keep modifiers, qualifiers). | Role extraction + property extraction (extraction prompts). |
| **2. Atomic decomposition** | Split compound mentions into atomic components. | Partially handled by our reified model (multiple roles per relation). Not yet explicitly enforced at extraction time. |
| **3. Abstraction** | Generalize specific entities/predicates into higher-level concepts. | Ontology governance (Arbiter typing, `merge_with_existing`), entity resolution (canonical names). |

Plus two cross-cutting concerns:

| Concern | Paper | Us |
|---------|-------|-----|
| **Entity normalization** | Cluster and merge co-referent and synonymous mentions. | Entity resolution (3-stage: norm → embed → LLM arbitration). |
| **Source tracking** | Keep provenance from triple back to source sentence. | `Mention` objects, `provenance` attributes, `raw_text` evidence. |

The gap is primarily in **stage 2** — we don't enforce or verify that extraction produces *atomic* relations. And secondarily in a missing **merge scan** (the reverse of type splitting) that would detect over-proliferation.

---

## What we already have that helps

| Mechanism | How it addresses granularity |
|-----------|------------------------------|
| Ontology typing + Arbiter | Prevents predicate explosion by forcing relations through a governed type vocabulary. |
| `merge_with_existing` | Controls over-proliferation by merging near-duplicate candidate types. |
| Reified relation model | Enables n-ary structure (multiple roles, properties) so compound facts don't have to be crammed into one binary edge. |
| Entity resolution | Normalizes entity surface forms, reducing graph fragmentation from synonymous mentions. |
| Drift detection | Catches ontology staleness when new documents diverge from existing types — triggers re-evaluation. |
| Relation specialization (deferred) | Compositional subtyping (`Sign` + Theme:`Contract` → `Contract_Signing`) preserves generic types while recovering fine-grained distinctions. See [relation_specialization.md](relation_specialization.md). |

---

## What we're still missing

### A. Atomic decomposition enforcement

There is no explicit check that an extracted relation is "atomic" — that it encodes one event/fact, not a compound sentence. A sentence like *"X acquired Y, which had previously partnered with Z"* could produce one fat relation or two atomic ones, and we don't currently guide or verify this.

Possible mitigations:
- **Prompt engineering**: instruct extraction to split compound sentences into independent facts.
- **Post-extraction validation**: flag relations whose text spans contain coordinating conjunctions, relative clauses, or multiple verb phrases.
- **Decomposition agent**: a second LLM pass that takes extracted relations and splits compound ones (analogous to the paper's stage 2).

### B. Merge scan (reverse of type splitting)

Type splitting detects when one type should become two. The inverse — detecting when two types should merge — has no periodic scan. The Arbiter's `merge_with_existing` only fires when a new candidate arrives.

A periodic merge scan would:
- Embed all ontology type definitions.
- Cluster them and flag pairs with high inter-type similarity and overlapping relation populations.
- Surface merge proposals to the Arbiter.

### C. Relation hierarchy / abstraction levels

The ontology is currently flat: each relation type exists at one level of abstraction. There is no mechanism for layered types (e.g., `COMMERCIAL_TRANSACTION` → `ACQUISITION`, `PURCHASE`, `LICENSING`).

A type hierarchy would let queries range over abstraction levels ("give me all commercial transactions" or "give me acquisitions only"). This is a v2+ design item that intersects with the type-splitting fate question (keep `EMPLOYS` as a supertype? see [ontology_type_splitting.md](ontology_type_splitting.md)).

---

## Relationship to other design notes

| Note | Connection |
|------|-----------|
| [ontology_type_splitting.md](ontology_type_splitting.md) | Addresses intra-type collapse (too coarse). |
| [relation_specialization.md](relation_specialization.md) | Addresses controlled sub-typing via role composition (too fine / too coarse tradeoff). |
| [reification_and_event_semantics.md](reification_and_event_semantics.md) | Provides the structural foundation (reified nodes + roles) that enables n-ary decomposition. |
| [coreference_resolution.md](coreference_resolution.md) | Addresses entity-side fragmentation that compounds relation granularity issues. |
| [drift_management.md](drift_management.md) | Catches ontology staleness, which is one trigger for granularity drift. |

---

## Summary

The relation granularity problem is not a single bug to fix — it is a **permanent design tradeoff** that every KG construction system faces. Our pipeline addresses the worst failure mode (predicate explosion) through ontology governance and entity resolution. The remaining gaps are:

1. **Intra-type collapse** → type splitting (periodic scan, deferred).
2. **Over-proliferation** → merge scan (not yet designed).
3. **Compound-relation loss** → atomic decomposition enforcement (not yet designed).
4. **Coreference fragmentation** → coref preprocessing (designed, not yet implemented).

None of these are blockers for a working v1, but all four will surface as quality issues as the graph grows.
