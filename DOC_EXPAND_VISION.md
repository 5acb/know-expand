# doc-expand — Vision, Architecture & Plan

> **Status:** Pre-implementation design document. Derived from the LLM Knowledge Graph project (this repository) as the reference implementation.
> **Future home:** This file becomes `CLAUDE.md` in the new `doc-expand/` project directory.

---

## Vision

A person reads a dense technical paper. They understand the words but not the field. They cannot ask good questions, cannot evaluate the claims, cannot know what to read next, cannot build on top of it.

`doc-expand` takes that document and produces a **navigable, ground-up knowledge expansion** — a structured field guide that covers everything needed to understand the paper deeply, go beyond it, and do independent research. It is not a summary. It is not a chatbot answer. It is a research-publication-grade document that treats the reader as a peer who needs the full picture, not a simplified one.

The LLM Knowledge Graph project in this repository is the proof of concept: given a structured knowledge graph of the LLM lifecycle, a multi-agent pipeline produced a 480KB, 241-page, 81-node skill-tree document with verified citations, LaTeX mathematics, and technical diagrams. `doc-expand` generalizes and automates that entire pipeline, starting from a raw document instead of a pre-built graph.

---

## Mission

**Given any technical document, automatically produce a research-publication-grade knowledge expansion that:**

- Identifies every substantive concept in the document
- Maps those concepts into a principled domain taxonomy
- Augments each domain from first principles to the current research frontier
- Synthesizes cross-domain insights invisible within any single domain
- Grounds every claim in verifiable, real citations
- Structures the output as a navigable skill tree so readers can trace concepts up, down, and sideways

**Not a summarizer. Not a Q&A system. Not a chatbot wrapper.**
A full-stack knowledge construction pipeline.

---

## What This Is / What This Is Not

### What it is
- A CLI multi-agent application
- A pipeline that reads one document and writes one expanded knowledge document (Markdown + PDF)
- Domain-agnostic: works for an ML paper, a biology textbook, a legal framework, an architecture RFC
- Adversarial by design: every LLM step has a critic and an external anchor
- Auditable: every decision is logged with the critique that validated it
- Restartable: every stage writes artifacts to disk before exiting; `--resume N` reruns from stage N

### What it is not
- Not a search engine or retrieval system
- Not a real-time or interactive system
- Not a summarizer or abstract generator
- Not a chatbot or conversational interface
- Not a Claude Code skill (it started as that idea; the scope demands a standalone app)
- Not dependent on a pre-built knowledge graph (the LLM project required one; this builds its own)

---

## Design Philosophy

The meta-level rules governing every decision: what tools to pick, when to hand-roll vs. when to use a library, how to handle failure, and what "good engineering" means for this project. These are the reasons behind the Core Design Principles that follow.

### Use solid existing tools. Hand-roll only what has no meaningful equivalent.

When a maintained, MIT-licensed library solves a problem well, use it — `aiolimiter` for async token-bucket rate limiting, `Docling` for PDF parsing, `LangGraph` for stateful pipeline orchestration. Don't reinvent these.

But "use existing tools" is not a blank check for dependency bloat. When the library is overkill for the *actual* problem scale, the stdlib implementation is correct: NetworkX for 300-item alias clustering is pulling in Dijkstra's algorithm to do a dictionary merge. The test: does this library meaningfully improve on a simple implementation at *our* actual scale? If not, don't add it.

The anti-pattern in both directions: hand-rolling a token-bucket rate limiter when `aiolimiter` exists is "poor man's SQLite." Importing NetworkX for Union-Find over 300 terms is cargo-culted framework use. Both are wrong.

### Latest and greatest. No backward-compatibility theater.

The pipeline targets Python 3.11+, current LangGraph, current Docling, current DSPy. Deprecated APIs (`dspy.Assert`, old Crossref query params) are excluded by name. No compatibility shims, no legacy fallbacks, no `if sys.version_info < (3, 11)` branches.

We write code for the present. The moment an API is deprecated, the design document calls it out and names the replacement.

### Pick one; use it consistently.

When two tools solve the same class of problem, pick one and apply it everywhere in the pipeline.

- **One PDF parser:** Docling. Not Docling + PyMuPDF. Running two heavy parsers over the same file doubles ingestion time, spikes memory, and introduces alignment issues between their outputs.
- **One concurrency model:** `asyncio`. Not `asyncio` + `anyio`. Mixing backends causes cancellation propagation mismatches and silent deadlocks under backoff pressure.
- **One graph model:** NetworkX for runtime ops, `graph.json` for persistence. Not DuckDB+PGQ in addition.

Consolidation eliminates alignment races, context mismatches, and debug complexity. When you have two of something, you have two sources of truth.

### Explicit over implicit. Loud over silent.

Flags are never coupled. `--auto-taxonomy` does not silently activate `--skip-assessment`. Every flag must be passed explicitly; no flag infers or suppresses another. If an orchestrating LLM forgets a required flag and runs without a TTY, it gets an `EOFError` immediately — a loud, clear crash — not a silent stage skip discovered three hours later when the output is wrong.

Error conditions are never swallowed. A corrupted PDF (xelatex exit 0, 12KB output) is caught by an explicit size check before the LangGraph node returns. A transient rate limit is caught by checking `Retry-After` before switching models. Precision in error handling prevents both unnecessary model degradation and false completion signals.

### Infrastructure proportional to actual problem scale.

This pipeline produces 81–200 graph nodes, ≤300 terms, ~8–12 domains. Infrastructure choices are calibrated to *this* scale, not a hypothetical future scale where we have millions of nodes.

- 81 nodes: `NetworkX` in memory, `graph.json` on disk. Not DuckDB + DuckPGQ.
- 300 terms: numpy cosine similarity matrix. Not FAISS ANN indexing.
- Single-document deduplication: Union-Find (30 stdlib lines). Not a graph framework.
- Single-run observability: flat JSONL log. Not a Docker telemetry stack.

The corollary: when the problem genuinely scales (8 concurrent domain requests to cloud APIs), the infrastructure scales with it (per-provider semaphores, per-API rate limiters). Calibration in both directions.

### Pipeline state lives in the filesystem. Orchestrator context is ephemeral.

LangGraph checkpoints, `.done` sentinels, and `pipeline.json` are the canonical state store. The orchestrating LLM's conversation history is not state — it is a transient view of state that may be compacted, truncated, or lost entirely across a multi-hour pause.

Every resume starts with `doc-expand --status` emitting a `pipeline_status` event built from the filesystem, not from memory. The orchestrating LLM always reads current ground truth; it never relies on remembering what happened earlier in the conversation.

### Distinguish failure modes precisely. Handle them differently.

Imprecise failure handling causes cascading damage:

| Failure | Cause | Correct response |
|---|---|---|
| HTTP 429 + `Retry-After` | Transient rate limit | Sleep `Retry-After` seconds; retry same model |
| HTTP 429, no `Retry-After` | True quota exhaustion | Switch to next model in fallback list |
| All models exhausted | Full quota drain | Emit `quota_exhausted`; preserve progress; wait for `--resume` |
| xelatex exit 0, tiny PDF | Silent corruption | Explicit size check; raise before checkpoint is written |
| SIGKILL mid-subprocess | Process death | LangGraph writes no completion checkpoint; Stage 7 reruns on resume |
| Concurrent 429s from fan-out | Rate limit misread as quota exhaustion | Cloud semaphore prevents the fan-out; limiters absorb the burst |

Conflating any two rows wastes the fallback list on recoverable errors or misses real failures. The handling table above is fixed, not ad hoc.

### The frontier is not optional.

A pipeline that claims to produce a "research frontier" expansion must structurally include recent work. Sorting bibliography by citation count descending is a recency tax: a 2017 survey with 4,000 citations outranks a 2025 breakthrough with 12. This is not a tuning choice — it is a systematic exclusion of the thing we claim to deliver.

The 65% foundational / 35% frontier bibliography split is a design invariant. The frontier window (default: last 24 months) is configurable, but the existence of a frontier bucket is not.

---

## Core Design Principles

These principles are non-negotiable. Every architectural decision traces back to at least one of them. They are the *specific* consequences of the Design Philosophy above.

### 1. Bounded execution for every LLM step
No single agent processes an unbounded input. A 40-page PDF fed to one agent guarantees silent omission of middle-document content ("lost in the middle"). Every LLM step operates on a token-bounded input. Large inputs are chunked before agents see them.

**Consequence:** Stage 1 is a Map-Reduce pipeline, not a single extraction agent.

### 2. Explicit canonicalization before classification
Never ask an LLM to dynamically cluster a large raw set in one shot. The result is overlapping, unprincipled categories. First lock the taxonomy; then classify against it. Two separate, sequential steps.

**Consequence:** Stage 2 is two phases with a human checkpoint between them.

### 3. Independent verification vectors
A model auditing a graph it just generated is grading its own homework. External anchors — real papers, real field taxonomies, real APIs — must be pulled before the audit so the auditor has something to diff against that the generator has never seen.

**Consequence:** Stage 3 fetches external anchors before any gap analysis runs.

### 4. Citations must be grounded before writing, not verified after
Agents without web access will hallucinate plausible-sounding papers. Verifying hallucinated titles against Crossref/Semantic Scholar yields 100% failure. The fix is not better verification — it is preventing the hallucination by providing a bounded real bibliography before writing begins.

**Consequence:** Stage 3 pre-fetches a real bibliography per domain. Stage 4 agents are strictly constrained to cite only from that bibliography.

### 5. Domain-agnostic architecture
The system must work for CS, biology, law, economics, architecture. Any component hardcoded to a specific field's citation infrastructure (e.g., arXiv) is a domain assumption, not a design choice.

**Consequence:** Crossref REST API and Semantic Scholar Graph API are the global citation oracles. arXiv is a fallback for CS/Math/Physics only.

### 6. Synthesis must not consume narrative
At "deep" depth, 10 domain section files easily exceed 150k tokens. A synthesis agent fed raw narrative will truncate, lose thread, and hallucinate cross-domain connections. The fix is a tight JSON contract between domain research and synthesis.

**Consequence:** Each Stage 4 agent emits both a narrative `.md` and a structured `summary_{domain}.json`. Stage 5 consumes only the JSON summaries and the knowledge graph — never the raw sections.

### 7. Structural semantics over lexical frequency
A paper uses "Theorem" 80 times. It introduces its core mechanism "Speculative Chunking" exactly 4 times. Pure frequency-based centrality flags the boilerplate as core and demotes the science. Centrality must be grounded in document structure (abstract, headers, bold/italic), not just occurrence counts.

**Consequence:** Docling's `SECTION_HEADER`/`TITLE` labels drive structural zone extraction before chunking. Centrality fuses structural signals with frequency, filtered through a boilerplate stop-list.

### 8. Adversarial quality where logical leaps occur
A single agent producing an output with no challenge has no error-correction mechanism. But critics at every step is redundancy theater — it burns tokens to resolve artificially introduced non-determinism in stages where a deterministic check or structural reconciliation is sufficient. Critics are applied only where logical leaps happen: Stage 3 (gap analysis), Stage 4 (domain research), Stage 5 (cross-domain synthesis). Stages 1 and 2 use structural reconciliation instead.

**Consequence:** Adversarial loops with 1/2/3 rounds (survey/standard/deep) in Stages 3, 4, and 5 only.

### 9. Complementary redundancy where strategy divergence is genuine
Two agents with structurally different strategies (top-down vs. bottom-up, lexical vs. conceptual) run in parallel where the strategies actually produce different coverage. Divergence between them is signal, not error. Running two LLMs with slightly different prompts over the same chunk is non-determinism theater — it does not yield higher truth.

**Consequence:** Complementary agents in Stages 2 (Lumper/Splitter), 4 (top-down/bottom-up), and 5 (structural/semantic). Stage 1 uses spaCy (deterministic) + LLM (conceptual) + KeyBERT (embedding) — three genuinely different signal types, not two LLMs.

### 10. Idempotent stages
If the pipeline crashes at Stage 4 and `--resume 4` is run, it must not silently append to partially-written files from the crashed run. Each stage that writes parallel outputs explicitly clears those outputs before restarting.

**Consequence:** Every stage that writes parallel outputs calls `path.unlink(missing_ok=True)` on resume before launching agents.

### 11. Human interaction permitted only at Stage 0.5 and Stage 2, and only when not bypassed
The pipeline runs autonomously from start to finish. Exactly two human-facing interactions are permitted, both at the very beginning of the pipeline, and both are skippable via flags:

1. **Stage 0.5 — Calibration questions** (bypassable with `--skip-assessment` or `--user-profile`): 7 questions asked once, before any research runs. Costs ~2 minutes; calibrates every downstream stage. Automatically skipped in LLM-orchestrated runs.

2. **Stage 2 — Taxonomy lock** (bypassable with `--auto-taxonomy`): the orchestrating LLM or the human reviews and approves the domain taxonomy before classification and research run. The highest-consequence single decision in the pipeline.

Both interactions are at the absolute start of the pipeline. Neither one blocks research, writing, or building — they inform them. All other stages — EXTRACT, AUDIT, RESEARCH, SYNTHESIZE, VERIFY, ASSEMBLE — are fully autonomous. Any `input()`, `subprocess.call([editor, ...])`, or `pause_for_review()` call outside Stage 0.5 and Stage 2 is a design defect.

**Consequence:** Two and only two `subprocess.call([editor, ...])` paths exist in the codebase: Stage 0.5 (assessment presentation, gated behind `if not skip_assessment`) and Stage 2 (taxonomy review, gated behind `if not auto_taxonomy`). No other stage may block on human input under any conditions.

---

## Architecture

### Pipeline Overview

```
Input Document (text / file / URL / PDF)
        │
        ▼
┌───────────────────────────────────────────────────────┐
│ Stage 0: INGEST                                       │
│ Normalize → chunk with overlap → structural zones     │
└───────────────────────────┬───────────────────────────┘
                            │  state/chunks/*.json
                            │  state/structural_zones.json
                            ▼
┌───────────────────────────────────────────────────────┐
│ Stage 0.5: ASSESS (User Calibration)                  │
│ 7 questions tailored to document vocabulary           │
│ → UserProfile (depth, math mode, known concepts)      │
│ ← Human interaction #1 (earliest; bypassable)         │
└───────────────────────────┬───────────────────────────┘
                            │  state/user_profile.json
                            ▼
┌───────────────────────────────────────────────────────┐
│ Stage 1: EXTRACT (Map-Reduce)                         │
│ Map: parallel agents per chunk (lexical + conceptual) │
│ Reduce: merge, deduplicate, compute centrality        │
└───────────────────────────┬───────────────────────────┘
                            │  state/terms.json
                            ▼
┌───────────────────────────────────────────────────────┐
│ Stage 2: GRAPH (Two-Phase Lock)                       │
│ Phase 1: taxonomy proposals (lumper + splitter)       │
│          → $EDITOR review OR --auto-taxonomy (LLM)    │
│          ← ONLY human checkpoint in pipeline          │
│ Phase 2: term classification against locked taxonomy  │
└───────────────────────────┬───────────────────────────┘
                            │  state/taxonomy.json
                            │  state/graph.json
                            ▼
┌───────────────────────────────────────────────────────┐
│ Stage 3: AUDIT (Anchored)                             │
│ Fetch anchors (3 papers) + bibliography (10/30/50)    │
│ Gap finder vs. Defender adversarial loop              │
└───────────────────────────┬───────────────────────────┘
                            │  state/audit/gap_analysis.md
                            │  state/audit/corrections.md
                            │  state/audit/bibliography_{domain}.json
                            ▼
┌───────────────────────────────────────────────────────┐
│ Stage 4: RESEARCH (Parallel per domain)               │
│ Top-down + bottom-up agents per domain                │
│ Adversarial critic loop                               │
│ Constrained to bibliography JSON for citations        │
└───────────────────────────┬───────────────────────────┘
                            │  state/sections/section_{domain}.md
                            │  state/summaries/summary_{domain}.json
                            ▼
┌───────────────────────────────────────────────────────┐
│ Stage 5: SYNTHESIZE                                   │
│ Inputs: graph.json + summary_*.json ONLY              │
│ Structural agent + Semantic agent                     │
│ Adversarial critic loop                               │
└───────────────────────────┬───────────────────────────┘
                            │  state/sections/section_synthesis.md
                            ▼
┌───────────────────────────────────────────────────────┐
│ Stage 6: VERIFY                                       │
│ Structural citation audit (keys vs bibliography)      │
│ Crossref → Semantic Scholar → arXiv (CS/Math only)    │
└───────────────────────────┬───────────────────────────┘
                            │  state/audit/needs_citation.md
                            ▼
┌───────────────────────────────────────────────────────┐
│ Stage 7: ASSEMBLE + BUILD                             │
│ Topological sort → stitch → preprocess → xelatex ×2  │
└───────────────────────────┬───────────────────────────┘
                            │  output/expanded.md
                            │  output/expanded.pdf
```

---

### Adversarial Loop Pattern (applied to all LLM steps)

```
Generator(prompt, context) → output_v1
                                  │
Critic(output_v1, external_truth) → critique_1
                                  │
Generator(prompt, context, critique_1) → output_v2
                                  │
         ... up to max_rounds[depth]
                                  │
External arbiter applies hard veto
                                  │
                             final_output
```

**Round scaling:**
- `survey`: 1 round (no iteration, single pass)
- `standard`: 2 rounds max
- `deep`: 3 rounds max

**Early exit:** if Critic produces zero new challenges in structured output, break before `max_rounds`.

**Critique log:** every adversarial loop writes to `state/audit/critique_{stage}_{id}.md` — full provenance of what was challenged and what survived.

**Critical constraint:** Critic has access to `external_truth`. Generator does not. This prevents the Generator from pre-emptively aligning to the anchor before being challenged.

**Which stages use adversarial critics:**

| Stage | Critic? | Reason |
|---|---|---|
| Stage 0.5 — Assess | No | Deterministic question generation |
| Stage 1 — Extract | No | Reduce reconciliation is the error-correction step; per-chunk critics add cost without signal |
| Stage 2 — Graph | No | Taxonomy decision handled by human review or `--auto-taxonomy`; critic loop would be redundant |
| Stage 3 — Audit | **Yes** | Gap Finder vs. Defender — the sharpest epistemic step; gaps must survive rebuttal |
| Stage 4 — Research | **Yes** | Logical leaps and citation discipline; top-down and bottom-up agents produce divergent claims |
| Stage 5 — Synthesize | **Yes** | Cross-domain connections are the most hallucination-prone output in the pipeline |
| Stage 6 — Verify | No | Structural audit; deterministic bibliography key lookup |
| Stage 7 — Assemble | No | Mechanical stitching |

**Implementation — DSPy `dspy.Refine`:**

Adversarial loops use `dspy.Refine(module, reward_fn, threshold, N)`. The `reward_fn` wraps the Critic agent plus its external truth anchor; `N` is the depth-scaled max rounds (1/2/3 for survey/standard/deep). DSPy handles the Generator→Critic→Generator cycle, early exit when the reward exceeds threshold, and structured Signature definitions for both agents.

**Intermediate round checkpointing:** DSPy's loop runs inside a single LangGraph node. LangGraph cannot checkpoint intermediate rounds — it only checkpoints at node boundaries. A crash at round 2 of 3 would restart the node from round 1, burning real money and wall-clock time at Opus pricing across 8 domains. To prevent this, the `reward_fn` closure writes the generator output to disk before scoring it:

```python
def make_reward_fn(stage: str, domain_id: str, critic: dspy.Module,
                   external_truth: dict, state_dir: Path):
    def reward_fn(output, round_num: int) -> float:
        # Persist before scoring — survives a crash at any round
        round_path = state_dir / "audit" / f"critique_{stage}_{domain_id}_round{round_num}.md"
        round_path.write_text(output.narrative)

        score = critic(output=output, truth=external_truth).score
        return score
    return reward_fn
```

On resume, if a round file exists, the refine loop reads it as `initial_output` rather than regenerating. This costs ~20 lines and does not require unrolling the loop into separate LangGraph nodes.

**`dspy.Assert` is deprecated as of DSPy 2.6.** Use `dspy.Refine(module, reward_fn, threshold, N)` — not `Assert`. Simple replacement fails with TypeError; explicit `reward_fn`, `threshold`, and `N` are required.

---

### Complementary Redundancy Pattern (applied where strategy divergence is meaningful)

```
asyncio.gather(
    agent_a(prompt_a),   # strategy: approach X
    agent_b(prompt_b),   # strategy: approach Y (structurally different)
)
→ (output_a, output_b)
→ downstream stage receives both, reconciles explicitly
→ divergence logged as signal to state/audit/
```

Reconciliation rule is defined per step, not ad hoc. Divergence between A and B is the most valuable signal in the pipeline.

---

### Per-Stage Detail

#### Stage 0 — Ingest

**Input:** file path (`.txt`, `.md`, `.pdf`), URL, or stdin (`-`)

**PDF extraction — single pass, Docling only:**

Docling (`DocumentConverter`) is the sole PDF parser. Running Docling and PyMuPDF over the same PDF is redundant: double ingestion time, double memory spike, and alignment issues when mapping Docling's semantic chunks back to PyMuPDF's spatial spans. PyMuPDF's font-flag heuristic (`span["flags"]` bold/italic) was used for structural zone extraction, but Docling's DocLayNet model classifies document elements as `SECTION_HEADER`, `TITLE`, `PAGE_HEADER`, `PAGE_FOOTER`, `TABLE`, `FORMULA`, etc. — a cleaner signal than font boldness, trained on academic layout patterns, and unconfused by bolded dataset names.

**Structural zone extraction from Docling labels:**
```python
from docling.document_converter import DocumentConverter
from docling.datamodel.document import DocItemLabel

converter = DocumentConverter()
doc = converter.convert(source_path).document   # DoclingDocument; PAGE_HEADER/FOOTER stripped

STRUCTURAL_LABELS = {DocItemLabel.SECTION_HEADER, DocItemLabel.TITLE}

structural_zones: set[str] = set()
for item, _ in doc.iterate_items():
    if item.label in STRUCTURAL_LABELS:
        structural_zones.update(spacy_extract_noun_chunks(item.text))
# structural_zones feeds the centrality weight multiplier in Stage 1
```

**URL fetch:** `httpx` with timeout; PotatoMCP `fetch` as fallback for Cloudflare-blocked pages.

**Chunking — semantic boundaries only, no physical page splits:**

Page boundaries are not chunk split points. Technical PDFs regularly have equations, multi-line arguments, and tables that span page breaks. Docling's `HybridChunker` respects the `DoclingDocument` section hierarchy:

```python
from docling.chunking import HybridChunker

chunker = HybridChunker(
    tokenizer="cl100k_base",
    max_tokens=4000,
    merge_peers=True,            # merge small adjacent paragraphs to fill the window
    respect_page_break=False     # ignore physical page boundaries entirely
)

chunks = list(chunker.chunk(doc))
# Each chunk: text, metadata (section path, page range for reference only)
```

Overlap at the semantic level: if a paragraph doesn't fit in the remaining token budget, the chunk boundary falls at the end of the last complete paragraph. The next chunk opens at the beginning of that paragraph — natural overlap, zero structural noise.

**Output:** `state/source.txt`, `state/source_meta.json`, `state/chunks/chunk_{N:04d}.json`, `state/structural_zones.json`

---

#### Stage 0.5 — Assess (User Calibration)

**Position:** Immediately after Stage 0, before Stage 1. Skipped by default. Activated with `--interactive` for direct human use; takes ~2 minutes.

**Purpose:** Understand the user's background, existing knowledge, and learning goal before any research runs. The answers become a `UserProfile` JSON that calibrates every downstream stage — which concepts to explain from first principles, how much LaTeX to show, what the synthesis roadmap should optimize for.

**Why after Stage 0 and not before:** questions must reference real terms from the document. Without `state/structural_zones.json`, Q3, Q6, and Q7 are generic and poorly targeted. With it, "Which of these best describes *PagedAttention*?" can offer four grounded, meaningful options. The ingest step is fast (seconds); running it first costs nothing and makes the assessment order-of-magnitude more useful.

**Why 7 questions:** fewer than 5 gives insufficient signal across the required dimensions. More than 9 creates fatigue and signals "this is a form." Seven covers all required question types exactly once.

**Question type matrix (one per dimension, fixed order):**

| # | Type | Dimension assessed | Signal used downstream |
|---|---|---|---|
| Q1 | MCQ Likert | Domain familiarity | `familiarity_level` → Stage 4 opening depth |
| Q2 | Open-ended | Background field | `background_field` → Stage 4 analogy framing |
| Q3 | MCQ 4-option | Knowledge probe on a core document concept | `q3_correct` → `known_concepts` / `unknown_concepts` |
| Q4 | MCQ 4-option | Mathematical comfort | `math_mode` → Stage 4 LaTeX vs. prose ratio |
| Q5 | MCQ 4-option | Learning goal | `learning_goal` → Stage 5 synthesis roadmap |
| Q6 | Open-ended free | Explain a supporting concept in their own words | `q6_known` → first-principles tracing depth |
| Q7 | MCQ or True/False | Technical depth probe on a specific document claim | `q7_correct` → practitioner vs. expert boundary |

---

**The meta-prompt — given verbatim to the orchestrating LLM:**

```
CALIBRATION ASSESSMENT PROMPT
==============================

You are about to generate 7 calibration questions for a user who has submitted
a technical document for knowledge expansion.

Document title: {source_meta.title}
Top 20 structural terms by centrality (from state/structural_zones.json):
{structural_zones_top20}

YOUR TASK:
Generate exactly 7 questions using the document's real vocabulary.
These are NOT a test. Any answer — including wrong ones — is equally useful signal.
Calibrate, don't evaluate.

OUTPUT FORMAT:
Begin with this exact framing paragraph (do not alter it):

---
Before expanding this document, I have 7 quick questions.
These calibrate how the knowledge expansion is written — what depth to explain things at,
what to assume you already know, and what to trace from first principles.
There are no wrong answers. "I don't know" is just as useful as a correct technical response.
---

Then output questions Q1–Q7 in this exact structure:

Q1 — FAMILIARITY [MCQ Likert]:
  "How familiar are you with [top-level domain detected from structural_zones]?"
  a) Complete newcomer — I've heard the term but couldn't explain it
  b) Aware — I understand the broad landscape but not the details
  c) Practitioner — I apply these ideas regularly in my work or research
  d) Expert — I could teach this or have published / built production systems in it

Q2 — BACKGROUND [open-ended, 1–2 sentences]:
  "What field or discipline are you coming from?
  (e.g. 'software engineer', 'biology PhD', 'product manager', 'curious hobbyist')"

Q3 — KNOWLEDGE PROBE [MCQ, 4 options]:
  Select the single most central and specific term from structural_zones_top20.
  Write the question: "Which of these best describes [term]?"
  Write all four options: one correct definition and three plausible distractors drawn
  from adjacent concepts in structural_zones. Then randomly assign them to a/b/c/d —
  the correct answer must not be consistently in the same position across runs.
  No option should be obviously dismissible. All four should be defensible to a novice.

Q4 — MATH COMFORT [MCQ, 4 options]:
  Pick a real mathematical expression that appears in the document (a formula, 
  a complexity bound, or a LaTeX fragment from the structural content).
  Write: "When you encounter an expression like '[quoted expression]' in a technical doc,
  what best describes your reaction?"
  a) Skip it — I rely on intuition and verbal explanation
  b) Skim it — I check if it matches my expectation but don't work through it
  c) Engage with it — I'll work through the derivation if the steps are shown
  d) Primary language — give me the full derivation, minimal prose

Q5 — LEARNING GOAL [MCQ, 4 options]:
  "After reading the full knowledge expansion, what do you most want to be able to do?"
  a) Explain [detected domain] clearly to someone who hasn't read the document
  b) Critically evaluate papers and claims in this area
  c) Apply or implement something based on the techniques described
  d) Pursue independent research or extend the work beyond the document

Q6 — PRIOR KNOWLEDGE [open-ended, free response]:
  Select a supporting (non-core) concept from structural_zones — important but not the
  most central term. Write: "In your own words, what is [term]?
  If you're not sure, say so — that answer is just as useful."
  Do not hint at the correct answer. Do not provide options.

Q7 — DEPTH PROBE [MCQ 4-option OR True/False]:
  Select a specific technical claim from the document that distinguishes 
  practitioner-level from expert-level understanding.
  Prefer a claim that is correct but counterintuitive, or a common misconception.
  Write either:
  - "True or False: [specific claim]" (for binary claims)
  - "Which of the following is true about [concept]?" with 4 options
    (one correct, three plausible misconceptions)
  For the 4-option form: randomly assign the correct answer to a/b/c/d.
  Do not let it default to option a.

---

ANSWER INTERPRETATION:
After the user responds to all 7 questions, parse their answers into this JSON schema.
Emit it as a single JSON object (not wrapped in prose):

{
  "familiarity_level": "novice|aware|practitioner|expert",
  "background_field": "<user's stated field, normalized>",
  "q3_correct": true|false,
  "math_comfort": "intuition_only|skim|engage|formal",
  "learning_goal": "explain|critique|apply|research",
  "q6_response": "<verbatim user text, truncated to 200 chars>",
  "q6_known": true|false|"partial",
  "q7_correct": true|false|"partial",
  "known_concepts": [
    "<terms the user demonstrably understands based on Q3, Q6, Q7 answers>"
  ],
  "unknown_concepts": [
    "<terms the user likely needs traced from first principles>"
  ],
  "effective_depth": "survey|standard|deep",
  "math_mode": "intuition|equations_explained|full_derivations",
  "reading_goal_note": "<one sentence: what Stage 5 synthesis roadmap should optimize for>"
}

Rules for effective_depth:
- novice AND intuition_only → "survey" (override --depth flag downward)
- expert AND (research OR critique) → "deep" (override --depth flag upward)
- all other combinations → honour the --depth flag unchanged

Rules for math_mode:
- intuition_only OR skim → "intuition": no LaTeX derivation steps; verbal analogies and diagrams
- engage → "equations_explained": show LaTeX, annotate every term and step
- formal → "full_derivations": full multi-line LaTeX, minimal annotation

Rules for known_concepts / unknown_concepts:
- known: terms the user correctly identified in Q3, correctly explained in Q6, 
  or correctly answered in Q7
- unknown: terms the user got wrong or said they didn't know — these get first-principles 
  treatment in Stage 4, regardless of how central they are

Emit the result as:
{"event": "assessment_complete", "user_profile": { ...schema above... }}

Then confirm to the user in plain language:
"Got it. Starting the knowledge expansion — estimated time: [N] minutes.
I'll report progress after each stage."
```

---

**Output:** `state/user_profile.json`

**Propagation — how UserProfile flows into downstream stages:**

| Stage | Signal consumed | Concrete effect |
|---|---|---|
| Stage 1 | `unknown_concepts` | Reduce agent promotes these terms to `core` tier regardless of frequency |
| Stage 4 | `math_mode` | Agent A/B system prompt includes exact math mode instruction |
| Stage 4 | `known_concepts` | "Assume the reader already understands [X]. Do not re-explain it." |
| Stage 4 | `unknown_concepts` | "Trace [X] from first principles before using it." |
| Stage 4 | `familiarity_level` | Sets the depth of domain opening paragraphs (overview vs. expert framing) |
| Stage 4 | `background_field` | Agent B uses analogies from `background_field` (e.g. "for a biologist: ...") |
| Stage 5 | `learning_goal` | Synthesis reading roadmap is ordered by stated goal: apply → implementations first; research → open problems first |
| Stage 5 | `effective_depth` | Overrides `--depth` flag if UserProfile recommends different depth |
| Stage 7 | `reading_goal_note` | Included in document frontmatter as "Reader profile" note |

**Activation options:**

```
--interactive             Run Stage 0.5 interactively (~2 min); default is to skip
--user-profile <path>     Load a pre-computed UserProfile JSON (also skips Stage 0.5)
```

The pipeline defaults to autonomous mode. `--interactive` is an opt-in for direct human use. An LLM orchestrator never needs to pass any flag to bypass Stage 0.5 — skipping is the default. No implicit coupling between flags.

**Idempotency:** `state/user_profile.json` is the sentinel. If it exists and is valid on `--resume`, Stage 0.5 is skipped unconditionally.

---

#### Stage 1 — Extract (Map-Reduce)

**Map phase — per chunk, parallel:**

| | Signal A | Signal B | Signal C |
|---|---|---|---|
| Source | `spaCy` (deterministic) | Agent B — LLM (conceptual) | `KeyBERT` + `BGE-M3` (embedding) |
| Strategy | NER pipeline + noun chunk extractor + regex acronym detection | What would a domain expert recognize as load-bearing? | Top-K semantically central keyphrases |
| LLM call? | No | Yes (one call per chunk) | No |
| Constraint | Extract only what is lexically present. No inference. | Extract implicit domain concepts; may go beyond literal text. | Embedding similarity to chunk centroid |
| Output | merged into `map_a_{chunk_id}.json` | `map_b_{chunk_id}.json` | merged into `map_a_{chunk_id}.json` |

**Why spaCy + KeyBERT instead of two LLMs:** spaCy's NER and noun chunker handle lexical extraction deterministically and in milliseconds — an LLM adds no accuracy over a purpose-built NLP pipeline for this task. The divergence signal between an LLM (conceptual reasoning) and an embedding model (geometric centrality) is stronger than between two LLMs running over the same text, because they operate in fundamentally different representation spaces.

Each map output: `{ chunk_id, terms: [{ name, aliases, co_occurring_terms, context_snippet, occurrence_count }] }`

**Reduce phase — single agent:**

Merges all map outputs. Deduplicates by exact match + alias cluster using a Union-Find structure. Computes centrality.

**Alias clustering — Union-Find, not NetworkX:**

For ≤300 terms, importing a graph theory library to track alias clusters is absurd overhead. A standard disjoint-set structure handles it in ~30 lines with zero dependencies:

```python
class UnionFind:
    def __init__(self): self.parent: dict[str, str] = {}; self.rank: dict[str, int] = {}

    def find(self, x: str) -> str:
        if x not in self.parent: self.parent[x] = x; self.rank[x] = 0
        if self.parent[x] != x: self.parent[x] = self.find(self.parent[x])
        return self.parent[x]

    def union(self, x: str, y: str) -> None:
        rx, ry = self.find(x), self.find(y)
        if rx == ry: return
        if self.rank[rx] < self.rank[ry]: rx, ry = ry, rx
        self.parent[ry] = rx
        if self.rank[rx] == self.rank[ry]: self.rank[rx] += 1

    def clusters(self) -> dict[str, list[str]]:
        groups: dict[str, list[str]] = {}
        for x in self.parent:
            groups.setdefault(self.find(x), []).append(x)
        return groups

uf = UnionFind()
# BGE-M3 cosine similarity (numpy) finds near-duplicate pairs; Union-Find merges them
for term_a, term_b in high_similarity_pairs:   # cosine > 0.92
    uf.union(term_a, term_b)
clusters = uf.clusters()   # {canonical_term: [alias_1, alias_2, ...]}
```

**Centrality algorithm (deterministic, not LLM-assigned):**
```python
BOILERPLATE = {
    "introduction", "methodology", "conclusion", "figure", "table",
    "theorem", "proof", "lemma", "et al", "appendix", "section",
    "related work", "abstract", "references", "background", "overview"
}

STRUCTURAL_WEIGHT = 2.0   # configurable; structural presence amplifies, does not bypass

def compute_centrality(term, occurrences, chunk_count, structural_zones,
                       structural_weight=STRUCTURAL_WEIGHT):
    name = term.lower()
    if any(bp in name for bp in BOILERPLATE):
        return None                          # excluded entirely

    # Structural presence is a weight multiplier, not a frequency bypass.
    # A term bolded once in a 10-chunk doc: effective = 1 × 2.0 = 2.0 → "supporting".
    # A term bolded 3 times: effective = 3 × 2.0 = 6.0 → "core". Correct.
    # A dataset name bolded once in the abstract: stays "supporting" until frequency earns "core".
    effective = occurrences * (structural_weight if name in structural_zones else 1.0)

    if effective >= 5 or effective / chunk_count >= 0.3:
        return "core"
    if effective >= 2:
        return "supporting"
    return "incidental"
```

**Reduce reconciliation rule:** terms in both Agent B and KeyBERT → promote one tier (supporting → core; incidental → supporting). Terms in only one signal → centrality algorithm decides from effective occurrences.

**Why not LLM-assigned centrality:** LLMs make inconsistent, unjustifiable centrality calls. A document-structural + frequency algorithm is transparent, auditable, and reproducible.

**Why structural weight, not structural bypass:** authors bold dataset names, italicize loop variables, and highlight metrics in abstracts — none of which are conceptually central. Unconditional structural promotion floods the core tier with noise. The multiplier preserves the signal (bold/italic terms are more likely to be important) without surrendering to it entirely.

**Output:** `state/terms.json`

---

#### Stage 2 — Graph (Two-Phase Lock)

**Phase 1 — Ontology Lock:**

Receives only `core` terms (typically 20–40). Two agents propose independent taxonomies:

| | Agent A | Agent B |
|---|---|---|
| Strategy | Lumper: fewest domains that cleanly partition core terms | Splitter: finest-grained distinctions the core terms support |
| Constraint | Maximum 8 domains. Every proposed domain must map to ≥1 Semantic Scholar `fieldsOfStudy` category. |

**Validation (not adversarial critic):** each proposed domain label is looked up against the OpenAlex concept API (`api.openalex.org/concepts?search={label}`). Domains that resolve to no OpenAlex concept are flagged as `[UNVALIDATED]` in the proposal file. This is a structural check, not an iterative critique loop — the taxonomy decision itself is handled by human review or `--auto-taxonomy`, not a Critic agent.

**Taxonomy checkpoint (two paths):**

Both taxonomy proposals are written to `state/audit/taxonomy_a.json` and `state/audit/taxonomy_b.json`. `state/taxonomy.json` is pre-populated with Agent A's proposal as the default.

**Default path — clean exit + `--resume 2b`:**
```python
if not auto_taxonomy:
    emit({
        "event": "pipeline_paused",
        "reason": "taxonomy_review",
        "instructions": "Edit state/taxonomy.json (proposals in state/audit/taxonomy_a.json and taxonomy_b.json), then run: doc-expand --resume 2b",
        "checkpoint_id": state.checkpoint_id
    })
    sys.exit(0)   # clean exit; LangGraph checkpoint preserves all state
```
The pipeline exits. User edits `state/taxonomy.json` in any editor, then calls `doc-expand --resume 2b`. No blocking subprocess inside an async node.

**`--auto-taxonomy` path — orchestrating LLM decides (no human blocking):**
```python
if auto_taxonomy:
    decision = await orchestrating_llm.call(
        prompt=AUTO_TAXONOMY_PROMPT,
        context={
            "taxonomy_a": taxonomy_a,
            "taxonomy_b": taxonomy_b,
            "classification_conflicts": conflicts,
            "openalex_validation": openalex_validation
        },
        schema=TaxonomyDecision  # { chosen: "a"|"b"|"merged", merged_taxonomy: {...}, rationale: str }
    )
    write_json(state.dir / "taxonomy.json", decision.merged_taxonomy)
    mark_taxonomy_approved(state)
    emit({"event": "taxonomy_approved", "mode": "auto", "chosen": decision.chosen, "rationale": decision.rationale})
```
The orchestrating LLM reads both proposals and the OpenAlex validation output, then writes its preferred taxonomy. The `rationale` field is logged to `state/audit/` for provenance.

**Why vi default:** `nano` is not universally available. `vi` is POSIX-guaranteed. User's `$EDITOR` preference takes priority.

**Why this is the only acceptable human checkpoint:** taxonomy is the highest-consequence decision in the pipeline — every downstream stage is partitioned by it. Getting it wrong poisons Stages 3–7. One minute of review (human or LLM) here saves hours of downstream garbage. All other stages have fully deterministic or LLM-resolved decision points that do not require external arbitration.

**Phase 2 — Classification:**

Two agents classify the full term inventory against the locked taxonomy:

| | Agent A | Agent B |
|---|---|---|
| Strategy | Assign each term to its most fundamental domain | Assign each term to its most applied/downstream domain |
| Constraint | Each term maps to exactly one domain from `taxonomy.json`. No new domains. |

Divergences between A and B written to `state/audit/classification_conflicts.json`. These flow to Stage 3 as primary gap candidates — if A and B can't agree where a term belongs, the domain boundary there is ambiguous and needs external anchoring.

**Graph schema:**
```json
{
  "nodes": [{
    "id": "n001", "term_id": "t001", "name": "PagedAttention",
    "domain": "inference_systems", "tier": "journeyman", "xp": 100,
    "prerequisites": ["n_kv_cache"], "unlocks": ["n_continuous_batching"],
    "from_source_doc": true
  }],
  "edges": [
    { "from": "n001", "to": "n_continuous_batching", "type": "enables" },
    { "from": "n_virtual_memory", "to": "n001", "type": "prerequisite" }
  ],
  "domains": [{
    "id": "inference_systems", "label": "Inference Systems",
    "definition": "...", "node_count": 11, "primary": true
  }]
}
```

**`from_source_doc: true`** distinguishes concepts present in the input document from concepts added during augmentation. Enables the final document to clearly mark "this is what the paper covers" vs. "this is the surrounding field."

**Output:** `state/taxonomy.json`, `state/graph.json`, `state/audit/classification_conflicts.json`

---

#### Stage 3 — Audit (Anchored)

**Two fetches per domain:**

1. **Anchors** (3 papers): Semantic Scholar top-cited papers for the domain label. Used for gap diff.
2. **Bibliography** (10/30/50 papers by depth): Two-bucket fetch — foundational papers (all-time citation rank) combined with frontier papers (recent window, citation-ranked within it). These are the **only papers Stage 4 agents are permitted to cite.**

**Why two buckets:** sorting by `citationCount` descending guarantees blindness to the last 24 months. A dead-end 2017 survey with 4,000 citations outranks a state-of-the-art 2025 paper with 12. A pipeline claiming to build a "research frontier" expansion must structurally include the frontier.

**Two-bucket bibliography fetch:**
```python
def fetch_bibliography(domain_label: str, depth: str,
                        frontier_months: int = 24) -> list[Citation]:
    N = {"survey": 10, "standard": 30, "deep": 50}[depth]
    n_history  = int(N * 0.65)          # foundational: ~65% of slots
    n_frontier = N - n_history          # frontier: ~35% of slots

    cutoff_year = datetime.now().year - max(1, frontier_months // 12)

    # Bucket A: foundational — all time, citation-ranked, min citation floor
    history = semantic_scholar_search(
        domain_label, limit=n_history * 2,
        min_citations=50, sort="citationCount"
    )[:n_history]

    # Bucket B: frontier — recent window only, citation-ranked within window
    frontier = semantic_scholar_search(
        domain_label, limit=n_frontier * 4,
        from_year=cutoff_year, min_citations=0,
        sort="citationCount"
    )[:n_frontier]

    return deduplicate_by_doi(history + frontier)
```

Split ratios and `frontier_months` are configurable in `config.yaml`. Default: 65/35 split, 24-month frontier window.

Wikipedia lede (first 500 words of domain label article) as secondary anchor — useful for non-CS fields.

All fetches written to `state/audit/anchors_{domain}.json` and `state/audit/bibliography_{domain}.json`.

**External API rate limiting — per-API token buckets:**

8 concurrent domain fetches × (anchors + bibliography + OpenAlex validation) = many simultaneous requests. Semantic Scholar unauthenticated: 100 req / 5 min. Crossref without a `mailto:` Polite Pool header: aggressively throttled. Firing these concurrently results in IP bans before any LLM call fires.

Dedicated rate limiters queue requests per API using **`aiolimiter`** (MIT, async token bucket, ~200 lines, actively maintained — no need to hand-roll). Domain fetches can still run concurrently at the domain level; the limiter serializes at the API call level:

```python
from aiolimiter import AsyncLimiter

# Documented rate limits (conservative)
SEMANTIC_SCHOLAR_LIMITER = AsyncLimiter(max_rate=100, time_period=300)  # 100 req / 5 min
CROSSREF_LIMITER         = AsyncLimiter(max_rate=1,   time_period=1)    # Polite Pool ~1 req/s
OPENALEX_LIMITER         = AsyncLimiter(max_rate=10,  time_period=1)    # 10 req/s

CROSSREF_HEADERS = {
    "User-Agent": "doc-expand/1.0 (mailto:your@email.com)",  # Polite Pool
    "mailto": "your@email.com"
}

async def fetch_semantic_scholar(query: str) -> dict:
    async with SEMANTIC_SCHOLAR_LIMITER:
        return await http_get(SS_ENDPOINT, params={"query": query})
```

The `mailto:` address is configured via `CROSSREF_MAILTO` environment variable. Without it, Crossref routes requests through the unregistered pool with lower priority and no SLA.

**Adversarial gap analysis:**

| | Agent A | Agent B |
|---|---|---|
| Strategy | Gap Finder: what do external anchors cover that the KG omits? | Defender: argue each apparent gap is present in the KG under a different name |
| External truth | Anchor abstracts + Wikipedia lede + `classification_conflicts.json` |
| Iteration | Gap Finder must rebut each Defender argument to confirm the gap is real |

A gap survives to `gap_analysis.md` only if the Gap Finder rebuts the Defender. Defended gaps logged as `[AMBIGUOUS]` for human review. This is the sharpest epistemic break in the pipeline: the Defender agent prevents rubber-stamping by forcing explicit justification for every claimed gap.

**Output:** `state/audit/gap_analysis.md`, `state/audit/corrections.md`, `state/audit/bibliography_{domain}.json`

**Why Semantic Scholar + Crossref, not arXiv:**
- Crossref covers journals, proceedings, books across all fields — domain-agnostic
- Semantic Scholar covers CS/ML with citation counts and field tagging
- arXiv is CS/Math/Physics only; using it as primary oracle breaks the domain-agnostic principle

**Why pre-fetch bibliography before Stage 4, not verify after:**
- Agents without web access hallucinate citations
- Verifying hallucinated titles yields 100% failure rate at Stage 6
- A bounded pre-fetched bibliography makes citations verified by construction

---

#### Stage 4 — Research (Parallel per domain)

One pair of Opus agents per domain, all domain pairs launched simultaneously via `asyncio.gather`.

**Each agent receives:**
- Domain nodes from `state/graph.json`
- Gap analysis for this domain from `state/audit/gap_analysis.md`
- Corrections for this domain from `state/audit/corrections.md`
- `state/audit/bibliography_{domain}.json` — the only permitted citation pool
- Format spec verbatim (skill tier, XP, Trace Up/Down/Sideways, math protocol)
- Anti-hallucination constraint: *cite only from the provided bibliography JSON. If a claim requires a citation not in the list, write the claim without citation and mark it `[NEEDS_CITATION]`. Tag speculative claims `[INFERRED]`.*

| | Agent A | Agent B |
|---|---|---|
| Strategy | Top-down: field overview → paradigms → specific mechanisms → implementation | Bottom-up: implementation details → theoretical basis → first principles |

**Adversarial critic loop:**
- Critic challenges: unsupported claims; missing Trace Down implementations; math derivations with missing steps; `[INFERRED]` that should be citable from the bibliography
- External truth: `bibliography_{domain}.json` — critic can directly challenge any citation not in the provided pool

**Each domain pair writes:**
1. `state/sections/section_{domain}.md` — full narrative with LaTeX, examples, Trace tables (reconciled from A and B)
2. `state/summaries/summary_{domain}.json` — structured summary for Stage 5:

```json
{
  "domain_id": "inference_systems",
  "label": "Inference Systems",
  "node_count": 11,
  "perspectives": {
    "top_down": ["KV cache is the primary memory bottleneck → PagedAttention → continuous batching"],
    "bottom_up": ["GPU HBM bandwidth constraints → fragmentation → virtual paging analogy"]
  },
  "key_concepts": ["PagedAttention", "continuous batching", "speculative decoding"],
  "key_tensions": ["latency vs. throughput", "memory pressure vs. batch size"],
  "key_insights": ["Disaggregated prefill-decode decouples two distinct compute profiles"],
  "cross_domain_signals": [
    { "signal": "requires", "domain": "hardware_architecture" },
    { "signal": "enables", "domain": "multi_agent_systems" }
  ],
  "open_problems": ["Optimal chunked prefill scheduling under heterogeneous SLOs"],
  "citation_ids": ["kwon_2023_vllm", "yu_2023_orca"],
  "xp_total": 1250
}
```

**Resume handling:**
```python
if resume:
    for domain_id in domain_ids:
        for path in [sections / f"section_{domain_id}.md",
                     summaries / f"summary_{domain_id}.json"]:
            path.unlink(missing_ok=True)
```

**Why summary JSON, not narrative sections:** at "deep" depth, 10 section `.md` files easily exceed 150k tokens. Summary JSONs are designed to be tight (under 2k tokens each). Stage 5 can safely receive all of them simultaneously without truncation risk.

**Output:** `state/sections/section_{domain}.md`, `state/summaries/summary_{domain}.json`

---

#### Stage 5 — Synthesize

**Inputs: `state/graph.json` + `state/summaries/summary_*.json` ONLY.**
Never the narrative section `.md` files.

Total token budget at "deep" depth on an 8-domain paper: ~15,000 tokens. Well within context.

| | Agent A | Agent B |
|---|---|---|
| Strategy | Structural: identify cross-domain insights from typed edges in the KG | Semantic: identify cross-domain insights from `perspectives` fields in summaries |
| External truth | `graph.json` topology — synthesis cannot assert a cross-domain connection without a typed edge path in the graph |

**Adversarial critic:**
- Challenges: trivial connections (A requires B is not a synthesis insight — that's just a prerequisite edge); connections asserted without graph edge support; redundancy with single-domain content
- External truth: same graph topology

**Outputs 3–5 synthesis boss nodes** plus a **reading roadmap** — topologically sorted path from "understands the source doc" to "can do independent research."

**Reconciliation:** structural framing from Agent A, narrative depth from Agent B, merged by a lightweight reconciliation pass.

**Output:** `state/sections/section_synthesis.md`

---

#### Stage 6 — Verify

Structural citation audit, not a verification grind.

**Three-tier oracle:**
1. **Crossref REST API** (`api.crossref.org/works?query={title}` or `?query.bibliographic={title}`) — journals, proceedings, books, all fields, no auth
2. **Semantic Scholar Graph API** (`api.semanticscholar.org/graph/v1/paper/search?query={title}`) — CS/ML papers, citation counts
3. **arXiv Export API** (`export.arxiv.org/api/query?id_list={id}`) — fallback, only if domain tagged `cs.*`, `math.*`, or `physics.*` in `taxonomy.json`

**Check:** every citation key in assembled document maps to a known entry in `state/audit/bibliography_{domain}.json`. Unknown keys = agent defections. `[NEEDS_CITATION]` markers logged to `state/audit/needs_citation.md`. Build does not block on `[NEEDS_CITATION]` — these are flagged for human review, not treated as build failures.

**Acceptance criterion for build:** 0 `[UNVERIFIED]` tags. `[NEEDS_CITATION]` tags are acceptable and logged.

**Output:** `state/audit/needs_citation.md`, verified citation index

---

#### Stage 7 — Assemble + Build

**Assembly order:** topological sort of domains by dependency depth in `graph.json` (domains with more prerequisite edges come later). Mechanical stitch:

```
frontmatter → abstract → TOC → domain sections (topology order) → synthesis → bibliography
```

Bibliography compiled from all verified citation entries, deduplicated by DOI/arXiv ID, numbered in first-appearance order. XP totals and node counts derived mechanically from `graph.json` — never hand-counted.

**Bibliography format — CSL-JSON, not BibTeX:**

Stage 3 citation objects are stored in **CSL-JSON format** (the native bibliography format for Pandoc's `--bibliography` flag). No `.bib` file is generated or needed. Pandoc accepts CSL-JSON directly alongside a `--csl` citation style file, eliminating a format conversion step and a `bibtexparser` dependency.

Stage 7 assembly merges all per-domain `bibliography_{domain}.json` files into a single `state/bibliography.json`, deduplicates by DOI (then arXiv ID as fallback), and passes it to pandoc.

Each citation object uses the CSL-JSON schema (a direct mapping from Semantic Scholar / Crossref API responses):
```json
{
  "id": "kwon_2023_vllm",
  "type": "paper-conference",
  "title": "Efficient Memory Management for Large Language Model Serving with PagedAttention",
  "author": [{"family": "Kwon", "given": "Woosuk"}],
  "issued": {"date-parts": [[2023]]},
  "DOI": "10.1145/3600006.3613165",
  "URL": "https://arxiv.org/abs/2309.06180",
  "source": "semantic_scholar",
  "citation_count": 1847,
  "bucket": "history"
}
```

The `bucket` field (`"history"` or `"frontier"`) is metadata only — used for audit logging to confirm both buckets contributed to the final bibliography.

**Build pipeline:**

```python
def build_pdf(render_md, style_tex, bibliography_json, output_pdf):
    # Step 0: merge and deduplicate bibliography JSONs
    merge_bibliography(
        sources=list(state_dir.glob("audit/bibliography_*.json")),
        output=state_dir / "bibliography.json"
    )

    # Step 1: preprocess (emoji, paths, trace table formatting)
    subprocess.run(["python3", "style/preprocess.py"], check=True)

    # Step 2: pandoc md → tex (CSL-JSON bibliography, no .bib needed)
    subprocess.run([
        "pandoc", str(render_md),
        "--pdf-engine=xelatex",
        f"--include-in-header={style_tex}",
        f"--bibliography={bibliography_json}",
        "--csl=style/chicago-author-date.csl",
        "--standalone", "-o", str(tex_file)
    ], check=True)

    # Step 3: xelatex pass 1 (build aux, toc, cross-ref data)
    subprocess.run(
        ["xelatex", "-interaction=nonstopmode", tex_file.name],
        cwd=tex_dir, check=True
    )

    # Step 4: xelatex pass 2 (resolve forward refs, TOC page numbers)
    subprocess.run(
        ["xelatex", "-interaction=nonstopmode", tex_file.name],
        cwd=tex_dir, check=True
    )

    # Step 5: verify PDF before returning — LangGraph writes completion checkpoint only after this
    # xelatex exits 0 on font errors while producing a partial/corrupt file; do not trust exit code alone
    import pypdf
    if not output_pdf.exists():
        raise RuntimeError(f"PDF absent after xelatex: {output_pdf}")
    try:
        with open(output_pdf, "rb") as f:
            reader = pypdf.PdfReader(f)
            if reader.is_encrypted:
                raise RuntimeError(f"PDF is encrypted (zero-content output): {output_pdf}")
            if len(reader.pages) == 0:
                raise RuntimeError(f"PDF has 0 pages after xelatex: {output_pdf}")
    except pypdf.errors.PdfStreamError as e:
        raise RuntimeError(f"xelatex produced a corrupted PDF stream: {output_pdf}") from e
    # LangGraph node returns here → completion checkpoint written
```

**Why two xelatex passes:** the first pass builds the `.aux` file with forward reference placeholders (`??`). The second pass reads `.aux` and resolves all page numbers in the TOC and cross-references. Single-pass builds produce a TOC full of `??`. This is not optional.

**Why `-interaction=nonstopmode`:** prevents xelatex from blocking on a LaTeX error waiting for terminal input in an unattended pipeline run.

**PDF artifact audit:** 3 consecutive clean rounds at 20% page sampling. Known false positives encoded in the audit script:
- High brightness (mean > 0.97) but min pixel < 0.95 → sparse content or figure page, not blank
- `\text{}` in pdftotext output → alt-text metadata from math rendering, not unrendered source

---

### State Directory Layout

```
state/
├── pipeline.json                               Run state: per-stage status + timestamps + model_consistency
├── source.txt                                  Stage 0 output
├── source_meta.json
├── structural_zones.json                       Docling SECTION_HEADER/TITLE extraction (Stage 0)
├── user_profile.json                           Stage 0.5 output (also acts as sentinel)
├── chunks/
│   ├── chunk_{N:04d}.json
│   └── chunk_{N:04d}.done                      ← idempotency sentinel per chunk
├── map_outputs/
│   ├── map_a_{chunk_id}.json                   Stage 1 Map Agent A
│   ├── map_a_{chunk_id}.done                   ← sentinel
│   ├── map_b_{chunk_id}.json                   Stage 1 Map Agent B
│   └── map_b_{chunk_id}.done                   ← sentinel
├── terms.json                                  Stage 1 Reduce output
├── audit/
│   ├── taxonomy_a.json                         Stage 2 Phase 1 Agent A (Lumper)
│   ├── taxonomy_b.json                         Stage 2 Phase 1 Agent B (Splitter)
│   ├── classification_conflicts.json           Stage 2 Phase 2 divergences
│   ├── anchors_{domain_id}.json               Stage 3 gap diff anchors
│   ├── bibliography_{domain_id}.json          Stage 3 citation pool
│   ├── gap_analysis.md                         Stage 3 output
│   ├── corrections.md                          Stage 3 output
│   ├── needs_citation.md                       Stage 6 output
│   ├── model_usage.jsonl                       Per-call model log: model, tokens, cost, switches
│   └── critique_{stage}_{id}.md               Adversarial loop logs (all stages)
├── taxonomy.json                               Human-approved locked taxonomy (status: approved)
├── graph.json                                  Stage 2 knowledge graph
├── sections/
│   ├── section_{domain_id}.md                  Stage 4 narrative output
│   ├── section_{domain_id}.done                ← sentinel
│   └── section_synthesis.md                    Stage 5 output
└── summaries/
    ├── summary_{domain_id}.json               Stage 4 → Stage 5 JSON contract
    └── summary_{domain_id}.done               ← sentinel
```

---

### CLI Interface

```
doc-expand <input> [options]

Arguments:
  <input>                   File path (.txt .md .pdf), URL, or - (stdin)

Output / mode:
  --human                   Rich terminal output (default: JSON Lines for LLM consumption)
  --no-pdf                  Skip PDF build, output Markdown only
  --renderer                xelatex | typst  (default: xelatex)

Pipeline control:
  --depth                   survey | standard | deep  (default: standard)
  --status                  Emit pipeline_status event from pipeline.json; no work runs
  --resume                  Stage to resume from (0-7); uses LangGraph checkpoint
  --stage                   Run only this stage (0-7), then stop
  --interactive             Run Stage 0.5 user calibration interactively (default: skipped)
  --user-profile <path>     Load pre-computed UserProfile JSON; activates Stage 0.5 output without questions
  --auto-taxonomy           Skip $EDITOR; let orchestrating LLM decide taxonomy (fully autonomous)
  --no-bibliography-fetch   Skip external API fetch; use GROBID+AnyStyle extraction only

Paths:
  --output-dir              Path for final output  (default: ./output)
  --state-dir               Path for pipeline state  (default: ./state)

Concurrency:
  --cloud-concurrency N     Max concurrent cloud API calls (default: 8; Anthropic tier-dependent)
  --local-concurrency N     Max concurrent Ollama calls (default: 1; increase for high-VRAM workstations)

Model overrides (also settable via env vars):
  --model-researcher        LiteLLM model string for domain research agents
  --model-extractor         LiteLLM model string for extraction/classification
  --model-critic            LiteLLM model string for adversarial critic agents
```

`--resume` is backed by LangGraph checkpointing — not a hand-rolled directory scan. The pipeline graph is defined once; resuming replays from the checkpointed state with full context intact.

---

### Deployment Architecture

#### Philosophy: LLM CLI First

`doc-expand` is designed to be **called by an LLM agent**, not typed by a human. The orchestrating LLM — whatever model and quality it is — drives the pipeline by invoking the CLI, reading structured output, inspecting intermediate artifacts, and deciding whether to proceed, retry a stage, or edit a checkpoint artifact (e.g., `state/taxonomy.json`) before continuing.

This means the CLI has two output modes:

**Default: JSON Lines (machine-readable)**
Every stage emits a structured completion event to stdout:
```json
{"event": "stage_complete", "stage": 2, "phase": "ontology_lock", "artifact": "state/taxonomy.json", "action_required": "review_taxonomy", "domain_count": 5, "checkpoint_id": "ckpt_abc123"}
{"event": "stage_complete", "stage": 2, "phase": "classification", "artifact": "state/graph.json", "node_count": 47, "conflict_count": 3, "checkpoint_id": "ckpt_def456"}
{"event": "pipeline_paused", "reason": "taxonomy_review", "instructions": "Edit state/taxonomy.json, then run: doc-expand --resume 2b"}
```

The orchestrating LLM reads these events, inspects artifacts at the reported paths, and decides its next action. The `action_required` field tells it when human-equivalent judgment is needed.

**`--human` flag: rich terminal output**
Enables `Rich`-rendered progress bars, tables, and panels for direct human use.

The LLM orchestrator treats the CLI as a tool with well-defined inputs and structured outputs — not a black box. Every artifact path is reported; every decision point is surfaced. The orchestrating LLM can call `doc-expand --stage 2` to re-run only the taxonomy phase, read `state/taxonomy.json`, decide it's wrong, edit it, and call `doc-expand --resume 3` to continue from the audit phase.

**The orchestrating LLM's context window is stateless by design.** Pipeline state lives in the filesystem (`pipeline.json` + `.done` sentinels), not in the LLM's conversation history. This makes the architecture resilient to long pauses, quota exhaustion, and context compaction: the orchestrating LLM does not need to remember what happened two hours ago — it re-reads current state from the filesystem on every interaction.

Two mechanisms enforce this:

**`doc-expand --status`** — zero-work state query. Reads `pipeline.json` and `.done` sentinels, emits a single `pipeline_status` event, exits. No LLM calls, no stage execution. The orchestrating LLM calls this to re-orient after any pause:

```json
{
  "event": "pipeline_status",
  "run_id": "run_20260518_143201",
  "input": "/app/input/paper.pdf",
  "depth": "standard",
  "stages": {
    "0":   {"status": "complete", "completed_at": "2026-05-18T14:32:45Z"},
    "0.5": {"status": "complete", "completed_at": "2026-05-18T14:33:01Z"},
    "1":   {"status": "complete", "completed_at": "2026-05-18T14:33:12Z"},
    "2":   {"status": "complete", "completed_at": "2026-05-18T14:34:55Z"},
    "3":   {"status": "complete", "completed_at": "2026-05-18T14:41:20Z"},
    "4":   {"status": "partial",  "domains_complete": 5, "domains_pending": ["hardware_arch", "evaluation", "economics"]},
    "5":   {"status": "pending"},
    "6":   {"status": "pending"},
    "7":   {"status": "pending"}
  },
  "stage_summaries": {
    "1":  {"chunks": 12, "core_terms": 31, "supporting_terms": 87},
    "2":  {"taxonomy_domains": 6, "classification_conflicts": 3,
           "conflict_terms": ["attention_mask", "layer_norm", "dropout"],
           "artifact": "state/audit/classification_conflicts.json"},
    "3":  {"gaps_confirmed": 8, "gaps_defended": 3, "corrections": 2,
           "bibliography_total": 240, "history_papers": 156, "frontier_papers": 84},
    "4":  {"domains_complete": 5, "domains_pending": ["hardware_arch", "evaluation", "economics"],
           "needs_citation_count": 4}
  },
  "model_consistency": "mixed",
  "last_model_switch": {"stage": 4, "domain": "hardware_arch", "from": "claude-opus-4-7", "to": "claude-sonnet-4-6"},
  "resume_command": "doc-expand --resume 4"
}
```

`stage_summaries` gives the orchestrating LLM signal-level digests without requiring it to read full artifact files. It can see "3 classification conflicts on specific terms" and decide whether to inspect `state/audit/classification_conflicts.json` based on that count and the term names — not unconditionally on every restart. Artifact paths are included so targeted reads are one step away when needed.

**First event on any `--resume N`** is always `pipeline_status` before any work starts. The orchestrating LLM receives a complete current-state summary at the top of every resumed run, regardless of how long the pause was or how much context it has lost.

---

#### Docker Compose Stack

The full infrastructure runs in Docker Compose. Any machine with Docker installed can run the complete stack — no local Python environment, no TeX installation, no service configuration required.

```yaml
# docker-compose.yml
services:

  app:
    build: .
    volumes:
      - ./state:/app/state
      - ./output:/app/output
      - ./input:/app/input
    environment:
      - LITELLM_MODEL=${LITELLM_MODEL:-claude-opus-4-7}
      - ANTHROPIC_API_KEY
      - OPENAI_API_KEY
      - GEMINI_API_KEY
      - OLLAMA_BASE_URL=http://ollama:11434
    depends_on:
      grobid:
        condition: service_healthy
      anystyle:
        condition: service_started
    command: ["doc-expand", "/app/input/source.pdf", "--depth", "standard"]

  grobid:
    image: lfoppiano/grobid:0.8.2
    ports:
      - "8070:8070"
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8070/api/isalive"]
      interval: 10s
      timeout: 5s
      retries: 5

  anystyle:
    # No verified official Docker image exists on Docker Hub.
    # Build from source: https://github.com/inukshuk/anystyle-cli
    build:
      context: ./docker/anystyle
      dockerfile: Dockerfile
    ports:
      - "4567:4567"

  phoenix:
    image: arizephoenix/phoenix:latest
    profiles: ["observability"]    # only starts with: docker compose --profile observability up
    ports:
      - "6006:6006"    # Web UI
      - "4317:4317"    # OTLP gRPC ingest
    volumes:
      - phoenix_data:/mnt/data
    environment:
      - PHOENIX_WORKING_DIR=/mnt/data

  ollama:
    image: ollama/ollama:latest
    profiles: ["local"]          # only starts with: docker compose --profile local up
    ports:
      - "11434:11434"
    volumes:
      - ollama_data:/root/.ollama
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              capabilities: [gpu]
              count: all

volumes:
  phoenix_data:
  ollama_data:
```

**Service roles:**

| Service | Role | Port | Profile |
|---|---|---|---|
| `app` | doc-expand pipeline | — | default |
| `grobid` | Reference extraction from source PDF | 8070 | default |
| `anystyle` | Citation parsing (second extractor) | 4567 | default |
| `phoenix` | Observability UI + OTLP trace ingest | 6006 / 4317 | `observability` |
| `ollama` | Local LLM inference | 11434 | `local` |

**Usage:**
```bash
# Standard run (cloud LLM, API key required)
ANTHROPIC_API_KEY=sk-... docker compose up

# With observability UI (Phoenix on :6006)
docker compose --profile observability up

# Fully local run (no API keys, Ollama + local models)
docker compose --profile local up
# then pull a model: docker compose exec ollama ollama pull llama3.2:3b

# With a specific input file
docker compose run app doc-expand /app/input/paper.pdf --depth deep
```

---

#### LLM-Agnostic Model Layer

All LLM calls in the pipeline go through **LiteLLM** — a single interface covering 100+ providers. Provider is configuration, not code. Swapping from Claude to GPT-4o to a local Ollama model requires no code changes.

```python
# agents/base.py
import litellm

async def call_agent(role: str, prompt: str, schema: type[BaseModel]) -> BaseModel:
    model = config.models[role]          # resolved from models.yaml or env
    response = await litellm.acompletion(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        response_format=schema,          # Instructor handles validation + retry
    )
    return schema.model_validate_json(response.choices[0].message.content)
```

**Model configuration** — `models.yaml` (committed, safe defaults):
```yaml
roles:
  researcher:   "claude-opus-4-7"       # heaviest lifting: domain expansion
  synthesizer:  "claude-opus-4-7"       # cross-domain synthesis
  critic:       "claude-sonnet-4-6"     # adversarial loops
  extractor:    "claude-sonnet-4-6"     # map-reduce term extraction
  classifier:   "claude-sonnet-4-6"     # taxonomy classification
  assembler:    "claude-sonnet-4-6"     # document assembly
```

**Environment variable overrides** — any role can be overridden at runtime:
```bash
LITELLM_RESEARCHER=gpt-4o \
LITELLM_EXTRACTOR=ollama/llama3.2 \
doc-expand paper.pdf
```

**Global concurrency control — cloud and local:**

Both cloud and local providers require bounded concurrency, for different reasons:
- **Local (Ollama):** single GPU/unified memory; concurrent requests spike VRAM and cause thermal throttling. Default: 1.
- **Cloud (Anthropic, OpenAI, etc.):** concurrent request limits exist at every tier. 16 simultaneous Opus requests (8 domains × 2 agents at Stage 4) will hit Anthropic's concurrent request ceiling for non-enterprise accounts and return HTTP 429s. Without a semaphore, the `QuotaAwareRouter` misreads these transient 429s as quota exhaustion and immediately downgrades the entire remaining run to the weakest fallback model.

Separate semaphores are applied per provider class at the LiteLLM call site:

```python
# agents/base.py
import asyncio

_CLOUD_SEMAPHORE: asyncio.Semaphore | None = None
_LOCAL_SEMAPHORE: asyncio.Semaphore | None = None

def get_semaphore(model: str, cloud_concurrency: int, local_concurrency: int) -> asyncio.Semaphore | None:
    global _CLOUD_SEMAPHORE, _LOCAL_SEMAPHORE
    if model.startswith("ollama/"):
        if _LOCAL_SEMAPHORE is None:
            _LOCAL_SEMAPHORE = asyncio.Semaphore(local_concurrency)
        return _LOCAL_SEMAPHORE
    else:
        if _CLOUD_SEMAPHORE is None:
            _CLOUD_SEMAPHORE = asyncio.Semaphore(cloud_concurrency)
        return _CLOUD_SEMAPHORE

async def call_agent(role: str, prompt: str, schema: type[BaseModel],
                     cloud_concurrency: int = 8, local_concurrency: int = 1) -> BaseModel:
    model = config.models[role]
    sem = get_semaphore(model, cloud_concurrency, local_concurrency)
    async with sem:
        return await _do_call(model, prompt, schema)
```

Both semaphores are global singletons — they bound total concurrent calls across all pipeline stages, not just within a single stage. `--cloud-concurrency N` and `--local-concurrency N` override the defaults.

**QuotaAwareRouter — distinguishing rate limits from quota exhaustion:**

HTTP 429 with a `Retry-After` header is a transient burst; the router sleeps and retries the same model. HTTP 429 without `Retry-After` is true quota exhaustion; the router downgrades to the next model in the list. This distinction is implemented inside `QuotaAwareRouter.call()` — see the class definition below. Without it, a burst of 429s from concurrent fan-out would drain the entire fallback list in milliseconds.

**Supported provider strings (LiteLLM format):**
```
claude-opus-4-7          # Anthropic
gpt-4o                   # OpenAI
gemini/gemini-2.0-flash  # Google
ollama/llama3.2          # Local Ollama
ollama/qwen2.5:14b       # Local Ollama
mistral/mistral-large    # Mistral AI
```

---

#### Graceful Quality Degradation

The pipeline structure is invariant — same 8 stages, same adversarial loops, same JSON contracts — regardless of model quality. Quality affects output depth and accuracy, not pipeline correctness. Weaker models produce shallower knowledge expansions; they do not break the pipeline.

**Automatic depth adjustment by model capability:**

```python
MODEL_CAPABILITY_TIER = {
    "claude-opus-4-7":       "frontier",
    "claude-sonnet-4-6":     "strong",
    "gpt-4o":                "frontier",
    "gemini/gemini-2.0-flash": "strong",
    "ollama/llama3.2":       "capable",
    "ollama/llama3.2:1b":    "limited",
}

DEPTH_OVERRIDE = {
    # if researcher model is weaker than requested depth supports, cap it
    ("capable",  "deep"):    "standard",
    ("limited",  "deep"):    "survey",
    ("limited",  "standard"): "survey",
}

ROUND_OVERRIDE = {
    # max adversarial rounds by capability
    "frontier": MAX_ROUNDS,        # full depth-scaled rounds
    "strong":   MAX_ROUNDS,
    "capable":  min(MAX_ROUNDS, 2),
    "limited":  1,                 # single pass, no iteration
}
```

The orchestrating LLM is told its model's capability tier at startup in the JSON Lines output:
```json
{"event": "pipeline_init", "researcher_model": "ollama/llama3.2", "capability_tier": "capable", "effective_depth": "standard", "max_rounds": 2, "note": "depth capped from 'deep': researcher model capability tier is 'capable'"}
```

---

#### Fully Local Mode

With `--profile local`, no API keys are required. The entire pipeline runs on-device:

- LLMs served by Ollama (llama3.2, qwen2.5, mistral, etc.)
- GROBID and AnyStyle run in containers (Java/Ruby, no GPU needed)
- Phoenix observability runs locally
- OpenAlex, Semantic Scholar, Crossref API calls still require internet for bibliography fetch — disable with `--no-bibliography-fetch` to go fully air-gapped (Stage 3 then relies on GROBID+AnyStyle source extraction only)

**Recommended local models by role:**

| Role | Recommended | Minimum |
|---|---|---|
| Researcher / Synthesizer | `qwen2.5:14b` | `llama3.2:3b` |
| Extractor / Classifier / Critic | `llama3.2:3b` | `llama3.2:1b` |

Expected quality at local depth: roughly `survey` regardless of `--depth` flag. The structure, citations, and adversarial audit are all intact; the narrative depth and mathematical derivations are shallower.

**Concurrency note for local mode:** parallel Stage 4 domain agents are serialized via `asyncio.Semaphore(1)` when Ollama is the provider. This prevents unified memory exhaustion and thermal throttling on laptop hardware. Stage 4 on local at standard depth runs sequentially across domains rather than in parallel — slower wall-clock time, but thermally stable. Increase with `--local-concurrency 2` only on workstations with ≥32GB VRAM/unified memory.

---

### Idempotency

Every stage is fully idempotent. Running it twice produces the same result. Running it after a crash produces the same result as running it on a clean state. This is non-negotiable for a long-running pipeline where runs take 30–90 minutes and can be interrupted at any point.

#### Completion Sentinels

Every stage output — both atomic files and parallel-written collections — carries a completion marker. A stage is considered complete if and only if its sentinel exists and is valid.

```
state/
├── pipeline.json              ← top-level run state: per-stage status + timestamps
├── chunks/
│   └── chunk_{N:04d}.done    ← sentinel per chunk (Stage 0)
├── map_outputs/
│   └── map_{ab}_{id}.done    ← sentinel per map agent output (Stage 1)
├── sections/
│   └── section_{domain}.done ← sentinel per domain section (Stage 4)
└── summaries/
    └── summary_{domain}.done ← sentinel per domain summary (Stage 4)
```

`state/pipeline.json` tracks the overall run:
```json
{
  "run_id": "run_20260518_143201",
  "input": "/app/input/paper.pdf",
  "depth": "standard",
  "stages": {
    "0": {"status": "complete", "completed_at": "2026-05-18T14:32:45Z"},
    "1": {"status": "complete", "completed_at": "2026-05-18T14:33:12Z"},
    "2": {"status": "partial", "phase_1": "complete", "phase_2": "pending"},
    "3": {"status": "pending"},
    "4": {"status": "pending"},
    "5": {"status": "pending"},
    "6": {"status": "pending"},
    "7": {"status": "pending"}
  }
}
```

#### Stage-Level Idempotency Logic

```python
async def run_stage(stage_id: int, state: PipelineState, force: bool = False) -> None:
    if not force and stage_is_complete(stage_id, state):
        emit({"event": "stage_skipped", "stage": stage_id, "reason": "already_complete"})
        return

    # Clear any partial outputs before starting
    clear_partial_outputs(stage_id, state)

    # Run the stage
    await STAGES[stage_id](state)

    # Write sentinel
    mark_stage_complete(stage_id, state)
```

#### Parallel-Stage Idempotency (Stage 1 Map, Stage 4 Research)

For parallel stages, each unit (chunk, domain) is independently idempotent. On resume, only units without a `.done` sentinel are re-run. Complete units are skipped. This means a Stage 4 crash that completed 5 of 8 domains only re-runs the 3 incomplete ones.

```python
async def run_stage4_research(state: PipelineState) -> None:
    pending = [
        d for d in state.domain_ids
        if not sentinel_exists(state.sections / f"section_{d}.done")
    ]
    complete = len(state.domain_ids) - len(pending)
    emit({"event": "stage4_resume", "domains_complete": complete, "domains_pending": len(pending)})

    async with asyncio.TaskGroup() as tg:
        for domain_id in pending:
            tg.create_task(run_with_domain_timeout(domain_id, state))

async def run_with_domain_timeout(domain_id: str, state: PipelineState,
                                   timeout: float = 2700.0) -> None:
    try:
        async with asyncio.timeout(timeout):
            await research_domain(domain_id, state)
    except asyncio.TimeoutError:
        emit({
            "event": "domain_timeout",
            "domain_id": domain_id,
            "elapsed_seconds": timeout,
            "action": "domain marked pending; resume with --resume 4"
        })
```

#### What "Clear Partial Outputs" Means

Before re-running any unit, partial output files are deleted — not overwritten, deleted. This prevents corrupted partial writes from a previous crash from being silently merged with new output.

```python
def clear_partial_outputs(stage_id: int, state: PipelineState) -> None:
    patterns = STAGE_OUTPUT_PATTERNS[stage_id]   # e.g., ["sections/section_*.md", "sections/section_*.done"]
    for pattern in patterns:
        for path in state.dir.glob(pattern):
            if not sentinel_exists(path.with_suffix(".done")):
                path.unlink(missing_ok=True)      # only clear incomplete units
```

#### The Human Checkpoint Is Idempotent

Stage 2 Phase 1 (taxonomy review) pauses for `$EDITOR`. After the editor exits, the pipeline re-reads `state/taxonomy.json` and checks for an explicit `"status": "approved"` field set by the human. Quitting without saving (`:q!` in vi) leaves the field as `"pending"` — the pipeline detects this and pauses cleanly rather than launching an 8-domain Opus fan-out on unreviewed taxonomy.

```python
def taxonomy_checkpoint(state: PipelineState) -> None:
    if not taxonomy_is_approved(state):
        write_taxonomy_proposals(state)
        editor = os.environ.get("EDITOR", "vi")
        subprocess.call([editor, str(state.dir / "taxonomy.json")])

        # Re-read after editor exits — do not assume approval
        taxonomy = json.loads((state.dir / "taxonomy.json").read_text())
        if taxonomy.get("status") != "approved":
            emit({
                "event": "pipeline_paused",
                "reason": "taxonomy_not_approved",
                "instructions": (
                    'Set "status": "approved" in state/taxonomy.json, '
                    "then run: doc-expand --resume 2b"
                )
            })
            sys.exit(0)
        mark_taxonomy_approved(state)
```

---

### Model Quota Switching

The orchestrating LLM and the pipeline's internal agents may both hit rate limits or quota exhaustion mid-run. The pipeline must handle this gracefully without losing progress or failing the build.

#### Priority Fallback Lists

Each role defines a **priority-ordered list** of models in `models.yaml`. When the primary model exhausts its quota, the pipeline automatically falls back to the next in the list:

```yaml
roles:
  researcher:
    - "claude-opus-4-7"          # primary
    - "claude-sonnet-4-6"        # fallback 1: lower cost, lower quality
    - "gpt-4o"                   # fallback 2: different provider
    - "ollama/qwen2.5:14b"       # fallback 3: fully local, no quota
  extractor:
    - "claude-sonnet-4-6"
    - "gpt-4o-mini"
    - "ollama/llama3.2:3b"
  critic:
    - "claude-sonnet-4-6"
    - "gpt-4o-mini"
    - "ollama/llama3.2:3b"
  synthesizer:
    - "claude-opus-4-7"
    - "claude-sonnet-4-6"
    - "gpt-4o"
    - "ollama/qwen2.5:14b"
```

#### Runtime Quota Detection and Switching

```python
class QuotaAwareRouter:
    def __init__(self, role: str, models: list[str]):
        self.role = role
        self.models = models
        self.current_index = 0
        self.exhausted: set[str] = set()

    async def call(self, prompt: str, schema: type[BaseModel]) -> BaseModel:
        while self.current_index < len(self.models):
            model = self.models[self.current_index]
            try:
                return await litellm.acompletion(model=model, ...)
            except RateLimitError as e:
                # Inspect Retry-After before deciding whether to downgrade.
                # 429 + Retry-After = transient burst; back off and retry the same model.
                # 429 without Retry-After = true quota exhaustion; switch to next model.
                retry_after = (
                    getattr(e, "response", None)
                    and e.response.headers.get("retry-after")
                )
                if retry_after:
                    await asyncio.sleep(int(retry_after))
                    continue   # retry same model
                self.exhausted.add(model)
                self.current_index += 1
                emit({
                    "event": "model_quota_switch",
                    "role": self.role,
                    "exhausted_model": model,
                    "next_model": self.models[self.current_index] if self.current_index < len(self.models) else None,
                    "reason": str(e)
                })
                log_model_switch(self.role, model, self.models[self.current_index])

        # All models exhausted
        emit({"event": "quota_exhausted", "role": self.role, "all_models_tried": list(self.exhausted)})
        raise QuotaExhaustedError(f"All models exhausted for role: {self.role}")
```

#### Quota Exhaustion Is a Pause, Not a Failure

When all fallbacks for a role are exhausted, the pipeline emits a `quota_exhausted` event and **pauses cleanly at the current stage boundary** (not mid-stage). Progress is preserved via sentinels. The orchestrating LLM receives the event, can wait for quota reset, update `models.yaml` with new credentials, and call `--resume N` to continue.

```json
{"event": "quota_exhausted", "stage": 4, "domain_id": "inference_systems", "role": "researcher", "domains_complete": 5, "domains_pending": 3, "resume_command": "doc-expand --resume 4"}
```

#### Model Usage Audit Log

Every LLM call is logged to `state/audit/model_usage.jsonl`:
```jsonl
{"ts": "2026-05-18T14:45:01Z", "stage": 4, "domain": "inference_systems", "role": "researcher", "model": "claude-opus-4-7", "input_tokens": 8420, "output_tokens": 3100, "cached_tokens": 6200, "cost_usd": 0.041}
{"ts": "2026-05-18T14:51:33Z", "stage": 4, "domain": "hardware_arch", "role": "researcher", "model": "claude-sonnet-4-6", "input_tokens": 7800, "output_tokens": 2900, "cached_tokens": 6200, "cost_usd": 0.009, "switched_from": "claude-opus-4-7", "switch_reason": "quota_exceeded"}
```

This log is the full cost and provenance record for the run. If the final document used two different researcher models due to a mid-run quota switch, the audit log shows exactly which domains were written by which model — enabling reproducibility analysis.

#### Reproducibility Flag

If a model switch occurred during a run, the final `pipeline.json` carries a `"model_consistency": "mixed"` flag. The assembled document's frontmatter includes a note:

```yaml
model_consistency: mixed
model_switches:
  - stage: 4
    domain: hardware_arch
    from: claude-opus-4-7
    to: claude-sonnet-4-6
    reason: quota_exceeded
```

A fully consistent run (no switches) carries `"model_consistency": "uniform"`.

---

### Existing Work Integration

These are not dependencies to install and forget — each replaces or augments a specific pipeline step with proven, maintained work rather than a hand-rolled equivalent.

#### STORM / Co-STORM (Stanford) → Stage 4 researcher agent design

STORM's core contribution is **perspective-guided questioning**: before writing, agents embodying different expert personas generate questions about the topic from their angle, then the answers drive the research. This maps directly onto Stage 4's top-down / bottom-up complementary agents. Concretely: Stage 4 agents draw their expert persona from the domain's OpenAlex concept level — an L1 concept agent (survey-level) vs. an L4 concept agent (specialist-level) — mirroring STORM's multi-perspective structure. STORM is installable (`pip install knowledge-storm`; Co-STORM integrated since v1.0.0, September 2024); its prompting patterns for adversarial multi-agent research are directly applicable even if the full library isn't used.

**What it replaces:** ad-hoc "write a section about X" prompts. Gives Stage 4 agents a structured, debate-tested framework for generating deep, non-redundant coverage.

#### GROBID + AnyStyle → Stage 3 bibliography seeding

Before fetching any papers from Semantic Scholar, GROBID (ML-based, 87-90% F1 on reference extraction, TEI-XML output) and AnyStyle (CRF-based, v1.6.0, self-hostable) extract and parse the references the source document itself already contains. These are the most relevant papers by definition — the authors chose them. Two independent extractors provide cross-validation: papers appearing in both outputs get `confidence: high` in `bibliography_{domain}.json`; papers in one only get `confidence: medium`. This seeds Stage 3 with ground-truth signal before any API call happens.

**What it replaces:** fetching cold from Semantic Scholar with no prior signal. GROBID+AnyStyle make Stage 3 bibliography fetch targeted, not exploratory.

#### OpenAlex concept taxonomy → Stage 2 Phase 1 taxonomy validation + Stage 3 anchor fetch

OpenAlex (~297M works, 65,000 hierarchical concepts, 2B+ citation edges, free API) does two jobs:

1. **Stage 2 Phase 1:** proposed domain taxonomy nodes must resolve to real OpenAlex concept IDs via `api.openalex.org/concepts?search={label}`. Concept level (L0–L5) validates lumper vs. splitter — lumper proposals should land at L0-L2; splitter at L3-L5. This is the external anchor that prevents the ontologist from inventing unprincipled domains.

2. **Stage 3 anchor fetch:** OpenAlex's concept-filtered paper search (`/works?filter=concepts.id:{id}&sort=cited_by_count:desc`) returns top-cited papers for each locked domain. More comprehensive than Semantic Scholar for non-CS fields (biology, law, economics, materials science).

**What it replaces:** Semantic Scholar alone for domain taxonomy validation. OpenAlex is domain-agnostic at scale; Semantic Scholar skews CS/ML.

#### Docling (IBM) → Stage 0 primary document parser

Docling (MIT license, ~30k stars, integrated into LangChain/LlamaIndex, DocLayNet layout model + TableFormer table model) produces richly structured document representations from PDF, DOCX, PPTX, HTML — tables, equations, figures, section hierarchy. Docling is the sole PDF parser. Structural zone extraction uses Docling's `SECTION_HEADER`/`TITLE` element labels — a semantically richer signal than font-flag heuristics, trained on academic layout patterns.

**What it replaces:** raw `pdfplumber` text extraction. Docling understands document semantics; pdfplumber extracts characters.

#### KeyBERT + BGE-M3 → Stage 1 third Map signal

KeyBERT with BGE-M3 embeddings (BAAI, strong MTEB performer for multilingual and technical text, fully local, MIT license) provides a non-LLM, embedding-based keyword extraction signal independent of the Claude agents' weights. The three Stage 1 Map signals are: Agent A (lexical LLM), Agent B (conceptual LLM), KeyBERT/BGE-M3 (semantic embeddings). The Reduce phase reconciles: in all three → `core`; in two → frequency/structural decides; in KeyBERT only, not LLMs → likely a genuine term the LLMs glossed over; in LLMs only, not KeyBERT → possible boilerplate. The LLM/KeyBERT disagreement is the highest-signal input to the Reduce critic.

**What it replaces:** pure LLM extraction, which shares the same weight-space biases across both agents. BGE-M3 is an entirely independent signal source.

#### DSPy → adversarial loop implementation + automatic prompt optimization

DSPy's `dspy.Refine` module is the adversarial Generator→Critic→Generator loop. `Refine` runs a submodule up to N times, scoring each output via a `reward_fn` closure (which wraps the Critic agent + external truth), accepting when the score exceeds a threshold. Defining Generator and Critic as DSPy `Signature`s enables teleprompter-based prompt tuning over accumulated runs.

**Critical:** `dspy.Assert` is **deprecated as of DSPy 2.6** (mid-2025). Do not use it. The correct API is `dspy.Refine(module, reward_fn, threshold, N)`. Migration from Assert to Refine requires explicit `reward_fn`, `threshold`, and `N` parameters — simple replacement fails with TypeError.

**What it replaces:** hand-rolled retry loops with manually written critic prompts. DSPy makes the loop structured, testable, and improvable.

#### LangGraph → pipeline orchestration + checkpointing

LangGraph provides stateful graph execution with production-grade checkpointing (pause, resume, time-travel, state inspection). The `doc-expand` pipeline IS a directed graph; using a graph execution framework is the natural implementation. The `state/` directory layout maps 1:1 to LangGraph's TypedDict state schema. `--resume N` becomes `graph.invoke(state, config={"checkpoint_id": ...})`. Parallel Map agents in Stage 1 and parallel domain agents in Stage 4 are LangGraph `Send` API fan-out patterns.

**What it replaces:** hand-rolled stage orchestration, manual `--resume` directory scanning, and `asyncio.gather` without structured cancellation.

#### Typst → optional PDF renderer

Typst (v0.14+, pre-1.0 but production-ready, actively maintained, millisecond incremental compilation, ~27× faster than xelatex, no double-pass required) is offered as `--renderer typst` for users without a TeX installation. Faster for development iteration. Default remains `xelatex` because the `preprocess.py` + `llm_paper_style.tex` pipeline from the LLM KG project is proven, and Typst's math rendering for complex multi-line derivations is still maturing.

**What it does not replace:** xelatex as the production renderer for final output.

---

### Technology Stack

#### Pipeline Components

| Stage | Component | Tool | Why |
|---|---|---|---|
| 0 | Document parsing + structural zones | `Docling` (IBM, MIT) | Single-pass: layout (tables, equations, figures), semantic chunking via `HybridChunker`, structural zones from `SECTION_HEADER`/`TITLE` labels |
| 0 | URL fetch | `httpx` | Async, timeout; PotatoMCP `fetch` as Cloudflare fallback |
| 0 | Token counting | `anthropic` SDK | Consistent with Claude's actual tokenizer |
| 1 | Lexical extraction (Signal A) | `spaCy` | Deterministic NER + noun chunker + regex acronyms; reused for structural zone term extraction in Stage 0 |
| 1 | Conceptual extraction (Signal B) | LLM Agent B via LiteLLM | What a domain expert recognizes as load-bearing; one LLM call per chunk |
| 1 | Semantic extraction (Signal C) | `KeyBERT` + `BGE-M3` | Embedding-based keyphrase centrality; non-LLM; strong divergence signal vs. Agent B |
| 1 | Term deduplication | `numpy` cosine + Union-Find | BGE-M3 cosine finds near-duplicate pairs; Union-Find (stdlib, ~30 lines) clusters aliases; no library dependency |
| 2 | Taxonomy validation | OpenAlex concept API | ~65k hierarchical concepts; L0-L5 depth validates lumper/splitter proposals |
| 3 | Source doc reference extraction | `GROBID` + `AnyStyle` | Two independent extractors; cross-validated confidence scoring |
| 3 | Bibliography fetch | OpenAlex + Semantic Scholar + Crossref | OpenAlex primary (domain-agnostic); SS secondary (CS/ML); Crossref tertiary |
| 3 | arXiv fallback | arXiv Export API | CS/Math/Physics domains only |
| 3 | External API rate limiting | `aiolimiter` (MIT) | Async token bucket per API; Semantic Scholar (100 req/5 min), Crossref (Polite Pool + `mailto:`), OpenAlex (10 req/s); prevents IP bans in Stage 3 |
| 4/5 | Agent design patterns | STORM perspective framework | Persona-driven expert agents at domain concept depth level |
| 4/5 | Adversarial loops | `DSPy` `dspy.Refine` | Generator→Critic loops; `reward_fn` wraps critic + external truth; early exit when threshold exceeded. Note: loop runs inside a LangGraph node — resume restarts from round 1 on crash (accepted marginal cost). |
| All | LLM routing | `LiteLLM` | Provider-agnostic: Claude, GPT-4o, Gemini, Ollama local — provider is config not code |
| All | LLM agents (default) | `anthropic` SDK via LiteLLM | Opus 4.7 for research/synthesis; Sonnet 4.6 for extraction/classification |
| All | Pipeline orchestration | `LangGraph` | Stateful graph execution, fan-out/fan-in, checkpointing for `--resume` |
| 7 | Preprocessing | `style/preprocess.py` | Reused from LLM KG project |
| 7 | LaTeX style | `style/llm_paper_style.tex` | Reused from LLM KG project |
| 7 | PDF build (default) | `pandoc` + `xelatex` ×2 | Proven pipeline; complex math support |
| 7 | PDF integrity check | `pypdf` | Verifies page count and stream integrity after xelatex; catches silent corruption that exit code 0 misses |
| 7 | PDF build (optional) | `Typst` v0.14+ | ~27× faster than xelatex; no TeX dependency; no double-pass; `--renderer typst` flag |

#### Infrastructure & Utilities

| Category | Tool | Why / Notes |
|---|---|---|
| **LLM routing** | `LiteLLM` | Single interface for 100+ providers; swap Claude → GPT-4o → Ollama via config; no code changes |
| **Local LLM inference** | `Ollama` (Docker profile: local) | Runs Llama, Qwen, Mistral locally; no API keys; activated with `--profile local` |
| **Structured outputs** | `Instructor` + `Pydantic` v2 | Wraps tool use with schema validation + auto-retry; works across all providers; preferred for LLM-agnostic design. Anthropic has a grammar-constrained structured outputs beta (announced Nov 2025) but model support is in flux — do not depend on it as primary |
| **Schema validation** | `Pydantic` v2 | All JSON contracts (`TermInventory`, `KnowledgeGraph`, `DomainSummary`) defined as `BaseModel` |
| **NLP pipeline** | `spaCy` (en_core_web_sm or domain model) | Stage 1 Signal A: NER, noun chunks, acronym detection; deterministic, zero LLM cost |
| **API rate limiting** | `aiolimiter` (MIT) | Async token bucket; one limiter per external API (Semantic Scholar, Crossref, OpenAlex); `AsyncLimiter(max_rate, time_period)` |
| **Local inference throttle** | `asyncio.Semaphore(N)` | Bounds concurrent Ollama calls to `--local-concurrency N` (default 1); cloud providers bypass |
| **Async orchestration** | `asyncio.TaskGroup()` (Python 3.11+) | Native structured concurrency; consistent with `asyncio.Semaphore`; no backend mismatch. Replaces anyio. |
| **Retry / resilience** | Anthropic SDK built-in + `Tenacity` | SDK handles API rate limits; Tenacity wraps orchestration functions |
| **Prompt caching** | Anthropic prompt cache | Cache system prompts, format specs, bibliography JSONs; minimum: Sonnet 4.6 ≥2048 tokens, Haiku 4.5 ≥4096 tokens, Opus 4.7 ≥4096 tokens; up to 90% cost reduction |
| **Response caching** | `DiskCache` | SQLite-backed local cache for deterministic API calls (taxonomy fetch, anchor fetch); zero external dependencies; benchmarks show it is faster than Redis in local scenarios due to NVMe/mmap leverage |
| **Observability (default)** | flat `model_usage.jsonl` | Zero-dependency; `cat state/audit/model_usage.jsonl \| jq` answers every debug question |
| **Observability (optional)** | `Phoenix` (Arize, ELv2) or `Langfuse` (MIT) | Enable with `--observability phoenix\|langfuse`; useful for multi-run tuning and agent session replay. Not required for single-document CLI use. |
| **Graph storage** | `NetworkX` + `graph.json` | In-memory ops: topological sort, DAG traversal, neighbor lookup. Sufficient at ≤200 nodes; no compiled binary dependency. |
| **Terminal UI** | `Rich` | Progress bars, panels, tables, syntax highlighting; pipeline stage display |
| **Data validation (bulk)** | `Pandera` | DataFrame-level schema validation for batch term inventory and citation index |
| **HTTP client** | `httpx` | Async; timeout support; used for all external API calls |

#### What Not to Use

| Tool | Why Not |
|---|---|
| `Kuzu` (embedded graph DB) | Officially archived October 2025; community forks exist but unmaintained |
| `DuckDB` + DuckPGQ | OLAP engine at 100-node graph scale is absurd overhead; NetworkX + `graph.json` covers all required ops in two-line Python |
| `FAISS` | ANN index for billion-scale search; numpy brute-force cosine over ≤300 terms is faster and has zero dependencies |
| Direct `anthropic` SDK calls (bypassing LiteLLM) | Locks the pipeline to one provider; defeats the LLM-agnostic architecture |
| `LangChain` | Heavy abstraction layer; LangGraph is sufficient and lighter |
| `AutoGen` / `CrewAI` | Alternative multi-agent frameworks; LangGraph + Anthropic SDK is cleaner |
| `pdfplumber` as primary parser | Less accurate than Docling for layout; Docling's `SECTION_HEADER`/`TITLE` labels are the structural signal |
| `Redis` | Requires external process; DiskCache is sufficient for CLI-local caching |
| `Ray` / `Celery` | Distributed compute overkill; `asyncio.TaskGroup()` handles CLI parallelism |
| `anyio` | AnyIO was justified before `asyncio.TaskGroup` (Python 3.11). Mixing `anyio.create_task_group()` with `asyncio.Semaphore` causes cancellation propagation mismatches and silent deadlocks under 429 backoff pressure. Use native asyncio throughout. |
| `ChromaDB` / `Qdrant` | Embedded vector DBs add persistence overhead; numpy cosine is sufficient for transient deduplication |
| `marker-pdf` | GPL license constraint; Docling covers the same ground under MIT |
| `nano` as default editor | Not universally available; use `os.environ.get("EDITOR", "vi")` |
| Phoenix/Langfuse as required service | Nobody runs a telemetry Docker container for a single CLI run; make it `--observability` opt-in |

---

### Learnings from LLM Knowledge Graph Project (this repo)

This project is the reference implementation. Every pattern below was validated in production.

| Pattern | Source | Applied in |
|---|---|---|
| Scaffold before content | methodology_trace.md P7 | Stage 7 assembly fills a pre-written template |
| Structural audit before web research | P4 | Stage 3 runs after Stage 2; external anchors fetched before any gap analysis |
| DAG-model tasks, identify true parallelism | P5 | Stage 1 Map, Stage 4 research all parallel; Reduce and Phase locks are sequential |
| Constrained prompts → structured outputs | P6 | Every agent has strict JSON output schema |
| Anti-hallucination in all subagent prompts | P8 | Explicit in all Stage 4 researcher prompts |
| Main session citation sweep | P12 | Stage 6 runs in the main process, not a subagent |
| Isolated output files for parallel writers | P13 | Each Stage 4 agent writes to its own `section_{id}.md` |
| Citation index in assembly prompts | P14 | Bibliography JSON passed to Stage 4 agents before writing begins |
| WebSearch denied in subagents | F3 | Stage 6 is entirely main process; subagents tag `[UNVERIFIED]` for main session |
| brace expansion fails in MCP bash | F1 | No brace expansion anywhere in shell commands |
| cp in background hangs | CLAUDE.md | Use `cp -f`; never background a cp command |
| rsync for bulk sync, not write_file | CLAUDE.md | Bulk output transfer uses rsync; `write_file` only for small files |
| XP totals must not be hand-counted | content review M7 | Stage 7 derives all aggregate numbers from `graph.json` |
| PDF needs double xelatex pass | build experience | Encoded in `build_pdf()` — two passes, no exceptions |

### Anti-Patterns (never do these)

- **Single-pass extraction of a full document:** guaranteed silent omission. Always chunk first.
- **Dynamic LLM clustering without taxonomy lock:** produces overlapping domains and duplicated downstream research.
- **Cold self-audit without external anchors:** the model grades its own homework and rubber-stamps the graph.
- **Hallucinate-then-verify citation flow:** verification of hallucinated titles fails 100%. Ground citations before writing.
- **arXiv as primary citation oracle:** breaks for non-CS domains.
- **Raw narrative `.md` sections as synthesis input:** context window explosion at deep depth.
- **Pure frequency-based centrality:** boilerplate outranks novel mechanisms.
- **Open-ended research prompts to subagents:** unconstrained agents drift. Always provide format spec verbatim.
- **Silent resume after partial crash:** append to partial files corrupts structure. Always clear before restart.
- **Single xelatex pass:** TOC full of `??`. Always two passes.
- **`nano` as default editor:** not universally available. Default to `vi`; respect `$EDITOR`.
- **`dspy.Assert` in adversarial loops:** deprecated in DSPy 2.6. Use `dspy.Refine(module, reward_fn, threshold, N)`.
- **Single model per role with no fallback:** quota exhaustion kills the run. Always define a priority fallback list.
- **Overwriting partial outputs on resume:** silently merges crashed writes with new output, corrupting structure. Always delete before rewriting.
- **Treating quota exhaustion as a fatal error:** it is a pause event. Emit the event, preserve progress via sentinels, wait for `--resume`.
- **Using a vector DB for single-document term deduplication:** FAISS, ChromaDB, Qdrant — all overkill for ≤300 terms. Compute cosine similarity with numpy over BGE-M3 embeddings (already loaded for KeyBERT). No index to build, no dependency to manage.
- **Using a graph library for alias clustering of ≤300 terms:** NetworkX for disjoint-set merging is pulling in Dijkstra's algorithm to do a dictionary merge. Use Union-Find (~30 stdlib lines).
- **Running two PDF parsers over the same file:** Docling + PyMuPDF double ingestion time and spike memory. Docling's `SECTION_HEADER`/`TITLE` labels replace PyMuPDF's `span["flags"]` heuristic — single pass, cleaner structural signal.
- **Implicit flag coupling:** flags must never silently activate or suppress each other. Explicit is better than implicit.
- **Mixing anyio task groups with asyncio synchronization primitives:** `asyncio.Semaphore` inside `anyio.create_task_group()` causes cancellation propagation mismatches. Use native `asyncio.TaskGroup()` (Python 3.11+) throughout.
- **Trusting xelatex exit code 0 as proof of a valid PDF:** xelatex exits 0 on font errors while producing a partial/corrupt file. Always verify PDF structural integrity with `pypdf` (page count + stream check) before the Stage 7 node returns — LangGraph writes the completion checkpoint on node return, not on subprocess exit. A byte-size floor is not a substitute: a short valid document can be under 100KB, and a silently truncated large document can be over it.
- **Unconditional structural promotion:** `if name in structural_zones: return "core"` floods the core tier with bolded dataset names, italicized variables, and highlighted metrics. Structural presence is a weight multiplier (`effective = occurrences × 2.0`), not a frequency bypass.
- **Bibliography sorted by citation count only:** guarantees blindness to the last 24 months. Use two buckets: 65% foundational (all-time citation rank) + 35% frontier (last 24 months, citation-ranked within window).
- **Generating a `.bib` file for bibliography:** Pandoc accepts CSL-JSON natively via `--bibliography`. Stage 3 citation objects are already in CSL-JSON format. No BibTeX intermediate, no `bibtexparser` dependency, no format conversion step.
- **Applying semaphore only to local inference:** cloud APIs have concurrent request limits too. 16 simultaneous Opus calls in Stage 4 will hit Anthropic's ceiling and generate 429s. Without a cloud semaphore, the `QuotaAwareRouter` misreads these transient rate limits as quota exhaustion and degrades the run to local models within seconds. Apply `asyncio.Semaphore(--cloud-concurrency)` to all providers.
- **Treating all HTTP 429s as quota exhaustion:** 429 + `Retry-After` header = transient rate limit; back off and retry the same model. 429 without `Retry-After` = true exhaustion; switch model. Conflating them wastes the fallback list on recoverable errors.
- **Running two LLM agents for lexical extraction:** named entities, acronyms, and noun phrases are deterministic NLP tasks. spaCy handles them in milliseconds with no API call. Reserve LLM calls for conceptual extraction (Agent B), where reasoning over implicit domain knowledge actually matters.
- **Fan-out parallel inference against local Ollama without a semaphore:** concurrent requests against a single-GPU Ollama instance do not parallelize — they spike memory and trigger thermal throttling. Always apply `asyncio.Semaphore(--local-concurrency)` at the LiteLLM call site when the provider is `ollama/`.
- **Storing orchestrating LLM context as pipeline state:** the orchestrating LLM's conversation history is ephemeral. `pipeline.json` and `.done` sentinels are the canonical state store. Use `doc-expand --status` to re-orient after any pause — never rely on the LLM remembering previous events.
- **Skipping `pipeline.json` status tracking:** without it, `--resume` cannot know which stages are truly complete vs. partially written.
- **Human review checkpoints beyond Stage 2:** the pipeline must be fully autonomous from Stage 3 onward. Any `input()`, `subprocess.call([editor, ...])`, or `pause_for_review()` call outside Stage 0.5 and Stage 2 is a design defect. Route all other decisions through the adversarial loop or the orchestrating LLM.
- **Generic assessment questions that don't use structural_zones:** Q3, Q6, and Q7 must be tailored to the actual document's vocabulary. Questions like "Do you know what a neural network is?" on a transformer architecture paper are uncalibrated and useless.
- **Treating Q3/Q7 wrong answers as problems:** they are the most useful signal in the assessment. An expert who gets Q7 wrong has found a gap. A novice who gets Q3 right has found prior knowledge. Neither outcome is bad.
- **Adding `--interactive` to LLM-orchestrated invocations:** an orchestrating LLM has no terminal. The default autonomous mode already skips Stage 0.5. `--interactive` is for direct human use only.
- **Requiring `--auto-taxonomy` explicitly for LLM-driven runs:** when the orchestrating LLM is driving the pipeline end-to-end, `--auto-taxonomy` should be the default invocation. The `$EDITOR` path is a convenience for direct human use, not the canonical operating mode.
- **Leaving parallel domain tasks without a hard timeout:** `asyncio.TaskGroup` waits for all tasks. A domain agent stuck in exponential API backoff will idle the entire group indefinitely at 99% completion. Wrap each domain task with `asyncio.timeout(2700)` and emit a clean `domain_timeout` event on expiry so the group can finish and the stage can be resumed.
- **Calling `mark_taxonomy_approved` unconditionally after editor exit:** `subprocess.call` returns when the user closes the editor — it does not indicate they saved. If the user quits without saving (`:q!`), the taxonomy file is unchanged and still has `"status": "pending"`. Always re-read the file after editor exit and verify the status field before proceeding. Unconditional approval launches an 8-domain Opus fan-out on unreviewed garbage.
- **Checking HTTP 429 at the wrong level:** inspecting `Retry-After` must happen inside `QuotaAwareRouter.call()`, not in separate prose or a disconnected code block. A plain `except RateLimitError` that immediately downgrades the model will drain the fallback list on a single transient burst.

---

## Implementation Plan

### Phase 1 — Core extraction pipeline (Stages 0–2)
Build and validate on a known document (a paper from this repo's bibliography). Validate Stage 1 centrality output against manually identified key terms. Validate Stage 2 taxonomy against known domain structure.

### Phase 2 — Anchored audit + bibliography (Stage 3)
Validate Crossref and Semantic Scholar API integration. Test with a biology paper (non-CS) to confirm domain-agnostic oracle works.

### Phase 3 — Wire research pipeline (Stage 4)
Adapt domain researcher prompt from LLM KG project. Validate bibliography constraint is enforced. Validate `summary_{domain}.json` schema output.

### Phase 4 — Synthesis + verify (Stages 5–6)
Validate synthesis consumes only JSON summaries. Validate citation structural audit catches agent defections.

### Phase 5 — Assembly + build (Stage 7)
Reuse `preprocess.py` and `llm_paper_style.tex` directly. Validate double xelatex pass resolves TOC page numbers.

### Phase 6 — Adversarial loops + complementary redundancy
Add Generator/Critic loops to each stage. Add complementary Agent A/B to each stage. This is layered on top of the working pipeline, not built first.

### Phase 7 — Integration test
Full pipeline run on a short, domain-known paper. Compare output quality against the LLM KG paper as the reference bar.

---

## Reference Implementation

This repository (`llm-knowledge-graph/`) is the reference implementation of Stages 4–7. Key artifacts:

- `output/llm_lifecycle_paper.md` — what "deep" depth output looks like (480KB, 6347 lines, 81 nodes)
- `output/llm_lifecycle_paper.pdf` — rendered PDF (10MB, 241 pages)
- `audit/gap_analysis.md` — what anchored gap analysis produces
- `audit/corrections.md` — what factual correction logging looks like
- `audit/methodology_trace.md` — full pattern and failure log from the original run
- `style/preprocess.py` — reuse directly
- `style/llm_paper_style.tex` — reuse directly
- `CLAUDE.md` — infrastructure: server, rsync, build pipeline, known gotchas
