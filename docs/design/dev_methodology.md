# Development Methodology — Two-Phase AI-Assisted Development

**Status:** Guide  
**Date:** 2026-02-20

---

## Phase 1 — Exploration (throw-away prototype)

- Go fast, no tests, let the AI generate aggressively.
- Goal: discover the real problem space (models, pipeline steps, edge cases, complexity).
- Accept that the code will tangle. You're buying **knowledge**, not code.
- Stop when you can no longer confidently reason about what the system does.

## Bridge — Freeze design artifacts

- Capture everything learned into design notes, backlog, flow charts, model diagrams.
- These become the **spec** for Phase 2.

## Phase 2 — Incremental rebuild (with tests)

- Start from scratch, informed by Phase 1 knowledge + design artifacts.
- Each increment is a **thin vertical slice**: one capability, unit-tested, validated against a real-world input (e2e).
- AI is now **constrained**: it proposes, you validate against Phase 1 knowledge before accepting.
- Small increments keep the AI context window manageable; tests act as a **contract** preventing silent regressions.