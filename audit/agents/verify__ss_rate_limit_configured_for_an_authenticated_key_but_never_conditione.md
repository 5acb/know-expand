# Agent: verify__ss_rate_limit_configured_for_an_authenticated_key_but_never_conditione
**ID:** `agent-a4766f553297d7971`  **Turns:** 42

## Prompt
```
You are an adversarial verifier. A code auditor claims the following about the repo at /Users/cyan/ccr/know-expand:

TITLE: SS rate limit configured for an authenticated key but never conditioned on key presence
LAYER: execution
SEVERITY: high
CLAIM: config.yaml:21-23 sets semantic_scholar max_rate 1/time_period 1 with the comment 'authenticated key: 1 req/sec'. _get_ss_limiter (bibliography.py:77-90) reads this unconditionally; nothing checks whether SS_API_KEY is actually set (it is optional per README:305). Unauthenticated SS allows roughly 100 req/5 min (~0.33 req/s; CLAUDE.md says 0.1 req/s) — so the default configuration overruns the unauthenticated limit 3-10×, and the elaborate cooldown circuit breaker (_ss_cooldown_until, 30s→120s backoff) exists largely to absorb a self-inflicted overrun. Additionally _SS_API_KEY is read once at module import (bibliography.py:62), so a key injected later (e.g. via the web UI session env) inside the same process is ignored. The limiter should derive its rate from key presence and re-read the key per request.
FILES: /Users/cyan/ccr/know-expand/know_expand/bibliography.py:62, /Users/cyan/ccr/know-expand/know_expand/bibliography.py:77-90, /Users/cyan/ccr/know-expand/config.yaml:20-29

Read the actual code and try to REFUTE this claim. Check: does the cited code actually behave as claimed? Is the severity inflated? Is there mitigating code elsewhere (search the repo)? If the claim is materially correct, confirm it. Default to refuted if the evidence is ambiguous.
```

**[3] tool→Read:** `/Users/cyan/ccr/know-expand/know_expand/bibliography.py` 

<details><summary>[4] tool result (5359 chars)</summary>

```
1	"""Two-bucket bibliography fetcher (Semantic Scholar only)."""
2	
3	import asyncio
4	import datetime
5	import logging
6	import os
7	import re
8	
9	import httpx
10	from aiolimiter import AsyncLimiter
11	
12	from know_expand.agents.schemas import CitationRecord
13	from know_expand.config import Config
14	from know_expand.state import emit
15	
16	_FIELD_BASELINES: dict[tuple[str, int], float] = {
17	    # Field, Year -> approximate avg citation count for a paper in that field/year
18	    # These are order-of-magnitude estimates; good enough for ranking
19	    ("computer science", 2020): 15.0,
20	    ("computer science", 2021): 12.0,
21	    ("computer science", 2022): 8.0,
22	    ("computer science", 2023): 4.0,
23	    ("computer science", 2024): 1.5,
24	    ("medicine", 2020): 20.0,
25	    ("medicine", 2021): 16.0,
26	    ("medicine", 2022): 10.0,
27	    ("medicine", 2023): 5.0,
28	    ("biology", 2020): 12.0,
29	    ("biology", 2021): 9.0,
30	    ("biology", 2022): 6.0,
31	    ("biology", 2023): 3.0,
32	    ("mathematics", 2020): 5.0,
33	    ("mathematics", 2021): 4.0,
34	    ("mathematics", 2022): 2.5,
35	    ("mathematics", 2023): 1.5,
36	    ("physics", 2020): 10.0,
37	    ("physics", 2021): 8.0,
38	    ("physics", 2022): 5.0,
39	    ("physics", 2023): 2.5,
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
57	   ...[truncated]
```
</details>

**[5] tool→Read:** `/Users/cyan/ccr/know-expand/config.yaml` 

<details><summary>[6] tool result (1363 chars)</summary>

```
1	bibliography:
2	  foundational_fraction: 0.65
3	  frontier_months: 24
4	  depth_totals:
5	    survey: 10
6	    standard: 30
7	    deep: 50
8	  ss_max_retries: 5
9	  ss_retry_initial_delay: 30.0   # first 429 cooldown: 30s, then doubles
10	  ss_max_backoff: 120.0           # cap backoff at 2 min
11	  ss_anchors_n: 3
12	
13	centrality:
14	  structural_weight: 2.0
15	  core_effective_threshold: 10.0
16	  core_chunk_fraction: 0.4
17	  supporting_effective_threshold: 4.0
18	  alias_cosine_threshold: 0.92
19	
20	rate_limits:
21	  semantic_scholar:
22	    max_rate: 1
23	    time_period: 1    # authenticated key: 1 req/sec
24	  crossref:
25	    max_rate: 1
26	    time_period: 1
27	  openalex:
28	    max_rate: 10
29	    time_period: 1
30	
31	concurrency:
32	  default: 8
33	
34	timeouts:
35	  domain_research_seconds: 2700
36	  http_fetch_seconds: 30
37	  openalex_seconds: 10
38	  http_async_seconds: 30
39	  geminicli_timeout_s: 600   # allow 10 min for long-form persona generation
40	
41	adversarial_rounds:
42	  survey: 1
43	  standard: 2
44	  deep: 3
45	
46	nlp_models:
47	  spacy: "en_core_web_sm"
48	  embedding: "BAAI/bge-m3"
49	  tokenizer: "cl100k_base"
50	
51	chunking:
52	  word_size: 800
53	  max_tokens: 4000
54	  spacy_char_limit: 50000
55	  llm_map_char_limit: 6000
56	
57	keyword_extraction:
58	  top_n: 20
59	  ngram_min: 1
60	  ngram_max: 3
```
</details>

**[8] tool→Bash:** `grep -rn "_SS_API_KEY\|SS_API_KEY" /Users/cyan/ccr/know-expand --include="*.py" --include="*.yaml" --include="*.md" | gr` — Find all SS_API_KEY references in repo

<details><summary>[9] tool result (610 chars)</summary>

```
/Users/cyan/ccr/know-expand/CLAUDE.md:147:- `SS_API_KEY` — Semantic Scholar (optional, raises rate limit from 0.1 → 1 req/s)
/Users/cyan/ccr/know-expand/README.md:305:| `SS_API_KEY` | Semantic Scholar (optional; raises rate limit from 0.1 → 1 req/s) |
/Users/cyan/ccr/know-expand/know_expand/bibliography.py:62:_SS_API_KEY: str = os.environ.get("SS_API_KEY", "")
/Users/cyan/ccr/know-expand/know_expand/bibliography.py:150:    headers = {"x-api-key": _SS_API_KEY} if _SS_API_KEY else {}
/Users/cyan/ccr/know-expand/know_expand/bibliography.py:223:    headers = {"x-api-key": _SS_API_KEY} if _SS_API_KEY else {}
```
</details>

**[10] tool→Bash:** `grep -rn "rate_limits\|semantic_scholar" /Users/cyan/ccr/know-expand/know_expand --include="*.py" | grep -v ".venv"` — Find rate_limits usage in code

<details><summary>[11] tool result (392 chars)</summary>

```
/Users/cyan/ccr/know-expand/know_expand/config.py:101:    rate_limits: dict[str, RateLimitConfig]
/Users/cyan/ccr/know-expand/know_expand/config.py:130:        rate_limits={
/Users/cyan/ccr/know-expand/know_expand/config.py:132:            for name, vals in raw["rate_limits"].items()
/Users/cyan/ccr/know-expand/know_expand/bibliography.py:80:        rl = cfg.rate_limits["semantic_scholar"]
```
</details>

**[13] tool→Bash:** `grep -n "api/keys\|SS_API_KEY\|environ\|_spawn_pipeline\|subprocess\|Popen" /Users/cyan/ccr/know-expand/know_expand/obse` — Find key handling and pipeline spawn in observe.py

<details><summary>[14] tool result (1048 chars)</summary>

```
13:import subprocess
1469:    const d = await (await fetch('/api/keys')).json();
1690:    const d = await (await fetch('/api/keys')).json();
2445:def _spawn_pipeline(runs_dir: Path, params: dict) -> int:
2446:    """Determine run_id, create run dir tree, clear Q&A state, spawn pipeline subprocess, return PID."""
2521:    proc = subprocess.Popen(
2524:        stdout=subprocess.DEVNULL,
2525:        stderr=subprocess.DEVNULL,
2526:        env=os.environ.copy(),
2584:    # Pipeline subprocesses are spawned with start_new_session=True so they live
2659:            elif self.path == "/api/keys":
2667:                    "anthropic": bool(os.environ.get("ANTHROPIC_API_KEY")),
2668:                    "openai":    bool(os.environ.get("OPENAI_API_KEY")),
2669:                    "gemini":    bool(os.environ.get("GEMINI_API_KEY")),
2670:                    "groq":      bool(os.environ.get("GROQ_API_KEY")),
2671:                    "mistral":   bool(os.environ.get("MISTRAL_API_KEY")),
2689:                pid = _spawn_pipeline(runs_dir, data)
```
</details>

**[15] tool→Bash:** `grep -rn "ss_retry_initial_delay\|_ss_cooldown_until\|ss_max_backoff\|ss_max_retries" /Users/cyan/ccr/know-expand/know_e` — Find cooldown circuit breaker usage

<details><summary>[16] tool result (1681 chars)</summary>

```
/Users/cyan/ccr/know-expand/know_expand/config.py:14:    ss_max_retries: int = 5
/Users/cyan/ccr/know-expand/know_expand/config.py:15:    ss_retry_initial_delay: float = 30.0
/Users/cyan/ccr/know-expand/know_expand/config.py:16:    ss_max_backoff: float = 120.0
/Users/cyan/ccr/know-expand/know_expand/bibliography.py:67:_ss_cooldown_until: float = 0.0  # monotonic clock; all tasks pause until this time
/Users/cyan/ccr/know-expand/know_expand/bibliography.py:72:    global _ss_limiter, _ss_cooldown_until
/Users/cyan/ccr/know-expand/know_expand/bibliography.py:74:    _ss_cooldown_until = 0.0
/Users/cyan/ccr/know-expand/know_expand/bibliography.py:145:    global _ss_cooldown_until
/Users/cyan/ccr/know-expand/know_expand/bibliography.py:151:    delay = cfg.bibliography.ss_retry_initial_delay
/Users/cyan/ccr/know-expand/know_expand/bibliography.py:154:    for attempt in range(cfg.bibliography.ss_max_retries):
/Users/cyan/ccr/know-expand/know_expand/bibliography.py:157:        remaining = _ss_cooldown_until - now
/Users/cyan/ccr/know-expand/know_expand/bibliography.py:193:            _ss_cooldown_until = asyncio.get_event_loop().time() + delay
/Users/cyan/ccr/know-expand/know_expand/bibliography.py:199:                "max_retries": cfg.bibliography.ss_max_retries,
/Users/cyan/ccr/know-expand/know_expand/bibliography.py:200:                "cooldown_until": _ss_cooldown_until,
/Users/cyan/ccr/know-expand/know_expand/bibliography.py:202:            delay = min(delay * 2, cfg.bibliography.ss_max_backoff)
/Users/cyan/ccr/know-expand/know_expand/bibliography.py:208:    emit({"event": "ss_exhausted", "query": query[:60], "attempts": cfg.bibliography.ss_max_retries})
```
</details>

**[18] tool→Bash:** `grep -n "load_dotenv\|import" /Users/cyan/ccr/know-expand/know_expand/cli.py | head -30; echo ---; grep -rn "load_dotenv` — Check dotenv loading order and key-setting endpoint

<details><summary>[19] tool result (903 chars)</summary>

```
1:import argparse
2:import asyncio
3:import signal
4:import sys
5:from pathlib import Path
7:from dotenv import load_dotenv
8:load_dotenv()
10:from know_expand.config import load_config
11:from know_expand.state import emit, load_pipeline_json, new_run_id, setup_logging
115:        from know_expand.observe import cmd_tail
120:        from know_expand.observe import cmd_serve
164:    from know_expand.pipeline import run_pipeline
---
/Users/cyan/ccr/know-expand/know_expand/cli.py:7:from dotenv import load_dotenv
/Users/cyan/ccr/know-expand/know_expand/cli.py:8:load_dotenv()
/Users/cyan/ccr/know-expand/know_expand/observe.py:18:from dotenv import load_dotenv
/Users/cyan/ccr/know-expand/know_expand/observe.py:19:load_dotenv()
---
1469:    const d = await (await fetch('/api/keys')).json();
1690:    const d = await (await fetch('/api/keys')).json();
2659:            elif self.path == "/api/keys":
```
</details>

**[21] tool→Bash:** `grep -n "os.environ\[" /Users/cyan/ccr/know-expand/know_expand/observe.py; echo ---; grep -n "do_POST" -A 40 /Users/cyan` — Find env-setting code in observe.py POST handlers

<details><summary>[22] tool result (2047 chars)</summary>

```
---
2678:        def do_POST(self):
2679-            state_dir = _find_active_state_dir(runs_dir) or (runs_dir / "_default" / "state")
2680-
2681-            length = int(self.headers.get("Content-Length", 0))
2682-            body = self.rfile.read(length)
2683-            try:
2684-                data = json.loads(body)
2685-            except json.JSONDecodeError:
2686-                data = {}
2687-
2688-            if self.path == "/api/run":
2689-                pid = _spawn_pipeline(runs_dir, data)
2690-                payload = json.dumps({"ok": True, "pid": pid}).encode()
2691-                self.send_response(200)
2692-                self.send_header("Content-Type", "application/json; charset=utf-8")
2693-                self.send_header("Content-Length", str(len(payload)))
2694-                self.end_headers()
2695-                self.wfile.write(payload)
2696-
2697-            elif self.path == "/api/stop":
2698-                killed = False
2699-                pid = None
2700-                active_sd = _find_active_state_dir(runs_dir)
2701-                pid_file = (active_sd / "pipeline_pid") if active_sd else (state_dir / "pipeline_pid")
2702-                if pid_file.exists():
2703-                    try:
2704-                        pid = int(pid_file.read_text().strip())
2705-                        os.killpg(os.getpgid(pid), signal.SIGTERM)
2706-                        killed = True
2707-                    except Exception:
2708-                        pass
2709-                    try:
2710-                        pid_file.unlink()
2711-                    except Exception:
2712-                        pass
2713-                payload = json.dumps({"ok": True, "killed": killed}).encode()
2714-                self.send_response(200)
2715-                self.send_header("Content-Type", "application/json; charset=utf-8")
2716-                self.send_header("Content-Length", str(len(payload)))
2717-                self.end_headers()...[truncated]
```
</details>

**[24] tool→Read:** `/Users/cyan/ccr/know-expand/know_expand/observe.py` 

<details><summary>[25] tool result (4067 chars)</summary>

```
2445	def _spawn_pipeline(runs_dir: Path, params: dict) -> int:
2446	    """Determine run_id, create run dir tree, clear Q&A state, spawn pipeline subprocess, return PID."""
2447	    from know_expand.state import new_run_id as _new_run_id
2448	
2449	    is_resume = params.get("resume", False)
2450	
2451	    if is_resume:
2452	        # Reuse the run directory from the currently active run.
2453	        # Always use the directory name (UUID) as run_id — the run_id stored
2454	        # inside pipeline.json can differ (e.g. "run_4ed94c55" vs the UUID dir
2455	        # "85af6a8b-…"), and the CLI uses --run-id to construct the state path.
2456	        active_state = _find_active_state_dir(runs_dir)
2457	        if active_state is not None:
2458	            run_id = active_state.parent.name  # directory name is the canonical id
2459	            state_dir = active_state
2460	        else:
2461	            run_id = _new_run_id()
2462	            state_dir = runs_dir / run_id / "state"
2463	    else:
2464	        run_id = _new_run_id()
2465	        state_dir = runs_dir / run_id / "state"
2466	
2467	    run_dir = runs_dir / run_id
2468	    (run_dir / "state").mkdir(parents=True, exist_ok=True)
2469	    (run_dir / "logs").mkdir(parents=True, exist_ok=True)
2470	    (run_dir / "output").mkdir(parents=True, exist_ok=True)
2471	
2472	    # Clear IPC files
2473	    for fname in ("qa_queue.jsonl", "qa_answers.jsonl", "qa_complete",
2474	                   "taxonomy_review.json", "taxonomy_choice.json"):
2475	        p = state_dir / fname
2476	        if p.exists():
2477	            p.unlink()
2478	
2479	    # Fresh run: wipe stage completion markers so every stage re-runs.
2480	    # Resume run: preserve markers so only incomplete stages run.
2481	    if not is_resume:
2482	        pipeline_path = state_dir / "pipeline.json"
2483	        if pipeline_path.exists():
2484	            try:
2485	                data = json.loads(pipeline_path.read_text())
2486	                data["sta...[truncated]
```
</details>

**[27] tool→Read:** `/Users/cyan/ccr/know-expand/know_expand/bibliography.py` 

<details><summary>[28] tool result (3920 chars)</summary>

```
138	async def _ss_search(
139	    query: str,
140	    http: httpx.AsyncClient,
141	    cfg: Config,
142	    limit: int,
143	    year_filter: str | None = None,
144	) -> list[dict]:
145	    global _ss_cooldown_until
146	
147	    params: dict = {"query": query, "fields": _SS_FIELDS, "limit": min(limit, 100)}
148	    if year_filter:
149	        params["year"] = year_filter
150	    headers = {"x-api-key": _SS_API_KEY} if _SS_API_KEY else {}
151	    delay = cfg.bibliography.ss_retry_initial_delay
152	    limiter = _get_ss_limiter(cfg)
153	
154	    for attempt in range(cfg.bibliography.ss_max_retries):
155	        # Global circuit breaker: if any prior request got 429, all tasks wait here.
156	        now = asyncio.get_event_loop().time()
157	        remaining = _ss_cooldown_until - now
158	        if remaining > 0:
159	            emit({
160	                "event": "ss_cooldown_wait",
161	                "query": query[:60],
162	                "wait_s": round(remaining, 1),
163	                "attempt": attempt,
164	            })
165	            await asyncio.sleep(remaining)
166	
167	        emit({
168	            "event": "ss_rate_wait",
169	            "query": query[:60],
170	            "attempt": attempt,
171	            "interval_s": _ss_interval,
172	        })
173	        async with limiter:
174	            emit({
175	                "event": "ss_request",
176	                "query": query[:60],
177	                "limit": min(limit, 100),
178	                "year_filter": year_filter,
179	                "attempt": attempt,
180	            })
181	            resp = await http.get(_SS_BASE, params=params, headers=headers)
182	
183	        emit({
184	            "event": "ss_response",
185	            "query": query[:60],
186	            "status": resp.status_code,
187	            "result_count": len(resp.json().get("data") or []) if resp.status_code == 200 else 0,
188	            "attempt": attempt,
189	        })
190	
191	        if resp.status_code == 4...[truncated]
```
</details>

**[29] tool→Bash:** `grep -n "putenv\|environ.update\|setenv\|environ\b" /Users/cyan/ccr/know-expand/know_expand/observe.py | head; echo ---;` — Check for env injection, bibliography import in observe.py, README line 305

<details><summary>[30] tool result (1234 chars)</summary>

```
2526:        env=os.environ.copy(),
2667:                    "anthropic": bool(os.environ.get("ANTHROPIC_API_KEY")),
2668:                    "openai":    bool(os.environ.get("OPENAI_API_KEY")),
2669:                    "gemini":    bool(os.environ.get("GEMINI_API_KEY")),
2670:                    "groq":      bool(os.environ.get("GROQ_API_KEY")),
2671:                    "mistral":   bool(os.environ.get("MISTRAL_API_KEY")),
---
1154:  let html = kv([['total papers', a.bibliography_count ?? '—']]);
1273:    ['bibliography entries', a.bibliography_count ?? '—'],
2187:            for bib in audit_dir.glob("bibliography_*.json"):
2192:        out["bibliography_count"] = total_papers
2373:        # bibliography count across all domains
2377:            for bib in audit_dir.glob("bibliography_*.json"):
2382:        out["bibliography_count"] = total or None
---
| `ANTHROPIC_API_KEY` | Claude models |
| `OPENAI_API_KEY` | GPT models |
| `GROQ_API_KEY` | Groq fallback |
| `MISTRAL_API_KEY` | Mistral fallback |
| `GEMINI_API_KEY` | Gemini API models (not geminicli) |
| `SS_API_KEY` | Semantic Scholar (optional; raises rate limit from 0.1 → 1 req/s) |

**Never commit `.env` or write keys to disk from the serve process.**

---
```
</details>

**[32] tool→Bash:** `grep -rn "_ss_search\|fetch_bibliography\|fetch_anchors\|from know_expand.bibliography\|import bibliography" /Users/cyan` — Find bibliography callers and error handling

<details><summary>[33] tool result (2479 chars)</summary>

```
/Users/cyan/ccr/know-expand/know_expand/bibliography.py:138:async def _ss_search(
/Users/cyan/ccr/know-expand/know_expand/bibliography.py:285:async def fetch_anchors(
/Users/cyan/ccr/know-expand/know_expand/bibliography.py:293:    papers = await _ss_search(domain_label, http, cfg, limit=n * 4)
/Users/cyan/ccr/know-expand/know_expand/bibliography.py:340:            papers = await _ss_search(query, http, cfg, limit=1)
/Users/cyan/ccr/know-expand/know_expand/bibliography.py:357:async def fetch_bibliography(
/Users/cyan/ccr/know-expand/know_expand/bibliography.py:373:    foundational_raw = await _ss_search(domain_label, http, cfg, limit=n_history * 4)
/Users/cyan/ccr/know-expand/know_expand/bibliography.py:374:    frontier_raw = await _ss_search(
/Users/cyan/ccr/know-expand/know_expand/stages/s2_extract.py:16:from know_expand.bibliography import _ss_search, _get_ss_limiter
/Users/cyan/ccr/know-expand/know_expand/stages/s2_extract.py:292:                results = await _ss_search(term.name, http, cfg, limit=3)
/Users/cyan/ccr/know-expand/know_expand/stages/s6_align.py:31:from know_expand.bibliography import _ss_search, reset_ss_limiter
/Users/cyan/ccr/know-expand/know_expand/stages/s6_align.py:213:    tasks = [_ss_search(q, http, cfg, limit=5) for q in queries]
/Users/cyan/ccr/know-expand/know_expand/stages/s5_research.py:19:from know_expand.bibliography import _ss_search, _get_ss_limiter, _to_citation_record, _make_id
/Users/cyan/ccr/know-expand/know_expand/stages/s5_research.py:424:            return await _ss_search(query, http, cfg, limit=5)
/Users/cyan/ccr/know-expand/know_expand/stages/s4_audit.py:13:from know_expand.bibliography import fetch_anchor_neighbors, fetch_anchors, fetch_bibliography, reset_ss_limiter
/Users/cyan/ccr/know-expand/know_expand/stages/s4_audit.py:286:        fetch_anchors(domain_label, http, cfg),
/Users/cyan/ccr/know-expand/know_expand/stages/s4_audit.py:287:        fetch_bibliography(domain_label, depth, cfg, http),
---
3-/Users/cyan/ccr/kn...[truncated]
```
</details>

**[35] tool→Bash:** `grep -rn "SS_API_KEY\|x-api-key\|unauthenticated\|0.1" /Users/cyan/ccr/know-expand/know_expand/config.py /Users/cyan/ccr` — Check for key-conditional rate logic anywhere else

<details><summary>[36] tool result (1316 chars)</summary>

```
---
/Users/cyan/ccr/know-expand/tests/test_base.py:33:        rate_limits={"semantic_scholar": RateLimitConfig(max_rate=100, time_period=300)},
/Users/cyan/ccr/know-expand/tests/test_bibliography.py:32:        rate_limits={"semantic_scholar": RateLimitConfig(max_rate=100, time_period=300)},
/Users/cyan/ccr/know-expand/tests/test_s3_integration.py:91:        rate_limits={"semantic_scholar": RateLimitConfig(max_rate=100, time_period=1)},
/Users/cyan/ccr/know-expand/tests/test_config.py:83:def test_rate_limits_semantic_scholar_present():
/Users/cyan/ccr/know-expand/tests/test_config.py:85:    assert "semantic_scholar" in cfg.rate_limits
/Users/cyan/ccr/know-expand/tests/test_config.py:86:    rl = cfg.rate_limits["semantic_scholar"]
/Users/cyan/ccr/know-expand/tests/test_config.py:105:        rate_limits={"semantic_scholar": RateLimitConfig(max_rate=1, time_period=1)},
/Users/cyan/ccr/know-expand/tests/test_s6_verify.py:27:        rate_limits={"semantic_scholar": RateLimitConfig(max_rate=100, time_period=300)},
/Users/cyan/ccr/know-expand/tests/test_s7_assemble.py:28:        rate_limits={"semantic_scholar": RateLimitConfig(max_rate=100, time_period=300)},
/Users/cyan/ccr/know-expand/tests/test_s4_research.py:29:        rate_limits={"semantic_scholar": RateLimitConfig(max_rate=100, time_period=300)},
```
</details>

## Structured Output
```json
{
  "verdict": "partially_confirmed",
  "reasoning": "The core claim is materially correct and verified against the code. (1) /Users/cyan/ccr/know-expand/config.yaml lines 20-23 set semantic_scholar max_rate=1/time_period=1 with the literal comment \"authenticated key: 1 req/sec\". (2) _get_ss_limiter at /Users/cyan/ccr/know-expand/know_expand/bibliography.py:77-90 consumes this unconditionally; a repo-wide grep confirms no code anywhere conditions the rate on SS_API_KEY presence \u2014 the key (bibliography.py:62, read at module import) is used only for the optional x-api-key header at lines 150 and 223. (3) README.md:305 and CLAUDE.md:147 document the key as optional and the unauthenticated rate as 0.1 req/s, so the default keyless config overruns the project's own documented unauth limit 10x. (4) The 429 circuit breaker (bibliography.py:145-208, 30s\u2192120s doubling backoff, global _ss_cooldown_until, 5 retries) exists as described and is what absorbs the resulting 429s; worse than the claim states, after retry exhaustion line 209 calls resp.raise_for_status() on the final 429, so the overrun can raise into a stage, not just slow it down. Severity \"high\" is defensible for default keyless runs. However, the \"Additionally\" sub-claim is refuted: the \"key injected later via the web UI session env inside the same process\" scenario cannot occur. observe.py has no key-setting endpoint (GET /api/keys only reads bool(os.environ.get(...)); there is no os.environ[...]= write anywhere in the file), and the pipeline always runs as a fresh subprocess (observe.py:2521-2528, env=os.environ.copy()) in which cli.py calls load_dotenv() (cli.py:7-8) before bibliography.py is imported (pipeline import deferred to cli.py:164). So the import-time read picks up the key correctly in every real execution path; it is a latent code-smell, not a live bug.",
  "correction": "The rate-limit half of the claim is fully correct: the 1 req/s SS limiter is never conditioned on SS_API_KEY presence and overruns the documented unauthenticated limit (0.1 req/s per the repo's own README/CLAUDE.md) by 10x by default, with the 30s-120s cooldown breaker absorbing the self-inflicted 429s \u2014 and after 5 failed retries bibliography.py:209 raise_for_status() actually raises, so impact can exceed mere slowdown. The stale-key half is wrong in practice: there is no web UI mechanism that injects SS_API_KEY into a running process (observe.py's /api/keys is read-only and never writes os.environ), and the pipeline is always a freshly spawned subprocess that runs load_dotenv() before bibliography.py is imported, so the module-import-time read of _SS_API_KEY picks up the key in every actual execution path. The recommended fix should be limited to deriving the limiter rate from key presence; per-request key re-reading addresses a scenario that does not exist in this architecture."
}
```

<details><summary>[40] tool result (39 chars)</summary>

```
Structured output provided successfully
```
</details>

**[41] assistant:** Verification complete.
