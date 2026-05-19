# doc-expand — Implementation Plan

> **Companion to:** `DOC_EXPAND_VISION.md` — architecture, design philosophy, per-stage spec.
> **Status:** Active. Update as decisions are made and phases complete.

---

## Resolved Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Python package name | `doc_expand` | snake_case of `doc-expand`; matches repo naming convention |
| LangGraph TypedDict scope | Minimal | `run_id`, `state_dir`, `depth`, `domain_ids` only; filesystem is canonical state |
| Phase 1 fixture | `DOC_EXPAND_VISION.md` (this repo) | Dogfood early; technically dense, known structure, immediately available |
| GROBID + AnyStyle timing | Stub Phase 1–2; wire Phase 2 | Docker setup non-trivial; not needed until Stage 3 |
| `--renderer typst` | Defer post-v1 | xelatex is the production path; Typst is optional |

---

## Dogfooding Plan

`DOC_EXPAND_VISION.md` is the Phase 1 fixture and the first real test document. Running doc-expand on its own architecture doc validates:

- Stage 1 extracts the right terms (`LangGraph`, `DSPy`, `Docling`, `QuotaAwareRouter` rank as `core`)
- Stage 2 produces a sensible domain taxonomy (pipeline orchestration, LLM inference, document processing, bibliography management)
- The full output document is coherent enough to read

This is not just a convenience — a dense technical architecture document is exactly the use case doc-expand is built for. If it can't expand its own spec, it's not ready.

---

## Phase 1 — Core Extraction Pipeline (Stages 0–2)

**Goal:** Given any document, produce a validated knowledge graph (`graph.json`) with a locked taxonomy.

### Tasks

- [ ] Scaffold project: `pyproject.toml`, package layout, `models.yaml`, `config.yaml`
- [ ] `doc_expand/state.py` — `PipelineState` TypedDict, sentinel helpers, `emit()` JSONL function
- [ ] `doc_expand/config.py` — load `models.yaml` and `config.yaml` into typed dataclasses
- [ ] `doc_expand/union_find.py` — `UnionFind` class (~30 lines, no dependencies)
- [ ] `doc_expand/centrality.py` — `compute_centrality()`, boilerplate stop-list, structural weight multiplier
- [ ] `doc_expand/agents/schemas.py` — `TermInventory`, `TaxonomyProposal`, `KnowledgeGraph` Pydantic models
- [ ] `doc_expand/agents/base.py` — `call_agent()`, cloud/local semaphores, `QuotaAwareRouter` with `Retry-After` handling
- [ ] `doc_expand/stages/s0_ingest.py` — Docling parse, `HybridChunker`, structural zone extraction from `SECTION_HEADER`/`TITLE` labels, httpx URL fetch
- [ ] `doc_expand/stages/s0_5_assess.py` — `--interactive` path only; default no-op; writes `user_profile.json`
- [ ] `doc_expand/stages/s1_extract.py` — Map: spaCy + LLM Agent B + KeyBERT/BGE-M3 per chunk; Reduce: Union-Find dedup, centrality, reconciliation
- [ ] `doc_expand/stages/s2_graph.py` — Phase 1: Lumper/Splitter proposals, OpenAlex validation, taxonomy checkpoint; Phase 2: classification
- [ ] `doc_expand/pipeline.py` — LangGraph graph wiring for Stages 0–2
- [ ] `doc_expand/cli.py` — argument parsing, `--resume`, `--stage`, `--status`, `--interactive`, `--auto-taxonomy`

### Exit criteria

- `doc-expand DOC_EXPAND_VISION.md --depth survey --auto-taxonomy` runs to completion
- `state/graph.json` valid: 15–50 nodes, `from_source_doc` flags correct, no orphaned terms
- `state/terms.json` centrality: `LangGraph`, `DSPy`, `Docling` all rank `core`
- All `.done` sentinels written; `--resume 1` skips completed stages without re-running

### Testing

- **Unit:** `centrality.py` — boilerplate filtered, structural weight applied correctly, bypass not possible with single occurrence
- **Unit:** `union_find.py` — merge correctness, path compression, cluster output
- **Integration:** Stage 0 on a short PDF; verify chunk count and `structural_zones.json` non-empty
- **End-to-end:** full Phase 1 run on `DOC_EXPAND_VISION.md`; manually inspect `graph.json`

---

## Phase 2 — Anchored Audit + Bibliography (Stage 3)

**Goal:** Given a knowledge graph, produce a verified per-domain bibliography and gap analysis.

### Tasks

- [ ] `doc_expand/bibliography.py` — two-bucket fetch (65% foundational / 35% frontier), `aiolimiter` rate limiters per API, CSL-JSON dedup by DOI
- [ ] GROBID HTTP client (calls to Docker sidecar)
- [ ] AnyStyle HTTP client (calls to Docker sidecar)
- [ ] `doc_expand/stages/s3_audit.py` — reference extraction, bibliography fetch, Gap Finder vs Defender loop (single-round initially)
- [ ] `docker-compose.yml` — add GROBID and AnyStyle services
- [ ] Wire Stage 3 into `pipeline.py`

### Exit criteria

- Stage 3 on `DOC_EXPAND_VISION.md` produces non-empty `bibliography_{domain}.json` per domain
- Two-bucket split verified: frontier papers (last 24 months) present alongside foundational papers
- Domain-agnostic test: biology or economics paper produces valid bibliography from OpenAlex/Crossref without arXiv

### Testing

- **Unit:** `bibliography.py` bucket split with mocked Semantic Scholar + OpenAlex responses
- **Integration:** live API call for 1 domain; verify CSL-JSON schema and `bucket` field values
- **Domain-agnostic:** Stage 3 on a non-CS paper; confirm no arXiv-only citations

---

## Phase 3 — Research Pipeline (Stage 4)

**Goal:** Given graph + bibliography, produce per-domain narrative sections and structured summaries.

### Tasks

- [ ] `doc_expand/agents/schemas.py` additions: `DomainSummary`, `ResearchSection`
- [ ] `doc_expand/stages/s4_research.py` — top-down + bottom-up agents, bibliography constraint, `summary_{domain}.json` output
- [ ] `run_with_domain_timeout()` — `asyncio.timeout(2700)` per domain, clean `domain_timeout` event on expiry
- [ ] Wire Stage 4 into `pipeline.py` with LangGraph `Send` API fan-out

### Exit criteria

- All domain sections written; zero citations outside `bibliography_{domain}.json`
- `summary_{domain}.json` validates against `DomainSummary` schema for all domains
- Timeout: simulated stuck domain; other domains complete and sentinels preserved

### Testing

- **Citation constraint:** inject bibliography with 5 known keys; verify no other keys appear in output
- **Schema validation:** Pydantic parse on all `summary_*.json` files
- **Timeout:** mock a domain that sleeps 3000s; verify group completes in ~2700s and emits `domain_timeout`

---

## Phase 4 — Synthesis + Verify (Stages 5–6)

**Goal:** Cross-domain synthesis from JSON summaries only; citation structural audit.

### Tasks

- [ ] `doc_expand/agents/schemas.py` additions: `SynthesisOutput`
- [ ] `doc_expand/stages/s5_synthesize.py` — structural + semantic agents; reads only `summary_*.json` + `graph.json`; never raw `.md` sections
- [ ] `doc_expand/stages/s6_verify.py` — walk citation keys in all sections; check against bibliography; log `[NEEDS_CITATION]`
- [ ] Wire Stages 5–6 into `pipeline.py`

### Exit criteria

- Synthesis section contains cross-domain connections not present in any single domain section
- Stage 6 catches a manually injected bad citation key; logs to `needs_citation.md`
- Stage 5 token budget verified: all `summary_*.json` fit within context at deep depth on 8-domain paper

### Testing

- **Injection:** `summary_*.json` with explicit cross-domain signal; verify it surfaces in synthesis
- **Bad key:** inject unknown citation key in a section file; verify Stage 6 flags it

---

## Phase 5 — Assembly + Build (Stage 7)

**Goal:** Produce a valid, readable PDF from all stage outputs.

### Tasks

- [ ] `doc_expand/build.py` — pandoc → xelatex ×2 → `pypdf` integrity check
- [ ] `doc_expand/stages/s7_assemble.py` — topological sort, section stitch, bibliography merge (deduplicate by DOI)
- [ ] `style/preprocess.py` — port from LLM KG project
- [ ] `style/llm_paper_style.tex` — port from LLM KG project
- [ ] Wire Stage 7 into `pipeline.py`

### Exit criteria

- Full pipeline run on `DOC_EXPAND_VISION.md` produces a valid PDF that opens correctly
- TOC page numbers resolve (double-pass confirmed by checking for `??` in output)
- `pypdf` integrity check passes on valid output; raises `RuntimeError` on truncated file

### Testing

- **pypdf pass:** feed known-good `.tex` through full build; verify `PdfReader` succeeds
- **pypdf fail:** truncate a PDF to 10 bytes; verify `RuntimeError` raised before checkpoint written
- **Double-pass:** single-pass build produces `??` in TOC; double-pass resolves them

---

## Phase 6 — Adversarial Loops + Complementary Redundancy

**Goal:** Layer Generator→Critic loops and complementary agent pairs onto the working pipeline.

### Tasks

- [ ] DSPy `dspy.Refine` wiring in Stages 3, 4, 5
- [ ] `reward_fn` round-file checkpointing: write generator output to `critique_{stage}_{id}_round{N}.md` before scoring
- [ ] On resume: inject last round file as `initial_output` rather than regenerating
- [ ] Complementary agent pairs: Lumper/Splitter (Stage 2 already done), top-down/bottom-up (Stage 4), structural/semantic (Stage 5)
- [ ] Round scaling: 1 round survey / 2 rounds standard / 3 rounds deep

### Exit criteria

- At `deep` depth, Stage 4 writes 3 round files per domain to `state/audit/`
- Crash at round 2: `--resume` picks up from round 2, not round 1
- Early exit: critic produces zero challenges; loop terminates before `max_rounds`

### Testing

- **Round files:** verify `critique_s4_{domain}_round{1,2,3}.md` written at deep depth
- **Resume:** simulate crash at round 2 by deleting round 3 file; verify resume starts from round 2
- **Early exit:** mock critic that returns max score on round 1; verify loop exits after 1 round
