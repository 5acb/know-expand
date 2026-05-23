# know-expand

> Turn any technical document into a research-publication-grade knowledge expansion — automatically.

**Python 3.11+ · LangGraph · LiteLLM · Docling · asyncio**

---

## Quick start

```bash
# Install
pip install uv
uv sync

# Expand a paper
uv run know-expand path/to/paper.pdf

# Watch progress in your browser
uv run know-expand serve          # http://localhost:7842

# Resume a stopped run
uv run know-expand --status
uv run know-expand --resume 4
```

Each run writes to `runs/{run_id}/` containing `state/`, `logs/`, and `output/`.

---

## What it does

A person reads a dense technical paper. They understand the words but not the field. They cannot ask good questions, cannot evaluate the claims, cannot know what to read next, cannot build on top of it.

`know-expand` takes that document and produces a **navigable, ground-up knowledge expansion** — a structured field guide covering everything needed to understand the paper deeply, go beyond it, and do independent research.

**Not a summary. Not a chatbot answer. Not a Q&A wrapper.** A full-stack knowledge construction pipeline that identifies every substantive concept in a document, maps those concepts into a principled domain taxonomy, augments each domain from first principles to the current research frontier, synthesizes cross-domain insights invisible within any single domain, and grounds every claim in verifiable, real citations.

The reference output: a 480KB, 241-page, 81-node skill-tree document with verified citations, LaTeX mathematics, and technical diagrams — produced automatically from a single source paper.

### Cost and latency

**Cost:** a deep run is roughly 8 domains × (3 personas + 1 reconciler) × up to 3 critic rounds, plus adversarial loops in S4 and S7. ~150–300 LLM calls for the researcher/synthesizer role. At current Opus 4.7 pricing, expect **$50–150 per deep run** with Opus as primary researcher. Standard depth with Sonnet 4.6 is roughly $5–20. Monitor `state/audit/model_usage.jsonl` — every call is logged with `cost_usd`.

**Latency:** 1–3 hours end-to-end at deep depth on a standard 8-domain paper. Bottlenecks are Semantic Scholar rate limits and LLM call volume. Survey depth on a short paper: 10–20 minutes.

---

## Pipeline

### Stage order

```
S0 Ingest → S2 Extract → S1 Assess → S3 Graph → S4 Audit → S5 Research
         → S6 Align → S7 Synthesize → S8 Verify → S9 Prereq → S10 Assemble
```

S2 Extract runs **before** S1 Assess by design: the interview must reference real terms extracted from the document, not LLM guesses. Swapping them back is a known footgun.

### Architecture diagram

```
Input Document (text / file / URL / PDF)
        │
        ▼
┌───────────────────────────────────────────────────────┐
│ S0: INGEST                                            │
│ Normalize → chunk with overlap → structural zones     │
│ Degenerate-parse guard (scan-only PDF detection)      │
└───────────────────────────┬───────────────────────────┘
                            │  state/chunks/*.json
                            │  state/structural_zones.json
                            ▼
┌───────────────────────────────────────────────────────┐
│ S2: EXTRACT (Map-Reduce)                              │
│ Map: parallel agents per chunk (lexical + conceptual  │
│      + embedding); 3-signal extraction                │
│ Reduce: merge, Union-Find alias dedup, centrality     │
└───────────────────────────┬───────────────────────────┘
                            │  state/terms.json
                            ▼
┌───────────────────────────────────────────────────────┐
│ S1: ASSESS (User Calibration)                         │
│ 10–15 turn interview using real extracted terms       │
│ → UserProfile (depth, math mode, known concepts)      │
│ ← Human interaction #1 (opt-in via --interactive;    │
│   skipped by default in autonomous mode)              │
└───────────────────────────┬───────────────────────────┘
                            │  state/user_profile.json
                            ▼
┌───────────────────────────────────────────────────────┐
│ S3: GRAPH (Two-Phase Lock)                            │
│ Phase 1: taxonomy proposals (Lumper + Splitter)       │
│          → web UI review OR --auto-taxonomy           │
│          ← Human interaction #2 (skippable)           │
│ Phase 2: term classification against locked taxonomy  │
└───────────────────────────┬───────────────────────────┘
                            │  state/taxonomy.json
                            │  state/graph.json
                            ▼
┌───────────────────────────────────────────────────────┐
│ S4: AUDIT (Anchored)                                  │
│ Fetch anchors + bibliography (65% found / 35% front.) │
│ Multi-source fetch: Wikipedia / PyPI / arXiv          │
│ Gap Finder vs. Defender adversarial loop              │
└───────────────────────────┬───────────────────────────┘
                            │  state/audit/gap_analysis.md
                            │  state/audit/bibliography_{domain}.json
                            ▼
┌───────────────────────────────────────────────────────┐
│ S5: RESEARCH (Parallel per domain)                    │
│ Three personas: Theoretician / Engineer / Practitioner│
│ Adversarial critic loop; bibliography-constrained     │
│ knowledge_sources injected into all prompts           │
└───────────────────────────┬───────────────────────────┘
                            │  state/sections/section_{domain}.md
                            │  state/summaries/summary_{domain}.json
                            ▼
┌───────────────────────────────────────────────────────┐
│ S6: ALIGN (Pedagogical Alignment)                     │
│ Pedagogy checklist agent: what-is intro, symbol       │
│ tables, worked examples, Where-to-Go-Next, citations  │
└───────────────────────────┬───────────────────────────┘
                            │  state/sections/section_{domain}.md (patched)
                            ▼
┌───────────────────────────────────────────────────────┐
│ S7: SYNTHESIZE                                        │
│ Inputs: graph.json + summary_*.json ONLY              │
│ Structural + Semantic + Connector agents              │
│ Adversarial critic loop                               │
└───────────────────────────┬───────────────────────────┘
                            │  state/sections/section_synthesis.md
                            ▼
┌───────────────────────────────────────────────────────┐
│ S8: VERIFY                                            │
│ Structural citation audit (keys vs bibliography)      │
│ Crossref → Semantic Scholar → arXiv (CS/Math only)    │
└───────────────────────────┬───────────────────────────┘
                            │  state/audit/needs_citation.md
                            ▼
┌───────────────────────────────────────────────────────┐
│ S9: PREREQ (single LangGraph ReAct pass)              │
│ Threading agent: scan all sections for forward        │
│ references; insert inline primers for opaque terms    │
└───────────────────────────┬───────────────────────────┘
                            │  state/sections/*.md (patched)
                            ▼
┌───────────────────────────────────────────────────────┐
│ S10: ASSEMBLE + BUILD                                 │
│ Topological sort → stitch → preprocess → xelatex ×2  │
└───────────────────────────┬───────────────────────────┘
                            │  output/expanded.md
                            │  output/expanded.pdf
```

Stages skip automatically if already complete (`stage_is_complete()` checks `pipeline.json`). Resume a stopped run via the web UI Resume button or `--resume N`.

---

## CLI reference

```
know-expand <input> [options]
know-expand tail [run_id] [--log-dir DIR]
know-expand serve [--port PORT]

Arguments:
  <input>                   File path (.txt .md .pdf), URL, or - (stdin)

Subcommands:
  tail [run_id]             Stream pipeline events to stdout. Reads
                            runs/<run_id>/events.jsonl; uses latest run if
                            run_id omitted. Pipe-friendly, greppable.
  serve                     Launch HTTP dashboard at localhost:7842.
                            Left stage rail + full-width detail pane + run
                            drawer. Interview Q&A and taxonomy review IPC
                            handled in-browser. ETag-based 2s polling.

Output / mode:
  --no-pdf                  Skip PDF build, output Markdown only
  --renderer                xelatex | typst  (default: xelatex)

Pipeline control:
  --depth                   survey | standard | deep  (default: standard)
  --status                  Emit pipeline_status from pipeline.json; no work runs
  --resume STAGE            Resume from stage N (e.g. --resume 4)
  --stage STAGE             Run only stage N then stop
  --interactive             Run S1 user calibration interactively (default: skipped)
  --user-profile <path>     Load pre-computed UserProfile JSON; skips S1
  --auto-taxonomy           Skip editor review; orchestrating LLM decides taxonomy

Paths:
  --output-dir              Path for final output  (default: runs/{id}/output)
  --runs-dir                Base directory for all runs  (default: ./runs)

Concurrency:
  --cloud-concurrency N     Override config concurrency.cloud_default
  --local-concurrency N     Override config concurrency.local_default

Run identity:
  --run-id ID               Reuse an existing run ID (for --resume)
```

Every stage emits structured JSON Lines to stdout. The orchestrating LLM reads these events, inspects artifacts at the reported paths, and decides its next action:

```json
{"event": "stage_complete", "stage": 3, "artifact": "state/taxonomy.json", "domain_count": 5}
{"event": "pipeline_paused", "reason": "taxonomy_review", "instructions": "Review taxonomy in web UI, then click Resume"}
{"event": "quota_exhausted", "stage": 5, "domain_id": "inference_systems", "resume_command": "know-expand --resume 5"}
```

**`know-expand --status`** is the zero-work re-orientation command. Call it after any pause to get a complete current-state summary without executing any pipeline work.

---

## Configuration

### `config.yaml` — tunable knobs

All runtime parameters. Key sections:

```yaml
depth: standard               # survey | standard | deep

concurrency:
  default: 8                  # cloud provider semaphore
  groq: 3                     # free-tier provider semaphore
  mistral: 3

timeouts:
  geminicli_timeout_s: 600    # use cfg.timeouts.get(), not getattr()
  domain_research_s: 2700

bibliography:
  frontier_months: 24         # frontier bucket window
  split_foundational: 0.65    # 65% foundational / 35% frontier — non-negotiable
  split_frontier: 0.35

extract:
  structural_weight: 2.0      # centrality multiplier for structurally prominent terms
  cosine_alias_threshold: 0.92
```

### `models.yaml` — model lists per role

Models are tried in order; the `QuotaAwareRouter` falls back automatically on quota exhaustion or auth failure:

```yaml
roles:
  agent:                        # LangGraph ReAct graphs (S4.5, S6 — require tool calling)
    - "gemini/gemini-2.5-pro"
    - "llamacpp/qwen2.5-7b-instruct"
  researcher:
    - "claude-opus-4-7"
    - "claude-sonnet-4-6"
    - "gpt-4o"
    - "gemini/gemini-2.5-pro"
    - "ollama/qwen2.5:14b"
  synthesizer:
    - "claude-opus-4-7"
    - "claude-sonnet-4-6"
    - "gpt-4o"
    - "gemini/gemini-2.5-pro"
    - "ollama/qwen2.5:14b"
  critic:
    - "claude-sonnet-4-6"
    - "gpt-4o-mini"
    - "gemini/gemini-2.5-pro"
    - "llamacpp/gemma-4-26B-A4B-it-UD-Q4_K_XL.gguf"
    - "ollama/llama3.2:3b"
  extractor:
    - "claude-sonnet-4-6"
    - "gpt-4o-mini"
    - "gemini/gemini-2.5-pro"
    - "llamacpp/gemma-4-26B-A4B-it-UD-Q4_K_XL.gguf"
    - "ollama/llama3.2:3b"
  classifier:
    - "claude-sonnet-4-6"
    - "gpt-4o-mini"
    - "gemini/gemini-2.5-pro"
    - "llamacpp/gemma-4-26B-A4B-it-UD-Q4_K_XL.gguf"
    - "ollama/llama3.2:3b"
```

Override any role at runtime:
```bash
LITELLM_RESEARCHER=gpt-4o LITELLM_EXTRACTOR=ollama/llama3.2 know-expand paper.pdf
```

### API keys

Set in `.env` or via the web UI (session only — not persisted to disk):

| Key | Purpose |
|-----|---------|
| `ANTHROPIC_API_KEY` | Claude models |
| `OPENAI_API_KEY` | GPT models |
| `GROQ_API_KEY` | Groq fallback |
| `MISTRAL_API_KEY` | Mistral fallback |
| `GEMINI_API_KEY` | Gemini API models (not geminicli) |
| `SS_API_KEY` | Semantic Scholar (optional; raises rate limit from 0.1 → 1 req/s) |

**Never commit `.env` or write keys to disk from the serve process.**

---

## Stage deep dive

<details>
<summary><strong>S0 — Ingest</strong></summary>

**Input:** file path (`.txt`, `.md`, `.pdf`), URL, or stdin (`-`)

**PDF parsing — Docling only.** Running Docling and PyMuPDF over the same PDF doubles ingestion time, spikes memory, and creates alignment issues. Docling's DocLayNet model classifies elements as `SECTION_HEADER`, `TITLE`, `TABLE`, `FORMULA`, etc. — a cleaner signal than font-flag heuristics, trained on academic layout patterns.

**Chunking — semantic boundaries, not page splits.** Technical PDFs regularly have equations and tables spanning page breaks. Docling's `HybridChunker` respects the section hierarchy with `respect_page_break=False`. Chunks are token-bounded at 4,000 tokens with natural paragraph overlap.

**Degenerate-parse guard:** if `structural_zones < 5 AND chunks < 3`, raises immediately with a clear message. This combination indicates a scan-only or image-only PDF where all downstream stages will silently degrade.

**URL fetch:** `httpx` with timeout.

**Output:** `state/source.txt`, `state/source_meta.json`, `state/chunks/chunk_{N:04d}.json`, `state/structural_zones.json`

</details>

<details>
<summary><strong>S2 — Extract (Map-Reduce)</strong></summary>

**Position:** Immediately after S0, before S1. Extract runs first so the interview references real document vocabulary.

**Map phase — 3 independent signals per chunk, run in parallel:**

| Signal | Source | Strategy |
|--------|--------|----------|
| A | spaCy | Deterministic NER + noun chunker + regex acronyms. No LLM call. Milliseconds. |
| B | LLM Agent | What a domain expert recognizes as load-bearing. One call per chunk. Conceptual, may go beyond literal text. |
| C | KeyBERT + BGE-M3 | Embedding-based keyphrase centrality. Fully local, no API. Divergence from A and B is signal. |

The three signals operate in fundamentally different representation spaces — lexical, reasoning, geometric — producing stronger divergence signal than running two LLMs over the same text.

**Reduce phase — single deterministic pass:**

- Alias clustering via Union-Find (BGE-M3 cosine > 0.92 finds near-duplicate pairs; Union-Find merges them in ~30 stdlib lines — no NetworkX needed for 300 terms)
- Centrality algorithm: `effective = occurrences × (2.0 if in structural_zones else 1.0)`. Structural presence is a weight multiplier, not a frequency bypass. Boilerplate stop-list (`"introduction"`, `"conclusion"`, `"theorem"`, etc.) is applied before centrality.
- Classification into `academic` / `tool_library` / `concept` via LLM batch call
- Reduce reconciliation: terms in all 3 signals → `core`; terms in 2 → frequency/structural decides; terms in KeyBERT but not LLMs → likely real terms the LLMs glossed over

**Output:** `state/terms.json`

</details>

<details>
<summary><strong>S1 — Assess (User Calibration)</strong></summary>

**Position:** After S2, before S3. Skipped by default. Opt-in via `--interactive` for direct human use, or web UI Q&A during a `serve` run.

**Purpose:** 10–15 turn LLM-driven interview that establishes a `UserProfile` — familiarity level, background field, math comfort, known/unknown concepts — which calibrates every downstream stage. Questions reference real terms from `state/terms.json`, not LLM guesses.

**Why after S2:** Q3, Q6, and Q7 must offer grounded options from the document's actual vocabulary. Generic questions like "do you know what a neural network is?" on a transformer paper are uncalibrated and useless.

**Interaction modes:**
- TTY: question/answer loop in the terminal
- Web: Q&A displayed in the `serve` dashboard drawer; answers posted via `/api/qa/answer`
- Autonomous: skipped entirely (default)

**Output:** `state/user_profile.json` — propagated as `unknown_concepts`, `known_concepts`, `math_mode`, `learning_goal`, and `effective_depth` into S5, S6, S7, and S10.

</details>

<details>
<summary><strong>S3 — Graph (Two-Phase Taxonomy Lock)</strong></summary>

**Phase 1 — Ontology Lock:**

Two agents receive only `core` terms (typically 20–40) and independently propose taxonomies:

- **Lumper:** fewest domains that cleanly partition core terms (max 8; each must map to a Semantic Scholar `fieldsOfStudy`)
- **Splitter:** finest-grained distinctions the core terms support

Each proposed domain is validated against the OpenAlex concept API. Domains that resolve to no OpenAlex concept are flagged `[UNVALIDATED]`.

**Taxonomy checkpoint — two paths:**

*Default:* pipeline emits `pipeline_paused` and exits cleanly. User reviews proposals in `runs/{id}/state/audit/taxonomy_a.json` and `taxonomy_b.json`, edits `taxonomy.json`, and resumes. In the web UI, taxonomy proposals appear in a dedicated review pane.

*`--auto-taxonomy`:* orchestrating LLM receives both proposals plus OpenAlex validation, writes its decision and rationale to `taxonomy.json`, and the pipeline continues without pausing.

This is the highest-consequence single decision in the pipeline — every downstream stage is partitioned by the taxonomy. One minute of review (human or LLM) here saves hours of downstream garbage.

**Phase 2 — Classification:**

Two agents classify the full term inventory against the locked taxonomy. Divergences written to `state/audit/classification_conflicts.json` and flow to S4 as primary gap candidates.

**Graph schema excerpt:**
```json
{
  "nodes": [{"id": "n001", "term_id": "t001", "name": "PagedAttention",
              "domain": "inference_systems", "tier": "journeyman", "xp": 100,
              "prerequisites": ["n_kv_cache"], "unlocks": ["n_continuous_batching"],
              "from_source_doc": true}],
  "edges": [{"from": "n001", "to": "n_continuous_batching", "type": "enables"}],
  "domains": [{"id": "inference_systems", "label": "Inference Systems",
                "node_count": 11, "primary": true}]
}
```

`from_source_doc: true` distinguishes concepts present in the input document from concepts added during augmentation.

**Output:** `state/taxonomy.json`, `state/graph.json`, `state/audit/classification_conflicts.json`

</details>

<details>
<summary><strong>S4 — Audit (Anchored Gap Analysis)</strong></summary>

**Two fetches per domain:**

1. **Anchors** (3 papers): Semantic Scholar top-cited papers for the domain label — used for gap diff
2. **Bibliography** (10/30/50 papers by depth): two-bucket fetch — foundational (65%, all-time citation rank) + frontier (35%, last 24 months, citation-ranked within window)

The bibliography is the **only citation pool Stage 5 agents may draw from.** This prevents hallucination by construction.

**Why two buckets:** sorting by `citationCount` descending guarantees blindness to the last 24 months. A 2017 survey with 4,000 citations outranks a 2025 breakthrough with 12. The 65/35 split is a design invariant.

**Anchor-neighbor expansion:** after bibliography fetch, the top-3 anchor `paperId` values are used to query Semantic Scholar's `/paper/{id}/citations` endpoint. Recent papers (last 3 years) citing a top anchor are added to the frontier bucket (deduped by DOI). This catches state-of-the-art work that doesn't surface on a domain-label query.

**Multi-source knowledge fetch:** Wikipedia REST / PyPI JSON / arXiv Atom fetched per term by `term_type`:
- `tool_library` → PyPI first, then Wikipedia
- `concept` → Wikipedia, then arXiv (2 papers)
- `academic` → Wikipedia only (SS handles bibliography)

Cached at `audit/sources/sources_{domain_id}.json`. Injected into S5 persona prompts with caps: 8 terms / 1 source / 400 chars.

**External API rate limiting:** 8 concurrent domain fetches hit Semantic Scholar (100 req/5 min unauthenticated), Crossref, and OpenAlex simultaneously. `aiolimiter` (async token bucket) serializes at the API call level without blocking domain-level parallelism.

**Adversarial gap analysis:**

| Agent | Role |
|-------|------|
| Gap Finder | What do external anchors cover that the knowledge graph omits? |
| Defender | Argue each apparent gap is present under a different name |

A gap survives to `gap_analysis.md` only if the Gap Finder rebuts the Defender's argument. This prevents rubber-stamping.

**Output:** `state/audit/anchors_{domain}.json`, `state/audit/bibliography_{domain}.json`, `state/audit/gap_analysis.md`, `state/audit/corrections.md`, `state/audit/sources/sources_{domain}.json`

</details>

<details>
<summary><strong>S5 — Research (Parallel per domain)</strong></summary>

Three persona agents per domain, all domains launched simultaneously via `asyncio.gather`. Within each domain, the three personas run in parallel, then a Reconciler merges them. An adversarial critic loop runs on the reconciled output.

**Personas:**

| Persona | Scope | Strategy |
|---------|-------|----------|
| Theoretician | Mathematical foundations, formal definitions, historical context, theoretical guarantees | Top-down: axiomatic basis → mechanisms → derivations |
| Engineer | Algorithms, architectures, implementation patterns, failure modes | From mechanism to theory: show how it's built, explain why |
| Practitioner | Real-world trade-offs, benchmarks, gotchas, Where to Go Next | From usage to mechanism: practitioner workflow → underlying reasons |

**Each agent receives:** domain nodes from `graph.json`, gap analysis, `bibliography_{domain}.json` (only permitted citations), reader profile (`unknown_concepts` get first-principles treatment, `known_concepts` are assumed), math protocol, and per-persona drift guard.

**Adversarial critic loop:** terminates when issue set is empty, identical to prior round (stalled), or critic accepts. Hard cap: 1/2/3 rounds for survey/standard/deep.

**Prompt sizing and caching:** persona prompts are split into a shared context block (domain info, reader profile, graph nodes, gap analysis, bibliography, knowledge sources — marked `cache_control: {type: ephemeral}`) and a task block (persona-specific role and output instruction). The shared block exceeds Anthropic's 4,096-token caching threshold at deep depth, so resume/retry runs benefit from the 90% cache-read discount.

**Each domain writes:**
1. `state/sections/section_{domain}.md` — full narrative
2. `state/summaries/summary_{domain}.json` — structured JSON for S7 consumption (perspectives, key tensions, cross-domain signals, open problems, citation IDs)

On resume, only domains without a `.done` sentinel are re-run.

</details>

<details>
<summary><strong>S6 — Align (Pedagogical Alignment)</strong></summary>

Runs once per domain via a LangGraph ReAct graph. The agent checks a five-item pedagogy checklist against each domain section and surgically adds missing elements:

1. "What is {domain}?" — plain-English intro for a newcomer
2. Symbol tables — every equation preceded by a Markdown table defining every symbol
3. Worked examples — every major concept followed by a concrete numerical or code example
4. "Where to Go Next" — open problems, start-here resource, 3 essential papers
5. `[NEEDS_CITATION]` markers — resolved via Semantic Scholar search and bibliography append

**Constraint:** only ADD content; never delete or rewrite existing text. Max 12 tool calls per domain.

Completion sentinel: `state/sections/section_{domain_id}.aligned`

</details>

<details>
<summary><strong>S7 — Synthesize</strong></summary>

**Inputs: `state/graph.json` + `state/summaries/summary_*.json` ONLY.**

Never the raw narrative `.md` files. At deep depth, 10 section files easily exceed 150k tokens. Summary JSONs are designed to stay under 2k tokens each.

Three agents run in parallel:

| Agent | Strategy | External truth |
|-------|----------|----------------|
| Structural | Cross-domain insights from typed edges in the knowledge graph | `graph.json` topology — cannot assert a connection without an edge path |
| Semantic | Cross-domain insights from `perspectives` fields in summaries | `summary_*.json` perspectives fields |
| Connector | `CrossDomainBridge` objects: for each domain pair, a shared concept + 1–2 paragraph bridge explanation | Both — `evidence` field must reference a KG edge path or summary field |

**Output:** 3–5 synthesis boss nodes + a reading roadmap (topologically sorted path from "understands source doc" to "can do independent research"). Reading roadmap is injected as a `## Reading Roadmap` section at the top of the assembled document.

**Output:** `state/sections/section_synthesis.md`, `state/summaries/summary_synthesis.json`

</details>

<details>
<summary><strong>S8 — Verify</strong></summary>

Structural citation audit. Every citation key in the assembled document is checked against `state/audit/bibliography_{domain}.json`. Unknown keys = agent defections.

**Three-tier oracle:**
1. Crossref REST API — journals, proceedings, books, all fields, no auth
2. Semantic Scholar Graph API — CS/ML papers, citation counts
3. arXiv Export API — fallback, only for domains tagged `cs.*`, `math.*`, or `physics.*`

`[NEEDS_CITATION]` markers are logged to `state/audit/needs_citation.md` and flagged for human review. They do not block the build.

**Acceptance criterion:** 0 `[UNVERIFIED]` tags. `[NEEDS_CITATION]` tags are acceptable.

</details>

<details>
<summary><strong>S9 — Prereq (Prerequisite Threading)</strong></summary>

A single LangGraph ReAct pass over all sections. The threading agent scans for terms used but not defined in the section where they appear, and inserts blockquote primers inline:

```
> **Primer:** *PagedAttention* — A memory management technique for LLM inference
> that treats the KV cache as virtual memory pages. Enables serving many requests
> simultaneously without pre-allocating fixed memory per sequence.
```

Budget: max 30 tool calls across all sections. Primers enable continuation — they are not full definitions.

</details>

<details>
<summary><strong>S10 — Assemble + Build</strong></summary>

**Assembly order:** topological sort of domains by dependency depth in `graph.json`. Domains with more prerequisite edges appear later. Mechanical stitch:

```
frontmatter → abstract → TOC → domain sections (topology order) → synthesis → bibliography
```

**Bibliography format:** CSL-JSON, not BibTeX. Pandoc accepts CSL-JSON directly via `--bibliography`. No `.bib` file, no `bibtexparser` dependency, no format conversion. All per-domain `bibliography_{domain}.json` files are merged, deduplicated by DOI, and numbered in first-appearance order.

**Build pipeline:**
1. Merge and deduplicate bibliography JSONs
2. Preprocess (emoji, paths, trace table formatting)
3. `pandoc` md → tex with CSL-JSON bibliography
4. `xelatex` pass 1 — builds `.aux` file with forward reference placeholders
5. `xelatex` pass 2 — resolves TOC page numbers and cross-references (single-pass produces `??` throughout the TOC — two passes are mandatory)
6. `pypdf` integrity check — verifies page count and stream integrity. xelatex exits 0 on font errors while producing a partial/corrupt file; exit code alone cannot be trusted.

LangGraph writes the completion checkpoint **only after** the integrity check passes.

</details>

---

## Design principles

These principles are non-negotiable. Every architectural decision traces back to at least one.

### 1. Bounded execution for every LLM step

No single agent processes an unbounded input. A 40-page PDF fed to one agent guarantees silent omission of middle-document content. Every LLM step operates on a token-bounded input. Large inputs are chunked before agents see them.

*Consequence:* S2 is a Map-Reduce pipeline, not a single extraction agent.

### 2. Explicit canonicalization before classification

Never ask an LLM to dynamically cluster a large raw set in one shot. The result is overlapping, unprincipled categories. First lock the taxonomy; then classify against it. Two separate, sequential steps.

*Consequence:* S3 is two phases with a human (or LLM) checkpoint between them.

### 3. Independent verification vectors

A model auditing a graph it just generated is grading its own homework. External anchors — real papers, real field taxonomies, real APIs — must be pulled before the audit so the auditor has something to diff against that the generator has never seen.

*Consequence:* S4 fetches external anchors before any gap analysis runs.

### 4. Citations must be grounded before writing, not verified after

Agents without web access will hallucinate plausible-sounding papers. Verifying hallucinated titles against Crossref/Semantic Scholar yields 100% failure. The fix is preventing the hallucination by providing a bounded real bibliography before writing begins.

*Consequence:* S4 pre-fetches a real bibliography per domain. S5 agents are strictly constrained to cite only from that bibliography.

### 5. Domain-agnostic architecture

The system must work for CS, biology, law, economics, architecture. Any component hardcoded to a specific field's citation infrastructure (e.g., arXiv) is a domain assumption, not a design choice.

*Consequence:* Crossref REST API and Semantic Scholar are the global citation oracles. arXiv is a fallback for CS/Math/Physics only.

### 6. Synthesis must not consume narrative

At deep depth, 10 domain section files easily exceed 150k tokens. A synthesis agent fed raw narrative will truncate, lose thread, and hallucinate cross-domain connections. The fix is a tight JSON contract between domain research and synthesis.

*Consequence:* Each S5 agent emits both a narrative `.md` and a structured `summary_{domain}.json`. S7 consumes only the JSON summaries and the knowledge graph — never the raw sections.

### 7. Structural semantics over lexical frequency

A paper uses "Theorem" 80 times. It introduces its core mechanism exactly 4 times. Pure frequency-based centrality flags the boilerplate as core. Centrality must be grounded in document structure (abstract, headers, bold/italic), not just occurrence counts.

*Consequence:* Docling's `SECTION_HEADER`/`TITLE` labels drive structural zone extraction. Centrality fuses structural signals with frequency, filtered through a boilerplate stop-list.

### 8. Adversarial quality only where logical leaps occur

Critics at every step is redundancy theater — it burns tokens to resolve artificially introduced non-determinism in stages where a deterministic check or structural reconciliation is sufficient. Critics are applied only where logical leaps happen.

| Stage | Approach | Reason |
|-------|----------|--------|
| S2 Extract | No critic | Reduce reconciliation is the error-correction step |
| S3 Graph | No critic | Decision handled by human review or `--auto-taxonomy` |
| S4 Audit | Adversarial (Gap Finder / Defender) | Sharpest epistemic step; gaps must survive rebuttal |
| S5 Research | Adversarial | Logical leaps and citation discipline |
| S6 Align | LangGraph ReAct | Agent self-corrects against pedagogy checklist |
| S7 Synthesize | Adversarial | Cross-domain connections are the most hallucination-prone output |
| S8 Verify | No critic | Deterministic bibliography key lookup |
| S9 Prereq | LangGraph ReAct | Single pass; agent identifies and inserts primers autonomously |
| S10 Assemble | No critic | Mechanical stitching |

**Adversarial loop convergence:** loop exits when the critic's issue set is empty, identical to the prior round (stalled), or the critic explicitly accepts. Hard cap: 1/2/3 rounds for survey/standard/deep. Every round is persisted to disk before scoring — a crash at round 2 of 3 restarts from round 1, not round 0.

### 9. Complementary redundancy where strategy divergence is genuine

Two agents with structurally different strategies run in parallel where the strategies produce genuinely different coverage. Divergence between them is signal, not error. Running two LLMs with slightly different prompts over the same chunk is non-determinism theater.

*Consequence:* S5 runs Theoretician / Engineer / Practitioner — three genuinely distinct epistemic dimensions. S3 uses Lumper/Splitter. S2 uses spaCy + LLM + KeyBERT — three fundamentally different signal types.

### 10. Idempotent stages

If the pipeline crashes at S5 and `--resume 5` is run, it must not append to partially-written files from the crashed run. Each stage explicitly clears partial outputs before restarting. Running a stage twice produces the same result.

### 11. Human interaction permitted only at S1 and S3, and only when not bypassed

Exactly two human-facing interactions are permitted, both at the very beginning of the pipeline, both skippable via flags:

1. **S1 — User calibration** (skip with `--interactive` absent or `--user-profile`): 10–15 questions asked once. Calibrates every downstream stage.
2. **S3 — Taxonomy lock** (skip with `--auto-taxonomy`): review and approve the domain taxonomy before classification and research run.

Both are at the absolute start of the pipeline. All other stages are fully autonomous. Any `input()`, `subprocess.call([editor, ...])`, or `pause_for_review()` outside S1 and S3 is a design defect.

### 12. Pipeline state lives in the filesystem. Orchestrator context is ephemeral.

LangGraph completion sentinels and `pipeline.json` are the canonical state store. The orchestrating LLM's conversation history is not state — it is a transient view that may be compacted or lost across a multi-hour pause.

Every resume starts with `know-expand --status` emitting a `pipeline_status` event built from the filesystem, not from memory.

### 13. Distinguish failure modes precisely. Handle them differently.

| Failure | Cause | Correct response |
|---------|-------|-----------------|
| HTTP 429 + `Retry-After` | Transient rate limit | Sleep `Retry-After` seconds; retry same model |
| HTTP 429, no `Retry-After` | True quota exhaustion | Switch to next model in fallback list |
| All models exhausted | Full quota drain | Emit `quota_exhausted`; preserve progress; wait for `--resume` |
| xelatex exit 0, tiny PDF | Silent corruption | Explicit `pypdf` check; raise before checkpoint is written |
| Concurrent 429s from fan-out | Rate limit misread as exhaustion | Per-provider semaphore prevents the fan-out |

Conflating any two rows wastes the fallback list on recoverable errors or misses real failures.

### 14. The frontier is not optional.

Sorting bibliography by citation count descending is a recency tax: a 2017 survey with 4,000 citations outranks a 2025 breakthrough with 12. The 65% foundational / 35% frontier bibliography split is a design invariant. The frontier window (default: last 24 months) is configurable, but the existence of a frontier bucket is not.

---

## LLM routing

### QuotaAwareRouter (`agents/base.py`)

Tries models in order from `models.yaml` for the given role:

| Error | Action |
|-------|--------|
| `litellm.AuthenticationError` (may be wrapped in `InstructorRetryException`) | Permanently skip model; walk exception chain with `_find_in_chain()` |
| 429 + `Retry-After` header | Sleep exact duration; retry same model |
| Headerless 429/503 | Exponential backoff (2s → 4s → 8s); skip after 3 consecutive failures |
| geminicli `RuntimeError` | Permanently skip geminicli for this run; emit `model_geminicli_skip` |
| All models exhausted | Emit `quota_exhausted`; raise `RuntimeError` |

`_next_model()` checks both `self._skip` (per-router) and `_PROBED_UNAVAILABLE` (global) — a quota discovery in one coroutine immediately affects all other routers.

### geminicli provider

Spawns `npx @google/gemini-cli` as a subprocess. No API key — uses cached OAuth from `~/.gemini/oauth_creds.json`. Timeout: `cfg.timeouts.get("geminicli_timeout_s", 120)` — do not use `getattr()`, `Config.timeouts` is a `dict`.

`gemini/gemini-2.5-pro` (API) and `geminicli/gemini-2.5-pro` (OAuth CLI) are different providers.

### Instructor modes

`instructor.from_litellm()` defaults to `TOOLS` mode. Mistral and Groq return parallel tool calls for single-schema requests, which instructor rejects:

| Model | Mode | Reason |
|-------|------|--------|
| `mistral/*` | `instructor.Mode.JSON` | litellm works with JSON mode; `mistralai` SDK not required |
| `groq/*` | `instructor.Mode.JSON` | Groq Llama returns parallel tool calls in TOOLS mode |
| everything else | `instructor.Mode.TOOLS` | Default |

### Per-provider concurrency semaphores

Free-tier providers (Groq, Mistral) get `asyncio.Semaphore(3)`. All other models share the global semaphore (`cfg.concurrency.default = 8`). Cloud APIs have concurrent request limits too — 16 simultaneous Opus requests will hit Anthropic's ceiling and generate 429s that the `QuotaAwareRouter` will misread as quota exhaustion without a semaphore.

### `probe_models()` at startup

Checks env vars and binary presence only (no LLM calls). Adds unavailable models to `_PROBED_UNAVAILABLE`. Local prefixes (`llamacpp/`, `ollama/`, `lm_studio/`, `local/`) are always skipped here.

### Model usage audit log

Every LLM call is logged to `state/audit/model_usage.jsonl`:
```jsonl
{"ts": "...", "stage": 5, "domain": "inference_systems", "role": "researcher", "model": "claude-opus-4-7", "input_tokens": 8420, "output_tokens": 3100, "cost_usd": 0.041}
{"ts": "...", "stage": 5, "domain": "hardware_arch", "role": "researcher", "model": "claude-sonnet-4-6", "cost_usd": 0.009, "switched_from": "claude-opus-4-7", "switch_reason": "quota_exceeded"}
```

If a model switch occurred, `pipeline.json` carries `"model_consistency": "mixed"` and the document frontmatter records which domains were written by which model.

---

## Idempotency and resumability

Every stage is fully idempotent. Running it twice produces the same result. Running it after a crash produces the same result as running it on clean state.

**Completion sentinels:**
```
runs/{id}/state/
├── pipeline.json                  ← per-stage status + timestamps
├── chunks/chunk_{N:04d}.done      ← S0: sentinel per chunk
├── map_outputs/map_{ab}_{id}.done ← S2: sentinel per map output
├── sections/section_{domain}.done ← S5: sentinel per domain
├── sections/section_{domain}.aligned ← S6: sentinel per domain
└── summaries/summary_{domain}.done   ← S5: sentinel per domain
```

For parallel stages (S2 Map, S5 Research), each unit is independently idempotent. A S5 crash that completed 5 of 8 domains only re-runs the 3 incomplete ones on `--resume 5`.

Before re-running any unit, partial output files are deleted — not overwritten. This prevents corrupted partial writes from a previous crash from being merged with new output.

Each domain task in S5 is wrapped with `asyncio.timeout(2700)`. A domain agent stuck in API backoff emits a clean `domain_timeout` event, allowing the rest of the group to finish so the stage can be resumed.

---

## Deployment

### Local run (no Docker)

```bash
# Install dependencies
uv sync

# Set API keys
cp .env.example .env && $EDITOR .env

# Run on a paper
uv run know-expand paper.pdf --depth standard

# Monitor in browser
uv run know-expand serve
```

### Docker Compose

```yaml
services:
  app:
    build: .
    volumes:
      - ./runs:/app/runs
      - ./input:/app/input
    environment:
      - ANTHROPIC_API_KEY
      - OPENAI_API_KEY
    command: ["know-expand", "/app/input/source.pdf", "--depth", "standard"]

  ollama:
    image: ollama/ollama:latest
    profiles: ["local"]
    ports: ["11434:11434"]
    volumes: [ollama_data:/root/.ollama]
    deploy:
      resources:
        reservations:
          devices: [{driver: nvidia, capabilities: [gpu], count: all}]

volumes:
  ollama_data:
```

```bash
# Cloud LLMs
ANTHROPIC_API_KEY=sk-... docker compose up

# Fully local (no API keys)
docker compose --profile local up
docker compose exec ollama ollama pull qwen2.5:14b
```

### Fully local mode

With `--profile local`, no API keys are required. Semantic Scholar, Crossref, and OpenAlex calls still require internet for bibliography fetch.

**Recommended local models by role:**

| Role | Recommended | Minimum |
|------|-------------|---------|
| Researcher / Synthesizer | `qwen2.5:14b` | `llama3.2:3b` |
| Extractor / Classifier / Critic | `llama3.2:3b` | `llama3.2:1b` |

Expected quality at local depth: roughly `survey` regardless of `--depth` flag. Structure, citations, and adversarial audit are all intact; narrative depth and mathematical derivations are shallower.

Local concurrency: Stage 5 domain agents are serialized via `asyncio.Semaphore(1)` by default when Ollama is the provider. Increase with `--local-concurrency 2` only on workstations with ≥32GB VRAM.

### Observability

- **`know-expand serve`** — self-contained HTTP dashboard at `:7842`. Left stage rail, full-width detail pane, run drawer, interview Q&A IPC, taxonomy review IPC. All events from `runs/{run_id}/events.jsonl`. Zero external dependencies.
- **`know-expand tail`** — streams `events.jsonl` to stdout. Pipe-friendly, greppable.
- **`state/audit/model_usage.jsonl`** — per-call cost and model provenance log. `cat ... | jq` answers every debug question.
- **LangSmith** (multi-run production traces) — native LangGraph integration activated by `LANGCHAIN_API_KEY` + `LANGCHAIN_TRACING_V2=true`. No code changes required.

---

## Technology stack

### Pipeline components

| Stage | Component | Tool | Why |
|-------|-----------|------|-----|
| S0 | Document parsing + structural zones | `Docling` (IBM, MIT) | DocLayNet layout model; `SECTION_HEADER`/`TITLE` labels; `HybridChunker`; degenerate-parse guard |
| S0 | URL fetch | `httpx` | Async, timeout |
| S2 | Lexical extraction | `spaCy` | Deterministic NER + noun chunks + regex; zero LLM cost |
| S2 | Conceptual extraction | LLM via LiteLLM | One call per chunk; captures implicit domain knowledge |
| S2 | Semantic extraction | `KeyBERT` + `BGE-M3` | Embedding keyphrase centrality; non-LLM; strong divergence signal |
| S2 | Term deduplication | numpy cosine + Union-Find | BGE-M3 cosine finds near-duplicates; Union-Find clusters aliases in ~30 stdlib lines |
| S3 | Taxonomy validation | OpenAlex concept API | ~65k hierarchical concepts; L0–L5 depth validates lumper/splitter proposals |
| S4 | Bibliography fetch | Semantic Scholar + Crossref | Two-bucket (65% foundational / 35% frontier). Anchor-neighbor expansion via `/citations`. |
| S4 | API rate limiting | `aiolimiter` (MIT) | Async token bucket per API; SS circuit breaker on burst 429s |
| S4/S5 | Adversarial loops | `litellm` + `instructor` | Generator→Critic via `litellm.acompletion` with Pydantic `CritiqueResult`; round files prevent crash rework |
| S6/S9 | Agentic alignment | LangGraph `StateGraph` + `ToolNode` | ReAct graphs with `@tool` async closures; context trimmed to last 20 messages per turn |
| All | LLM routing | `LiteLLM` + `QuotaAwareRouter` | Provider-agnostic fallback chains; `Retry-After` header distinguishes burst vs. exhaustion |
| All | Pipeline orchestration | `LangGraph` | Stateful graph execution; fan-out/fan-in via `Send` API; resume via sentinel files |
| S7 | Output deduplication | Trigram Jaccard (stdlib) | Exact-deduplicates `**Primer:**` lines; Jaccard threshold 0.85 on blocks >150 chars |
| S10 | PDF build (default) | `pandoc` + `xelatex` ×2 | Complex math support; proven pipeline |
| S10 | PDF integrity check | `pypdf` | Verifies page count and stream integrity after xelatex |
| S10 | PDF build (optional) | `Typst` v0.14+ | ~27× faster than xelatex; no TeX dependency; `--renderer typst` |

### Infrastructure and utilities

| Category | Tool | Notes |
|----------|------|-------|
| LLM routing | `LiteLLM` | Single interface for 100+ providers; swap model via config, no code changes |
| Local LLM inference | `Ollama` | `--profile local`; no API keys required |
| Structured outputs | `Instructor` + `Pydantic` v2 | Schema validation + auto-retry; provider-agnostic |
| NLP pipeline | `spaCy` (en_core_web_sm) | S2 Signal A; deterministic; zero LLM cost |
| API rate limiting | `aiolimiter` (MIT) | One `AsyncLimiter` per external API |
| Async orchestration | `asyncio.TaskGroup()` (Python 3.11+) | Native structured concurrency; no anyio backend mismatch |
| Response caching | `DiskCache` | SQLite-backed for deterministic API calls; faster than Redis in local scenarios |
| Graph storage | `NetworkX` + `graph.json` | In-memory ops: topological sort, DAG traversal. Sufficient at ≤200 nodes. |
| HTTP client | `httpx` | Async; timeout support |

### What not to use

| Tool | Why not |
|------|---------|
| `Kuzu` (embedded graph DB) | Archived October 2025 |
| `DuckDB` + DuckPGQ | OLAP engine at 100-node graph scale; NetworkX covers all required ops |
| `FAISS` | ANN index for billion-scale search; numpy brute-force cosine over ≤300 terms is faster with zero dependencies |
| Direct `anthropic` SDK | Locks pipeline to one provider |
| `LangChain` | Heavy abstraction; LangGraph is sufficient and lighter |
| `AutoGen` / `CrewAI` | Alternative multi-agent frameworks; LangGraph + Anthropic SDK is cleaner |
| `pdfplumber` as primary parser | Docling's `SECTION_HEADER`/`TITLE` labels are a better structural signal |
| `Redis` | Requires external process; DiskCache is sufficient |
| `Ray` / `Celery` | Distributed overkill; `asyncio.TaskGroup()` handles CLI parallelism |
| `anyio` | Mixing `anyio.create_task_group()` with `asyncio.Semaphore` causes cancellation propagation mismatches under 429 backoff |
| `ChromaDB` / `Qdrant` | Vector DB persistence overhead; numpy cosine is sufficient for transient deduplication |
| `marker-pdf` | GPL license constraint |
| Phoenix/Langfuse as Docker service | `know-expand serve` is the built-in observability surface; LangSmith handles multi-run traces via env vars only |

---

## Anti-patterns

The following patterns are explicitly excluded from this codebase. Each is a documented footgun from production experience or prior art analysis.

**Extraction and classification:**
- **Single-pass extraction of a full document:** guaranteed silent omission. Always chunk first.
- **Dynamic LLM clustering without taxonomy lock:** produces overlapping domains and duplicated downstream research.
- **Pure frequency-based centrality:** boilerplate outranks novel mechanisms.
- **Running two LLM agents for lexical extraction:** named entities and noun phrases are deterministic NLP tasks. spaCy handles them in milliseconds with no API call.
- **Unconditional structural promotion:** `if name in structural_zones: return "core"` floods the core tier with bolded dataset names and italicized variables. Structural presence is a weight multiplier (`effective = occurrences × 2.0`), not a frequency bypass.

**Citations and bibliography:**
- **Hallucinate-then-verify citation flow:** verification of hallucinated titles fails 100%. Ground citations before writing.
- **arXiv as primary citation oracle:** breaks for non-CS domains.
- **Bibliography sorted by citation count only:** guarantees blindness to the last 24 months. Use two buckets: 65% foundational + 35% frontier.
- **Generating a `.bib` file for bibliography:** Pandoc accepts CSL-JSON natively via `--bibliography`. No BibTeX intermediate, no `bibtexparser` dependency.
- **Cold self-audit without external anchors:** the model grades its own homework and rubber-stamps the graph.

**LLM routing and reliability:**
- **Single model per role with no fallback:** quota exhaustion kills the run. Always define a priority fallback list.
- **Treating all HTTP 429s as quota exhaustion:** 429 + `Retry-After` = transient rate limit; sleep and retry the same model. 429 without `Retry-After` = true exhaustion; switch model.
- **Treating quota exhaustion as a fatal error:** it is a pause event. Emit, preserve progress via sentinels, wait for `--resume`.
- **Applying semaphore only to local inference:** cloud APIs have concurrent request limits too. 16 simultaneous Opus calls will hit Anthropic's ceiling and generate 429s that drain the fallback list.
- **Fan-out parallel inference against local Ollama without a semaphore:** concurrent requests against a single-GPU instance spike memory and trigger thermal throttling.
- **Trusting xelatex exit code 0 as proof of a valid PDF:** xelatex exits 0 on font errors while producing a partial/corrupt file. Always verify with `pypdf` before the S10 node returns.

**Pipeline state and resumability:**
- **Storing orchestrating LLM context as pipeline state:** conversation history is ephemeral. `pipeline.json` and `.done` sentinels are the canonical state store.
- **Silent resume after partial crash:** appending to partial files corrupts structure. Always delete before rewriting.
- **Overwriting partial outputs on resume:** silently merges crashed writes with new output. Use `path.unlink(missing_ok=True)` before rewriting.
- **Leaving parallel domain tasks without a hard timeout:** a domain agent stuck in backoff idles the entire `asyncio.TaskGroup` indefinitely at 99% completion.
- **Skipping `pipeline.json` status tracking:** without it, `--resume` cannot know which stages are truly complete vs. partially written.

**Architecture and coupling:**
- **Raw narrative `.md` sections as synthesis input:** context window explosion at deep depth. Use `summary_{domain}.json`.
- **Implicit flag coupling:** flags must never silently activate or suppress each other. `--auto-taxonomy` does not silently activate `--skip-assessment`.
- **Human review checkpoints beyond S3:** the pipeline is fully autonomous from S4 onward. Any `input()` or `subprocess.call([editor, ...])` outside S1 and S3 is a design defect.
- **Mixing anyio task groups with asyncio synchronization primitives:** use native `asyncio.TaskGroup()` (Python 3.11+) throughout.
- **Using a graph library for alias clustering of ≤300 terms:** NetworkX for disjoint-set merging pulls in Dijkstra's algorithm to do a dictionary merge.
- **Using a vector DB for single-document term deduplication:** FAISS, ChromaDB, Qdrant — all overkill for ≤300 terms.
- **Running two PDF parsers over the same file:** double ingestion time and memory spike with alignment issues.
- **Single xelatex pass:** TOC full of `??`. Always two passes.
- **Calling `mark_taxonomy_approved` unconditionally after editor exit:** the user may have quit without saving. Always re-read the file and verify `"status": "approved"` before proceeding.
- **Generic assessment questions that don't use structural_zones:** Q3, Q6, and Q7 must be tailored to the document's actual vocabulary.

---

## State directory layout

```
runs/{run_id}/
├── state/
│   ├── pipeline.json                           Run state: per-stage status + timestamps
│   ├── source.txt                              S0 output
│   ├── source_meta.json
│   ├── structural_zones.json                   Docling SECTION_HEADER/TITLE extraction
│   ├── user_profile.json                       S1 output + sentinel
│   ├── chunks/
│   │   ├── chunk_{N:04d}.json
│   │   └── chunk_{N:04d}.done                  idempotency sentinel per chunk
│   ├── map_outputs/
│   │   ├── map_a_{chunk_id}.json               S2 Map Signal A+C
│   │   ├── map_a_{chunk_id}.done
│   │   ├── map_b_{chunk_id}.json               S2 Map Signal B
│   │   └── map_b_{chunk_id}.done
│   ├── terms.json                              S2 Reduce output
│   ├── audit/
│   │   ├── taxonomy_a.json                     S3 Phase 1: Lumper proposal
│   │   ├── taxonomy_b.json                     S3 Phase 1: Splitter proposal
│   │   ├── classification_conflicts.json       S3 Phase 2: divergences → S4 gap candidates
│   │   ├── anchors_{domain_id}.json            S4 gap diff anchors
│   │   ├── bibliography_{domain_id}.json       S4 citation pool (only S5 may cite from this)
│   │   ├── sources/sources_{domain_id}.json    S4 multi-source knowledge cache
│   │   ├── gap_analysis.md                     S4 output
│   │   ├── corrections.md                      S4 output
│   │   ├── needs_citation.md                   S8 output
│   │   ├── model_usage.jsonl                   Per-call: model, tokens, cost, switches
│   │   └── critique_{stage}_{id}.md            Adversarial loop logs
│   ├── taxonomy.json                           Approved locked taxonomy
│   ├── graph.json                              S3 knowledge graph
│   ├── sections/
│   │   ├── section_{domain_id}.md              S5 narrative; patched by S6 + S9
│   │   ├── section_{domain_id}.done            S5 completion sentinel
│   │   ├── section_{domain_id}.aligned         S6 completion sentinel
│   │   └── section_synthesis.md               S7 output
│   └── summaries/
│       ├── summary_{domain_id}.json            S5 → S7 JSON contract
│       └── summary_{domain_id}.done
├── logs/
│   └── events.jsonl                            All pipeline events (streamed to dashboard)
└── output/
    ├── expanded.md
    └── expanded.pdf
```

---

## Key files

```
know_expand/
  cli.py          — entry point; SIGTERM handler emits run_stopped then sys.exit(0)
  pipeline.py     — LangGraph StateGraph; writes run_id + input_path to pipeline.json at startup
  state.py        — emit(), mark_stage_complete(), PipelineState TypedDict, log setup
  config.py       — Config dataclass; timeouts is a dict (use cfg.timeouts.get(), not getattr)
  observe.py      — self-contained web server + HTML/CSS/JS dashboard (~2700 lines)
  sources.py      — multi-source knowledge fetcher: Wikipedia REST / PyPI JSON / arXiv Atom
  agents/
    base.py       — QuotaAwareRouter, geminicli provider, _instructor_mode(), probe_models()
    schemas.py    — all Pydantic models for LLM structured outputs
  stages/
    s*.py         — one file per stage

config.yaml       — all tunable knobs
models.yaml       — model lists per role
runs/             — one subdir per run: state/ logs/ output/
style/
  preprocess.py   — emoji normalization, path rewriting, trace table formatting
  llm_paper_style.tex — XeLaTeX style sheet
```

**Important constraints for contributors:**
- S2 Extract runs before S1 Assess — do not swap them back
- `observe.py` Python code is always below the HTML template string — never mix them
- SIGTERM handler in `cli.py` must call `emit({event: run_stopped})` then `sys.exit(0)`
- `signal.signal(SIGCHLD, SIG_IGN)` in `cmd_serve` is paired with `preexec_fn=lambda: signal.signal(SIGCHLD, SIG_DFL)` in `_spawn_pipeline()` — both are required
- 65% foundational / 35% frontier bibliography split — non-negotiable
- Two xelatex passes — always
- `[NEEDS_CITATION]` is logged, never a build failure
- `gemini/gemini-2.5-pro` and `geminicli/gemini-2.5-pro` are different providers
