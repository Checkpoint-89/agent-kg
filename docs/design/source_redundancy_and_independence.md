# Design Note — Information Tracing: Source Redundancy, Independence, and Robustness

**Status:** Problem analysis (no implementation planned)
**Date:** 2026-02-20

---

## General problem: information tracing

Information tracing aims to understand how a statement is **produced, transformed, transmitted, and legitimised** within a social system — independently of its truth.

Four complementary dimensions:

| Dimension | Question |
|-----------|----------|
| **Provenance** | Who produced what, from what? (derivation graph) |
| **Diffusion** | Who amplifies, who relays, through which communities? (temporal/network trajectory) |
| **Epistemic independence** | Do multiple occurrences constitute real corroboration, or structural dependence? |
| **Credibilisation chain** | How does a statement gain legitimacy via intermediaries, institutions, or aligned interests? |

These are long-term concerns: causal modelling, narrative dynamics, independence metrics, amplification analysis.

---

## Motivating example

We ingest 100 transcripts where the same analyst repeats the same claims across interviews.

- Containers differ (genuinely different interviews).
- Content is highly redundant (same arguments, often same phrasing).
- Naive counting → 100 "supports". Reality → ~1 independent support.

This is not a chunking/ID problem. It is a **dependence** problem.

Moreover: the analyst is *also* a container — he derives his claims from somewhere. A transcript contains his statements; he contains/transforms upstream sources. **Everything is a container.** The question is: where do we stop tracing upstream?

---

## The stopping problem (regress of causes)

Every source is downstream of something else. Tracing upstream indefinitely is not feasible. We need a **policy boundary**: what counts as an acceptable root?

Possible root categories (descriptive, not prescriptive):

| Role | Description |
|------|-------------|
| **Originator** | Produces primary evidence (dataset, signed document, direct recording) |
| **Witness** | First-hand observation |
| **Institution** | Official statement (carries authority but also incentives) |
| **Interpreter / Analyst** | Adds framing, model, selection |
| **Broker / Curator** | Compiles, selects, republishes |
| **Advocate / Propagandist** | Goal-driven assertion |
| **Amplifier** | Repeats/boosts (high frequency, low originality) |

High repetition by a single actor does not increase support — but it *does* identify that actor as an **amplifier**, which is itself useful information.

---

## Two distinct redundancy problems

### A) Content duplication ("same tape")
Same underlying passage appears across containers (verbatim or near-verbatim).
→ Should count as ~1 support unit.

### B) Claimant dependence ("same person keeps saying it")
Same actor repeats the same claim in different venues. Genuinely different interviews, but not independent evidence.
→ Repeated attributions should have **diminishing returns**.

(A) is about **passage identity**. (B) is about **source independence** in a sociological sense.

---

## Two notions of "robustness" (do not conflate)

| Notion | What it measures | 100 interviews, same speaker |
|--------|-----------------|------------------------------|
| `support_strength` | Independent evidence for truth | ~1 |
| `attribution_strength` | How widely/often asserted | 100 (high propagation) |

Both are useful. They must remain **separate signals**.

---

## What makes this hard

1. **Near-duplicate text** — transcription differences, timestamps, formatting.
2. **Overlap / containment** — a quote inside a larger quote; chunk overlap.
3. **Paraphrase vs repetition** — same claim restated ≠ same passage replayed.
4. **Mixtures** — an interview contains a repeated "standard spiel" *plus* genuinely new content.
5. **Containers all the way down** — every source is downstream of something.

---

## Related fields and prior art

| Field | Relevance |
|-------|-----------|
| **Provenance / data lineage** (W3C PROV) | `derived_from`, `was_informed_by` semantics |
| **Computational social science** | Information cascades, echo chambers, amplification |
| **Media studies** | Agenda-setting, information laundering |
| **Epistemology of testimony** | Independence of sources, corroboration conditions |
| **Citation / influence networks** | Common-source bias, centrality, community detection |

Key concept from epistemology: **common-source dependence** — many downstream mentions sharing the same upstream origin do not constitute independent corroboration.

---

## Short-term goal: KG v2 expressiveness

We do not aim to *solve* information tracing now. The v2 goal is to build a graph **expressive enough** to support these analyses later.

### Required separations

The graph must cleanly distinguish:

- **Content** (what is said — the claim/relation)
- **Occurrence** (where it appears — container, chunk, span)
- **Actor** (who produces/asserts/relays it)
- **Assertion** (the act of stating — linking actor, content, occurrence)

### Illustrative edge types (not prescriptive)

- `derived_from` — provenance lineage
- `attributed_to` — who asserted
- `published_at` — temporal/channel anchoring
- `cites` — explicit reference

### Capabilities the structure should enable (a posteriori)

- Detection of common lineages (shared upstream sources)
- Estimation of epistemic independence
- Identification of amplifiers (high occurrence, low originality)
- Centrality and community analysis on the attribution/diffusion layer
- Fingerprint-based grouping of similar assertions

---

## Open questions

1. **Where to draw the "root" boundary?** Policy choice, not a technical one.
2. **How to separate paraphrase from repetition?** Collapsing paraphrases risks undercounting real corroboration.
3. **Is high repetition a signal of amplification, or of robustness?** Both — but in different layers.
4. **Is the KG a graph of reality, of discourse, or both?** If both, the signals must stay separate.

---

## Summary

**Long term:** understand the structural life of narratives — provenance, diffusion, independence, credibilisation.

**Short term:** build a graph expressive enough to make that analysis possible later, without solving it now.
