# doc-expand pipeline audit

Run: `5987ee9e` | Input: `DOC_EXPAND_VISION.md` | Depth: survey | Model: `gemini/gemini-3.5-flash`

> Audited by parallel review agents against stage artifacts in `state/` and `output/`.

---

## Comparative benchmark: doc-expand vs. llm-knowledge-graph

Baseline: `/home/cyan/ccr/llm-knowledge-graph/output/` — the prior pipeline's best output, used as the quality bar to beat.

| Dimension | doc-expand | llm-knowledge-graph | Notes |
|-----------|:----------:|:-------------------:|-------|
| Coverage | 3/5 | 4/5 | doc-expand covers eclectic out-of-scope domains (Arabic DLA, bibliometrics) due to broken domain classification (S2-1) |
| Citation quality | 3/5 | 5/5 | 70 `[NEEDS_CITATION]` markers in final output vs. 22 `[VERIFIED]` stamps + full arXiv IDs |
| Structural coherence | 3/5 | 5/5 | No through-line between sections; first line of output is `# DOC_EXPAND_VISION` (pipeline internal) |
| Technical depth | 4/5 | 5/5 | doc-expand's best dimension — Trust Paradox formula, Union-Find impl, CC-PAR derivation are strong |
| Artifact cleanliness | 1/5 | 5/5 | doc-expand's worst failure: `[UNVALIDATED]` x6, `[NEEDS_CITATION]` x70, `[INFERRED]` x30+ in reader-facing output |
| Actionability | 3/5 | 5/5 | Unfilled gaps undermine trust; reader can't tell verified from speculative |
| **Total** | **17/30** | **29/30** | |

**doc-expand wins:**
- Deeper on in-scope multi-agent orchestration (HALO / OSC / SagaLLM / Trust Paradox cluster)
- Better 2025–2026 paper coverage in that narrow domain
- Concrete implementation code (Union-Find, SagaLLM rollback, Quota-Aware Router with asyncio) not present in the baseline

**llm-knowledge-graph wins decisively on:**
- Zero pipeline internals in output (cleanliness)
- Citation rigour — full arXiv IDs, venue info, per-paper summaries, verified stamps
- Navigable structure — prerequisite graph, Trace system, synthesis boss nodes

**Root cause of the gap:** The output quality difference is largely explainable by three P0 bugs: broken domain classification (S2-1) causing off-topic section selection, pipeline markers not stripped before assembly (S7-1), and the citation scanner not excluding code blocks (S6-1). The underlying research quality (gap analysis, synthesis reasoning, technical math) is already competitive. Fixing those three issues would close most of the gap.

---

---

## Summary scorecard

| Stage | Score | Verdict |
|-------|-------|---------|
| 1 extract | 2/5 | Centrality calibration broken, code artifacts leaked into terms |
| 2 graph | 2/5 | All nodes stub-assigned to one domain; all 6 domains unvalidated |
| 3 audit | 4/5 | Gap analysis is substantive; finder/defender process has real bite |
| 4 research | 3.3/5 | MAS and ontology sections strong; concurrent systems section weak |
| 5 synthesize | 3/5 | Insights plausible but thinly sourced; roadmap duplicated |
| 6 verify | 3/5 | 15/23 "unknown keys" are parser false positives on code block variables |
| 7 assemble | 3/5 | Pipeline artifact markers visible in final output |

---

## Stage 1 — Term extraction (2/5)

### Critical

**[S1-1] Incidental tier is never assigned.**
Every term is core or supporting — the three-tier system is collapsed to two. The incidental tier exists in the schema but the extraction logic never produces it. Supporting becomes a flat catch-all junk tier (mean occurrence_count 3.1).

**[S1-2] Core tier massively over-populated and miscalibrated.**
469 core terms, of which 410 have occurrence_count ≤ 10 and 230 have count ≤ 5. Generic tokens occupy core: `stage` (67), `api` (29), `the default` (21), `first` (19), `str` (17), `none` (16), `assert` (12), `the user` (12), `zero` (11). These are structural glue words, not knowledge concepts.

**[S1-3] Code block and JSON artifacts treated as terms (278 entries).**
Examples: `{"status`, `{ "event`, `` the `reward_fn ``, `` (`api.openalex.org/concepts?search={label ``, `runtimeerror(f"pdf absent`. The extractor processed code fence contents as natural language.

### Moderate

**[S1-4] Article-prefix duplication not fully resolved.**
At least 15 terms have both a bare and article-prefixed canonical form simultaneously (`the pipeline` + `pipeline`, `the user` + `user`, `the cli` + `cli`, `an llm` + `llm`). Aliases partially cover this but miss the bare form as target.

**[S1-5] Unicode arrow leaked into term name.**
`→ semantic scholar` (occurrence 21, core) — the arrow character from a bullet list was absorbed into the canonical name. The alias holds the clean form; name and alias are inverted.

### Notable

Legitimate high-signal terms are well-captured: `langgraph` (32), `adversarial loop pattern` (19), `openalex` (19), `docling` (16), `dspy` (14), `quota exhaustion` (11). Multi-word compound concepts are preserved as single entries. Schema is consistent across all 3,121 entries.

### Fixes

- Stop-list filter for single generic tokens (`str`, `none`, `first`, `zero`, `assert`, `state`, `api`) and generic NPs (`the user`, `the output`, `the default`).
- Pre-filter code block contents before extraction (strip fenced code blocks from chunked text).
- Strip leading Unicode arrows and non-alpha prefixes from term names.
- Enforce incidental tier: occurrence_count 3–5 + not in structural zones → incidental.
- Deduplicate article-prefixed forms against bare canonical.

---

## Stage 2 — Graph / taxonomy (2/5)

### Critical

**[S2-1] All nodes stub-assigned to first domain.**
Every node in `graph.json` has `"domain": "multi_agent_systems"` regardless of content. `xelatex`, `asyncio`, `api`, `pdf` — all classified as multi-agent systems. The TODO in `s2_graph.py` acknowledges this is a stub pending phase-6 implementation. Domain signals are garbage for all downstream stages that depend on them (learning paths, research targeting, XP weighting).

**[S2-2] All 6 approved domains are `[UNVALIDATED]`.**
`openalex_concept_id` is null for every domain in the approved taxonomy. The user chose the splitter proposal, which has zero validated domains. The rejected lumper proposal had real OpenAlex IDs (ai_nlp: `C2779439875`, software_engineering: `c41008148`, digital_libraries: `C164120249`). The splitter is strictly worse-validated than the alternative that was not chosen.

### Moderate

**[S2-3] Lumper and splitter proposals not meaningfully distinct.**
The splitter file is structurally identical to the approved taxonomy — same domains, same definitions, same example terms. No genuine adversarial tension between the two proposals. `classification_conflicts.json` is empty (`[]`), confirming zero real conflicts surfaced.

**[S2-4] Domain overlap: concurrent_systems vs. multi_agent_systems.**
`langgraph checkpointing` and `pipeline state` appear in concurrent_systems examples, but `langgraph` and `multi-agent pipeline` are in multi_agent_systems. LangGraph is simultaneously a concurrency framework and agent orchestration tool; the domain definitions do not resolve this ambiguity.

**[S2-5] Scientometrics scope mismatch.**
Example terms (`openalex`, `crossref`, `csl-json`) are API/tooling terms, not scientometric methods. These are more accurately "scholarly data retrieval infrastructure," closer to the lumper's `digital_libraries_info_science` domain.

### Notable

The 6 splitter domains are conceptually appropriate for the source document. Definitions are technically literate with real tool names rather than vague buzzwords.

### Fixes

- Implement per-node domain classification (the existing Phase 6 TODO). Use `example_terms` lists as seed vocabulary for a lightweight classifier.
- Backfill OpenAlex IDs: map each splitter domain to its nearest lumper parent's validated ID at minimum.
- Resolve the LangGraph overlap with an explicit disambiguation rule in `concurrent_systems_programming`'s definition.
- Force the adversarial phase to produce at least one contested node per run; reject classification if conflicts is empty.

---

## Stage 3 — Audit / gap analysis (4/5)

### Notable success

The finder/defender/rebuttal adversarial process has real bite. The 44% acceptance rate (7 real, 9 not-gaps) is appropriately skeptical — typical LLM gap finders run 60–80% acceptance. Specific strong calls:
- Correctly rejected "concurrent mixed methods" by distinguishing software concurrency from mixed-methods research methodology.
- Correctly rejected KBC/domain-adaptation by distinguishing ABox vs. TBox scope.
- Correctly rejected "monitoring data in distributed systems" by redirecting to data governance.

Anchor papers are well-matched: OSC, HALO, and subbiah_2025_tool map precisely to their evidenced gaps. Citations have specific DOIs and plausible citation counts for 2025 preprints.

### Minor

**[S3-1] Citation key author mismatch.**
`wang_2025_osc` — Zhang is first author, Wang is last. The key is misleading but the paper content is correct.

**[S3-2] `[UNVALIDATED]` on domain headings not resolved.**
All gap analysis domain headings carry the `[UNVALIDATED]` suffix from stage 2. Expected intermediate artifact, but should be stripped or resolved before stage 4 consumes this file.

**[S3-3] Missing gap: citation graph traversal.**
The pipeline uses Semantic Scholar + CrossRef fetch but performs no snowballing or citation graph traversal. This is a real methodological gap for research-grade expansion that the audit did not surface.

---

## Stage 4 — Research sections (3.3/5)

### Multi-agent systems (4/5)

Strong direct connection to source document concepts. Code examples (SagaLLM rollback, UnionFind alias clustering, asyncio quota router) are technically correct. Trust Paradox derivation is coherent.

**Issues:**
- `HALO` expanded as "Hierarchical Autonomous Logic-Oriented Orchestration" — feels like a backronym generated by the LLM, not the real paper's expansion.
- Router code snippet has a semantic bug: `"semantic" in model` gates a Semantic Scholar rate limiter but `model` is a model-name string, not an API endpoint variable.
- Heavy `[INFERRED]` use in the OSC cognitive alignment subsection; the $O(N^2 \cdot L)$ token-cost claim is unanchored.

### Concurrent systems (2/5)

Weakest section. Generated as a general textbook chapter on distributed systems fundamentals with tenuous connection to the source document.

**Issues:**
- Citation stuffing: `@drachen_2020_how` (developer meetups) and `@nouroozi_2020_mobile` (mobile education for engineering faculties) are padding with no relevance.
- `@yang_2026_megaflow` — 2026-dated paper cited three times; existence is suspect.
- Formal math section (Lamport, FLP, CAP) has zero citations — every claim is `[NEEDS_CITATION]`.

**Recommendation:** Drop or heavily restructure this section. Its inclusion was driven by the pipeline using asyncio, not by the document genuinely covering concurrent systems theory.

### Ontology learning (4/5)

Well-structured narrative arc. LLMs4OL benchmark series citations are specific and consistent. Information-theoretic derivation from Choi et al. is mathematically sound.

**Issues:**
- RAG pseudo-code has a typo (`hallocinated_class`) and references undefined functions.
- Does not connect back to source document's specific tool choices (BGE-M3, SpaCy NER) mentioned in the multi-agent section.

### Cross-cutting issues

**[S4-1] Citation reliability is uneven across sections.** MAS and ontology have specific, plausible citations. Concurrent systems has obvious padding.

**[S4-2] Code quality is inconsistent.** SagaLLM and UnionFind are clean; router snippet has a logic bug; RAG pseudo-code has a typo.

**[S4-3] Domain relevance varies sharply.** MAS and ontology are well-grounded in the source. Concurrent systems is effectively off-topic. This is a downstream consequence of the broken domain classification in stage 2.

---

## Stage 5 — Synthesis (3/5)

Three cross-domain insights (CC-PAR routing, session-typed AMAG guardrails, martingale-bounded ingestion) are technically specific and mathematically framed.

**Issues:**

**[S5-1] Insights weakly connected to verified research.**
The synthesis reads as fresh LLM reasoning rather than connections derived from the domain section citations. Insights should cite specific papers from the stage 4 bibliographies; they don't.

**[S5-2] Thin coverage for 6 domains.**
Three insights across 6 domains — AI observability and ontology learning domains are barely touched.

**[S5-3] Roadmap duplicated.**
The "Actionable Research Roadmap" appears verbatim as both an ASCII diagram and a numbered list, followed immediately by a second "Reading Roadmap" that substantially overlaps. Internal post-processing duplication; should be collapsed to one.

**[S5-4] CC-PAR formula ungrounded.**
The $k_i^\alpha \cdot (1 - Q_i/C_i)^\beta$ regularization form is asserted without reference to capacity-planning or queuing theory literature. Plausible formula, not a derived one.

---

## Stage 6 — Citation verification (3/5)

Reported: 60 needs-citation, 23 unknown keys, 167 verified.

**[S6-1] ~15/23 unknown keys are parser false positives.**
The citation scanner fires on Python list-indexing and function parameters inside code blocks: `@item`, `@root_a`, `@root_b`, `@node`, `@step_name`, `@str`. These are not citation keys. The regex pattern should exclude content inside fenced code blocks.

**[S6-2] `[UNVALIDATED]` section headers counted as unknowns.**
All 6 section headers carry `[UNVALIDATED]` from stage 2. These are domain-validation artifacts, not citation issues. Actual true unknown citations are probably 5–7 entries.

**[S6-3] `[@M_0]` is a mathematical variable, not a citation key.**
Appears in the Ville's inequality section of the synthesis. The martingale initial value $M_0$ is being flagged as a missing citation.

---

## Stage 7 — Final assembly (3/5)

**[S7-1] Pipeline markers visible in final output.**
All 6 section `h1` headers carry `[UNVALIDATED]`. `[NEEDS_CITATION]` markers appear inline throughout (lines 12, 27, 87, 96–100, etc.). These are raw pipeline artifacts that should be stripped or converted to `[citation needed]` footnotes before final output.

**[S7-2] Bibliography format inconsistent.**
Key-first format (`[maldonado_2024_generative]`) rather than numeric or author-year inline, inconsistent with standard academic output (CSL-JSON supports both; pandoc should be able to render correctly with a CSL style file).

**[S7-3] Code-to-analysis ratio too high.**
Long code blocks (SagaLLM, UnionFind, call_orchestrator, Arabic DLA, OntologyRAGPipeline) tilt the document toward reference manual rather than research survey. Appropriate for a practitioner audience but should be configurable.

**Overall:** Document is genuinely useful research scaffolding for a practitioner familiar with LLM orchestration. It would confuse a newcomer due to visible artifact markers. A post-processing pass to strip/resolve markers and de-duplicate the synthesis roadmap would substantially lift output quality.

---

## Prioritised fix list

### P0 — correctness blockers

| ID | Issue | Stage | Effort |
|----|-------|-------|--------|
| S2-1 | All nodes stub-assigned to first domain | 2 | High (implement phase-6 classifier) |
| S7-1 | Pipeline markers (`[UNVALIDATED]`, `[NEEDS_CITATION]`) in final output | 7 | Low (post-process strip/convert) |
| S6-1 | Citation scanner fires on code block variables | 6 | Low (exclude fenced code from scan) |

### P1 — quality degraders

| ID | Issue | Stage | Effort |
|----|-------|-------|--------|
| S1-2 | Core tier over-populated with generic tokens | 1 | Medium (stop-list + tier thresholds) |
| S1-3 | Code artifacts in term inventory | 1 | Low (strip fenced code before chunking) |
| S2-2 | All 6 domains unvalidated (no OpenAlex IDs) | 2 | Low (prefer lumper for validation; backfill) |
| S4-1 | Concurrent systems section irrelevant / padding citations | 4 | Medium (domain relevance filter pre-research) |
| S5-3 | Synthesis roadmap duplicated | 5 | Low (dedup in post-processing) |

### P2 — improvements

| ID | Issue | Stage | Effort |
|----|-------|-------|--------|
| S1-1 | Incidental tier never assigned | 1 | Low (tier threshold rules) |
| S1-4 | Article-prefix duplicate terms | 1 | Medium (normalization pass) |
| S2-3 | Lumper/splitter proposals not adversarially distinct | 2 | Medium (strengthen splitter prompt) |
| S3-3 | Missing gap: citation graph traversal / snowballing | 3 | Low (add to gap finder prompt) |
| S4-2 | Code quality inconsistent in sections | 4 | Medium (add code validator to critique pass) |
| S5-1 | Synthesis insights not grounded in stage-4 citations | 5 | Medium (pass bibliography to synthesis prompt) |
| S5-2 | Only 3 insights for 6 domains | 5 | Low (raise min_insights config) |
| S7-2 | Bibliography key-first format | 7 | Low (add CSL style file for pandoc) |
