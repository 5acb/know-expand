# know-expand — CLAUDE.md

Multi-agent CLI that turns any technical document (PDF, URL, text) into a research-grade knowledge expansion. Runs as a LangGraph pipeline with an optional web dashboard.

## Run / serve

```bash
uv run know-expand path/to/paper.pdf          # run pipeline (stdout progress)
uv run know-expand serve                       # web UI at http://localhost:7842
uv run know-expand serve --port 7843          # custom port
uv run know-expand --status                   # check current state_dir
```

Each run creates `runs/{run_id}/` containing `state/`, `logs/`, `output/`.
`runs/` is the default runs directory; override with `--runs-dir`.

## Pipeline stages

**Stage order:** `ingest → extract → assess → graph → audit → research → align → synthesize → quality_eval → prereq → verify → assemble`

Note the runtime order is `prereq` before `verify` (not `verify` before `prereq` as the pipeline-stage numbers S8/S9 might suggest) — this was already true before the quality-eval stage was added; `quality_eval` slots in right after `synthesize`, ahead of both.

S2 Extract runs before S1 Assess so the interview presents real extracted terms instead of LLM guesses.

| Stage | File | Does |
|-------|------|------|
| S0 Ingest | `s0_ingest.py` | Docling parse (PDF/URL/text), HybridChunker, structural zones via spaCy `en_core_web_sm` |
| S2 Extract | `s2_extract.py` | 3-signal extraction (spaCy + KeyBERT/BGE-M3 + LLM); Union-Find alias dedup; centrality scoring; **LLM batch classification into `academic` / `tool_library` / `concept`** |
| S1 Assess | `s1_assess.py` | LLM-driven 10–15 turn interview using extracted core terms → `UserProfile`; web IPC (`qa_queue.jsonl` / `qa_answers.jsonl` / `qa_complete`) or TTY |
| S3 Graph | `s3_graph.py` | Lumper/Splitter taxonomy proposals, OpenAlex validation, human web review or `--auto-taxonomy`, term → domain assignment → `graph.json` |
| S4 Audit | `s4_audit.py`, `s4_tools.py` | SS anchor papers + bibliography (65% foundational / 35% frontier); adversarial Gap Finder → Defender → Rebuttal loop, with Finder/Defender each running a live-tool ReAct grounding pass (`_run_react_grounding`, built on `call_with_tools`) before their structured verdict — Finder checks candidate gaps via `check_recent_coverage` (SS/arXiv), Defender checks claimed rebrands via `check_graph_synonym` (graph-term similarity + Wikipedia); falls back to the ungrounded prompt in `--no-bibliography-fetch` mode or on tool/model failure; **multi-source fetch (Wikipedia/PyPI/arXiv) cached at `audit/sources/sources_{domain_id}.json`** |
| S5 Research | `s5_research.py` | Three-persona parallel research (Theoretician / Engineer / Practitioner) + Reconciler; adversarial critic loop; full pedagogical protocol; knowledge_sources injected into all prompts |
| S6 Align | `s6_align.py` | Pedagogical alignment pass: inject symbol tables, worked examples, where-to-go-next, resolve `[NEEDS_CITATION]` |
| S7 Synthesize | `s7_synthesize.py` | Cross-domain synthesis, connector bridges, boss nodes; reads ONLY `graph.json` + `summary_*.json` |
| S7b Quality Eval | `s7b_quality_eval.py` | Terminal agent-as-judge scoring pass (12-dimension rubric, 3 weighted categories, adapted from arXiv 2509.18661); scores are logged to `audit/quality_eval*.json`/`.md`, never gates or triggers revision |
| S8 Verify | `s8_verify.py` | Structural citation audit + two-check claim verification: live abstract fetch (`fetch_paper_abstract`) when the cached one is missing, then a debiased ensemble-adjudicated entailment check (`ensemble_verify`) instead of a single-model verdict; `[NEEDS_CITATION]` logged, not a build failure |
| S9 Prereq | `s9_prereq.py` | Blockquote primers for unknown terms |
| S10 Assemble | `s10_assemble.py` | Topological sort → stitch → pandoc + xelatex ×2; CSL-JSON bibliography; PDF size check |

Stages skip automatically if already complete (`stage_is_complete()` checks `pipeline.json`). Resume a stopped run via the web UI Resume button or by keeping stage markers in `pipeline.json`.

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
    base.py       — QuotaAwareRouter, DeepInfra/NVIDIA NIM providers, _instructor_mode(), probe_models()
    schemas.py    — all Pydantic models for LLM structured outputs
  stages/
    s*.py         — one file per stage

config.yaml       — all tunable knobs
models.yaml       — model lists per role (researcher, extractor, classifier, etc.)
runs/             — one subdir per run: state/ logs/ output/
```

## LLM routing (`agents/base.py`)

### QuotaAwareRouter

Tries models in order from `models.yaml` for the given role. Fallback rules:

| Error type | Action |
|------------|--------|
| `litellm.AuthenticationError` (may be wrapped in `InstructorRetryException`) | Permanently skip model; use `_find_in_chain()` to walk exception chain |
| Rate-limit / 503 **with** `Retry-After` header | Sleep exact duration, retry same model |
| Headerless 429/503 | Exponential backoff (2s → 4s → 8s); skip after 3 consecutive failures |
| All models exhausted | Emit `quota_exhausted`; raise `RuntimeError` |

`_next_model()` checks both `self._skip` (per-router) **and** `_PROBED_UNAVAILABLE` (global) — so a quota discovery in one coroutine immediately affects all other routers.

### DeepInfra / NVIDIA NIM providers

Both are plain litellm-native model prefixes (`deepinfra/*`, `nvidia_nim/*`) — no custom transport code, unlike the geminicli/llamacpp providers they replaced (removed 2026-09). Auth is a single env var per provider (`DEEPINFRA_API_KEY`, `NVIDIA_API_KEY`); `_probe_one()` checks it before any LLM call.

DeepInfra (Palo Alto, CA; US data centers; SOC 2 / ISO 27001; zero data retention) is the consolidated open-weight fallback tier — replaces the old Groq / Mistral / local llama.cpp / geminicli patchwork with one provider, one API key, one quota model. It also hosts some Chinese-lab open-weight checkpoints (GLM-5.2, Kimi-K2.7-Code) — see "No Chinese API providers" below for why that's fine.

### Instructor modes (`_instructor_mode(model)`)

`instructor.from_litellm()` defaults to `TOOLS` mode (function calling). The open-weight Llama/Mistral models served through DeepInfra return parallel tool calls for single-schema requests, which instructor rejects — same behavior these model families showed via Groq/Mistral's own APIs. Use JSON mode instead:

| Model | Mode | Reason |
|-------|------|--------|
| `deepinfra/*` | `instructor.Mode.JSON` | Open-weight Llama/Mistral models return parallel tool calls in TOOLS mode |
| `gemini/*` | `instructor.Mode.JSON` | `GEMINI_TOOLS` mode requires native `google-generativeai` SDK; litellm works with JSON mode |
| everything else | `instructor.Mode.TOOLS` | Default; correct for OpenAI/Anthropic/NVIDIA NIM |

### Per-provider concurrency semaphores

DeepInfra gets its own `asyncio.Semaphore(5)` to bound burst concurrency against per-model rate limits (it's pay-as-you-go, not free-tier-capped like the old Groq setup, so this is a courtesy cap rather than a hard necessity). All other models share the global semaphore (`cfg.concurrency.default = 8`).

### `probe_models()`

Runs at pipeline startup — checks env vars only (no LLM calls, no health-check subprocess). Adds unavailable models to `_PROBED_UNAVAILABLE`. Prefixes `ollama/`, `lm_studio/`, `local/` are always skipped.

## Multi-source knowledge (`sources.py`)

Fetches documentation for extracted terms. Routing by `term_type`:

| `term_type` | Sources |
|-------------|---------|
| `tool_library` | PyPI JSON first, then Wikipedia |
| `concept` | Wikipedia first; OpenAlex as fallback (2 papers) |
| `academic` | Wikipedia first; OpenAlex as fallback (SS handles bibliography) |

Fetched in S4, cached at `audit/sources/sources_{domain_id}.json`. Injected into S5 persona prompts with depth-aware caps (`config.yaml` `research_context` section): survey 5 terms/1 source/300 chars, standard 8/1/400, deep 15/2/500. Prompt size ~20k chars at deep depth.

Wikipedia requires `User-Agent` header or returns 403. Relevance gate rejects mismatches. PyPI stub filter rejects deprecated backport packages.

**OpenAlex daily budget:** separate from its 10 req/s polite-pool rate limit, OpenAlex enforces a small per-caller daily USD budget (observed as $0 free budget, 2026-09) — once exhausted, every request 429s with an `"Insufficient budget"` body until reset (~UTC midnight), not seconds later like an ordinary rate limit. `_parse_openalex_budget_error()` in `sources.py` distinguishes the two: budget phrase in the body, or a `Retry-After` longer than `config.yaml`'s `timeouts.openalex_budget_retry_after_threshold_s` (default 600s), is treated as exhausted. On detection it emits `{"event": "openalex_budget_exhausted", "term", "retry_after_s", "resets_at"}` and sets a module-level flag (`_OPENALEX_BUDGET_EXHAUSTED`) that skips all further OpenAlex calls for the rest of the process — no retry loop, no stall. `fetch_sources_for_term`'s `"academic"` branch falls back to arXiv when OpenAlex comes up empty for any reason (budget exhausted or no results), same as the `"concept"` branch already did.

## S5 Research prompt sizing and caching

Persona prompts were ~44k chars (causing 120s timeouts). Reduced to ~11k via depth-aware caps (see `ResearchContextConfig`).

Each persona message is split into two content blocks:
- **Shared context block** (`_SHARED_CONTEXT_BLOCK`): domain info, reader profile, graph nodes, gap analysis, bibliography, knowledge sources, and all static protocols (math/concept/anti-hallucination). Marked `cache_control: {type: ephemeral}` → Anthropic caches this prefix; the 90% cache-read discount applies on resume/retry runs.
- **Task block**: persona-specific role description, structure, drift guard, and output instruction.

Caching threshold: 4,096 tokens for Sonnet 4.6 / Opus 4.7. Deep depth reliably exceeds this (~6k tokens in shared block). Survey and standard are below threshold. Monitor activation via `cache_read_tok` in `llm_call_done` events (non-null and > 0 means cache hit).

## Message Batches API (--batch-mode, not yet implemented)

Planned `--batch-mode` flag for unattended deep runs: submits all 8-domain × 4 persona calls as one batch at 50% discount. Wall-clock increases to 1–3 hours (batch completes before any reconciler starts); this is intentional and not a concern for overnight runs. **Observability gap to solve before shipping:** the batch polling loop must emit `{"event": "s5_batch_poll", "pending": N, "completed": M}` every poll cycle so the web dashboard reflects progress. Without this the UI shows the pipeline frozen for up to an hour. See Anthropic Batches API docs for polling pattern.

## S4 junk-venue filter (`bibliography.py`)

`_drop_junk()` runs on raw Semantic Scholar result lists **before** ranking in `fetch_bibliography` (both buckets), `fetch_anchors`, and `fetch_anchor_neighbors`. It drops papers whose `venue` / `publicationVenue.name` matches a regex in `config.yaml` `bibliography.junk_venues` (case-insensitive) or whose DOI starts with a `bibliography.junk_doi_prefixes` entry (Zenodo, SSRN, Research Square, TechRxiv). Toggle with `bibliography.junk_venue_filter` (default on). arXiv is deliberately not filtered. `fetch_source_refs` is not filtered — the source document's own references are authoritative regardless of venue. Every filter pass on a non-empty list emits `ss_junk_filtered` (`query`, `bucket`, `dropped`, `kept`, `dropped_titles[:5]`). The 65/35 split arithmetic is untouched; a short frontier bucket is not backfilled.

## S8 Citation classifier — abstract field and ensemble verification

The claim-verification loop in `s8_verify.py::run()` skips entries with no `abstract` field on the cached `CitationRecord` — but not immediately. Abstracts **are** fetched and stored at S4 time: `bibliography.py` includes `"abstract"` in `_SS_FIELDS` and stores it in `_to_citation_record`. When SS genuinely returns no abstract for a paper (common for older works), the cached field is `""`.

Before falling back to `"No abstract available for this citation."`, `s8_verify.py` now calls `bibliography.fetch_paper_abstract(http, cfg, doi=..., title=...)` for a live, on-demand SS lookup: DOI-exact lookup first (`GET /graph/v1/paper/DOI:{doi}`), title search fallback via the existing `_ss_search()`. Never raises — returns `""` on any failure so the original fallback path is unchanged when both the cache and the live fetch miss.

The decomposition/entailment call itself no longer goes through a single `classifier_router.call()`. It goes through `agents.base.ensemble_verify()` — a debiased LLM-as-judge panel (Zheng et al., MT-Bench/Chatbot Arena): two proposers (`verifier_proposer_a` = gemini/*, `verifier_proposer_b` = gpt-4o-mini + deepinfra/*, both in `models.yaml`) answer the same prompt concurrently; agreement (compared via `_overall_relation()`, which collapses a `SentenceVerification`'s per-claim relations to a single `supports`/`contradicts`/`neutral` signal — see its docstring for why raw claim-list equality doesn't work across two independently-decomposed sentences) returns immediately with no third call. Disagreement escalates to a third-family adjudicator (`verifier_adjudicator` = claude-sonnet-5/claude-opus-5) shown both candidates anonymized and in a randomized order (`random.random()` coin flip per call — never a fixed order, to avoid positional bias). `ensemble_verify()` is a pure addition to `agents/base.py`; it composes `make_router()`/`QuotaAwareRouter` rather than changing them.

`[NEEDS_CITATION]`/verification results remain logged, never a build failure — `ensemble_verify()` can still raise (e.g. `RuntimeError` on quota exhaustion across all three roles), and `s8_verify.py`'s existing per-claim `try/except` around the call is unchanged, falling back to a `"neutral"` claim with the error message as `reason`.

## API keys

Set in `.env` or via web UI (session only — not persisted to disk):
- `DEEPINFRA_API_KEY` — required for the `deepinfra/*` open-weight fallback tier
- `NVIDIA_API_KEY` — required for `nvidia_nim/*` (Nemotron 3 Ultra)
- `GEMINI_API_KEY` / `GOOGLE_API_KEY` — for `gemini/` API models
- `ANTHROPIC_API_KEY` — for Claude models
- `OPENAI_API_KEY` — for GPT models
- `SS_API_KEY` — Semantic Scholar (optional, raises rate limit from 0.1 → 1 req/s)

**Never commit `.env` or write keys to disk from the serve process.**

## Web UI (`observe.py`)

Single Python file serving a self-contained HTML/CSS/JS dashboard. No external files. Python server code is always **below** the HTML template string — never mix them.

**Layout:** left stage rail + full-width detail pane + right slide-in run drawer.

**SIGCHLD fix:** `cmd_serve()` sets `signal.signal(SIGCHLD, SIG_IGN)` to suppress zombie subprocesses. POSIX: `SIG_IGN` survives `fork+exec` and propagates to pandoc → xelatex, breaking `wait()` with `ECHILD`. Fixed via `preexec_fn=lambda: signal.signal(SIGCHLD, SIG_DFL)` in `_spawn_pipeline()`.

**Sidebar drawer fix:** `pollQa()` opens the drawer only when `d.question && !d.interview_complete && runPaneMode === 'running'`. Checking `d.history.length > 0` caused it to re-open every 2s poll after the interview completed.

**API endpoints:**

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/status` | Stages, domains, summary, `pipeline_running`, `state_dir`, `input_path` |
| GET | `/api/events` | All events from current run's `events.jsonl` |
| GET | `/api/stage/:id` | Stage-specific artifacts |
| GET | `/api/qa` | Current interview question + history |
| POST | `/api/qa/answer` | Submit interview answer |
| GET | `/api/taxonomy` | Pending taxonomy review proposals |
| POST | `/api/taxonomy/choice` | Submit taxonomy choice (`l`/`s`/`m`) |
| POST | `/api/run` | Spawn pipeline subprocess |
| POST | `/api/stop` | Send SIGTERM to pipeline process group |
| POST | `/api/stage/:id/clear` | Remove stage from pipeline.json |
| GET | `/api/section/:id` | Rendered HTML for a section file |
| GET | `/api/files` | File listing for a state subdirectory |
| GET | `/api/keys` | Which API keys are set in server env |

**IPC files (in state dir):**
- `qa_queue.jsonl` / `qa_answers.jsonl` / `qa_complete` — interview Q&A
- `taxonomy_review.json` / `taxonomy_choice.json` — S3 review
- `pipeline_pid` — PID of running subprocess
- `pipeline.json` — completed stages + `run_id` + `input_path`

## Important constraints

- S2 Extract runs before S1 Assess — do not swap them back
- `observe.py` Python code is always below the HTML template string
- SIGTERM handler in `cli.py` must call `emit({event: run_stopped})` then `sys.exit(0)`
- `spacy en_core_web_sm` must be installed
- `signal.signal(SIGCHLD, SIG_IGN)` in `cmd_serve` — paired with `preexec_fn` in `_spawn_pipeline()`
- Stage markers in `pipeline.json` are the only resume mechanism
- 65% foundational / 35% frontier bibliography split — non-negotiable
- Two xelatex passes — always
- Human interaction only at S1 (Assess) and S3 (Graph)
- `[NEEDS_CITATION]` is logged, never a build failure
- S7b Quality Evaluator scores (`audit/quality_eval_{domain_id}.json`, `audit/quality_eval.md`) are logged, never a build failure or a trigger for revision — same philosophy as `[NEEDS_CITATION]`
- No Chinese API providers — meaning: never send document/prompt data to a China-operated endpoint (Alibaba Cloud, Zhipu/Z.ai, Moonshot's own APIs). This does NOT ban Chinese-authored open-weight models outright: GLM-5.2 and Kimi-K2.7-Code are served through DeepInfra (US company, US data centers, zero retention), so the weights are Chinese-authored but inference/data handling stay in the US. Do not add a model pointed directly at a Chinese cloud provider's own API.
- `gemini/gemini-2.5-flash` ($0.30/$2.50/M) is the intended cheap paid fallback — do NOT confuse with 3.5-flash which is 5× more expensive
- DeepInfra (`deepinfra/*`) is the consolidated open-weight fallback tier (replaced Groq/Mistral/llama.cpp/geminicli 2026-09) — do not reintroduce those providers
