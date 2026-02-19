# Design Note — Relations as Nodes, Roles as Edges

**Status:** Research / v2 consideration  
**Date:** 2026-02-19

---

## The two ideas behind our KG

Our knowledge graph rests on two independent design choices. Each answers a different question:

1. **"What is a relation in the graph?"** — We treat relations as **nodes with their own identity**, not as simple edges between entities. This comes from Davidson (1967).

2. **"How do participants connect to a relation?"** — We use a **fixed set of semantic roles** (Agent, Theme, Instrument…) applied uniformly to all relation types. This comes from Fillmore's Case Grammar (1968).

These two ideas are orthogonal — one decides the shape of the graph, the other decides the vocabulary of the links.

---

## Idea 1 — Relations are nodes (Davidson)

The standard way to model "Alice signed a contract in Paris" is a single edge:

```
Alice --signed--> Contract
```

The problem: where does "in Paris" go? You can't hang metadata off an edge without hacks.

Davidson's fix: make the relation itself a node.

```
(r:Sign) --Agent--> Alice
         --Theme--> Contract
         --Location--> Paris
         --Time--> 2026-01
```

The relation instance `r` is now a first-class object. You can attach any number of properties, link it to other relations (cause, sub-relation), and assert partial information (you can know the Theme without knowing the Agent).

**Our `Relation` object does exactly this.** It has an ID, a type, provenance, and properties. In Neo4j it is stored as a node; entities connect to it via a standardized relationship (e.g. `HAS_ROLE`), and the role itself is stored as an attribute on the entity-in-relation record.

Parsons (1990) pushed this further: treat each role link (Agent, Theme, etc.) as an independent fact about the relation instance, not a mandatory slot you must always fill. Our data model supports this: we can keep a relation even when some roles are unknown, and (re)introduce stricter requirements later via configurable validation rules.

---

## Idea 2 — One role set for all relations (Case Grammar)

Once you have relation nodes, you need a vocabulary for the edges. Fillmore's Case Grammar (1968) provides one: a small, universal set of deep roles — Agent, Theme, Instrument, Beneficiary, Location, Time… — that apply to **every** verb the same way.

"Sign a contract" and "deliver goods" both get Agent, Theme, Instrument — no distinction.

**Our `DEFAULT_ROLES` is exactly this:** 12 universal roles, defined once per domain in `DomainConfig.roles`, applied uniformly to every relation type.

**What this gives us:**
- Simple configuration — one role set to learn, prompt with, and validate against.
- Cross-domain portability — the same roles work whether you're modelling energy contracts or software incidents.

**What this means in practice:**
- Role labels stay generic (Agent/Theme/…), and specialization lives primarily in the **ontology** (relation types + entity types/labels), not in role names.
    This still lets us recover distinctions like Buyer vs. Seller indirectly via (a) the discovered relation type, (b) the types/labels of the participants that fill roles, and (c) the role-composition-based specialization described in [relation specialization](relation_specialization.md).
- Constraints (required/optional roles) live in **validation rules**, not in a first-class per-relation-type role schema.
    We can already enforce per-type constraints via `DomainConfig.validation_rules` (predicates that branch on relation type). The main trade-off is ergonomics/maintainability: a declarative per-type role schema (FrameNet-style) would make such constraints easier to author, review, and evolve.

---

## How it all fits together

| What we do | Where it comes from |
|-----------|-------------------|
| Relations are nodes with IDs, not edges | Davidson |
| Metadata (time, location) hangs off the relation node | Davidson |
| Participants linked via typed roles (Agent, Theme…) | Case Grammar |
| Same role set for all relation types | Case Grammar |
| Per-relation-type role sets (Buyer, Seller…) | FrameNet — **not yet** |
| First-class per-type required/optional roles | FrameNet — **not yet** (but can be approximated with validation rules) |
| Relations referencing other relations (cause, sub-relation) | Davidson — **not yet** |
| Partial extraction (keeping relations with missing roles) | Parsons — supported; optionally constrained |

---

## A possible upgrade: FrameNet

Fillmore himself evolved Case Grammar into **FrameNet** (1998+). The key change: instead of one universal role set, each relation type defines its **own** roles, split into:

- **Core** — participants that define the relation type (e.g. *Commercial_transaction* → Buyer, Seller, Goods, Money).
- **Peripheral** — optional modifiers shared across relation types (Time, Place, Manner).

Think of it as **typed subgraph templates**: a "Signing" frame is a template with Signatory, Document, Counterparty as required slots, plus the universal peripherals.

| | Case Grammar (v1) | FrameNet (possible v2) |
|--|-------------------|----------------------|
| Role inventory | One universal set | Per-relation-type |
| Role requirements | Expressed via validation rules (can vary by type, but not a per-type role schema) | Declared per relation type (core vs. peripheral) |
| Precision | Generic (Agent, Theme) | Specific (Buyer, Seller) |
| Config cost | Low | Higher — must define roles per relation type |

### What FrameNet would change concretely

1. **A stabilized subgraph becomes a frame:** once a recurring pattern (e.g. *Signing*) is repeatedly observed and accepted into the ontology, we can treat it as a reusable subgraph template: named slots (Signatory/Document/Counterparty) plus allowed peripherals.

2. **Relation types can point to their frame:** each discovered/negotiated `RelationType` can optionally indicate the frame it instantiates. This makes the ontology more descriptive: a relation type is not just a label, but a label + expected role structure.

3. **Extraction becomes more robust:** when a relation type has a known frame, extraction can condition on it, reducing role-mapping ambiguity and constraining which fillers “make sense” in each slot (type expectations, typical patterns, allowed peripherals).

4. **Querying becomes more direct:** with frames, queries can be expressed closer to natural language (“find Signings where Signatory is ACME and Document is an MSA”). v1 can already approximate this via entity typing + role-mapping instructions, but frames make it more explicit and reusable.

5. **Validation constraints are embedded in the frame:** instead of encoding requirements primarily as free-form predicates, frames can carry declarative constraints (required slots, cardinalities, type expectations). The validator can still keep the rule layer, but frames become the first place to look for structural correctness.

6. **Link to [relation specialization](relation_specialization.md):** a frame is essentially a specialization driven by role composition. Stabilizing a frame is one way to make those specializations explicit, reusable, and enforceable.

**Trade-off:** more expressive, but higher configuration cost. This is a v2 item.

---

## How Case Grammar, FrameNet, and Davidson relate

A common confusion is treating these as competing approaches. They're not — they answer different questions and layer on top of each other:

**Davidson** gives us the graph backbone: relations are nodes, participants attach via predicates. He did not prescribe *which* predicates to use — Agent, Theme, etc. are just convenient labels, not the point of the theory. The point is: **make the relation a first-class object**.

**Case Grammar** (Fillmore 1968) gives us the role vocabulary: a small, verb-independent set of participant types. It's structurally very close to Davidson — both are verb-centered, both decompose relations into role predicates.

**Frame Semantics** (Fillmore later) moves further: from abstract roles to rich conceptual scenes, from argument structure to encyclopedic knowledge. In graph terms, frames look like **recurring subgraph patterns** — typed templates with specific roles. This is closer to cognitive science than to formal logic.

| Layer | What it provides | Source |
|-------|-----------------|--------|
| Graph backbone | Relations are nodes, roles are edges | Davidson (1967) |
| Role vocabulary | Universal role set (Agent, Theme…) | Case Grammar (1968) |
| Relation templates | Per-relation-type roles, core vs. peripheral | Frame Semantics / FrameNet (1998+) |

Our KG sits at **layers 1 + 2**. Layer 3 is a possible v2 enhancement.

These layers are **additive, not exclusive** — adopting FrameNet-style templates doesn't replace Davidson or Case Grammar, it builds on top of them.

---

## What we could add (v2 directions)

### From Davidson / Parsons

1. **Relation-to-relation links:** let a `Relation` reference another `Relation` as a role filler — enables causal chains, temporal ordering, and decomposition into sub-relations.

### From FrameNet

3. **Relation-type-specific roles:** define Buyer/Seller/Goods for commercial relations, Mover/Source/Goal for transfers, instead of reusing generic Agent/Theme everywhere.

4. **Required vs. optional roles per relation type:** "this relation needs a Signatory and a Document; Instrument is optional."

---

## Related frameworks worth reviewing

| Framework | What it offers | Example gain |
|------------|---------------|--------------|
| **FrameNet** | Per-relation-type roles with core/peripheral distinction. | Relation: `Hiring`. Core roles = Employer, Employee; peripherals = Time, Location. → Makes it natural to validate that `Employee` is present for a well-formed Hiring relation. |
| **Parsons / Neo-Davidsonian** | Clear logical structure: reified relation instance + independent role predicates. | Formalizes/justifies the design we already implement (partial information + constraints handled via validation). |
| **VerbNet** | Verb-class-based groupings with shared thematic-role patterns (useful for clustering/normalizing relation types). | `buy`/`purchase`/`acquire` often fall into related classes. → Helps normalize near-synonymous relation types under a shared taxonomy node (e.g. `Transaction`). |
| **PropBank** | NLP-compatible numbered roles (Arg0, Arg1…) and adjuncts (ArgM-*). | SRL output: `Arg0=CEO`, `Arg1=contract`, `ArgM-TMP=Monday` → Provides a systematic (predicate-specific) mapping source into your role inventory (e.g. Agent/Theme/Time). |
| **AMR** | Graph-native semantic representation combining reified predicates with role-labelled edges. | AMR parse: `(s / sign-01 :ARG0 CEO :ARG1 contract :time Monday)` → Often close to a direct transformation into relation nodes + role edges (with project-specific mapping). |


---

## Recommendation

The current design — **relations as nodes (Davidson) + universal roles (Case Grammar)** — is a solid, pragmatic v1.

For v2, in order of expected impact:

1. **FrameNet-style relation-type-specific roles** — biggest gain for extraction quality. Requires extending `SeedType` or `RelationType` to carry their own role definitions.
2. **Relation-to-relation links** — enable causal and temporal reasoning across relations.
