# Design Note — Relation Specialization via Compositional Typing

**Status:** Deferred to v2  
**Author:** Design team  
**Date:** 2026-02-19

---

## Problem

Relation types defined by **verb alone** (e.g. *Sign*) are under-specified.  
Relation types defined by **verb + target** (e.g. *Sign_Contract*) collapse orthogonal layers and reduce generalization.

## Proposed solution

Keep three orthogonal layers:

| Layer              | Example                        |
|--------------------|--------------------------------|
| Relation type      | `Sign` (verb only)             |
| Roles              | Agent, Theme, Counterparty … |
| Entity classes     | Contract, Person, Organization |

Subtypes **emerge** from composition rather than being baked in:

```
Relation.type = Sign  AND  Theme.class = Contract  →  subtype = Contract_Signing
```

## Narrowing-role guidelines

Per relation type, identify which role(s) reclassify the event category:

- **Theme** is decisive most often (Deliver + Goods, Sign + Contract).
- **Another core role** can be decisive (Pay + Beneficiary=Government → Tax_Payment).
- **Multiple roles** sometimes combine (Transfer + Theme=Shares + Source/Goal=Orgs → Acquisition).

Operational form:

```
IF   Relation.type = X
AND  Role_i.class ∈ Y
[AND Role_j.class ∈ Z]
→    Relation.subtype = S
```

## Benefits

- Generalization preserved (verb stays generic).
- Structural clarity (layers don't collapse).
- Controlled specialization (rules, not ad-hoc naming).
- Queryability (query by type, subtype, or role independently).

## Open questions (to resolve before implementation)

1. **Where do specialization rules live?**
   - `DomainConfig` (new field `specialization_rules`)?
   - `SeedOntology` (attached to seed relation types)?
   - Emergent (LLM-discovered, human-validated)?

2. **When is subtype computed?**
   - At extraction time (LLM decides) — flexible but less controllable.
   - Post-extraction rule engine — deterministic, auditable.
   - During ontology negotiation — fits the arbiter pattern.

3. **Clustering target:** today we cluster on `verb + target_category` embeddings. Decoupling means choosing a new clustering input (verb alone? verb + role signature?).

4. **Graph schema:** Neo4j must store `type` and `subtype` as separate properties (or use a label hierarchy) to preserve queryability.

## Current state (v1)

`RelationType._compute_label()` concatenates `verb + target_category` into a single label. This is the collapse the note critiques. Changing it touches:

- `models/base.py` — `RelationType` model and label computation.
- `executors/clustering.py` — embedding text construction.
- `agents/prompts.py` — prompt templates referencing relation types.
- `models/graph.py` — graph edge properties.
- `agents/arbiter_agent.py` — type negotiation logic.
