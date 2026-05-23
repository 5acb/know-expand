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

**Stage order:** `ingest → extract → assess → graph → audit → research → align → synthesize → verify → prereq → assemble`

S2 Extract runs before S1 Assess so the interview presents real extracted terms instead of LLM guesses.

| Stage | File | Does |
|-------|------|------|
| S0 Ingest | `s0_ingest.py` | Docling parse (PDF/URL/text), HybridChunker, structural zones via spaCy `en_core_web_sm` |
| S2 Extract | `s2_extract.py` | 3-signal extraction (spaCy + KeyBERT/BGE-M3 + LLM); Union-Find alias dedup; centrality scoring; **LLM batch classification into `academic` / `tool_library` / `concept`** |
| S1 Assess | `s1_assess.py` | LLM-driven 10–15 turn interview using extracted core terms → `UserProfile`; web IPC (`qa_queue.jsonl` / `qa_answers.jsonl` / `qa_complete`) or TTY |
| S3 Graph | `s3_graph.py` | Lumper/Splitter taxonomy proposals, OpenAlex validation, human web review or `--auto-taxonomy`, term → domain assignment → `graph.json` |
| S4 Audit | `s4_audit.py` | SS anchor papers + bibliography (65% foundational / 35% frontier); adversarial Gap Finder → Defender → Rebuttal loop; **multi-source fetch (Wikipedia/PyPI/arXiv) cached at `audit/sources/sources_{domain_id}.json`** |
| S5 Research | `s5_research.py` | Three-persona parallel research (Theoretician / Engineer / Practitioner) + Reconciler; adversarial critic loop; full pedagogical protocol; knowledge_sources injected into all prompts |
| S6 Align | `s6_align.py` | Pedagogical alignment pass: inject symbol tables, worked examples, where-to-go-next, resolve `[NEEDS_CITATION]` |
| S7 Synthesize | `s7_synthesize.py` | Cross-domain synthesis, connector bridges, boss nodes; reads ONLY `graph.json` + `summary_*.json` |
| S8 Verify | `s8_verify.py` | Citation audit, fact-check; `[NEEDS_CITATION]` logged, not a build failure |
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
    base.py       — QuotaAwareRouter, geminicli provider, _instructor_mode(), probe_models()
    schemas.py    — all Pydantic models for LLM structured outputs
  stages/
    s*.py         — one file per stage

config.yaml       — all tunable knobs; timeouts.geminicli_timeout_s = 600
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
| geminicli `RuntimeError` (any) | Permanently skip geminicli for this run; emit `model_geminicli_skip`; try next model |
| All models exhausted | Emit `quota_exhausted`; raise `RuntimeError` |

`_next_model()` checks both `self._skip` (per-router) **and** `_PROBED_UNAVAILABLE` (global) — so a quota discovery in one coroutine immediately affects all other routers.

### geminicli provider (`geminicli/` prefix)

Spawns `npx @google/gemini-cli -m gemini-3.1-pro-preview -p <prompt>` as a subprocess. No API key — uses cached OAuth from `~/.gemini/oauth_creds.json`.

**Quota detection:** `_parse_geminicli_stderr()` checks stderr for `TerminalQuotaError`. When found:
1. Adds `geminicli/{model}` to the global `_PROBED_UNAVAILABLE` set immediately — all routers across all roles stop trying geminicli without spawning further subprocesses
2. Raises `RuntimeError("geminicli quota exhausted resets in Xh")` with parsed `retryDelayMs`

Timeout: `cfg.timeouts.get("geminicli_timeout_s", 120)` — currently 600s in `config.yaml`.

**Do not use `getattr(cfg, "geminicli_timeout_s", 120)`** — `Config.timeouts` is a `dict`, not dataclass fields.

### Instructor modes (`_instructor_mode(model)`)

`instructor.from_litellm()` defaults to `TOOLS` mode (function calling). Mistral and Groq return parallel tool calls for single-schema requests, which instructor rejects. Use JSON mode for both:

| Model | Mode | Reason |
|-------|------|--------|
| `mistral/*` | `instructor.Mode.JSON` | `MISTRAL_STRUCTURED_OUTPUTS` requires `mistralai` SDK; litellm works with JSON mode |
| `groq/*` | `instructor.Mode.JSON` | Groq Llama models return parallel tool calls in TOOLS mode |
| everything else | `instructor.Mode.TOOLS` | Default; correct for OpenAI |

### Per-provider concurrency semaphores

Free-tier providers (Groq, Mistral) get their own `asyncio.Semaphore(3)` to prevent 20 parallel map-phase tasks from bursting them simultaneously and triggering 429 cascades. All other models share the global semaphore (`cfg.concurrency.default = 8`).

### `probe_models()`

Runs at pipeline startup — checks env vars and binary presence only (no LLM calls). Adds unavailable models to `_PROBED_UNAVAILABLE`. Local prefixes (`llamacpp/`, `ollama/`, `lm_studio/`, `local/`) always skipped.

## Multi-source knowledge (`sources.py`)

Fetches documentation for extracted terms. Routing by `term_type`:

| `term_type` | Sources |
|-------------|---------|
| `tool_library` | PyPI JSON first, then Wikipedia |
| `concept` | Wikipedia, then arXiv (2 papers) |
| `academic` | Wikipedia only (SS handles bibliography) |

Fetched in S4, cached at `audit/sources/sources_{domain_id}.json`. Injected into S5 persona prompts with caps: 8 terms / 1 source / 400 chars — keeps persona prompts under ~15k chars.

Wikipedia requires `User-Agent` header or returns 403. Relevance gate rejects mismatches. PyPI stub filter rejects deprecated backport packages.

## S5 Research prompt sizing and caching

Persona prompts were ~44k chars (causing 120s timeouts). Reduced to ~11k via depth-aware caps (see `ResearchContextConfig`).

Each persona message is split into two content blocks:
- **Shared context block** (`_SHARED_CONTEXT_BLOCK`): domain info, reader profile, graph nodes, gap analysis, bibliography, knowledge sources, and all static protocols (math/concept/anti-hallucination). Marked `cache_control: {type: ephemeral}` → Anthropic caches this prefix; the 90% cache-read discount applies on resume/retry runs.
- **Task block**: persona-specific role description, structure, drift guard, and output instruction.

Caching threshold: 4,096 tokens for Sonnet 4.6 / Opus 4.7. Deep depth reliably exceeds this (~6k tokens in shared block). Survey and standard are below threshold. Monitor activation via `cache_read_tok` in `llm_call_done` events (non-null and > 0 means cache hit).

## Message Batches API (--batch-mode, not yet implemented)

Planned `--batch-mode` flag for unattended deep runs: submits all 8-domain × 4 persona calls as one batch at 50% discount. Wall-clock increases to 1–3 hours (batch completes before any reconciler starts); this is intentional and not a concern for overnight runs. **Observability gap to solve before shipping:** the batch polling loop must emit `{"event": "s5_batch_poll", "pending": N, "completed": M}` every poll cycle so the web dashboard reflects progress. Without this the UI shows the pipeline frozen for up to an hour. See Anthropic Batches API docs for polling pattern.

## S8 Citation classifier — abstract requirement

`_classify_citation_contexts` in `s8_verify.py` skips entries with no `abstract` field in `bibliography_{domain_id}.json`. Whether abstracts are present depends on whether `_to_citation_record` in `bibliography.py` fetches and stores them. **Verify in a real run:** check a live `bibliography_*.json` for an `abstract` key. If absent, the classifier always emits `s8_citation_classify_skipped` and does nothing. Fix would be fetching the abstract field from the SS `/paper/{id}` response in `_to_citation_record`.

## API keys

Set in `.env` or via web UI (session only — not persisted to disk):
- `GROQ_API_KEY` — required for groq fallback (present in `.env`)
- `MISTRAL_API_KEY` — required for mistral fallback (present in `.env`)
- `GEMINI_API_KEY` / `GOOGLE_API_KEY` — for `gemini/` API models (not geminicli)
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
| GET | `/api/models` | LiteLLM chat model list + `keys_set` |
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
- No Chinese API providers
- Models and llama.cpp are pre-configured in `~/ccr` — do not pollute home or other locations
- `gemini/gemini-3.5-flash` is the API model; `geminicli/gemini-3.5-flash` uses the free OAuth CLI — they are different providers
