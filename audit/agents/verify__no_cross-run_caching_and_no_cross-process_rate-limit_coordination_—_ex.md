# Agent: verify__no_cross-run_caching_and_no_cross-process_rate-limit_coordination_—_ex
**ID:** `agent-aabf39129a7988183`  **Turns:** 40

## Prompt
```
You are an adversarial verifier. A code auditor claims the following about the repo at /Users/cyan/ccr/know-expand:

TITLE: No cross-run caching and no cross-process rate-limit coordination — external API layer cannot scale past one run per host
LAYER: architecture
SEVERITY: critical
CLAIM: All external data (Wikipedia, PyPI, arXiv, OpenAlex, Semantic Scholar) is cached only inside a run's state dir (e.g. audit/sources/sources_{domain_id}.json, s4_audit.py:226-258); re-running the same paper refetches everything. README:869 claims DiskCache ('SQLite-backed for deterministic API calls') is part of the stack — it is not in pyproject.toml dependencies and is never imported. Rate limiters are module-level globals (_ss_limiter in bibliography.py:65-90, _OPENALEX_LIMITER in sources.py:40-47) scoped to one process; each pipeline run is a separate subprocess (spawned by observe.py), so N concurrent runs each get their own 'serialized' limiter and collectively send N× the per-IP budget to Semantic Scholar — guaranteed 429 cascades exactly when concurrency matters. SS unauthenticated limits are per-IP, so a multi-tenant deployment shares one budget across all users with no queue, broker, or shared cache to mediate it. This is the binding throughput ceiling for the SaaS goal, and nothing in the architecture addresses it.
FILES: /Users/cyan/ccr/know-expand/know_expand/bibliography.py:62-90, /Users/cyan/ccr/know-expand/know_expand/sources.py:40-47, /Users/cyan/ccr/know-expand/know_expand/stages/s4_audit.py:226-258, /Users/cyan/ccr/know-expand/pyproject.toml:9-34

Read the actual code and try to REFUTE this claim. Check: does the cited code actually behave as claimed? Is the severity inflated? Is there mitigating code elsewhere (search the repo)? If the claim is materially correct, confirm it. Default to refuted if the evidence is ambiguous.
```

**[4] tool→Read:** `/Users/cyan/ccr/know-expand/know_expand/bibliography.py` 

<details><summary>[5] tool result (3030 chars)</summary>

```
40	}
41	
42	
43	def _mncs_score(citation_count: int, field: str, year: int, field_baselines: dict) -> float:
44	    """
45	    Mean Normalized Citation Score: citation_count / field_year_average.
46	    Eliminates recency bias and field-size bias.
47	    field_baselines: {(field, year): avg_citation_count}
48	    Falls back to raw count if no baseline available.
49	    """
50	    key = (field.lower(), year)
51	    baseline = field_baselines.get(key)
52	    if baseline and baseline > 0:
53	        return citation_count / baseline
54	    # Fallback: apply a recency bonus to papers from last 24 months
55	    # (approximates normalization without baselines)
56	    current_year = datetime.datetime.now().year
57	    age = max(1, current_year - year)
58	    # Score = citations * recency_factor where newer papers get more weight
59	    recency_factor = 1.0 + max(0, (3 - age) * 0.3)  # 1.9x for this year, 1.6x for last, 1.3x for 2y ago
60	    return citation_count * recency_factor
61	
62	_SS_API_KEY: str = os.environ.get("SS_API_KEY", "")
63	
64	_logger = logging.getLogger("know_expand.bibliography")
65	_ss_limiter: AsyncLimiter | None = None
66	_ss_interval: float = 20.0  # cached for emit
67	_ss_cooldown_until: float = 0.0  # monotonic clock; all tasks pause until this time
68	
69	
70	def reset_ss_limiter() -> None:
71	    """Reset rate limiter and circuit breaker state at the start of each run."""
72	    global _ss_limiter, _ss_cooldown_until
73	    _ss_limiter = None
74	    _ss_cooldown_until = 0.0
75	
76	
77	def _get_ss_limiter(cfg: Config) -> AsyncLimiter:
78	    global _ss_limiter, _ss_interval
79	    if _ss_limiter is None:
80	        rl = cfg.rate_limits["semantic_scholar"]
81	        # Use 1-token bucket to serialize requests and prevent burst traffic.
82	        # AsyncLimiter(N, T) starts with N tokens, so concurrent calls all
83	        # get tokens immediately. Instead, use 1 token per (T/N) seconds.
84	        _ss_interval = rl.time_period / max(rl.max_rate, 1...[truncated]
```
</details>

**[6] tool→Read:** `/Users/cyan/ccr/know-expand/know_expand/sources.py` 

<details><summary>[7] tool result (2604 chars)</summary>

```
1	"""Multi-source knowledge fetcher for know-expand.
2	
3	Fetches Wikipedia, PyPI, and arXiv content for terms extracted from the pipeline.
4	All fetches are best-effort — exceptions are caught and logged, never propagated.
5	No API keys required.
6	"""
7	
8	import asyncio
9	import logging
10	import re
11	import urllib.parse
12	import xml.etree.ElementTree as ET
13	from datetime import datetime, timezone
14	from typing import Literal
15	
16	import httpx
17	from aiolimiter import AsyncLimiter
18	from pydantic import BaseModel
19	
20	_logger = logging.getLogger("know_expand.sources")
21	
22	_ARXIV_NS = "http://www.w3.org/2005/Atom"
23	
24	# Wikipedia requires a descriptive User-Agent; without it they return 403.
25	_USER_AGENT = "know-expand/0.1 (research document expander; open-source) python-httpx"
26	_WIKI_HEADERS = {"User-Agent": _USER_AGENT, "Accept": "application/json"}
27	
28	
29	class FetchedSource(BaseModel):
30	    term: str
31	    source_type: Literal["wikipedia", "pypi", "arxiv", "openalex"]
32	    title: str
33	    url: str
34	    content: str   # truncated excerpt, max ~1500 chars
35	    fetched_at: str  # ISO timestamp
36	
37	
38	# OpenAlex allows 10 req/s for polite pool (with email in User-Agent).
39	# Lazy-initialised so the module can be imported without a Config in scope.
40	_OPENALEX_LIMITER: AsyncLimiter | None = None
41	
42	
43	def _get_openalex_limiter() -> AsyncLimiter:
44	    global _OPENALEX_LIMITER
45	    if _OPENALEX_LIMITER is None:
46	        _OPENALEX_LIMITER = AsyncLimiter(10, 1)
47	    return _OPENALEX_LIMITER
48	
49	
50	_OPENALEX_HEADERS = {
51	    "User-Agent": "know-expand/1.0 (mailto:research@example.com)",
52	    "Accept": "application/json",
53	}
54	
55	
56	def _now_iso() -> str:
57	    return datetime.now(timezone.utc).isoformat()
58	
59	
60	def _strip_markup(text: str) -> str:
61	    """Very light markup stripping — removes reStructuredText/Markdown/HTML noise."""
62	    # Remove RST/Sphinx directives
63	    text = re.sub(r"\....[truncated]
```
</details>

**[8] tool→Read:** `/Users/cyan/ccr/know-expand/pyproject.toml` 

<details><summary>[9] tool result (1177 chars)</summary>

```
1	[build-system]
2	requires = ["hatchling"]
3	build-backend = "hatchling.build"
4	
5	[project]
6	name = "know-expand"
7	version = "0.1.0"
8	requires-python = ">=3.11"
9	dependencies = [
10	    # Document parsing
11	    "docling>=2.0.0",
12	    # NLP / extraction
13	    "spacy>=3.7.0",
14	    "keybert",
15	    "sentence-transformers>=3.0.0",   # BGE-M3
16	    "numpy>=1.26.0",
17	    # LLM routing + structured outputs
18	    "litellm>=1.40.0",
19	    "anthropic>=0.30.0",
20	    "instructor>=1.3.0",
21	    "pydantic>=2.7.0",
22	    # Pipeline orchestration + agent loops
23	    "langgraph>=0.2.0",
24	    # Observability (serve renders markdown in browser)
25	    "markdown>=3.6",
26	    # Rate limiting
27	    "aiolimiter>=1.1.0",
28	    # HTTP
29	    "httpx>=0.27.0",
30	    # PDF integrity check
31	    "pypdf>=4.2.0",
32	    # .env loading
33	    "python-dotenv>=1.0.0",
34	]
35	
36	[project.scripts]
37	know-expand = "know_expand.cli:main"
38	
39	[project.optional-dependencies]
40	dev = [
41	    "pytest>=8.0.0",
42	    "pytest-asyncio>=0.23.0",
43	    "pytest-httpx>=0.30.0",
44	]
45	
46	[tool.pytest.ini_options]
47	asyncio_mode = "auto"
48	testpaths = ["tests"]
49	
```
</details>

**[11] tool→Read:** `/Users/cyan/ccr/know-expand/know_expand/stages/s4_audit.py` 

<details><summary>[12] tool result (3310 chars)</summary>

```
210	) -> GapAnalysisResult:
211	    domain_id = domain["id"]
212	    domain_label = domain["label"]
213	    t0 = time.monotonic()
214	
215	    # Load term_type lookup from terms.json (best-effort)
216	    terms_path = audit_dir.parent / "terms.json"
217	    term_type_map: dict[str, str] = {}
218	    if terms_path.exists():
219	        try:
220	            for t in json.loads(terms_path.read_text()):
221	                term_type_map[t["name"]] = t.get("term_type", "academic")
222	        except Exception as exc:
223	            _logger.warning("s4 term_type_map load failed: %s", exc)
224	
225	    # Fetch knowledge sources for non-trivial terms in this domain (best-effort)
226	    sources_dir = audit_dir / "sources"
227	    sources_dir.mkdir(exist_ok=True)
228	    sources_cache = sources_dir / f"sources_{domain_id}.json"
229	
230	    if not sources_cache.exists():
231	        try:
232	            from know_expand.sources import fetch_sources_for_terms  # noqa: PLC0415
233	            # Only fetch core + supporting terms, not incidental
234	            nodes_for_fetch = domain_nodes or []
235	            terms_to_fetch = [
236	                (n["name"], term_type_map.get(n["name"], "academic"))
237	                for n in nodes_for_fetch
238	                if n.get("centrality") in ("core", "supporting")
239	            ]
240	            # Fallback: if no node dicts available, use all graph_terms as academic
241	            if not terms_to_fetch and graph_terms:
242	                terms_to_fetch = [
243	                    (name, term_type_map.get(name, "academic")) for name in graph_terms
244	                ]
245	            if terms_to_fetch:
246	                emit({
247	                    "event": "s4_sources_fetch_start",
248	                    "domain_id": domain_id,
249	                    "term_count": len(terms_to_fetch),
250	                })
251	                fetched = await fetch_sources_for_terms(terms_to_fetch, http, concurrency=4, domain_labe...[truncated]
```
</details>

**[13] tool→Bash:** `grep -rni "diskcache" /Users/cyan/ccr/know-expand --include="*.py" --include="*.toml" --include="*.md" --include="*.yaml` — Search repo for diskcache references

<details><summary>[14] tool result (231 chars)</summary>

```
/Users/cyan/ccr/know-expand/README.md
---
869:| Response caching | `DiskCache` | SQLite-backed for deterministic API calls; faster than Redis in local scenarios |
884:| `Redis` | Requires external process; DiskCache is sufficient |
```
</details>

**[17] tool→Read:** `/Users/cyan/ccr/know-expand/README.md` 

<details><summary>[18] tool result (5055 chars)</summary>

```
840	|-------|-----------|------|-----|
841	| S0 | Document parsing + structural zones | `Docling` (IBM, MIT) | DocLayNet layout model; `SECTION_HEADER`/`TITLE` labels; `HybridChunker`; degenerate-parse guard |
842	| S0 | URL fetch | `httpx` | Async, timeout |
843	| S2 | Lexical extraction | `spaCy` | Deterministic NER + noun chunks + regex; zero LLM cost |
844	| S2 | Conceptual extraction | LLM via LiteLLM | One call per chunk; captures implicit domain knowledge |
845	| S2 | Semantic extraction | `KeyBERT` + `BGE-M3` | Embedding keyphrase centrality; non-LLM; strong divergence signal |
846	| S2 | Term deduplication | numpy cosine + Union-Find | BGE-M3 cosine finds near-duplicates; Union-Find clusters aliases in ~30 stdlib lines |
847	| S3 | Taxonomy validation | OpenAlex concept API | ~65k hierarchical concepts; L0–L5 depth validates lumper/splitter proposals |
848	| S4 | Bibliography fetch | Semantic Scholar + Crossref | Two-bucket (65% foundational / 35% frontier). Anchor-neighbor expansion via `/citations`. |
849	| S4 | API rate limiting | `aiolimiter` (MIT) | Async token bucket per API; SS circuit breaker on burst 429s |
850	| S4/S5 | Adversarial loops | `litellm` + `instructor` | Generator→Critic via `litellm.acompletion` with Pydantic `CritiqueResult`; round files prevent crash rework |
851	| S6/S9 | Agentic alignment | LangGraph `StateGraph` + `ToolNode` | ReAct graphs with `@tool` async closures; context trimmed to last 20 messages per turn |
852	| All | LLM routing | `LiteLLM` + `QuotaAwareRouter` | Provider-agnostic fallback chains; `Retry-After` header distinguishes burst vs. exhaustion |
853	| All | Pipeline orchestration | `LangGraph` | Stateful graph execution; fan-out/fan-in via `Send` API; resume via sentinel files |
854	| S7 | Output deduplication | Trigram Jaccard (stdlib) | Exact-deduplicates `**Primer:**` lines; Jaccard threshold 0.85 on blocks >150 chars |
855	| S10 | PDF build (default) | `pandoc` + `xelatex` ×2 | Complex math support; proven p...[truncated]
```
</details>

**[19] tool→Bash:** `grep -rni "cache" /Users/cyan/ccr/know-expand/know_expand --include="*.py" | grep -vi "cache_control\|cache_read\|cache_` — Find cache-related code in package

<details><summary>[20] tool result (2216 chars)</summary>

```
/Users/cyan/ccr/know-expand/know_expand/observe.py:899:let stageArtifacts = {};  // cache { [stageId]: artifact data }
/Users/cyan/ccr/know-expand/know_expand/observe.py:1314:  // Load artifacts (cached)
/Users/cyan/ccr/know-expand/know_expand/observe.py:1415:      // Invalidate artifact cache for stages that are now running
/Users/cyan/ccr/know-expand/know_expand/observe.py:1729:    // Invalidate status cache so stage list refreshes
/Users/cyan/ccr/know-expand/know_expand/bibliography.py:66:_ss_interval: float = 20.0  # cached for emit
/Users/cyan/ccr/know-expand/know_expand/state.py:182:        "llm_call_start", "chunk_map_start", "chunk_map_cached",
/Users/cyan/ccr/know-expand/know_expand/stages/s2_extract.py:65:        cached = json.loads(out_path.read_text())
/Users/cyan/ccr/know-expand/know_expand/stages/s2_extract.py:66:        emit({"event": "chunk_map_cached", "chunk_id": chunk_id, "term_count": len(cached)})
/Users/cyan/ccr/know-expand/know_expand/stages/s2_extract.py:67:        return cached
/Users/cyan/ccr/know-expand/know_expand/stages/s4_audit.py:228:    sources_cache = sources_dir / f"sources_{domain_id}.json"
/Users/cyan/ccr/know-expand/know_expand/stages/s4_audit.py:230:    if not sources_cache.exists():
/Users/cyan/ccr/know-expand/know_expand/stages/s4_audit.py:253:                sources_cache.write_text(json.dumps(raw_dump, indent=2))
/Users/cyan/ccr/know-expand/know_expand/stages/s5_research.py:692:    sources_cache = audit_dir / "sources" / f"sources_{domain_id}.json"
/Users/cyan/ccr/know-expand/know_expand/stages/s5_research.py:694:    if sources_cache.exists():
/Users/cyan/ccr/know-expand/know_expand/stages/s5_research.py:696:            term_sources = json.loads(sources_cache.read_text())
/Users/cyan/ccr/know-expand/know_expand/agents/base.py:23:# Uses `npx @google/gemini-cli` with cached OAuth — no API key required.
/Users/cyan/ccr/know-expand/know_expand/agents/base.py:424:    """True if npx exists and the Gemini CLI OAuth credentials are ...[truncated]
```
</details>

**[21] tool→Bash:** `grep -n "pipeline_running\|_spawn_pipeline\|pipeline_pid\|already.*run" /Users/cyan/ccr/know-expand/know_expand/observe.` — Check concurrent-run handling in observe.py

<details><summary>[22] tool result (1047 chars)</summary>

```
1389:      _pipelineRunning = s.pipeline_running;
1390:      renderStageList(s.stages || {}, s.pipeline_running);
1398:      if (s.pipeline_running && runPaneMode === 'setup') {
1400:      } else if (!s.pipeline_running && runPaneMode === 'running') {
1404:      } else if (!s.pipeline_running && runPaneMode === 'taxonomy') {
1408:      if (s.pipeline_running && !wasRunning && selectedView !== 'all-events') {
1550:  // Don't call setRunMode here — the next poll will detect pipeline_running=false
2009:    """True if the pipeline process (recorded in pipeline_pid) is still alive."""
2010:    pid_file = state_dir / "pipeline_pid"
2045:                    "pipeline_running": _pipeline_is_running(state_dir),
2125:    elif result["pipeline_running"]:
2445:def _spawn_pipeline(runs_dir: Path, params: dict) -> int:
2529:    (state_dir / "pipeline_pid").write_text(str(proc.pid))
2689:                pid = _spawn_pipeline(runs_dir, data)
2701:                pid_file = (active_sd / "pipeline_pid") if active_sd else (state_dir / "pipeline_pid")
```
</details>

**[24] tool→Bash:** `grep -n "^#\|^##\|^###" /Users/cyan/ccr/know-expand/README.md | sed -n '1,60p'; echo "==="; grep -ni "saas\|multi-tenant` — List README sections and search for SaaS mentions

<details><summary>[25] tool result (1948 chars)</summary>

```
1:# know-expand
9:## Quick start
12:# Install
16:# Expand a paper
19:# Watch progress in your browser
22:# Resume a stopped run
31:## What it does
41:### Cost and latency
49:## Pipeline
51:### Stage order
60:### Architecture diagram
160:## CLI reference
215:## Configuration
217:### `config.yaml` — tunable knobs
243:### `models.yaml` — model lists per role
294:### API keys
311:## Stage deep dive
574:## Design principles
578:### 1. Bounded execution for every LLM step
584:### 2. Explicit canonicalization before classification
590:### 3. Independent verification vectors
596:### 4. Citations must be grounded before writing, not verified after
602:### 5. Domain-agnostic architecture
608:### 6. Synthesis must not consume narrative
614:### 7. Structural semantics over lexical frequency
620:### 8. Adversarial quality only where logical leaps occur
638:### 9. Complementary redundancy where strategy divergence is genuine
644:### 10. Idempotent stages
648:### 11. Human interaction permitted only at S1 and S3, and only when not bypassed
657:### 12. Pipeline state lives in the filesystem. Orchestrator context is ephemeral.
663:### 13. Distinguish failure modes precisely. Handle them differently.
675:### 14. The frontier is not optional.
681:## LLM routing
683:### QuotaAwareRouter (`agents/base.py`)
697:### geminicli provider
703:### Instructor modes
713:### Per-provider concurrency semaphores
717:### `probe_models()` at startup
721:### Model usage audit log
733:## Idempotency and resumability
756:## Deployment
758:### Local run (no Docker)
761:# Install dependencies
764:# Set API keys
767:# Run on a paper
770:# Monitor in browser
774:### Docker Compose
803:# Cloud LLMs
806:# Fully local (no API keys)
811:### Fully local mode
826:### Observability
835:## Technology stack
837:### Pipeline components
859:### Infrastructure and utilities
873:### What not to use
893:## Anti-patterns
940:## State directory layout
990:## Key files
===
```
</details>

**[26] tool→Read:** `/Users/cyan/ccr/know-expand/know_expand/observe.py` 

<details><summary>[27] tool result (1631 chars)</summary>

```
2675	                self.send_response(404)
2676	                self.end_headers()
2677	
2678	        def do_POST(self):
2679	            state_dir = _find_active_state_dir(runs_dir) or (runs_dir / "_default" / "state")
2680	
2681	            length = int(self.headers.get("Content-Length", 0))
2682	            body = self.rfile.read(length)
2683	            try:
2684	                data = json.loads(body)
2685	            except json.JSONDecodeError:
2686	                data = {}
2687	
2688	            if self.path == "/api/run":
2689	                pid = _spawn_pipeline(runs_dir, data)
2690	                payload = json.dumps({"ok": True, "pid": pid}).encode()
2691	                self.send_response(200)
2692	                self.send_header("Content-Type", "application/json; charset=utf-8")
2693	                self.send_header("Content-Length", str(len(payload)))
2694	                self.end_headers()
2695	                self.wfile.write(payload)
2696	
2697	            elif self.path == "/api/stop":
2698	                killed = False
2699	                pid = None
2700	                active_sd = _find_active_state_dir(runs_dir)
2701	                pid_file = (active_sd / "pipeline_pid") if active_sd else (state_dir / "pipeline_pid")
2702	                if pid_file.exists():
2703	                    try:
2704	                        pid = int(pid_file.read_text().strip())
2705	                        os.killpg(os.getpgid(pid), signal.SIGTERM)
2706	                        killed = True
2707	                    except Exception:
2708	                        pass
2709	                    try:
```
</details>

**[29] tool→Bash:** `grep -rni "saas\|tenant\|multi-user" /Users/cyan/ccr/know-expand --include="*.md" --include="*.py" --include="*.yaml" | ` — Search for SaaS mentions, cross-process locking, and read _spawn_pipeline

<details><summary>[30] tool result (1209 chars)</summary>

```
===
===
def _spawn_pipeline(runs_dir: Path, params: dict) -> int:
    """Determine run_id, create run dir tree, clear Q&A state, spawn pipeline subprocess, return PID."""
    from know_expand.state import new_run_id as _new_run_id

    is_resume = params.get("resume", False)

    if is_resume:
        # Reuse the run directory from the currently active run.
        # Always use the directory name (UUID) as run_id — the run_id stored
        # inside pipeline.json can differ (e.g. "run_4ed94c55" vs the UUID dir
        # "85af6a8b-…"), and the CLI uses --run-id to construct the state path.
        active_state = _find_active_state_dir(runs_dir)
        if active_state is not None:
            run_id = active_state.parent.name  # directory name is the canonical id
            state_dir = active_state
        else:
            run_id = _new_run_id()
            state_dir = runs_dir / run_id / "state"
    else:
        run_id = _new_run_id()
        state_dir = runs_dir / run_id / "state"

    run_dir = runs_dir / run_id
    (run_dir / "state").mkdir(parents=True, exist_ok=True)
    (run_dir / "logs").mkdir(parents=True, exist_ok=True)
    (run_dir / "output").mkdir(parents=True, exist_ok=True)
```
</details>

**[33] tool→Bash:** `grep -n "429\|cooldown\|Retry-After\|retry_after" /Users/cyan/ccr/know-expand/know_expand/bibliography.py | head -20; ec` — Check SS 429 handling and stack section intro

<details><summary>[34] tool result (818 chars)</summary>

```
67:_ss_cooldown_until: float = 0.0  # monotonic clock; all tasks pause until this time
72:    global _ss_limiter, _ss_cooldown_until
74:    _ss_cooldown_until = 0.0
145:    global _ss_cooldown_until
155:        # Global circuit breaker: if any prior request got 429, all tasks wait here.
157:        remaining = _ss_cooldown_until - now
160:                "event": "ss_cooldown_wait",
191:        if resp.status_code == 429:
192:            # Set global cooldown: every queued task will pause until this expires.
193:            _ss_cooldown_until = asyncio.get_event_loop().time() + delay
195:                "event": "ss_429_retry",
200:                "cooldown_until": _ss_cooldown_until,
===
---

## Technology stack

### Pipeline components

| Stage | Component | Tool | Why |
|-------|-----------|------|-----|
```
</details>

## Structured Output
```json
{
  "verdict": "partially_confirmed",
  "reasoning": "Every code-level observation checks out: (1) _ss_limiter (bibliography.py:65-90) and _OPENALEX_LIMITER (sources.py:40-47) are module-level aiolimiter globals with zero cross-process coordination \u2014 no fcntl/filelock/redis anywhere in the repo; (2) external-source results are cached only per-run at audit/sources/sources_{domain_id}.json (s4_audit.py:226-258), and _spawn_pipeline (observe.py:2445) mints a fresh run_id per non-resume run, so re-running the same paper refetches all Wikipedia/PyPI/arXiv/OpenAlex/SS data; (3) README:869 lists DiskCache in the present-tense stack table (and README:884 rejects Redis because \"DiskCache is sufficient\"), yet diskcache is absent from pyproject.toml dependencies and never imported \u2014 a genuine doc/code mismatch; (4) the POST /api/run handler (observe.py:2688) spawns a pipeline subprocess without checking pipeline_running, so N concurrent runs with independent per-process SS limiters are mechanically possible and would collectively exceed the per-IP budget. However, the framing and severity are inflated. The \"SaaS goal\" the claim calls this a binding ceiling for does not exist: grep for saas/tenant/multi-user across all .md/.py/.yaml files returns zero hits, and the README's \"What not to use\" table explicitly rejects Redis/Ray/Celery as distributed overkill \u2014 single-host, single-run scope is a documented deliberate design choice. \"Guaranteed 429 cascades\" is also overstated: bibliography.py:145-200 implements a Retry-After-honoring circuit breaker (_ss_cooldown_until) that pauses all SS tasks in a process on 429, so concurrent runs degrade to retry-paced throughput rather than uncontrolled cascade. For the project as actually scoped (local single-user CLI with one active run in the web UI), per-run caching and per-process limiters are adequate; the real defects are a README documentation bug (DiskCache), wasteful refetching across re-runs of the same input, and a missing concurrent-spawn guard in /api/run \u2014 real issues, but not architecture-critical for any goal the repo states.",
  "correction": "The mechanics are all true (per-process limiters, per-run-only caching, DiskCache documented at README:869 but never implemented, unguarded /api/run concurrent spawning), but the severity is wrong. There is no SaaS or multi-tenant goal anywhere in the repo \u2014 the README explicitly scopes the project to single-host local use and rejects distributed infrastructure by design, so \"binding throughput ceiling for the SaaS goal\" judges the architecture against a requirement it never claims. Additionally, \"guaranteed 429 cascades\" overstates the failure mode: a per-process circuit breaker (bibliography.py:145-200) honors Retry-After and pauses all SS tasks on 429, so concurrent runs degrade gracefully rather than cascading. Accurate severity: a minor documentation bug (DiskCache), a moderate efficiency gap (refetch on re-run), and a missing single-run guard in the web API \u2014 not a critical architecture flaw."
}
```

<details><summary>[38] tool result (39 chars)</summary>

```
Structured output provided successfully
```
</details>

**[39] assistant:** Verification complete. Verdict: partially_confirmed — the code-level facts hold (per-process module-level rate limiters, per-run-only source caching at `/Users/cyan/ccr/know-expand/know_expand/stages/s4_audit.py:226-258`, DiskCache documented at README:869 but absent from `/Users/cyan/ccr/know-expand/pyproject.toml` and never imported, unguarded `/api/run` spawning in `/Users/cyan/ccr/know-expand/know_expand/observe.py:2688`), but the "critical" severity rests on a SaaS goal that appears nowhere in the repo, and the "guaranteed 429 cascades" wording ignores the Retry-After circuit breaker at `/Users/cyan/ccr/know-expand/know_expand/bibliography.py:145-200`.
