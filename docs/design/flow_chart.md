# Build flow (step-first)

This note is being built incrementally.

## Step 1 — Route execution mode

The pipeline’s first processing step is to decide whether to run the **Full pipeline** or the **Fast path**.

- **Entry points**: `Pipeline.process()` / `Pipeline.process_async()`
- **Routing logic**: `Pipeline._process_async(documents)`
- **Decision rule**:
  - If `self._ontology` is `None` → **Full pipeline**
  - Else if `self._ontology.is_stale(config.ontology_staleness_threshold)` → **Full pipeline**
  - Else → **Fast path**

## Step 2 — Fast path: extract raw relations (with optional graph context)

When Step 1 routes to the **Fast path**, the first processing step is to extract **raw relations** from each input document, optionally injecting context retrieved from the existing graph.

- **Where it runs**: `Pipeline._fast_path(documents)`
  - Calls: `self._extract_raw_batch(documents, ontology=self._ontology, with_context=True)`

- **How it works**:
  - For each document in the batch, the pipeline prepares an optional `graph_context`.
    - If Neo4j is configured, it calls `Pipeline._get_context(document_text)`.
    - `Pipeline._get_context()` delegates to `ContextRetriever.retrieve(document_text)`.
    - Retrieval prefers chunk-embedding vector search; it falls back to substring matching if embeddings/indexes are unavailable or retrieval fails.
    - If anything fails, context is treated as `None` and extraction continues.
  - The pipeline calls `extract_raw_relations(document_text, document_id, ..., graph_context=graph_context, ontology=self._ontology)`.
    - Implementation detail: this is a structured-output LLM call using `instructor` (not an Agent loop).

- **Artefact produced**: `list[RawRelation]` (one list for the whole batch)

## Step 3 — Fast path: fill roles per relation

After raw relations are extracted, the pipeline fills semantic roles (agent/theme/etc.) for each raw relation.

- **Where it runs**: `Pipeline._fast_path(documents)`
  - Calls: `self._fill_roles_batch(documents, raw_relations)`
- **How it works**:
  - Builds `doc_texts = {document_id -> document_text}` for lookup.
  - Retrieves graph context once for the batch (best-effort) and passes it through.
    - Note: this is a *separate* retrieval from Step 2. Step 2 fetches context per document for raw extraction; Step 3 currently fetches a single context (from the first document) for role filling and reuses it for all relations.
  - For each `RawRelation`, calls `extract_roles(raw_relation, doc_text, ..., ontology=self._ontology, graph_context=graph_context)`.
    - Implementation detail: this is also a structured-output LLM call using `instructor`.

- **Artefact produced**: `list[Relation]` (role-filled relations)
