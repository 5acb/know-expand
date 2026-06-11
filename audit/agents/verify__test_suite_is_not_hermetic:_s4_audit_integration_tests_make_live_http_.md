# Agent: verify__test_suite_is_not_hermetic:_s4_audit_integration_tests_make_live_http_
**ID:** `agent-a29c9e7323ca50f50`  **Turns:** 45

## Prompt
```
You are an adversarial verifier. A code auditor claims the following about the repo at /Users/cyan/ccr/know-expand:

TITLE: Test suite is not hermetic: s4_audit integration tests make live HTTP calls to OpenAlex/Wikipedia/PyPI/arXiv
LAYER: execution
SEVERITY: high
CLAIM: tests/test_s3_integration.py:186-189 and 309-314 patch fetch_anchors/fetch_bibliography/fetch_anchor_neighbors/make_router but NOT fetch_sources_for_terms, which s4_audit.run() invokes unconditionally (know_expand/stages/s4_audit.py:232,251). Every test run issues real network requests for the fixture terms 'Alpha'/'Beta'/'Gamma'. The suite only passes offline because sources.py swallows every exception into debug logs (know_expand/sources.py:292-294, 346-348) — i.e., the network dependency is masked, not absent. Smoking gun: the observed RuntimeWarning during the run ('AsyncLimiter instance is being re-used across loops' at know_expand/sources.py:314) proves _fetch_openalex_works executed inside the 'fully mocked' test. That warning also exposes a latent production bug: the module-level cached _OPENALEX_LIMITER (sources.py:40-46) is reused across event loops, which aiolimiter documents as undefined behavior — relevant the moment multiple runs share a process.
FILES: /Users/cyan/ccr/know-expand/tests/test_s3_integration.py, /Users/cyan/ccr/know-expand/know_expand/sources.py, /Users/cyan/ccr/know-expand/know_expand/stages/s4_audit.py

Read the actual code and try to REFUTE this claim. Check: does the cited code actually behave as claimed? Is the severity inflated? Is there mitigating code elsewhere (search the repo)? If the claim is materially correct, confirm it. Default to refuted if the evidence is ambiguous.
```

**[4] tool→Read:** `/Users/cyan/ccr/know-expand/tests/test_s3_integration.py` 

<details><summary>[5] tool result (13923 chars)</summary>

```
1	"""
2	Integration test for Stage 3 (Anchored Audit).
3	
4	Mocks:
5	  - fetch_anchors / fetch_bibliography  — no real SS network calls
6	  - router.call                         — canned GapAnalysisResult responses
7	
8	Asserts:
9	  - gap_analysis.md written
10	  - corrections.md written
11	  - stage 3 marked complete in pipeline.json
12	"""
13	import json
14	import shutil
15	import tempfile
16	from pathlib import Path
17	from unittest.mock import AsyncMock, patch
18	
19	import pytest
20	
21	from know_expand.agents.schemas import CitationRecord, GapAnalysisResult, GapFinding
22	from know_expand.config import (
23	    BibliographyConfig,
24	    CentralityConfig,
25	    ConcurrencyConfig,
26	    Config,
27	    LlamaCppConfig,
28	    RateLimitConfig,
29	)
30	from know_expand.state import new_run_id, setup_logging
31	
32	
33	# ---------------------------------------------------------------------------
34	# Fixtures
35	# ---------------------------------------------------------------------------
36	
37	_TAXONOMY = {
38	    "domains": [
39	        {"id": "domain-a", "label": "Domain A", "definition": "x", "example_terms": []},
40	        {"id": "domain-b", "label": "Domain B", "definition": "y", "example_terms": []},
41	    ]
42	}
43	
44	_GRAPH = {
45	    "nodes": [
46	        {"id": "n1", "name": "Alpha", "domain": "domain-a", "tier": "journeyman", "xp": 100,
47	         "prerequisites": [], "unlocks": [], "from_source_doc": True, "centrality": "core"},
48	        {"id": "n2", "name": "Beta", "domain": "domain-a", "tier": "apprentice", "xp": 50,
49	         "prerequisites": [], "unlocks": [], "from_source_doc": True, "centrality": "supporting"},
50	        {"id": "n3", "name": "Gamma", "domain": "domain-b", "tier": "expert", "xp": 200,
51	         "prerequisites": [], "unlocks": [], "from_source_doc": True, "centrality": "core"},
52	    ],
53	    "edges": [],
54	}
55	
56	_ANCHORS: list[dict] = [
57	    {
58	        "id": f"anchor_{i}", "type": "article-journal",
59	     ...[truncated]
```
</details>

**[6] tool→Read:** `/Users/cyan/ccr/know-expand/know_expand/stages/s4_audit.py` 

<details><summary>[7] tool result (3563 chars)</summary>

```
200	
201	async def _process_domain(
202	    domain: dict,
203	    graph_terms: list[str],
204	    audit_dir: Path,
205	    depth: str,
206	    cfg: Config,
207	    router,
208	    http: httpx.AsyncClient,
209	    domain_nodes: list[dict] | None = None,
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
247	                    "event": "s4_sources...[truncated]
```
</details>

**[8] tool→Read:** `/Users/cyan/ccr/know-expand/know_expand/sources.py` 

<details><summary>[9] tool result (1291 chars)</summary>

```
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
63	    text = re.sub(r"\.\.\s+\w[^:\n]*::[^\n]*\n", "", text)
64	    # Remove HTML tags
65	    text = re.sub(r"<[^>]+>", "", text)
66	    # Collapse multiple blank lines
67	    text = re.sub(r"\n{3,}", "\n\n", text)
68	    return text.strip()
69	
```
</details>

**[11] tool→Read:** `/Users/cyan/ccr/know-expand/know_expand/sources.py` 

<details><summary>[12] tool result (7954 chars)</summary>

```
230	        return None
231	
232	
233	async def _fetch_arxiv(
234	    query: str, http: httpx.AsyncClient, domain_context: str = "", max_results: int = 3
235	) -> "list[FetchedSource]":
236	    """Fetch arXiv abstracts for a query.
237	
238	    Uses `ti+abs` field search which returns more relevant results than bare `all:`.
239	    Multi-word queries are space-joined (Lucene AND within field).
240	
241	    Short terms (≤2 words) are enriched with domain_context to avoid off-topic results
242	    (e.g. "domain" alone → astronomy; "domain NLP" → relevant ML papers).
243	    """
244	    # For multi-word queries search title OR abstract; single words use all fields.
245	    # Pass plain strings — httpx params= handles URL encoding automatically.
246	    words = query.strip().split()
247	
248	    # Enrich short/generic queries with domain context to improve relevance
249	    effective_query = query
250	    if domain_context and len(words) <= 2:
251	        effective_query = f"{query} {domain_context}"
252	
253	    if len(effective_query.strip().split()) > 1:
254	        arxiv_query = f"ti:{effective_query} OR abs:{effective_query}"
255	    else:
256	        arxiv_query = f"all:{effective_query}"
257	    results: list[FetchedSource] = []
258	    try:
259	        resp = await http.get(
260	            "https://export.arxiv.org/api/query",
261	            params={"search_query": arxiv_query, "max_results": max_results, "sortBy": "relevance"},
262	            timeout=20.0,
263	        )
264	        resp.raise_for_status()
265	        root = ET.fromstring(resp.text)
266	
267	        for entry in root.findall(f"{{{_ARXIV_NS}}}entry"):
268	            title_el = entry.find(f"{{{_ARXIV_NS}}}title")
269	            summary_el = entry.find(f"{{{_ARXIV_NS}}}summary")
270	            link_el = entry.find(f'{{{_ARXIV_NS}}}link[@type="text/html"]')
271	            if link_el is None:
272	                link_el = entry.find(f"{{{_ARXIV_NS}}}id")
273	
274	            title = title_el.t...[truncated]
```
</details>

**[13] tool→Read:** `/Users/cyan/ccr/know-expand/know_expand/stages/s4_audit.py` 

<details><summary>[14] tool result (7729 chars)</summary>

```
1	"""Stage 3 — Anchored Audit: per-domain bibliography and gap analysis."""
2	
3	import asyncio
4	import json
5	import logging
6	import time
7	from pathlib import Path
8	
9	import httpx
10	
11	from know_expand.agents.base import make_router
12	from know_expand.agents.schemas import GapAnalysisResult
13	from know_expand.bibliography import fetch_anchor_neighbors, fetch_anchors, fetch_bibliography, reset_ss_limiter
14	from know_expand.config import Config
15	from know_expand.state import PipelineState, emit, mark_stage_complete, stage_is_complete
16	
17	_logger = logging.getLogger("know_expand.s4")
18	
19	_GAP_FINDER_PROMPT = """\
20	You are a research gap analyst. Given the terms from a domain's knowledge graph and \
21	the titles and abstracts of anchor papers for that domain, identify what the anchor \
22	papers cover that the graph is missing.
23	
24	Domain: {domain_label}
25	
26	Graph terms: {graph_terms}
27	
28	Anchor papers:
29	{anchor_summaries}
30	
31	Look especially for gaps in these categories:
32	- Methodological gaps: approaches described in the papers but absent from the graph
33	- Citation graph gaps: the papers cite foundational work or datasets not present in the graph
34	- Snowballing gaps: if the anchor papers reference key sub-fields that the graph does not cover
35	- Evaluation gaps: benchmarks, metrics, or reproducibility practices mentioned in papers but missing
36	
37	For each gap, provide a gap_description and list the evidence_anchor_ids (paper IDs \
38	from the anchor list) that reveal the gap. Return a GapAnalysisResult with \
39	domain_id="{domain_id}".
40	"""
41	
42	_GAP_DEFENDER_PROMPT = """\
43	You are a knowledge graph defender. For each gap finding below, argue that the graph \
44	already covers it — either under a different term name, as an alias, or that it is \
45	out of scope for this domain.
46	
47	Domain: {domain_label}
48	Domain ID: {domain_id}
49	
50	Graph terms: {graph_terms}
51	
52	Gap findings:
53	{gap_findings}
54	
55	CRIT...[truncated]
```
</details>

**[16] tool→Read:** `/Users/cyan/ccr/know-expand/know_expand/stages/s4_audit.py` 

<details><summary>[17] tool result (7631 chars)</summary>

```
280	        "label": domain_label,
281	        "term_count": len(graph_terms),
282	    })
283	
284	    emit({"event": "domain_fetch_start", "domain_id": domain_id, "depth": depth})
285	    (anchors_list, anchor_ss_ids), bibliography = await asyncio.gather(
286	        fetch_anchors(domain_label, http, cfg),
287	        fetch_bibliography(domain_label, depth, cfg, http),
288	    )
289	    neighbor_papers = await fetch_anchor_neighbors(anchor_ss_ids, bibliography, http, cfg)
290	    if neighbor_papers:
291	        bibliography = bibliography + neighbor_papers
292	    emit({
293	        "event": "domain_fetch_done",
294	        "domain_id": domain_id,
295	        "elapsed_s": round(time.monotonic() - t0, 2),
296	        "neighbor_count": len(neighbor_papers),
297	    })
298	
299	    anchors_dicts = [a.model_dump() for a in anchors_list]
300	    anchors_path = audit_dir / f"anchors_{domain_id}.json"
301	    anchors_path.write_text(json.dumps(anchors_dicts, indent=2))
302	    emit({
303	        "event": "anchors_fetched",
304	        "domain_id": domain_id,
305	        "anchor_count": len(anchors_dicts),
306	        "artifact": str(anchors_path),
307	    })
308	
309	    bib_path.write_text(json.dumps([b.model_dump() for b in bibliography], indent=2))
310	    emit({
311	        "event": "bibliography_fetched",
312	        "domain_id": domain_id,
313	        "entry_count": len(bibliography),
314	        "artifact": str(bib_path),
315	    })
316	
317	    gap_result = await _run_gap_loop(
318	        domain_id, domain_label, graph_terms, anchors_dicts, router,
319	        rounds=cfg.adversarial_rounds.get(depth, 3),
320	    )
321	    emit({
322	        "event": "domain_complete",
323	        "domain_id": domain_id,
324	        "elapsed_s": round(time.monotonic() - t0, 2),
325	        "real_gaps": sum(1 for g in gap_result.gaps if g.verdict == "real_gap"),
326	    })
327	    return gap_result
328	
329	
330	def _build_gap_markdown(results: list[GapAnalysisResult], domains_by_i...[truncated]
```
</details>

**[18] tool→Bash:** `ls /Users/cyan/ccr/know-expand/tests/ && cat /Users/cyan/ccr/know-expand/tests/conftest.py 2>/dev/null; echo "---"; grep` — List tests and search for network mocking of fetch_sources_for_terms

<details><summary>[19] tool result (422 chars)</summary>

```
Exit code 2
__init__.py
__pycache__
test_base.py
test_bibliography.py
test_centrality.py
test_config.py
test_llamacpp_routing.py
test_s10_pdf.py
test_s3_integration.py
test_s4_research.py
test_s5_synthesize.py
test_s6_verify.py
test_s7_assemble.py
test_sources.py
test_union_find.py
---
/Users/cyan/ccr/know-expand/tests/test_s3_integration.py:6:  - router.call                         — canned GapAnalysisResult responses
```
</details>

**[21] tool→Read:** `/Users/cyan/ccr/know-expand/know_expand/sources.py` 

<details><summary>[22] tool result (7600 chars)</summary>

```
69	
70	
71	def _is_searchable_term(term: str) -> bool:
72	    """Return False for terms that are structurally unsuitable as external search queries.
73	
74	    Uses only document-agnostic structural rules — no hardcoded domain vocabulary,
75	    so the same filter works for any input document.
76	    """
77	    t = term.strip()
78	    tl = t.lower()
79	
80	    # Too short or too long
81	    if len(t) < 3 or len(t.split()) > 4:
82	        return False
83	
84	    # Phrases starting with determiners/articles — sentence fragments, not concepts
85	    if re.match(r'^(a |an |the |every |any |all |some |each |this |that |these |those )', tl):
86	        return False
87	
88	    # "N <noun>" patterns — metrics/counts, not searchable concepts
89	    # e.g. "300 terms", "24 months", "8 domains"
90	    if re.match(r'^\d+\s+\w+', tl):
91	        return False
92	
93	    # Possessive forms — refer to something else, not a standalone concept
94	    # e.g. "stage 5's", "model's output"
95	    if re.search(r"'s\b", tl):
96	        return False
97	
98	    # snake_case or camelCase — almost certainly a variable/identifier artifact
99	    if re.search(r'[a-z]_[a-z]', t) or re.search(r'[a-z][A-Z]', t):
100	        return False
101	
102	    # Verb phrases — action descriptions extracted as terms
103	    # e.g. "running docling", "mapping chunks", "writing sections"
104	    if re.match(r'^(using|running|writing|reading|loading|saving|mapping|fetching|'
105	                r'building|parsing|generating|computing|extracting|merging|'
106	                r'resolving|validating|processing|producing|inserting)\b', tl):
107	        return False
108	
109	    return True
110	
111	
112	# ---------------------------------------------------------------------------
113	# Per-source fetchers
114	# ---------------------------------------------------------------------------
115	
116	async def _fetch_wikipedia(term: str, http: httpx.AsyncClient) -> "FetchedSource | None":
117	    """Search Wikipedia and r...[truncated]
```
</details>

**[23] tool→Bash:** `grep -n "reset_ss_limiter\|_SS_LIMITER\|new_event_loop\|asyncio_mode\|loop_scope" /Users/cyan/ccr/know-expand/know_expan` — Check limiter reset functions and pytest asyncio config

<details><summary>[24] tool result (635 chars)</summary>

```
/Users/cyan/ccr/know-expand/know_expand/bibliography.py:70:def reset_ss_limiter() -> None:
/Users/cyan/ccr/know-expand/pyproject.toml:47:asyncio_mode = "auto"
---reset openalex---
/Users/cyan/ccr/know-expand/know_expand/sources.py:40:_OPENALEX_LIMITER: AsyncLimiter | None = None
/Users/cyan/ccr/know-expand/know_expand/sources.py:44:    global _OPENALEX_LIMITER
/Users/cyan/ccr/know-expand/know_expand/sources.py:45:    if _OPENALEX_LIMITER is None:
/Users/cyan/ccr/know-expand/know_expand/sources.py:46:        _OPENALEX_LIMITER = AsyncLimiter(10, 1)
/Users/cyan/ccr/know-expand/know_expand/sources.py:47:    return _OPENALEX_LIMITER
```
</details>

**[27] tool→Bash:** `timeout 300 uv run pytest tests/test_s3_integration.py -p no:cacheprovider -W default -o log_cli=true -o log_cli_level=D` — Run s3 integration tests with debug logging, grep for network activity

<details><summary>[28] tool result (31 chars)</summary>

```
(Bash completed with no output)
```
</details>

**[30] tool→Bash:** `uv run pytest tests/test_s3_integration.py -p no:cacheprovider -W default 2>&1 | tail -30` — Run s3 integration tests and show output tail

<details><summary>[31] tool result (2712 chars)</summary>

```
tests/test_s3_integration.py::test_s3_domain_failure_does_not_abort_other_domains
  /Users/cyan/ccr/know-expand/know_expand/state.py:62: ResourceWarning: unclosed file <_io.TextIOWrapper name='/private/var/folders/5f/f86rs20s0f7dxt4z8t2jrkhr0000gn/T/pytest-of-cyan/pytest-1/test_s3_skips_completed_stage0/logs/events.jsonl' mode='a' encoding='utf-8'>
    _event_fh = open(_log_dir / "events.jsonl", "a", buffering=1)
  Enable tracemalloc to get traceback where the object was allocated.
  See https://docs.pytest.org/en/stable/how-to/capture-warnings.html#resource-warnings for more info.

tests/test_s3_integration.py::test_s3_domain_failure_does_not_abort_other_domains
  /Users/cyan/ccr/know-expand/know_expand/state.py:80: ResourceWarning: unclosed file <_io.TextIOWrapper name='/private/var/folders/5f/f86rs20s0f7dxt4z8t2jrkhr0000gn/T/pytest-of-cyan/pytest-1/test_s3_skips_completed_stage0/logs/errors.log' mode='a' encoding='utf-8'>
    root.handlers.clear()
  Enable tracemalloc to get traceback where the object was allocated.
  See https://docs.pytest.org/en/stable/how-to/capture-warnings.html#resource-warnings for more info.

tests/test_s3_integration.py::test_s3_domain_failure_does_not_abort_other_domains
  /Users/cyan/ccr/know-expand/know_expand/state.py:80: ResourceWarning: unclosed file <_io.TextIOWrapper name='/private/var/folders/5f/f86rs20s0f7dxt4z8t2jrkhr0000gn/T/pytest-of-cyan/pytest-1/test_s3_skips_completed_stage0/logs/debug.log' mode='a' encoding='utf-8'>
    root.handlers.clear()
  Enable tracemalloc to get traceback where the object was allocated.
  See https://docs.pytest.org/en/stable/how-to/capture-warnings.html#resource-warnings for more info.

tests/test_s3_integration.py::test_s3_domain_failure_does_not_abort_other_domains
  /Users/cyan/ccr/know-expand/know_expand/state.py:80: ResourceWarning: unclosed file <_io.TextIOWrapper name='/private/var/folders/5f/f86rs20s0f7dxt4z8t2jrkhr0000gn/T/pytest-of-cyan/pytest-1/test_s3_skips_completed_stage0/logs/pipel...[truncated]
```
</details>

**[34] tool→Bash:** `grep -rhiE "wikipedia|openalex|arxiv|pypi|httpx|httpcore" /private/var/folders/5f/f86rs20s0f7dxt4z8t2jrkhr0000gn/T/pytes` — Grep test debug logs for HTTP fetch attempts to external APIs

<details><summary>[35] tool result (567 chars)</summary>

```
   4 D know_expand.sources: wikipedia search failed for 'Gamma': Client error '403 Forbidden' for url 'https://en.wikipedia.org/w/api.php?action=opensearch&search=Gamma&limit=3&format=json'
   4 D know_expand.sources: wikipedia search failed for 'Beta': Client error '403 Forbidden' for url 'https://en.wikipedia.org/w/api.php?action=opensearch&search=Beta&limit=3&format=json'
   4 D know_expand.sources: wikipedia search failed for 'Alpha': Client error '403 Forbidden' for url 'https://en.wikipedia.org/w/api.php?action=opensearch&search=Alpha&limit=3&format=json'
```
</details>

**[38] tool→Read:** `/Users/cyan/ccr/know-expand/know_expand/bibliography.py` 

<details><summary>[39] tool result (1395 chars)</summary>

```
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
84	        _ss_interval = rl.time_period / max(rl.max_rate, 1)
```
</details>

## Structured Output
```json
{
  "verdict": "confirmed",
  "reasoning": "Attempted refutation failed; the claim verified at every load-bearing point, both structurally and empirically. (1) tests/test_s3_integration.py:185-190 and 309-314 patch only fetch_anchors/fetch_bibliography/fetch_anchor_neighbors/make_router; fetch_sources_for_terms is never patched, and there is no conftest.py or network-blocking plugin (no pytest-socket/respx/responses anywhere in the repo). (2) s4_audit.run() builds a real httpx.AsyncClient (s4_audit.py:410) and _process_domain lazily imports (line 232) and awaits fetch_sources_for_terms (line 251); the test fixtures guarantee the guards pass (fresh tmpdir so no sources cache; nodes have core/supporting centrality). (3) Ran the suite: 3 passed, and the test-run debug logs contain live HTTP responses from en.wikipedia.org \u2014 \"wikipedia search failed for 'Alpha': Client error '403 Forbidden' for url 'https://en.wikipedia.org/w/api.php?action=opensearch&search=Alpha...'\" (same for Beta/Gamma) \u2014 real network requests issued and their failures swallowed to debug logs exactly at the cited lines (sources.py 183-184, 292-294, 346-348), which is why the suite passes regardless of connectivity: masked, not absent. (4) The cited RuntimeWarning reproduced verbatim at know_expand/sources.py:314 during test_s3_domain_failure_does_not_abort_other_domains, proving _fetch_openalex_works ran inside the \"mocked\" test and that the module-cached _OPENALEX_LIMITER (sources.py:40-47) created in one test's event loop was reused in another loop. (5) The latent-bug portion is corroborated in-repo: bibliography.py:70 reset_ss_limiter() exists for the identical cross-loop problem with the SS limiter and is called at s4_audit.run() start (line 376), but no reset exists for _OPENALEX_LIMITER. Severity \"high\" is defensible: every suite run hits public third-party APIs, and total breakage of the sources feature would be invisible to tests.",
  "correction": "Three immaterial overstatements: \"invokes unconditionally\" \u2014 the call is actually guarded by `if not sources_cache.exists()` and a non-empty terms list (plus try/except), though both guards always pass under the test fixtures so the effect in tests is as claimed; the title's \"PyPI/arXiv\" \u2014 with no terms.json in the fixtures all terms default to term_type \"academic\", so only Wikipedia and OpenAlex endpoints are actually hit by these tests (PyPI/arXiv paths are reachable in general but not exercised here); and \"every test run\" \u2014 test_s3_skips_completed_stage individually makes no network calls (early return), though every run of the file as a whole does."
}
```

<details><summary>[43] tool result (39 chars)</summary>

```
Structured output provided successfully
```
</details>

**[44] assistant:** Verification complete — verdict: confirmed.
