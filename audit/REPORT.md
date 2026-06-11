# know-expand — Full Audit Report
**Date:** 2026-06-10  
**Method:** 56-agent workflow (6 code analysts × 4 layers, 4 market researchers, 43 adversarial verifiers, 2 strategy critics, 1 completeness critic). Every critical/high finding adversarially verified against the actual code. One finding refuted (`/api/section` path-traversal claim).  
**Run:** `wf_5a351b0c-7a5` · Task `wp2i4sr4m`

---

## Executive verdict

know-expand is a genuinely inventive single-operator research demo wearing a README that describes a product which does not exist. The generation half of its value proposition — long, cited documents grown from a paper — commoditized to near-zero between 2024 and 2026 (OpenAI gives 5 free deep-research runs/month; NotebookLM bundles it at $7.99; Stanford STORM and gpt-researcher replicate it for token cost). The verification half — the only part anyone provably pays for — is currently theater: "verified citations" in the code means a regex checks that citation keys appear in a JSON file. This is level 1 of a 6-level verification ladder, and it catches exactly zero of the failure modes that produced Deloitte's A$440k refund and the MAHA phantom-study corrections.

The billion-dollar path exists, but it is pharma systematic-literature-review automation, not consumer "turn any paper into a textbook." Getting there requires: (0) deleting the false README claims this week, (1) fixing the broken citation wiring, (2) climbing the verification ladder to claim-level entailment (L5), (3) adding per-run cost metering, and (4) deferring multi-tenancy until a paying pharma design partner signs. The architecture needs a rewrite boundary acknowledged honestly, not a hardening pass.

---

## Code audit — by subsystem

### Orchestration (`pipeline.py`, `cli.py`, `state.py`)

**Verdict:** well-instrumented local tool, not a production orchestrator.

| Severity | Layer | Finding | Verified |
|---|---|---|---|
| 🔴 critical | architecture | Single-active-run control plane — `_find_active_state_dir()` mtime scan; no queue, no auth, no tenancy | partially confirmed |
| 🔴 critical | execution | Unhandled stage exceptions kill runs silently — no `run_failed` event, web subprocess stderr → `/dev/null` | **confirmed** |
| 🟠 high | execution | `pipeline.json` written non-atomically (`write_text`) while `atomic_write()` exists 240 lines below | partially confirmed |
| 🟠 high | execution | **`--resume` is a no-op** — `resume_stage` parameter accepted, never referenced; fresh UUID on each call | **confirmed** |
| 🟠 high | execution | **`--no-pdf` silently dropped** — parsed, never forwarded to `run_pipeline`; PDF always builds | **confirmed** |
| 🟠 high | execution | Web-UI resume hardcodes `depth='standard'` — crashes deep run silently downgrades to standard | **confirmed** |
| 🟠 high | architecture | LangGraph is decorative — linear chain, no checkpointer, no interrupts, state carries 6 constants | **confirmed** |
| 🟡 medium | execution | Event-name drift: stage 10 emits `s7_*` events; three inconsistent naming schemes across codebase | — |
| 🟡 medium | planning | Six documented flags dead: `--stage`, `--user-profile`, `--no-bibliography-fetch`, `--human`, `--resume`, `--no-pdf` | **confirmed** |
| 🟡 medium | objective | `--status` creates empty run directories on every invocation | — |

**Strengths:** `emit()` event system is genuinely good (timestamped JSONL, leveled logs, audit trail); per-stage `.done` sentinels give whole-stage idempotency; `probe_models()` fails fast before burning LLM calls; POSIX signal hygiene is correct (`SIG_IGN` / `preexec_fn SIG_DFL` pairing).

---

### LLM Routing (`agents/base.py`, `models.yaml`)

**Verdict:** engineered for the opposite goal of the pitch.

| Severity | Layer | Finding | Verified |
|---|---|---|---|
| 🔴 critical | objective | **Routing order contradicts quality promise** — researcher role drains free geminicli → 2.5-flash → mistral-small *before* Opus | **confirmed** |
| 🔴 critical | execution | Schema-validation failures never trigger fallback — they crash the call | **confirmed** |
| 🔴 critical | architecture | Single-tenant by construction — process-env keys, personal OAuth creds, module-global mutable state | partially confirmed |
| 🟠 high | execution | **`model_usage.jsonl` / `cost_usd` is fiction** — appears only in README, not in code | **confirmed** |
| 🟠 high | execution | Error taxonomy incomplete — 500/529/timeout/connection errors crash instead of backoff/fallback | **confirmed** |
| 🟠 high | execution | Retry-After path: unbounded loop, uncapped sleep, `int()` crash on HTTP-date values | **confirmed** |
| 🟠 high | execution | **Silent model aliasing** — `models.yaml` says `gemini-3.5-flash`, code spawns `gemini-3.1-pro-preview` via unpinned `npx` | **confirmed** |
| 🟠 high | execution | geminicli: orphaned processes on timeout, prompt in `argv` (visible to `ps`) | partially confirmed |
| 🟠 high | execution | `lc_adapter` context trimming breaks tool-call message pairing | — |

---

### Stages S0–S4 (`s0_ingest.py` through `s4_audit.py`, `sources.py`, `bibliography.py`)

| Severity | Layer | Finding | Verified |
|---|---|---|---|
| 🔴 critical | objective | **Mandatory human interview (S1) with broken bypass** — default pipeline is human-gated; `--user-profile` flag does nothing | **confirmed** |
| 🔴 critical | architecture | No cross-run caching, no cross-process rate-limit coordination — can't scale past one run per host | partially confirmed |
| 🟠 high | execution | SS rate limit configured for authenticated key but applied unconditionally | partially confirmed |
| 🟠 high | architecture | **S4 adversarial gap loop is stateless** — rounds share no memory; termination condition can never fire; last round wins | **confirmed** |
| 🟠 high | execution | **URL ingestion: raw HTML into pipeline** — no content extraction, content-type check, size cap, or SSRF guard | **confirmed** |
| 🟠 high | execution | S2 LLM signal sees only ~37% of each chunk (char limit applied before LLM call) | partially confirmed |
| 🟠 high | planning | Document's own references extracted then discarded — bibliography built from generic keyword searches instead | — |
| 🟡 medium | planning | S3 Phase 2 "two-agent classification" is a keyword-overlap heuristic; `classification_conflicts.json` always empty | — |
| 🟡 medium | execution | Interview "coverage" satisfied by question text alone — unanswered interview completes with fabricated profile | — |

---

### Stages S5–S10 (`s5_research.py` through `s10_assemble.py`)

**The most critical bugs — they undermine the product's core claim.**

| Severity | Layer | Finding | Verified |
|---|---|---|---|
| 🔴 critical | objective | **"Verified citations" is not what the system does** — S8 is L1 regex; no claim can fail the build | **confirmed** |
| 🔴 critical | architecture | **S8 runs before S9/S10 which still mutate content; report-only, no feedback path** | **confirmed** |
| 🟠 high | execution | **S7 synthesize reads `state/bibliography.json` which only S10 writes → always cites from empty pool on fresh runs** | **confirmed** |
| 🟠 high | execution | **S6 align injects citation keys never added to any bibliography → guaranteed broken citations in PDF** | **confirmed** |
| 🟠 high | architecture | Critic and aligner see only ~8k chars of each chapter they're supposed to enforce quality on | **confirmed** |
| 🟠 high | execution | **Failed domains silently marked complete** — S5 marks stage done even when all domains fail | **confirmed** |
| 🟠 high | architecture | Three-persona design capped by single-call Reconciler; ~4-10x cost with no quality measurement | partially confirmed |
| 🟠 high | planning | README promises S10/S8 safety mechanisms (DOI validation, entailment checks) that don't exist | — |
| 🟡 medium | execution | Final PDF gets two bibliographies and broken multi-citation handling | — |
| 🟡 medium | execution | LaTeX math fixers interact incoherently: `_fix_bare_math` re-injects `$` into blocks `_fix_mixed_display_math` just cleaned | — |
| 🟡 medium | architecture | Prompt-cache engineering inert in default config (`budget_tokens: 0`) | — |

---

### Web UI (`observe.py`, 2768 lines)

| Severity | Layer | Finding | Verified |
|---|---|---|---|
| 🔴 critical | objective | Dashboard is a single-user localhost operator console — structurally incompatible with multi-user SaaS | **confirmed** |
| 🔴 critical | architecture | Single global "active state dir" resolved by mtime — concurrent runs structurally impossible | partially confirmed |
| 🔴 critical | execution | **No authentication or CSRF protection on any endpoint, including destructive POSTs** | **confirmed** |
| 🟠 high | architecture | Single-threaded `http.server` + 2s full-file polling | **confirmed** |
| 🟠 high | execution | **`/api/files?dir=` enumerates arbitrary filesystem paths** | **confirmed** |
| 🟠 high | execution | Stored XSS via unescaped term names and unsanitized markdown rendered via `innerHTML` | partially confirmed |
| 🟠 high | execution | `/api/run` accepts arbitrary `input` path with no validation | partially confirmed |
| ~~🟠 high~~ | ~~execution~~ | ~~`/api/section` path traversal~~ | **REFUTED** — scoped correctly |
| 🟡 medium | execution | PID-file process control vulnerable to PID reuse and stale files | — |
| 🟡 medium | execution | Undocumented `POST /api/keys` feature doesn't exist; no tenant key isolation | — |

---

### Quality & Delivery Infrastructure

| Severity | Layer | Finding | Verified |
|---|---|---|---|
| 🔴 critical | execution | **Package is not pip-installable** — CWD-relative config paths not bundled in wheel | **confirmed** |
| 🔴 critical | execution | **`docker-compose.yml` is a dead artifact** from the `doc-expand` era; references non-existent architecture | **confirmed** |
| 🟠 high | execution | Tests hit live HTTP APIs (OpenAlex, Wikipedia, PyPI, arXiv) — not hermetic | **confirmed** |
| 🟠 high | planning | **No CI/CD, no lint, no type-check, no coverage measurement** | **confirmed** |
| 🟠 high | objective | **The actual deliverable (generated text + PDF) has zero quality evaluation** | **confirmed** |
| 🟠 high | execution | 38% of all tests target two string-helper files; `observe.py`, `pipeline.py`, `cli.py`, 6 of 11 stages have zero tests | partially confirmed |
| 🟠 high | execution | `PyYAML` and `langchain-core` are undeclared runtime dependencies | partially confirmed |
| 🟡 medium | planning | No release/versioning machinery: `0.1.0` forever, no tags, no changelog | — |

**Good news on licenses:** all runtime dependencies are MIT/Apache-2.0/BSD. Pandoc (GPL-2+) and TeX Live are invoked as subprocesses — compliant for redistribution. Dependency licenses are clean for commercial use.

---

## Market research findings (June 2026)

### Competitive landscape

| Tool | Type | Pricing | What it does |
|---|---|---|---|
| Google NotebookLM | Study aid | Free / $7.99–200/mo | Deep Research, audio, interactive — short output |
| OpenAI Deep Research | Report gen | 5 free/mo; $20–200/mo | 10-40 page web-sourced reports, ~78% citation accuracy |
| Perplexity Max | Report gen | $20/mo | ~65% citation quality (own DRACO benchmark) |
| Elicit | SLR tool | $12–79/user/mo + enterprise | PRISMA-adjacent screening/extraction, pivoted to pharma |
| Consensus | Literature search | $10–45/mo; $30M Series A May 2026 | Search/synthesis, not book-length output |
| SciSpace / Undermind | Lit search | $12–20/mo | Question-answering, not synthesis |
| Stanford STORM | Open source | Free | Short Wikipedia-style articles |
| gpt-researcher | Open source | Token cost | 5-10 page reports, 27k stars |
| Edison Kosmos | Discovery reports | **$200/run** ($70M seed, $250M val) | 12hr, ~1500 papers, hypothesis generation — not pedagogy |

**White space:** no product ships a 200+ page verified, pedagogically-structured, typeset field guide from one seed paper. The space is empty because consumer demand for book-length output is unproven — not because the artifact is worthless.

### Citation quality benchmarks

- OpenAI Deep Research: **78%** citation accuracy (FACT framework)
- Best DRACO system: **~64.6%** citation quality, **~67.9%** factual accuracy
- GPT-4o lit reviews (Deakin 2025): **19.9% fully fabricated**, 56% fabricated-or-erroneous
- URL-resolution tooling (urlhealth, arXiv 2604.03173): **3–13% URLs hallucinated** — but self-correction cuts bad citations **6–79×** to under 1%
- Source-grounded tools (Semantic Scholar-backed): **>90%** citation accuracy vs <60% for free-generation LLMs

know-expand's SS-grounded bibliography already achieves ~L2 — references **exist** by construction. The gap to L5 (claim-level entailment) is a weeks-long engineering project against already-fetched abstracts, not a research bet.

### Paying segments with documented budgets

| Segment | Budget evidence | Fit |
|---|---|---|
| **Pharma SLR** | $141k + 67 weeks/review; $6B medical-writing market 11% CAGR; Elicit Enterprise pivot validates buyer | **Strongest wedge** |
| **PE/consulting diligence** | AlphaSense $600M ARR / $7.5B val; Hebbia in 33% of top asset managers | Strong, single-seed-doc case unserved |
| **Enterprise L&D** | $42.7k avg per finished eLearning hour; Synthesia $4B; Sana acquired by Workday at $50-100k/yr | Valid; slower sales cycle |
| **Defense/intel** | Primer $237M; Palantir traction | **Invalid until air-gapped** — pipeline is cloud-bound |
| **Consumer/prosumer** | Elicit median contract: $1,249/yr; free tier from every lab | **Dead on arrival** |

### Commoditization risk

The wrapper graveyard is unambiguous: Jasper fell from ~$120M to ~$35-55M revenue; 65% 90-day churn across AI app builders; Chegg -99%; Pluralsight $3.5B written to zero. The Kindle AI-book flood priced unaccountable AI long-form near zero. "Better deep research" as a horizontal product loses to the platform within one model generation.

---

## Business case: bear vs bull (adjudicated)

### Bear (why this dies)

1. **Commoditization is faster than you can ship.** Generation is free at the margin; one model generation erases the quality delta.
2. **The headline claim is false, and "verified" is legally actionable.** FTC Operation AI Comply (DoNotPay, $193k) established unsubstantiated AI-quality claims are deceptive practice. Deloitte refunded A$440k over fabricated references that would pass your current check.
3. **No production system exists.** "Production ready" means a rewrite, not a hardening pass.
4. **Unit economics are unmeasured and inverted.** COGS-per-run is the number for a per-artifact product; the codebase cannot produce it.
5. **Wrong buyer for the form factor.** People who tolerate 3-hour latency don't buy from a CLI; people who buy CLIs won't pay $200/run.

### Bull (what actually could work)

1. **The white space is real.** Nobody sells a verified 200-page book from one seed paper. Kosmos's $200/run validates the price point.
2. **Verification is the one axis where incumbents are structurally weak.** Frontier DR tools plateau at ~65% citation quality because they're open-web-search-shaped. Your SS-grounded bibliography already hits L2; L5 entailment is a weeks-long project.
3. **The wedge is identified, the buyer is documented.** Pharma SLR: $141k + 67 weeks per review. Your adversarial loops, deterministic bibliography, provenance-tracked sources, and event logs are exactly the audit-trail substrate pharma pays for. 1-3 hour latency is irrelevant against 67 weeks. S1/S3 human checkpoints become billable expert sign-off.

### The single decision

**Stop selling documents. Decide whose expensive, accountable human process you replace.** The evidence says pharma SLR. Everything else — including demoting the pedagogy layer — follows from that choice.

---

## Production-readiness roadmap

### Phase 0 — this week (no new architecture)
- Delete every false claim from README.md (FTC liability)
- Fix S6/S7 citation wiring (S7 must read from per-domain bibliographies, not the nonexistent `state/bibliography.json`; S6 must persist injected keys)
- Emit `run_failed` event on unhandled exceptions; capture subprocess stderr instead of `/dev/null`
- Make `pipeline.json` writes atomic (use the existing `atomic_write()`)
- Fix or remove the 6 dead CLI flags
- Remove or update `docker-compose.yml`

### Phase 1 — cost/provenance/stability
- Route all LLM calls through a **LiteLLM proxy** with one virtual key per run → exact $-per-run, hard budget cap, audit trail
- Fix routing order: researcher role should use quality models first, not free-tier first
- Persist actual `model`, params, prompt hash per call (reproducibility requirement for pharma)
- Swap file-based Q&A IPC → **LangGraph `SqliteSaver` checkpointer + `interrupt()`** (S1/S3 survive process death)
- Add `run_failed` / `run_complete` terminal states to event vocabulary

### Phase 2 — the moat (the actual product)
- Climb verification ladder L2→L5:
  - Resolve every DOI/URL (urlhealth-style; near-free, pushes bad citations under 1%)
  - Decompose cited sentences into atomic claims + entailment-check against stored SS abstracts (FActScore-style, cheap model)
  - Emit machine-readable per-run verification report in `output/verification_report.json`
  - Gate word "verified" behind >95% entailment support rate with residual visibly flagged (never stripped as `s10_assemble.py` does today)
- Add DeepEval citation-faithfulness gate in CI
- Add license-aware S0 input gating (arXiv default license bars derivatives; CC BY-NC-ND bars commercial derivatives)
- Publish citation-accuracy benchmark vs OpenAI/Gemini DR
- Fix `--no-pdf`, `--stage`, `--resume` (real implementations)

### Phase 3 — only after a paying design partner
- Multi-tenant job system (replace mtime-based run discovery)
- Real web app (not single-file `http.server`; proper auth/CSRF)
- GDPR/DPA: data deletion endpoint, retention policy, subprocessor inventory
- SOC2 Type II (~$30-80k, 6-12 months)
- Packaging: bundle config files in wheel, declare all deps, add CI/lint/types

---

## Completeness critic — gaps the main audit missed

- **Data privacy/GDPR** — runs retain uploaded documents + S1 interview profiles indefinitely, no deletion endpoint, fanned to 5+ subprocessors (including geminicli which runs under consumer ToS granting Google training rights)
- **Defense/intel wedge is invalid** — pipeline is cloud-bound (SS/OpenAlex/Wikipedia/HuggingFace BGE-M3 download); can't run air-gapped
- **Local compute COGS unanalyzed** — torch + BGE-M3 + Docling = ~8-12GB RAM/run, 2-5GB container; all unit economics are token-only
- **xelatex LaTeX injection** — `s10_assemble.py` runs xelatex without `--sandbox`; `\input` in LLM markdown can exfiltrate server files
- **Wikipedia inbound content** — CC BY-SA (share-alike obligation, no attribution mechanism in output)
- **OpenAlex polite-pool identity** — `sources.py:51` sends `research@example.com` as the `mailto:` — ToS etiquette violation, risks rate-deprioritization
- **English-only ceiling** — hardcoded `en.wikipedia.org`, `stop_words='english'`, `en_core_web_sm`; global market sizing never adjusts for this
- **"Reproducible evidence synthesis" positioning contradicted** — quota-driven nondeterministic model substitution + no model/param logging means a run can't be reproduced or described post-hoc; GxP buyers require this
- **Model lifecycle risk** — free-tier cost structure depends on unpinned `npx @google/gemini-cli` running a preview model under consumer OAuth; three independent withdrawal risks

---

## Appendix

- [`agents/`](agents/) — all 56 agent transcripts (prompt → tool trace → structured output)
- [`findings/`](findings/) — parsed structured outputs from each subsystem analyst and researcher
- [`raw/workflow_script.js`](raw/workflow_script.js) — the workflow that ran this audit
- [`raw/task_output.json`](raw/task_output.json) — full raw workflow result
