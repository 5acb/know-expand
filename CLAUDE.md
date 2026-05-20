# doc-expand — CLAUDE.md

Multi-agent CLI that turns any technical document (PDF, URL, text) into a research-grade knowledge expansion. Runs as a LangGraph pipeline with an optional web dashboard.

## Run / serve

```bash
uv run doc-expand path/to/paper.pdf          # run pipeline (stdout progress)
uv run doc-expand serve                       # web UI at http://localhost:7842
uv run doc-expand serve --port 7843          # custom port
uv run doc-expand --status                   # check current state_dir
```

State dir defaults to `./state/`. Logs go to `./logs/{run_id}/`.

## Pipeline stages

| Stage | File | Does |
|-------|------|------|
| S0 Ingest | `s0_ingest.py` | Parse PDF/URL with docling, extract structural zones (spaCy `en_core_web_sm`) |
| S1 Assess | `s1_assess.py` | LLM-driven 6-question user interview → `UserProfile`; writes `qa_queue.jsonl` / `qa_answers.jsonl` / `qa_complete` |
| S2 Extract | `s2_extract.py` | Chunk text, extract `CanonicalTerm` inventory, rank by centrality |
| S3 Graph | `s3_graph.py` | Propose lumper/splitter taxonomy, validate with OpenAlex, user reviews via web IPC (`taxonomy_review.json` / `taxonomy_choice.json`), classify terms → `graph.json` |
| S4 Audit | `s4_audit.py` | Fetch anchor papers from Semantic Scholar, build bibliography, gap analysis |
| S5 Research | `s5_research.py` | Per-domain multi-persona research (Theoretician / Engineer / Practitioner) + Reconciler → section markdown files |
| S6 Align | `s6_align.py` | Pedagogical alignment pass: inject symbol tables, worked examples, where-to-go-next, resolve `[NEEDS_CITATION]` |
| S7 Synthesize | `s7_synthesize.py` | Cross-domain synthesis, connector bridges, boss nodes |
| S8 Verify | `s8_verify.py` | Citation audit, fact-check |
| S9 Prereq | `s9_prereq.py` | Inject blockquote primers for unknown terms |
| S10 Assemble | `s10_assemble.py` | Merge sections → final markdown + optional PDF |

Stages skip automatically if already complete (`stage_is_complete()` checks `pipeline.json`). Resume a stopped run by keeping stage markers in `pipeline.json` (the web UI does this via the Resume button).

## Key files

```
doc_expand/
  cli.py          — entry point; installs SIGTERM handler that emits run_stopped
  pipeline.py     — LangGraph StateGraph; writes run_id + input_path to pipeline.json at startup
  state.py        — emit(), mark_stage_complete(), PipelineState TypedDict, log setup
  config.py       — Config dataclass loaded from config.yaml
  observe.py      — self-contained web server + HTML/CSS/JS dashboard (2700 lines)
  agents/
    base.py       — QuotaAwareRouter: model fallback, rate-limit retry, auth skip, probe_models()
    schemas.py    — all Pydantic models for LLM structured outputs
  stages/
    s*.py         — one file per stage

config.yaml       — all tuneable knobs (bibliography depth, concurrency, timeouts, etc.)
models.yaml       — model lists per role (classifier, researcher, etc.)
state/            — default state dir (pipeline.json, graph.json, terms.json, sections/, etc.)
logs/             — one dir per run_id, contains events.jsonl + pipeline.log + debug.log
```

## LLM routing (`agents/base.py`)

`QuotaAwareRouter` tries models in order. On auth error: permanently skip that model. On rate-limit / 503 with `retry-after` header: sleep and retry. On headerless 429/503: exponential backoff (2s, 4s, 8s), skip after 3 consecutive failures.

`probe_models()` runs at pipeline startup — checks env vars only (no LLM calls), adds unavailable models to a skip set. Local prefixes (`llamacpp/`, `ollama/`, `lm_studio/`, `local/`) are always skipped.

## API keys

Set in environment or via web UI (session only — not persisted to disk):
- `ANTHROPIC_API_KEY`
- `OPENAI_API_KEY`
- `GEMINI_API_KEY` (or `GOOGLE_API_KEY`)
- `SS_API_KEY` — Semantic Scholar (optional, raises rate limit from 0.1 → 1 req/s)

**Never commit `.env` or write keys to disk from the serve process.**

## Web UI (`observe.py`)

Single Python file serving a self-contained HTML/CSS/JS dashboard. No external files.

**Layout:** left stage rail + full-width detail pane + right slide-in run drawer.

**API endpoints (all served by the embedded HTTPServer):**

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/status` | Stages, domains, summary, `pipeline_running` (pid probe), `state_dir`, `input_path` |
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
- `taxonomy_review.json` / `taxonomy_choice.json` — Stage 3 review
- `pipeline_pid` — PID of running subprocess (used by `/api/stop` and `_pipeline_is_running()`)
- `pipeline.json` — completed stages + `run_id` + `input_path` (written by pipeline at startup)

**Run pane modes:** `setup` → `running` → (`qa` during interview) → (`taxonomy` during S3) → `setup`

Mode is synced from `pipeline_running` on every poll — no manual state management needed.

**Active stage inference:** `pipeline.json` only records *completed* stages (never writes `status: running`). The active stage is inferred as the first non-terminal stage in `STAGE_ORDER` when `pipeline_running=true`.

## Important constraints

- `observe.py` Python code (server, handlers) is always below the HTML template string — never mix them
- The SIGTERM handler in `cli.py` must fire before `asyncio.run()` returns, so it calls `emit({event: run_stopped})` then `sys.exit(0)`
- `spacy en_core_web_sm` must be installed: `pip install https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl`
- `signal.signal(SIGCHLD, SIG_IGN)` in `cmd_serve` prevents zombie subprocesses
- Stage markers in `pipeline.json` are the only resume mechanism — clearing them forces a full re-run
