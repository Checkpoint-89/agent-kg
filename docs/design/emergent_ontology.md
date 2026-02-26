# Design Note — Emergent Ontology: Abstraction, Specialisation, and Convergence

**Status:** Design guide  
**Date:** 2026-02-22 (revision 2)

---

> **Notes structurantes**

🔹 **Split / Merge établis: sur les protocoles split/merge et la détection des candidats**

| Variance (ou Jaccard) | Silhouette | Diagnostic | Confirmation |
|:---------------------:|:----------:|:-----------|:-------------|
| +                     | +          | Split possible | Agglomératif + silhouette |
| -                     | +          | Bien formé     | — |
| +                     | -          | Split ou merge possible | Tester split d’abord, merge ensuite |
| -                     | -          | Merge possible | Corrélation TF-IDF (niveau type) |

Principe clé :
— Toujours traiter le split avant le merge en cas d’ambiguïté.
— Si confirmation, passage à l’Arbiter.

🔹 **Splits émergents**

Principe :
— Test léger de dispersion locale (heuristique low-cost).
— Si suspicion, passage à HDBSCAN.
— Clusters stables et non marginaux = candidats.
— Si confirmation, passage à l’Arbiter.

🔹 **Régimes relationnels**

Toutes les relations partagent la structure (Relation → Role → Entité) mais elles n’ont pas la même nature.
Un régime relationnel regroupe des relations qui partagent : une même logique d’interprétation, les mêmes invariants, une même grammaire de rôles.

1. Régime événementiel (MVP) => Occurrences, Rôles Fillmore (AGENT, THEME…), Base du clustering (role → type).

2. Régime structurel (ultérieur) => Liens constitutifs (PART_OF, MEMBER_OF…), Rôles structuraux (WHOLE, PART), Pas de trigger.

    Un seul graphe, plusieurs régimes. Le MVP active uniquement l’événementiel.

---

## Overview [Relational regime]

The ontology in this system is **emergent**: it is not defined up-front and frozen, but discovered from data and continuously reshaped through four operations organised along two axes — **vertical** (abstraction / specialisation), **horizontal** (deduplication / disambiguation). This document formalises the graph model, the operations, their coupling, and the convergence dynamics that prevents runaway divergence. The third axis is not implemented for the time being.

It synthesises and extends the mechanisms described in [ontology_type_splitting.md](ontology_type_splitting.md), [drift_management.md](drift_management.md), and [relation_granularity.md](relation_granularity.md).

> **Note.** [relation_specialization.md](relation_specialization.md) is obsolete — compositional subtyping is subsumed by the label-trail mechanism (§1.4).

---

## 1. Foundational categories

The graph model separates **ontology** (typing) from **identification** (real-world anchoring). These are two independent concerns factored into distinct node classes.

### 1.1 Type

A single, unified meta-node that lives outside the graph. It is characterised along two axes:

| Axis | Level | Definition |
|------|-------|-----------|
| **Conceptual** | Declared on Type | A human-readable label + textual definition + property names and définitions. |
| **Structural** | Derived from instances | Statistics on the distribution of  profiles observed across the Type's instance population — i.e., set of motives `(role → Type)`  or `(role → Type → role → Type)`. |

Each Type carries a **`type_kind`** drawn from a configuration. In the current configuration:

| `type_kind` | Semantics |
|-------------|-----------|
| `entity` | Classifies identity-bearing things |
| `relation` | Classifies configurations linking entities via roles |

**Bipartite constraint (current config):** a Type of `type_kind=relation` can only be linked (via role edges) to Types of `type_kind=entity`, and vice versa. This preserves the Davidsonian backbone (see [reification_and_event_semantics.md](reification_and_event_semantics.md)).

### 1.2 Typed instance

A typed instance is the graph-level entity — it participates in edges and carries occurrence-specific information:

- **Structural profile** (own) — the concrete role edges the instance participates in. This is the instance's primary, graph-level information.
- **Property values** (own) — occurrence-specific attributes declared by the Type, valued per instance (see §1.2.1).
- **Conceptual definition** (inherited) — the label and definition of the instance's Type. Shared by all instances of the same Type.
- For entity-kinded instances, persistent knowledge about the real-world referent lives on the **Identity** node (§1.3), not on the instance itself.

What distinguishes one instance from another under the same Type is its **particularity**: its structural profile and property values.

#### 1.2.1 Properties (placeholder)

A **property** is a named feature declared at the Type level whose **value** is set per instance. Properties are the shared attributes of a Type; their values define the specifics of one of its instances. For example, a Type `EMPLOYMENT` might declare properties `start_date`, `end_date`, `title`; each `EMPLOYMENT` instance carries its own values for those properties.

### 1.3 Identity

A separate, first-class node representing a **persistent real-world referent**.

| Property | Detail |
|----------|--------|
| **Link to instances** | One Identity → many typed instances (same real-world thing seen in different typed contexts). One typed instance → exactly one Identity. |
| **Scope** | Only typed instances of `type_kind=entity` are linked to Identities. Relation-kinded instances have no Identity. |
| **Own description** | The Identity carries its own description: accumulated knowledge about the real-world referent. |
| **Mentions** | Surface references in source text are recorded as **Mention** nodes. Entity resolution maps Mentions to Identities. Each Mention carries its own description (surface form + context). |

> **Instance merge rule:** When entity resolution maps a new extraction to an existing Identity, and that Identity already holds a typed instance of the **same Type**, the system does not create a duplicate instance. Instead: (1) the property values of the two instances are checked for consistency and merged — discrepancies are checked; (2) the Identity's own description is updated with any new knowledge; (3) all Mention nodes from both observations are preserved and linked to the surviving instance, maintaining full provenance.

### 1.4 Label trail (inline taxonomy)

Each Type node carries an ordered list of labels recording its lineage through abstraction and specialisation operations. Rather than maintaining explicit parent–child edges between Types, the trail encodes the taxonomy inline: ancestry is readable directly from the label sequence, and queries at any abstraction level match all Types whose trail contains the queried label.

- **Vertical specialisation** (split): the child Type inherits the parent's labels and appends its own. E.g., `EMPLOYS → EMPLOYS_GOVERNMENTAL` produces the trail `["EMPLOYS", "EMPLOYS_GOVERNMENTAL"]`.
- **Vertical abstraction** (merge): a new, more general label is created and prepended to the trails of the merged Types. E.g., `EMPLOYS_GOVERNMENTAL` acquires the trail `["EMPLOYS", "EMPLOYS_GOVERNMENTAL"]` — the abstraction adds an ancestor, it does not retire any label.
- **Horizontal disambiguation** (conflation repair): the parent label is **retired** (it was never a coherent concept); children get fresh labels with no inherited ancestry from it. E.g., `BANK → FINANCIAL_BANK or RIVER_BANK` — the label `BANK` is retired.
- **Horizontal deduplication** (synonym repair): both labels retire; one canonical label is chosen. No hierarchy is created.

The label trail enables queries at any ancestor level: a query for `EMPLOYS` matches any Type whose trail contains `EMPLOYS`.

### 1.5 Node summary

| Node class | Carries own data | Identity-linked | Role in graph |
|------------|-----------------|-----------------|---------------|
| **Type** | Conceptual definition + property schema + statistics on the instances | No | Ontology (classification) |
| **Typed instance** | Property values + structural profile (edges) | Entity-kinded: yes; Relation-kinded: no | Ontology instantiation |
| **Identity** | Accumulated referent knowledge | Is the anchor | Identification (persistence) |
| **Mention** | Surface form + source context | Resolved to Identity | Provenance |

### 1.6 Towards a generic graph schema configuration

Right now the system assumes an entity/relation KG: the `type_kind` values, the bipartite linking rules, and the Identity layer are fixed. A natural next step is to move these assumptions into a **graph schema configuration** that declares the `type_kind` set, allowed links between kinds, and whether an Identity layer exists. Then the same emergent ontology engine can support other graph shapes by swapping the schema config.

---

## 2. Instance representation and the embedding function $f$

### 2.1 The (R,T) feature space — primary clustering substrate

[TODO: introduce tfidf]
For any set of instances under study $\mathcal{I}$ (e.g., all instances of a Type $T$, or the union $T_a \cup T_b$), define the **axis set** as the (Role, Type) pairs actually observed across $\mathcal{I}$:

$$\mathcal{A}(\mathcal{I}) = \bigl\{(R_i, T_j) \;\big|\; \exists\, x \in \mathcal{I} \text{ such that } x \text{ participates in role } R_i \text{ with a } T_j\text{-typed counterpart}\bigr\}$$

Each instance $x \in \mathcal{I}$ is then represented as a binary (or count-valued) vector in $\{0,1\}^{|\mathcal{A}|}$, where each coordinate indicates the presence (or count) of the corresponding $(R_i, T_j)$ edge on that instance.

This representation is the **primary substrate for clustering in all ontology operations** (specialisation §4.1, abstraction §4.2, disambiguation §5.1, deduplication §5.2). It is directly observable from the graph, interpretable (each axis is a named role–type pair), and free from model dependency.

**Scoping rule:** the axis set $\mathcal{A}$ is always scoped to the instances under investigation — not to the entire graph. This keeps the feature matrix dense enough for reliable clustering.

**Distance metric:** Jaccard distance or cosine distance over the binary/count vectors. The choice is a tuneable implementation detail; Jaccard is a natural fit for sparse binary profiles.

**Property values as secondary evidence:** instance property values (§1.2.1) are not included in the primary (R,T) feature space. Properties are irregularly populated (extraction is opportunistic), and their missingness can masquerade as cluster structure. Instead, property coherence can be used as **confirmation evidence**: if a structural (R,T) cluster also exhibits coherent property patterns, that strengthens the case for split or merge. Property values alone do not trigger an ontology operation.

### 2.2 The embedding function $f$

The embedding function maps each typed instance to a vector by independently embedding two axes — structural profile and conceptual definition — and combining them with tuneable weights:

$$\vec{v} = \alpha \cdot f_{\text{struct}}(\text{profile}) + \beta \cdot f_{\text{concept}}(\text{definition}) \;\in\; \mathbb{R}^d$$
[TODO: decide whether to keep the profile part of the embedding]

The two axes are embedded independently. The system can adjust weights per operation (e.g., structural-heavy for split scans, conceptual-heavy for merge scans). Each axis can also be clustered independently, avoiding signal contamination.

The combined embedding $\vec{v}$ serves **entity resolution, similarity search, and drift detection**. It is **not** the substrate for ontology operation clustering — that role belongs to the (R,T) feature space (§2.1).

**Conceptual embedding as pre-filter:** the conceptual axis *is* used as a cheap gate in the merge scan (§4.2.1): pairs of Types whose definition embeddings are distant are not worth investigating at the instance level. This is a screening step, not a clustering input.

$f$ must be deterministic for a given input. Re-embedding with a different model version invalidates all cached similarities; the system should track `(embed_model, embed_version)` alongside stored vectors (see [todos.md](todos.md)).

---

## 3. Ontology operations — the four operations

The emergent ontology evolves through four operations. Four form a 2×2 matrix along two axes (vertical/horizontal).

### 3.1 The 2×2 matrix

| | Split | Merge |
|--|-------|-------|
| **Vertical** | **Specialisation** — a coherent parent is specialised into sub-types | **Abstraction** — two types generalise into one |
| **Horizontal** | **Disambiguation** — a conflated label is separated into unrelated peers | **Deduplication** — two synonymous types are unified |

All four are driven by the same clustering / embedding machinery and validated by the Arbiter. They differ in the **label trail semantics** applied after the decision.


### 3.2 Candidate selection

The ontology operations described in §4–§5 require **candidate Types or candidate pairs** to investigate.  
Detection begins with a shared **Type-level structural pre-screening** that routes each Type toward the split or merge pipeline.

---

#### 3.2.1 Phase 1 — Structural pre-screening (per Type)

For every Type $T$ with instance count $N \geq N_{\min}$:

1. Build its (R,T) feature matrix (§2.1) from instance-level event motifs $(role \rightarrow type)$.
2. Compute:
   - Structural variance (or Jaccard dispersion),
   - Silhouette score under agglomerative clustering.

The pair of signals determines routing:

| Variance | Silhouette | Diagnostic   | Action                                  |
|:--------:|:----------:|:------------|:------------------------------------------|
| +        | +          | Split likely | Confirm via split pipeline                |
| −        | +          | Well-formed  | No action                                 |
| +        | −          | Ambiguous    | Test split first, merge second            |
| −        | −          | Merge likely | Escalate to merge screening               |

**Principle:** Always evaluate split before merge in ambiguous cases.

---

#### 3.2.2 Split path (specialisation, disambiguation)

If routed toward split:

1. Run agglomerative clustering.
2. Select the dendrogram cut maximising silhouette.
3. If $s(T) \geq \theta_{\text{split}}$ and $k \geq 2$, surface $T$ to the Arbiter.

**Emergent splits:**  
If dispersion is locally suspicious but global separation is weak:
- Apply a lightweight dispersion heuristic [TODO: define the lightweight dispersion heuristic].
- Escalate to HDBSCAN.
- Stable, non-marginal clusters are surfaced.

The Arbiter determines whether the candidate corresponds to:
- Specialisation,
- Disambiguation,
- or rejection.

---

#### 3.2.3 Merge path (abstraction, deduplication)

If routed toward merge:

1. Apply TF-IDF structural similarity screening.
2. Pairs passing thresholds proceed to instance-level separability testing.
3. Inseparable pairs are surfaced to the Arbiter for abstraction, deduplication, or rejection.

This staged routing keeps candidate detection tractable as the ontology grows.

**Type-level structural matrix**

For each Type $T$, build a TF-IDF-weighted vector over the aggregate motif space:

$$
M_{T,(R,T')} = \text{TF-IDF weight of motif } (R,T')
$$

This vector is computed over the Type’s instance-level event profiles and reflects how characteristic each $(R,T')$ motif is for that Type.

---

**Primary screening signal**

| Signal | What it computes | Cost | Rationale |
|--------|-----------------|------|-----------|
| **Structural similarity** | Cosine similarity between TF-IDF(Type, motif) vectors | $O(|\mathcal{T}|^2)$ over compact vectors — cheap | Types with highly similar structural signatures may be duplicates or abstractions. |

Pairs exceeding a structural similarity threshold $\theta_{\text{merge}}$ advance to instance-level separability testing.

---

**Secondary semantic filter**

| Signal | What it computes | Purpose |
|--------|-----------------|----------|
| **Conceptual similarity** | Cosine similarity of definition embeddings | Prevent structurally similar but semantically distant merges |

Conceptual similarity acts as a semantic guardrail; structural similarity remains the primary driver.

---

**Protocol**

1. Compute cosine similarity over TF-IDF(Type, motif) vectors for all same-`type_kind` pairs.
2. Retain pairs with similarity $\ge \theta_{\text{merge}}$.
3. Apply conceptual similarity as a semantic filter.
4. For surviving pairs, build the joint instance matrix over $T_a \cup T_b$ and run separability testing.
5. Inseparable pairs are surfaced as merge candidates to the Arbiter.

This two-stage design — structural screening followed by instance-level validation — keeps merge detection tractable while remaining structurally principled.

---

## 4. Semantic interpretation (Arbiter classification)

Candidates are semantically classified by the Arbiter.

---

### 4.1 Split classification

Given candidates, the Arbiter decides:

| Classification | Meaning | Label policy |
|---------------|----------|--------------|
| **Specialisation** | Parent Type is coherent; children refine it | Parent label retained in label trail |
| **Disambiguation** | Parent was a conflation | Parent label retired |

---

### 4.2 Merge classification

Given a structural Merge, the Arbiter decides:

| Classification | Meaning | Label policy |
|---------------|----------|--------------|
| **Abstraction** | One Type generalises the other | Parent–child relation recorded |
| **Deduplication** | Both Types represent the same concept | One canonical label retained |


## 5. Structural operations

All ontology evolution reduces to two structural operations:

- **Split** (one Type → several)
- **Merge** (several Types → one)

Candidate detection are defined in §3 and final decision in §4.  
This section defines the structural transformation and its graph effects.

---

### 5.1 Split

A Split replaces a Type $T$ with $k \ge 2$ new Types $T_1, \dots, T_k$.

**Input:**  
- Structural clustering evidence (§3)
- The semantic interpretation of the Split is determined by the Arbiter (§4.1).
- Partition of instances into $k$ clusters  

**Transformation:**

1. Create new Types $T_1, \dots, T_k$.
2. Reassign each instance of $T$ to its cluster Type.
3. Update all role references pointing to $T$ so they now reference the appropriate $T_i$.
4. Mark $T$ as superseded.

---

## 6. Coupled dynamics and cascade control

### 6.1 The coupling loop

Because all Types share a uniform structure, exogenous cascades propagate through the role graph without crossing a meta-type boundary:

```
Type specialised
    → structural signatures of linked Types change
        → some linked Types now exhibit bimodality → specialised
            → structural signatures of their linked Types change
                → ...
```

The same loop operates in the abstraction and horizontal directions. Without regulation, a single operation can trigger an unbounded cascade.

### 6.2 Convergence criterion

Each operation (split or merge, vertical or horizontal) must satisfy:

$$\Delta \mathcal{I}(\text{step}) \;\geq\; \epsilon$$

where $\Delta \mathcal{I}$ is the **information gain** of the step — the improvement in overall type homogeneity (measured by aggregate silhouette score, or equivalently, the reduction in intra-type variance).

**Halting rule:** the system stops when no candidate step exceeds $\epsilon$. This makes convergence an intrinsic property of the process, not just an external guard rail.

### 6.3 Formal sketch

Define the ontology state as $\mathcal{O} = \mathcal{T}$ — the set of all Types (regardless of `type_kind`). Define a quality function:

$$Q(\mathcal{O}) = \frac{1}{|\mathcal{T}|} \sum_{T \in \mathcal{T}} w_T \cdot h(T) - \lambda \cdot C(\mathcal{O})$$

where:

- $h(T)$ = **homogeneity** of Type $T$ = mean intra-type cosine similarity of instance embeddings.
- $w_T$ = weight proportional to instance count (Types with more instances matter more).
- $C(\mathcal{O})$ = **complexity penalty** = number of Types (or another measure of ontology size).
- $\lambda$ = trade-off parameter controlling the equilibrium between expressiveness and parsimony.

A split (vertical or horizontal) increases $\sum h(T)$ but increases $C(\mathcal{O})$. A merge (vertical or horizontal) decreases $C(\mathcal{O})$ but may decrease $\sum h(T)$.

**The system converges to a local optimum of $Q$** when no single operation improves $Q$ by more than $\epsilon$.

### 6.4 Practical convergence controls

| Control | Role | Parameter |
|---------|------|-----------|
| $\epsilon$-halting | No step with gain $< \epsilon$ is applied | $\epsilon$ (minimum information gain) |
| Complexity penalty $\lambda$ | Penalises ontology growth; biases toward parsimony | $\lambda$ |
| Minimum cluster fraction $\phi_{\min}$ | Prevents splits that produce trivially small sub-types | $\phi_{\min}$ (e.g., 0.10) |
| Silhouette threshold $\theta_{\text{split}}$ | Minimum separation to consider a split | $\theta_{\text{split}}$ (e.g., 0.3) |
| Maximum cascade depth | Hard limit on consecutive exogenous propagation steps per trigger | $d_{\max}$ (e.g., 3) |
| Arbiter validation | Every proposed change goes through LLM semantic validation | — |
| Epoch gating | Operations scans run periodically, not continuously | scan interval $M$ |

### 6.5 Cascade protocol

When an operation propagates exogenously:

1. Apply the primary change (split or merge on the triggering Type).
2. Re-evaluate affected Types on the other side of the role graph (only those whose structural signatures actually changed).
3. For each affected Type, check the split/merge criterion. If met and $\Delta \mathcal{I} \geq \epsilon$, surface as a candidate.
4. Apply validated candidates. Increment cascade depth counter.
5. If cascade depth $< d_{\max}$, repeat from (2) with the newly changed Types. Otherwise, halt and log unresolved candidates for the next scheduled scan.

---

## 7. Temporal dynamics

### 7.1 Epochs and windows

The clustering signals that drive the four ontology operations (§3) operate on **accumulated data**, but "accumulated" can mean different things:

| Strategy | Description | Trade-off |
|----------|-------------|-----------|
| **Full history** | All instances ever mapped to a type | Most statistically robust; but conflates past and current distributions |
| **Sliding window** | Last $W$ batches or $N$ documents | Responsive to recent shifts; may lose long-tail patterns |
| **Exponential decay** | Weight recent instances higher | Smooth compromise; requires tuning decay rate |

The choice interacts with drift management: a type that was bimodal 1000 documents ago but has been unimodal for the last 500 should probably not be split. Conversely, a type that only became bimodal in the last 50 documents should not be suppressed by 950 documents of unimodal history.

**Recommendation:** default to sliding window aligned with the staleness epoch (`ontology_staleness_threshold`). Use full history only for infrequent deep scans.

### 7.2 Interaction with drift detection

Drift detection (see [drift_management.md](drift_management.md)) operates **per-batch** on the fast path. Ontology operation scans (split, merge, disambiguation, deduplication) operate **across batches** on the accumulated graph. They are complementary:

| Signal | Scope | Detects |
|--------|-------|---------|
| Drift score | Per-batch | New content that the ontology doesn't cover at all |
| Split scan | Cross-batch | Intra-type divergence that accumulated silently |
| Merge scan | Cross-batch | Inter-type convergence that accumulated silently |

A high drift score forces a full pipeline re-run (immediate). An operation scan surfaces candidates for the Arbiter (deliberative). They can and should coexist.

---

## 8. Roles in the emergent ontology

Roles (Agent, Theme, Instrument, …) are a **fixed universal set** drawn from Case Grammar (see [reification_and_event_semantics.md](reification_and_event_semantics.md)). They do not participate in the emergent dynamics: they are not split, merged, or refined.

However, roles are **structural primitives** that define Type signatures (the `(role → Type)` pairs in §1.1). A change in role vocabulary would reshape every signature in the system. Keeping roles fixed simplifies the algebra and keeps the operation search space manageable.

A potential extension is FrameNet-style per-relation-type roles. If adopted, role emergence becomes a third axis of the dynamics — but should be governed separately (role refinement is a schema-level change, not an instance-level clustering concern).

---

## 9. Emergent type taxonomy via label trails

The ontology does not maintain an explicit is-a hierarchy. Instead, the label trail on each Type (§1.4) serves as an **inline taxonomy**:

- Vertical specialisation produces parent → child lineage readable from the trail.
- Any query for a parent label matches all descendant Types whose trail contains that label.
- Horizontal operations (disambiguation, deduplication) retire labels, pruning false branches from the taxonomy.

This subsumes the compositional subtyping mechanism of [relation_specialization.md](relation_specialization.md).

Benefits:

- Queries can range over abstraction levels ("all `EMPLOYS`-descended types" vs. "`EMPLOYS_GOVERNMENTAL` only").
- No explicit is-a edges to maintain — the trail is the hierarchy.
- Retired labels provide an audit trail of ontology evolution.

---

## 10. Triadic stabilisation of meaning

Meaning in this system is not grounded in any single mechanism. It emerges from the convergence of three independent stabilisation forces:

| Force | Axis | How it stabilises a concept |
|-------|------|-----------------------------|
| **Structural (extension)** | (R,T) feature space (§2.1) | A Type stabilises when its instances exhibit repeating `(Role, Type)` profiles in the (R,T) feature space — the same relational motifs recur across observations. This is the primary substrate for clustering in ontology operations. |
| **Vector-space (embedding)** | Combined embedding $\vec{v}$ (§2.2) | A Type stabilises when its instance embeddings form a tight, coherent cluster under $f$. Used for entity resolution and drift detection, not for ontology operation clustering. |
| **Intensional (definition)** | Explicit conceptual description | A Type stabilises when its Arbiter-validated definition consistently predicts and explains its instances. Used as a pre-filter (merge scan screening) and confirmation signal, not as a clustering input. |

These forces are not equally decisive in every context. A concept is **robust** when all three converge — its relational profile, its embedding cluster, and its explicit definition all agree. When they diverge, the divergence is itself a diagnostic signal:

| Divergence | Interpretation |
|------------|---------------|
| Structural ↔ vector-space | Instances share relational patterns but scatter in embedding space (or vice versa). The conceptual definition may be conflating or under-specifying. |
| Structural ↔ intensional | The definition says one thing, but the relational evidence says another. The Type may have drifted. |
| Vector-space ↔ intensional | Embeddings cluster coherently, but the definition no longer describes what the cluster contains. |

Meaning is therefore not a fixed essence but a **dynamic equilibrium** among three stabilisation mechanisms. The seven ontology operations (§3) are the system's means of restoring equilibrium when divergence is detected: splits resolve intra-type divergence; merges resolve inter-type convergence; the compositional axis (decomposition/recomposition) restructures composite boundaries; promotion corrects misclassified structural roles; the Arbiter guards intensional coherence throughout.

---

## 11. Summary: the optimisation landscape

The emergent ontology is shaped by a continuous optimisation across seven operations:

| Axis | Operation | Effect on $Q(\mathcal{O})$ |
|------|-----------|---------------------------|
| **Vertical split** | Specialisation | Increases homogeneity $h(T)$, increases complexity $C(\mathcal{O})$ |
| **Vertical merge** | Abstraction | Decreases complexity $C(\mathcal{O})$, may decrease homogeneity $h(T)$ |
| **Horizontal split** | Disambiguation | Increases homogeneity (conflation resolved), complexity stays similar |
| **Horizontal merge** | Deduplication | Decreases complexity (synonyms unified), homogeneity preserved |
| **Compositional split** | Decomposition | Increases homogeneity (composite resolved into facets), complexity increases, structural clarity improves via meta-relations |
| **Orthogonal migration** | Promotion | Neutral on homogeneity; restores bipartite correctness; triggers cascading signature updates |
| **Compositional merge** | Recomposition | Decreases complexity (stable compound unified), homogeneity preserved or improved |

The system converges when no single operation can improve the quality function $Q$ by more than $\epsilon$.

The result is a **hybrid system**:

- **Factorised** — a single Type node with `type_kind` discriminator; Identity as a separate layer.
- **Structural** — Types are grounded in observable relational distributions.
- **Vector-space (embedding)** — similarity is computed via embeddings over structural + conceptual axes.
- **Semantically validated** — every proposed change passes through LLM arbitration.
- **Co-evolutionary** — Types reshape each other through exogenous coupling across the role graph.
- **Convergent** — bounded by $\epsilon$-halting, complexity penalty, cascade depth limit, and epoch gating.
- **Taxonomic** — the label trail provides an inline, evolving type hierarchy without explicit is-a edges.
- **Structurally adaptive** — promotion corrects `type_kind` misclassifications; the compositional axis (decomposition/recomposition) restructures composite boundaries.

---

## Related design notes

| Note | Relationship |
|------|-------------|
| [ontology_type_splitting.md](ontology_type_splitting.md) | Endogenous specialisation mechanism and split scan |
| [relation_granularity.md](relation_granularity.md) | Over-proliferation (need for merge scan) and type hierarchy |
| [relation_specialization.md](relation_specialization.md) | Obsolete — compositional subtyping subsumed by label trail (§1.4, §9) |
| [drift_management.md](drift_management.md) | Per-batch drift detection — complementary to cross-batch scans |
| [reification_and_event_semantics.md](reification_and_event_semantics.md) | Davidsonian backbone and role vocabulary |
| [entity_resolution.md](entity_resolution.md) | Persistent entity identity — factored into the Identity node (§1.3) |
