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

## Overview

The ontology in this system is **emergent**: it is not defined up-front and frozen, but discovered from data and continuously reshaped through six operations organised along three axes — **vertical** (abstraction / specialisation), **horizontal** (deduplication / disambiguation), **compositional** (decomposition / recomposition). This document formalises the graph model, the operations, their coupling, and the convergence regime that prevents runaway divergence. The third axis is not implemented for the time being.

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

Properties are the user facing internal details of the type. The type could be itself decomposed into sub-types, this is not implemented here.

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

## 3. Ontology operations — the six operations

The emergent ontology evolves through six operations. Four form a 2×2 matrix along two axes (vertical/horizontal). Two form the compositional axis (decomposition/recomposition).

### 3.1 The 2×2 matrix

| | Split | Merge |
|--|-------|-------|
| **Vertical** | **Specialisation** — a coherent parent is specialised into sub-types | **Abstraction** — two types generalise into one |
| **Horizontal** | **Disambiguation** — a conflated label is separated into unrelated peers | **Deduplication** — two synonymous types are unified |

All four are driven by the same clustering / embedding machinery and validated by the Arbiter. They differ in the **label trail semantics** applied after the decision.

### 3.2 Decomposition (compositional split)

A Type that is not merely over-general (vertical) nor accidentally conflated (horizontal) but is a **composite** of two functionally distinct, conceptually adjacent concepts.

**Example:** `SIGN_CONTRACT` is found to blend two distinct processes — `NEGOTIATE_CONTRACT` and `EXECUTE_CONTRACT`. Neither is a specialisation of `SIGN_CONTRACT` (vertical), nor is `SIGN_CONTRACT` a spurious homonym (horizontal). The concept is a genuine **composite**: a macro-event that the ontology should decompose into its functional constituents.

**Trigger:** the Arbiter, during a split validation, determines that the proposed sub-clusters are not in a parent–child relationship (vertical) nor unrelated homonyms (horizontal), but are **functionally distinct phases or facets** of a single composite concept.

**Mechanics:**

1. The parent Type is retired.
2. Each child receives a fresh label — no inherited ancestry from the parent (same as horizontal disambiguation).
3. A **typed meta-relation** may be created between the sibling Types to record their functional relationship (e.g., `NEGOTIATE_CONTRACT --precedes--> EXECUTE_CONTRACT`). This meta-relation is a first-class relation-kinded Type in the ontology.
4. Instances of the parent are re-classified to the appropriate child based on cluster assignment.

**Label trail:** parent label is retired. Children get independent trails. The meta-relation between siblings captures the structural link that the shared parent label used to encode implicitly.

**Distinction from other splits:**

| Criterion | Vertical specialisation | Horizontal disambiguation | Decomposition |
|-----------|--------------------|-----------------------------|----------------|
| Parent coherence | Coherent concept | Spurious conflation | Genuine composite |
| Children relationship | Specialisations of parent | Unrelated peers | Functionally distinct facets |
| Label trail | Parent preserved as ancestor | Parent retired | Parent retired |
| Meta-relation between children | None | None | Yes (functional link) |


### 3.3 Recomposition (compositional merge; inverse of decomposition)

Recomposition fuses two or more Types that were separate but turn out to **always co-occur in a stable pattern**, forming a compound concept that deserves its own Type.

This is distinct from:
- **Vertical abstraction** (one subsumes the other — hierarchy).
- **Horizontal deduplication** (they are synonyms — identity).

Recomposition says: the Types are genuinely different concepts, but they form a **stable compound** — they co-occur so systematically that treating them as a single unit improves both parsimony and structural clarity.

**Example:** `NEGOTIATE_CONTRACT` and `EXECUTE_CONTRACT` always appear together with the same participants and temporal sequence. The system recomposes them into `CONTRACT_PROCESS`, a single composite Type.

**Trigger:** diagnostic signals include:
- Two or more Types of the same `type_kind` exhibit a **near-perfect co-occurrence** at the instance level: whenever an instance of $T_a$ exists, an instance of $T_b$ exists with the same (or overlapping) role fillers.
- The inter-type meta-relation (if one exists from a prior decomposition) has become structurally trivial — it carries no additional information beyond "these always go together."
- The Arbiter judges that the compound is conceptually coherent as a single concept.

**Mechanics:**

1. A new composite Type is created with a fresh label.
2. The source Types are retired; their labels are recorded as aliases.
3. Instances are merged pairwise (matched by co-occurrence) into instances of the composite Type. Property values are reconciled.
4. Any meta-relation linking the source Types is dissolved.
5. Role edges from other Types that pointed to the source Types are redirected to the composite Type.

**Label trail:** source labels are retired. The composite Type gets a fresh trail.

**Distinction from other merges:**

| Criterion | Vertical abstraction | Horizontal deduplication | Recomposition |
|-----------|---------------------|---------------------------|----------------|
| Relationship | One subsumes the other | Same concept (synonym) | Different concepts, stable compound |
| Label trail | Subsumee retained as alias | Both retire, one canonical chosen | Both retire, new composite label |
| Hierarchy created | Yes (parent–child) | No | No |
| Meta-relation | N/A | N/A | Dissolved (was the co-occurrence link) |

### 3.4 Candidate selection

The ontology operations described in §4–§5 require **candidate pairs or candidate Types** to investigate. The selection strategy differs between split and merge families.

#### 3.4.1 Split candidates (specialisation, disambiguation, decomposition)

Split candidate selection scans **each Type independently**:

1. For every Type $T$ with instance count $N \geq N_{\min}$, build its (R,T) feature matrix (§2.1) and run the dendrogram / silhouette test (§4.1.1).
2. Types with $s(T) \geq \theta_{\text{split}}$ and $k \geq 2$ are surfaced as candidates.

This is $O(|\mathcal{T}|)$ — linear in the number of Types — and the instance count threshold $N_{\min}$ eliminates Types with too few observations for reliable clustering. The Arbiter then determines whether a surfaced candidate is a specialisation (§4.1), disambiguation (§5.1), or decomposition (§3.2) based on the semantic relationship between sub-clusters.

#### 3.4.2 Merge candidates (abstraction, deduplication, recomposition)

Merge candidate selection must identify **pairs** $(T_a, T_b)$ of the same `type_kind` that are worth investigating. The naïve space is $O(|\mathcal{T}|^2)$ — potentially large. Three complementary screening signals reduce it to a short list:

| Signal | What it computes | Cost | Rationale |
|--------|-----------------|------|-----------|
| **Conceptual proximity** | Cosine similarity between the definition embeddings of $T_a$ and $T_b$ | $O(|\mathcal{T}|^2)$ over precomputed vectors — cheap | Types with distant definitions are unlikely merge candidates. |
| **Structural (R,T) overlap** | Set-overlap score (e.g., Jaccard) between $\mathcal{A}(T_a)$ and $\mathcal{A}(T_b)$ — the aggregate (R,T) axis sets at the Type level, not instance level | $O(|\mathcal{T}|^2)$ over small sets — cheap | Types whose instances participate in entirely different role–type configurations are not candidates. |
| **Role adjacency** | Frequency with which $T_a$ and $T_b$ are linked via the same roles to the same counterpart Types | Readable from graph topology — cheap | Types that systematically appear in the same relational contexts may be duplicates or abstractions of each other. |

**Screening protocol:**

1. Compute all three signals for every same-`type_kind` pair.
2. A pair must pass **at least two of three** screening thresholds to advance to the instance-level test (the silhouette separability test of §4.2.1).
3. For pairs that pass screening, build the (R,T) feature matrix over $T_a \cup T_b$ and run the separability test.
4. Inseparable pairs are surfaced to the Arbiter, which determines whether the merge is an abstraction (§4.2), deduplication (§5.2), or recomposition (§3.4).

This two-stage design — cheap Type-level screening followed by expensive instance-level testing — keeps the merge scan tractable even as the ontology grows.

#### 3.4.3 Recomposition candidates

Recomposition (§3.4) additionally requires a **co-occurrence signal**: pairs of Types whose instances systematically co-occur with the same (or overlapping) role fillers. This is detected by:

1. For each pair of same-`type_kind` Types, compute the fraction of instances of $T_a$ that co-occur with an instance of $T_b$ sharing at least one role filler.
2. Pairs above a co-occurrence ratio threshold $\theta_{\text{cooccur}}$ are surfaced as recomposition candidates.
3. The Arbiter judges whether the co-occurrence is structural (→ recompose) or coincidental.

This scan piggybacks on the merge screening infrastructure (§3.6.2) — co-occurrence is a fourth signal that specifically gates the recomposition path.

---

## 4. Vertical operations

### 4.1 Specialisation (vertical split)

Specialisation splits a Type when its instances are no longer homogeneous.

#### 4.1.1 Endogenous specialisation

**Trigger:** for a Type $T$ with $N$ mapped instances:

1. Build the (R,T) feature matrix over all instances of $T$, with axes $\mathcal{A}(\text{instances}_T)$ as defined in §2.1.
2. Run agglomerative clustering (e.g., Ward or average linkage with Jaccard distance) to produce a dendrogram.
3. Cut the dendrogram at successive levels and compute the silhouette score $s$ for each resulting partition.
4. Select the cut that maximises $s$. If $s(T) \geq \theta_{\text{split}}$ and the best partition has $k \geq 2$ clusters, surface $T$ as a **split candidate** with $k$ proposed sub-types.

$$s(T) = \max_{\text{cuts}} \; \text{silhouette\_score}\bigl(\text{(R,T) features of instances}_T,\; \text{partition at cut}\bigr)$$

The dendrogram provides interpretable structure: the (R,T) axes that drive each split are identifiable, and the hierarchy of cuts tells the Arbiter whether the split is a clean two-way division or a deeper cascade.

**Validation gate:** the Arbiter receives the existing Type, its definition, representative examples from each proposed sub-cluster, the distinguishing (R,T) axes, and the silhouette evidence. It decides whether the split is semantically meaningful or an artefact of surface variation, using the `split_type` tool.

**Minimum cluster fraction:** a split into a cluster of 2 and a cluster of 500 is noise. Enforce a minimum fraction $\phi_{\min}$ (e.g., 10%) per sub-cluster before surfacing.

**Temporal scope:** clustering operates over the **accumulated graph** (not a single batch). This is a post-ingestion, graph-level analysis triggered periodically or on demand — not per-batch. See [ontology_type_splitting.md](ontology_type_splitting.md).

**Label trail:** the parent label is preserved; each child appends its own. E.g., `EMPLOYS → ["EMPLOYS", "EMPLOYS_GOV"]`.

#### 4.1.2 Exogenous specialisation

Specialising a Type modifies the structural signatures visible on Types linked to it via roles:

- Splitting Type $E$ (`entity`) into $E_1, E_2$ changes the role signatures of every Type (`relation`) whose instances link to $E$-typed instances. Some relation Types may now exhibit internal bimodality that the coarser typing masked.
- Splitting Type $R$ (`relation`) into $R_1, R_2$ changes the relational profiles of every Type (`entity`) whose instances participate in $R$-typed instances.

Because all Types share the same node structure, exogenous propagation traverses a uniform role graph. The cascade protocol (§6.5) governs depth.

### 4.2 Abstraction (vertical merge)

Abstraction is the **dual** of specialisation: it merges Types whose instances are no longer distinguishable.

#### 4.2.1 Endogenous abstraction

**Trigger:** for a pair of Types $(T_a, T_b)$ of the same `type_kind`:

1. **Pre-filter (conceptual):** compute cosine similarity between the definition embeddings of $T_a$ and $T_b$. If below a threshold $\theta_{\text{merge\_screen}}$, skip — the Types are conceptually distant and not worth investigating at the instance level.
2. **Build the (R,T) feature matrix** over all instances of $T_a \cup T_b$, with axes $\mathcal{A}(\text{instances}_{T_a} \cup \text{instances}_{T_b})$ as defined in §2.1.
3. **Separability test:** assign each instance to a group by its current Type label ($T_a$ or $T_b$). Compute the silhouette score $s$ of this **given** two-group partition ($k=2$ is not searched — it is fixed by the Type labels).
   - **Low $s$** (below $\theta_{\text{split}}$): the two populations overlap in (R,T) space — they are not structurally distinguishable → **merge candidate**.
   - **High $s$**: the populations separate cleanly — the distinguishing (R,T) axes are real → **do not merge**.

Operationally, a periodic **merge scan** (the inverse of the split scan):

1. Embed all ontology Type definitions (conceptual axis only).
2. Flag pairs with high inter-type definition similarity (pre-filter).
3. For each flagged pair, build the (R,T) feature matrix and run the separability test.
4. If inseparable, surface a merge proposal to the Arbiter.

**Validation gate:** same Arbiter pattern. The Arbiter receives both Types, their definitions, the shared and distinguishing (R,T) axes, and representative instances from each. It decides via `merge_with_existing` whether the merge is semantically justified or if the structural overlap is coincidental.

**Label trail:** one Type subsumes the other. The subsumed label may be retained as an alias in the trail.

#### 4.2.2 Exogenous abstraction

Symmetric to exogenous specialisation: merging two Types collapses role-signature distinctions that other Types relied on, potentially triggering merge cascades on the other side.

#### 4.2.3 Relationship to existing mechanisms

| Mechanism | Scope | When |
|-----------|-------|------|
| Arbiter `merge_with_existing` | Fires when a **new candidate** arrives that resembles an existing Type | Per-batch, during ontology negotiation |
| Merge scan | Fires between **existing** Types that have converged over time | Periodic, post-ingestion |
| Abstraction (general) | Generalises both: any structural convergence, whether from new data or accumulated drift | Periodic |

---

## 5. Horizontal operations

### 5.1 Disambiguation (horizontal split)

Disambiguation separates a Type whose instances cluster into groups that are **conceptually unrelated** — the parent label was a conflation, not a coherent category.

#### 5.1.1 Distinction from vertical specialisation

The clustering evidence (silhouette, bimodality) is the same. The difference is in the **Arbiter's semantic judgement**:

| Criterion | Vertical specialisation | Horizontal disambiguation |
|-----------|--------------------|-----------------------------|
| Parent coherence | Parent names a real concept; children are specialisations | Parent was a spurious grouping; children share nothing but a surface label |
| Label trail | Parent label preserved as ancestor | Parent label **retired** (marked as dissolved conflation) |
| Example | `EMPLOYS → EMPLOYS_GOV + EMPLOYS_PRIVATE` | `BANK → FINANCIAL_BANK + RIVER_BANK` |

#### 5.1.2 Mechanics

1. The split scan surfaces the candidate identically to §4.1.1.
2. The Arbiter is presented with the sub-clusters and judges whether the parent concept is coherent.
3. If the Arbiter determines the parent is a conflation:
   - The parent Type is retired (not preserved as an ancestor).
   - Each child receives a fresh label trail with no inherited ancestry from the retired parent.

### 5.2 Deduplication (horizontal merge)

Deduplication unifies two Types that are **the same concept** but entered the ontology through different ingestion paths — neither is a generalisation of the other; they are peers that should have been one Type from the start.

#### 5.2.1 Distinction from vertical abstraction

| Criterion | Vertical abstraction | Horizontal deduplication |
|-----------|---------------------|---------------------------|
| Relationship | One subsumes the other (generalisation) | Both are the same concept (synonym) |
| Label trail | Subsumee may be retained as alias under the merged type | Both labels retire; one canonical label is chosen |
| Hierarchy created | Yes (parent–child) | No |

#### 5.2.2 Mechanics

1. The merge scan surfaces the candidate identically to §4.2.1.
2. The Arbiter judges whether the two Types represent distinct concepts at different abstraction levels (vertical) or the same concept (horizontal).
3. If horizontal:
   - One canonical label is chosen; the other is recorded as a retired synonym.
   - Instance populations are merged under the canonical Type.
   - No parent–child hierarchy is created.

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
