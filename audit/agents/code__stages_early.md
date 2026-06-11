# Agent: agent__unknown_02
**ID:** `agent-a8a7ca3b3a93e8e50`  **Turns:** 62

## Prompt
```
You are auditing the codebase at /Users/cyan/ccr/know-expand (a Python multi-agent CLI called know-expand that turns a technical document into a long "knowledge expansion" PDF via a LangGraph pipeline of 11 stages, with a QuotaAwareRouter over many LLM providers and a single-file web dashboard). Read the actual files with Read/Grep/Bash. Be a rigorous senior staff engineer: every issue must cite specific files/lines and be classified by layer — "objective" (is the goal itself right / coherent), "architecture" (structural design decisions), "planning" (roadmap, sequencing, what's prioritized), "execution" (bugs, code quality, correctness). Judge against the goal of making this a production-grade product that many users run concurrently (SaaS or self-hosted), not a personal tool. Do NOT pad with trivia; do report real critical/high problems. Also read /Users/cyan/ccr/know-expand/CLAUDE.md and /Users/cyan/ccr/know-expand/README.md for stated intent so you can flag intent-vs-code mismatches.

Your subsystem: stages S0–S4 — /Users/cyan/ccr/know-expand/know_expand/stages/s0_ingest.py, s2_extract.py, s1_assess.py, s3_graph.py, s4_audit.py plus sources.py and bibliography.py. Assess: ingestion robustness (Docling failure modes, URL fetch, scanned PDFs), 3-signal term extraction quality and determinism, the 10-15 turn human interview (S1) — is mandatory human interaction at two stages compatible with a scalable product? The taxonomy review loop (S3), the adversarial gap-finder loop (S4), Semantic Scholar/Crossref/OpenAlex rate limits (0.1-1 req/s) as a throughput ceiling, the 65/35 foundational/frontier bibliography split ("non-negotiable" per CLAUDE.md — evaluate whether hard-coding that is justified), and caching/reuse across runs (is the same Wikipedia/SS data refetched per run?).
```

**[4] tool→Bash:** `wc -l /Users/cyan/ccr/know-expand/know_expand/stages/s0_ingest.py /Users/cyan/ccr/know-expand/know_expand/stages/s2_extr` — Count lines in subsystem files

<details><summary>[5] tool result (647 chars)</summary>

```
     173 /Users/cyan/ccr/know-expand/know_expand/stages/s0_ingest.py
     480 /Users/cyan/ccr/know-expand/know_expand/stages/s2_extract.py
     741 /Users/cyan/ccr/know-expand/know_expand/stages/s1_assess.py
     420 /Users/cyan/ccr/know-expand/know_expand/stages/s3_graph.py
     464 /Users/cyan/ccr/know-expand/know_expand/stages/s4_audit.py
     481 /Users/cyan/ccr/know-expand/know_expand/sources.py
     413 /Users/cyan/ccr/know-expand/know_expand/bibliography.py
    1022 /Users/cyan/ccr/know-expand/README.md
     146 /Users/cyan/ccr/know-expand/know_expand/config.py
     380 /Users/cyan/ccr/know-expand/know_expand/state.py
    4720 total
```
</details>

**[6] tool→Bash:** `ls /Users/cyan/ccr/know-expand /Users/cyan/ccr/know-expand/know_expand /Users/cyan/ccr/know-expand/know_expand/stages /U` — List project directories

<details><summary>[7] tool result (617 chars)</summary>

```
/Users/cyan/ccr/know-expand:
CLAUDE.md
LICENSE
README.md
config.yaml
docker-compose.yml
know_expand
models.yaml
pyproject.toml
tests
uv.lock

/Users/cyan/ccr/know-expand/know_expand:
__init__.py
agents
bibliography.py
centrality.py
cli.py
config.py
observe.py
pipeline.py
sources.py
stages
state.py
union_find.py

/Users/cyan/ccr/know-expand/know_expand/agents:
__init__.py
base.py
lc_adapter.py
schemas.py

/Users/cyan/ccr/know-expand/know_expand/stages:
__init__.py
s0_ingest.py
s10_assemble.py
s1_assess.py
s2_extract.py
s3_graph.py
s4_audit.py
s5_research.py
s6_align.py
s7_synthesize.py
s8_verify.py
s9_prereq.py
```
</details>

**[9] tool→Read:** `/Users/cyan/ccr/know-expand/know_expand/stages/s0_ingest.py` 

<details><summary>[10] tool result (7541 chars)</summary>

```
1	import json
2	from pathlib import Path
3	
4	import httpx
5	import spacy
6	from docling.datamodel.document import DocItemLabel
7	from docling.document_converter import DocumentConverter
8	from docling.chunking import HybridChunker
9	
10	from know_expand.config import Config
11	from know_expand.state import PipelineState, emit, mark_stage_complete, stage_is_complete
12	
13	_STRUCTURAL_LABELS = {DocItemLabel.SECTION_HEADER, DocItemLabel.TITLE}
14	
15	
16	def _extract_structural_zones(doc, nlp) -> set[str]:
17	    zones: set[str] = set()
18	    for item, _ in doc.iterate_items():
19	        if item.label in _STRUCTURAL_LABELS and item.text:
20	            parsed = nlp(item.text)
21	            zones.update(chunk.text.lower() for chunk in parsed.noun_chunks)
22	            zones.update(tok.text.lower() for tok in parsed if tok.pos_ == "NOUN")
23	    return zones
24	
25	
26	def _fetch_url(url: str, timeout: int = 30) -> str:
27	    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
28	        resp = client.get(url)
29	        resp.raise_for_status()
30	        return resp.text
31	
32	
33	async def run(state: PipelineState, cfg: Config) -> None:
34	    state_dir = Path(state["state_dir"])
35	
36	    if stage_is_complete(state_dir, 0):
37	        emit({"event": "stage_skipped", "stage": 0, "reason": "already_complete"})
38	        return
39	
40	    emit({"event": "stage_start", "stage": 0})
41	
42	    input_path = state["input_path"]
43	    chunks_dir = state_dir / "chunks"
44	    chunks_dir.mkdir(parents=True, exist_ok=True)
45	
46	    nlp = spacy.load(cfg.nlp_models.spacy)
47	
48	    # --- Ingest source ---
49	    if input_path.startswith("http://") or input_path.startswith("https://"):
50	        raw_text = _fetch_url(input_path, timeout=cfg.timeouts["http_fetch_seconds"])
51	        source_txt = state_dir / "source.txt"
52	        source_txt.write_text(raw_text)
53	        meta = {"source_type": "url", "url": input_path, "title": input_path}
54	     ...[truncated]
```
</details>

**[11] tool→Read:** `/Users/cyan/ccr/know-expand/know_expand/stages/s2_extract.py` 

<details><summary>[12] tool result (20609 chars)</summary>

```
1	import asyncio
2	import json
3	import logging
4	import re
5	import time
6	from pathlib import Path
7	
8	import httpx
9	import numpy as np
10	import spacy
11	from keybert import KeyBERT
12	from sentence_transformers import SentenceTransformer
13	
14	from know_expand.agents.base import make_router
15	from know_expand.agents.schemas import TermInventory, TermOccurrence, CanonicalTerm, TermKindBatch
16	from know_expand.bibliography import _ss_search, _get_ss_limiter
17	from know_expand.centrality import compute_centrality, BOILERPLATE
18	from know_expand.config import Config
19	from know_expand.state import (
20	    PipelineState, emit, mark_stage_complete, stage_is_complete,
21	)
22	from know_expand.union_find import UnionFind
23	
24	_logger = logging.getLogger("know_expand.s2")
25	
26	_MAP_PROMPT = """\
27	You are a domain expert reading a chunk of a technical document.
28	Extract every substantive concept that a domain expert would recognise as load-bearing.
29	Go beyond literal text: include implicit domain concepts implied by the content.
30	
31	Strict exclusions (return nothing for these):
32	- Boilerplate structural terms: introduction, methodology, conclusion, etc.
33	- Code fragments, variable names, function names, class names, import paths
34	- JSON keys, string literals, Python tokens (str, None, True, False, etc.)
35	- File paths, URLs, version numbers, numeric literals
36	- Single-character tokens or generic NPs: "the user", "the output", "the default"
37	
38	Chunk:
39	{text}
40	
41	For each term, provide: name (clean English noun phrase, no code syntax), aliases \
42	(variant spellings/abbreviations), co_occurring_terms (terms that appear near it), \
43	a short context_snippet, and occurrence_count within this chunk.
44	"""
45	
46	
47	async def _map_chunk(
48	    chunk_path: Path,
49	    map_dir: Path,
50	    nlp,
51	    kw_model: KeyBERT,
52	    router,
53	    structural_zones: set[str],
54	    cfg: Config,
55	) -> list[TermOccurrence]:
56	    chunk = ...[truncated]
```
</details>

**[13] tool→Read:** `/Users/cyan/ccr/know-expand/know_expand/stages/s1_assess.py` 

<details><summary>[14] tool result (32190 chars)</summary>

```
1	"""Stage 1 — User Assessment (Agentic Interview).
2	
3	Conducts a dynamic 10–15 question interview tailored to the document content
4	and the user's evolving answers, then synthesises a structured UserProfile.
5	
6	Two delivery modes:
7	  - Terminal (TTY present): prompts are printed, answers read from stdin.
8	  - Web IPC (no TTY / headless): questions written to qa_queue.jsonl, answers
9	    polled from qa_answers.jsonl; sentinel qa_complete created when done.
10	
11	IPC file protocol (all files inside state_dir/):
12	  qa_queue.jsonl   — one JSON line per question appended by the pipeline
13	  qa_answers.jsonl — one JSON line per answer appended by the web server
14	  qa_complete      — sentinel: pipeline creates this when Q&A is finished
15	"""
16	
17	import asyncio
18	import json
19	import re
20	import sys
21	import time
22	from pathlib import Path
23	
24	from know_expand.agents.base import make_router
25	from know_expand.agents.schemas import ConceptList, InterviewDecision, UserProfile
26	from know_expand.config import Config
27	from know_expand.state import (
28	    PipelineState,
29	    atomic_write,
30	    emit,
31	    mark_stage_complete,
32	    stage_is_complete,
33	)
34	
35	_MAX_TURNS = 15
36	_MIN_TURNS = 10
37	_WEB_POLL_INTERVAL = 0.5   # seconds between polls for qa_answers.jsonl
38	_WEB_TIMEOUT = 300         # seconds to wait for a web answer before giving up
39	
40	_DEFAULT_PROFILE = UserProfile(
41	    familiarity_level="practitioner",
42	    background_field="unknown",
43	    q3_correct=True,
44	    math_comfort="engage",
45	    learning_goal="apply",
46	    q6_response="",
47	    q6_known="partial",
48	    q7_correct="partial",
49	    known_concepts=[],
50	    unknown_concepts=[],
51	    effective_depth="standard",
52	    math_mode="equations_explained",
53	    reading_goal_note="Apply the techniques described in the document.",
54	    primary_use_case="",
55	    time_available="study",
56	    prior_exposure=[],
57	    preferred_analogy_domain="...[truncated]
```
</details>

**[15] tool→Read:** `/Users/cyan/ccr/know-expand/know_expand/stages/s3_graph.py` 

<details><summary>[16] tool result (16424 chars)</summary>

```
1	import asyncio
2	import json
3	import os
4	import subprocess
5	import sys
6	import time
7	from pathlib import Path
8	
9	import httpx
10	
11	from know_expand.agents.base import make_router
12	from know_expand.agents.schemas import (
13	    TaxonomyProposal, KnowledgeGraph,
14	    GraphNode, DomainProposal,
15	)
16	from know_expand.config import Config
17	from know_expand.state import PipelineState, emit, mark_stage_complete, stage_is_complete
18	
19	_BOLD  = "\033[1m"
20	_CYAN  = "\033[36m"
21	_GREEN = "\033[32m"
22	_RESET = "\033[0m"
23	
24	
25	def _print_proposal(label: str, proposal: TaxonomyProposal) -> None:
26	    print(f"\n{_CYAN}::{_RESET} {_BOLD}{label}{_RESET} ({len(proposal.domains)} domains)")
27	    for i, d in enumerate(proposal.domains, 1):
28	        oa = f"  openalex L{d.openalex_level}" if d.openalex_level is not None else ""
29	        print(f"   {i:2}.  {d.label}{oa}")
30	
31	
32	async def _explain_taxonomy(
33	    lumper: TaxonomyProposal,
34	    splitter: TaxonomyProposal,
35	    router,
36	) -> None:
37	    from know_expand.agents.schemas import TaxonomyExplanation
38	    msg = [{
39	        "role": "user",
40	        "content": _EXPLAIN_PROMPT.format(
41	            lumper=lumper.model_dump_json(),
42	            splitter=splitter.model_dump_json(),
43	        ),
44	    }]
45	    explanation = await router.call(msg, TaxonomyExplanation)
46	    print(f"\n{_CYAN}{'─' * 60}{_RESET}")
47	    print(f"{_BOLD}What each option means for this document:{_RESET}\n")
48	    print(explanation.body)
49	    print(f"{_CYAN}{'─' * 60}{_RESET}\n")
50	
51	
52	_TAXONOMY_REVIEW_FILE = "taxonomy_review.json"
53	_TAXONOMY_CHOICE_FILE = "taxonomy_choice.json"
54	_TAXONOMY_WEB_TIMEOUT = 600   # 10 minutes
55	
56	
57	async def _prompt_taxonomy_web(
58	    lumper: TaxonomyProposal,
59	    splitter: TaxonomyProposal,
60	    taxonomy_path: Path,
61	    router,
62	    issues: list[str],
63	    cfg,
64	) -> dict:
65	    """Web IPC variant: write proposals to state dir, po...[truncated]
```
</details>

**[17] tool→Read:** `/Users/cyan/ccr/know-expand/know_expand/stages/s4_audit.py` 

<details><summary>[18] tool result (18920 chars)</summary>

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

**[19] tool→Read:** `/Users/cyan/ccr/know-expand/know_expand/sources.py` 

<details><summary>[20] tool result (19855 chars)</summary>

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

**[21] tool→Read:** `/Users/cyan/ccr/know-expand/know_expand/bibliography.py` 

<details><summary>[22] tool result (15985 chars)</summary>

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

**[24] tool→Read:** `/Users/cyan/ccr/know-expand/README.md` 

<details><summary>[25] tool result (59584 chars)</summary>

```
1	# know-expand
2	
3	> Turn any technical document into a research-publication-grade knowledge expansion — automatically.
4	
5	**Python 3.11+ · LangGraph · LiteLLM · Docling · asyncio**
6	
7	---
8	
9	## Quick start
10	
11	```bash
12	# Install
13	pip install uv
14	uv sync
15	
16	# Expand a paper
17	uv run know-expand path/to/paper.pdf
18	
19	# Watch progress in your browser
20	uv run know-expand serve          # http://localhost:7842
21	
22	# Resume a stopped run
23	uv run know-expand --status
24	uv run know-expand --resume 4
25	```
26	
27	Each run writes to `runs/{run_id}/` containing `state/`, `logs/`, and `output/`.
28	
29	---
30	
31	## What it does
32	
33	A person reads a dense technical paper. They understand the words but not the field. They cannot ask good questions, cannot evaluate the claims, cannot know what to read next, cannot build on top of it.
34	
35	`know-expand` takes that document and produces a **navigable, ground-up knowledge expansion** — a structured field guide covering everything needed to understand the paper deeply, go beyond it, and do independent research.
36	
37	**Not a summary. Not a chatbot answer. Not a Q&A wrapper.** A full-stack knowledge construction pipeline that identifies every substantive concept in a document, maps those concepts into a principled domain taxonomy, augments each domain from first principles to the current research frontier, synthesizes cross-domain insights invisible within any single domain, and grounds every claim in verifiable, real citations.
38	
39	The reference output: a 480KB, 241-page, 81-node skill-tree document with verified citations, LaTeX mathematics, and technical diagrams — produced automatically from a single source paper.
40	
41	### Cost and latency
42	
43	**Cost:** a deep run is roughly 8 domains × (3 personas + 1 reconciler) × up to 3 critic rounds, plus adversarial loops in S4 and S7. ~150–300 LLM calls for the researcher/synthesizer role. At current Opus 4.7 pricing, expect **$50–150 per dee...[truncated]
```
</details>

**[27] assistant:** Now let me check the supporting files — config, schemas, centrality, CLI — and verify some cross-references.

**[28] tool→Read:** `/Users/cyan/ccr/know-expand/config.yaml` 

<details><summary>[29] tool result (2497 chars)</summary>

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
61	
62	graph_defaults:
63	  node_tier: "journeyman"
64	  node_xp: 100
65	
66	llamacpp:
67	  base_url: "http://192.168.1.117:8080"
68	  # health_path: /health  (llama-server default)
69	
70	# S5 persona prompt sizing.
71	# knowledge sources caps: survey=~1.5k / standard=~3.2k / deep=~15k chars.
72	# All well under the ~44k that caused geminicli timeouts.
73	# Raise deep.sources_max_terms if domain has 11+ core nodes and output quality is thin.
74	research_context:
75	  nodes_top_n: 30
76	  sources_max_terms:
77	    survey: 5
78	    standard: 8
79	    deep: 15
80	  sources_max_per_term:
81	    survey: 1
82	    standard: 1
83	    d...[truncated]
```
</details>

**[30] tool→Bash:** `grep -rn "fetch_source_refs\|source_refs_raw\|diskcache\|DiskCache\|crossref\|Crossref" /Users/cyan/ccr/know-expand/know` — Find usage of source refs, DiskCache, Crossref

<details><summary>[31] tool result (495 chars)</summary>

```
/Users/cyan/ccr/know-expand/know_expand/bibliography.py:319:async def fetch_source_refs(
/Users/cyan/ccr/know-expand/know_expand/stages/s0_ingest.py:86:            (state_dir / "source_refs_raw.json").write_text(
/Users/cyan/ccr/know-expand/know_expand/stages/s0_ingest.py:163:    source_ref_count = len(json.loads((state_dir / "source_refs_raw.json").read_text())) \
/Users/cyan/ccr/know-expand/know_expand/stages/s0_ingest.py:164:        if (state_dir / "source_refs_raw.json").exists() else 0
```
</details>

**[33] tool→Bash:** `grep -n "add_argument\|add_parser\|def cmd_\|sys.stdin" /Users/cyan/ccr/know-expand/know_expand/cli.py | head -60` — List CLI flags and subcommands

<details><summary>[34] tool result (1470 chars)</summary>

```
60:    parser.add_argument("input", nargs="?", help="File path, URL, or - for stdin")
65:    tail_p = subparsers.add_parser("tail", help="Stream pipeline events to stdout")
66:    tail_p.add_argument("run_id", nargs="?", default=None, help="Run ID (default: latest)")
67:    tail_p.add_argument("--runs-dir", type=Path, default=Path("runs"))
70:    serve_p = subparsers.add_parser("serve", help="Serve completed sections at localhost")
71:    serve_p.add_argument("--port", type=int, default=7842)
72:    serve_p.add_argument("--runs-dir", type=Path, default=Path("runs"))
75:    parser.add_argument("--human", action="store_true", help="Rich terminal output")
76:    parser.add_argument("--no-pdf", action="store_true", help="Skip PDF build")
79:    parser.add_argument(
82:    parser.add_argument("--status", action="store_true", help="Emit pipeline status and exit")
83:    parser.add_argument("--resume", type=str, metavar="STAGE", help="Resume from stage N")
84:    parser.add_argument("--stage", type=str, metavar="STAGE", help="Run only stage N then stop")
85:    parser.add_argument("--user-profile", type=Path, metavar="PATH")
86:    parser.add_argument("--auto-taxonomy", action="store_true")
87:    parser.add_argument("--no-bibliography-fetch", action="store_true")
88:    parser.add_argument(
94:    parser.add_argument(
100:    parser.add_argument("--concurrency", type=int, default=None,
104:    parser.add_argument(
152:        content = sys.stdin.read()
```
</details>

**[35] tool→Bash:** `grep -n "class TermOccurrence" -A 12 /Users/cyan/ccr/know-expand/know_expand/agents/schemas.py; grep -n "class Canonical` — Inspect schema defaults for term models

<details><summary>[36] tool result (1506 chars)</summary>

```
10:class TermOccurrence(BaseModel):
11-    name: str
12-    aliases: list[str] = Field(default_factory=list)
13-    co_occurring_terms: list[str] = Field(default_factory=list)
14-    context_snippet: str = ""
15-    occurrence_count: int = 1
16-
17-
18-class TermInventory(BaseModel):
19-    chunk_id: str
20-    terms: list[TermOccurrence]
21-
22-
23:class CanonicalTerm(BaseModel):
24-    name: str
25-    aliases: list[str]
26-    centrality: str          # core | supporting | incidental
27-    occurrence_count: int
28-    in_structural_zones: bool
29-    grounded: bool = True    # False if SS title search returned no confident results
30-    term_type: Literal["academic", "tool_library", "concept"] = "academic"
31-
32-
33-class TermKindItem(BaseModel):
34-    term: str
35-    term_type: Literal["academic", "tool_library", "concept"]
46:class DomainProposal(BaseModel):
47-    id: str                  # snake_case identifier
48-    label: str               # human-readable name
49-    definition: str
50-    example_terms: list[str]
51-    openalex_concept_id: str | None = None
52-    openalex_level: int | None = None    # L0–L5; L0-L2 = lumper, L3-L5 = splitter
53-
54-
55:class TaxonomyProposal(BaseModel):
56-    strategy: str            # "lumper" or "splitter"
57-    domains: list[DomainProposal]
58-    rationale: str
59-
60-
61-class TaxonomyExplanation(BaseModel):
62-    body: str                # plain-text explanation for the user
63-
64-
65-class TermClassification(BaseModel):
```
</details>

**[38] tool→Read:** `/Users/cyan/ccr/know-expand/know_expand/centrality.py` 

<details><summary>[39] tool result (1190 chars)</summary>

```
1	from know_expand.config import CentralityConfig
2	
3	_DEFAULT = CentralityConfig()
4	
5	BOILERPLATE: frozenset[str] = frozenset({
6	    "introduction", "methodology", "conclusion", "figure", "table",
7	    "theorem", "proof", "lemma", "et al", "appendix", "section",
8	    "related work", "abstract", "references", "background", "overview",
9	})
10	
11	
12	def compute_centrality(
13	    term: str,
14	    occurrences: int,
15	    chunk_count: int,
16	    structural_zones: set[str],
17	    cfg: CentralityConfig = _DEFAULT,
18	) -> str | None:
19	    """Return 'core', 'supporting', 'incidental', or None (boilerplate)."""
20	    name = term.lower()
21	    if any(bp in name for bp in BOILERPLATE):
22	        return None
23	
24	    in_structure = name in {z.lower() for z in structural_zones}
25	    effective = occurrences * (cfg.structural_weight if in_structure else 1.0)
26	
27	    if effective >= cfg.core_effective_threshold:
28	        return "core"
29	    if chunk_count > 0 and occurrences / chunk_count >= cfg.core_chunk_fraction:
30	        return "core"
31	    if effective >= cfg.supporting_effective_threshold:
32	        return "supporting"
33	    return "incidental"
34	
```
</details>

**[40] tool→Bash:** `grep -rn "no_bibliography_fetch\|no-bibliography-fetch" /Users/cyan/ccr/know-expand/know_expand | grep -v pyc; echo ---;` — Check flag wiring, pipeline, and tests

<details><summary>[41] tool result (464 chars)</summary>

```
/Users/cyan/ccr/know-expand/know_expand/cli.py:87:    parser.add_argument("--no-bibliography-fetch", action="store_true")
---
9:    s1_assess,
34:        await s1_assess.run(state, cfg)
---
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
```
</details>

**[43] tool→Read:** `/Users/cyan/ccr/know-expand/know_expand/pipeline.py` 

<details><summary>[44] tool result (5220 chars)</summary>

```
1	from pathlib import Path
2	
3	from langgraph.graph import StateGraph, END
4	
5	from know_expand.config import Config
6	from know_expand.state import PipelineState, emit, load_pipeline_json
7	from know_expand.stages import (
8	    s0_ingest,
9	    s1_assess,
10	    s2_extract,
11	    s3_graph,
12	    s4_audit,
13	    s5_research,
14	    s6_align,
15	    s7_synthesize,
16	    s8_verify,
17	    s9_prereq,
18	    s10_assemble,
19	)
20	
21	
22	def build_graph(
23	    cfg: Config,
24	    auto_taxonomy: bool,
25	    no_pdf: bool = False,
26	) -> StateGraph:
27	    graph = StateGraph(PipelineState)
28	
29	    async def node_ingest(state: PipelineState) -> PipelineState:
30	        await s0_ingest.run(state, cfg)
31	        return state
32	
33	    async def node_assess(state: PipelineState) -> PipelineState:
34	        await s1_assess.run(state, cfg)
35	        return state
36	
37	    async def node_extract(state: PipelineState) -> PipelineState:
38	        await s2_extract.run(state, cfg)
39	        return state
40	
41	    async def node_graph(state: PipelineState) -> PipelineState:
42	        await s3_graph.run(state, cfg, auto_taxonomy=auto_taxonomy)
43	        return state
44	
45	    async def node_audit(state: PipelineState) -> PipelineState:
46	        await s4_audit.run(state, cfg)
47	        return state
48	
49	    async def node_research(state: PipelineState) -> PipelineState:
50	        await s5_research.run(state, cfg)
51	        return state
52	
53	    async def node_align(state: PipelineState) -> PipelineState:
54	        await s6_align.run(state, cfg)
55	        return state
56	
57	    async def node_synthesize(state: PipelineState) -> PipelineState:
58	        await s7_synthesize.run(state, cfg)
59	        return state
60	
61	    async def node_verify(state: PipelineState) -> PipelineState:
62	        await s8_verify.run(state, cfg)
63	        return state
64	
65	    async def node_prereq(state: PipelineState) -> PipelineState:
66	        await s9_prereq.run...[truncated]
```
</details>

**[45] tool→Bash:** `sed -n 100,200p /Users/cyan/ccr/know-expand/know_expand/cli.py` — Read CLI input handling section

<details><summary>[46] tool result (2433 chars)</summary>

```
    parser.add_argument("--concurrency", type=int, default=None,
                        help="Override config concurrency.default")

    # Run identity
    parser.add_argument(
        "--run-id", type=str, default=None,
        help="Reuse an existing run ID (for --resume); a new UUID is generated otherwise",
    )

    args = parser.parse_args(raw_argv)
    if _input_file and not args.input:
        args.input = _input_file

    # Handle subcommands first
    if args.command == "tail":
        from know_expand.observe import cmd_tail
        cmd_tail(args.run_id, args.runs_dir)
        return

    if args.command == "serve":
        from know_expand.observe import cmd_serve
        cmd_serve(args.runs_dir, port=args.port)
        return

    runs_dir: Path = args.runs_dir
    run_id = args.run_id or new_run_id()
    run_dir = runs_dir / run_id
    state_dir = run_dir / "state"
    output_dir = run_dir / "output"
    log_dir = run_dir / "logs"
    state_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)

    if args.status:
        # If no explicit run_id, find the most recently modified run with a pipeline.json
        if not args.run_id:
            state_dir = _find_latest_state_dir(runs_dir) or state_dir
        setup_logging(run_id, log_dir=log_dir)
        print(f"logs → {log_dir}", flush=True)
        _status(state_dir)
        return

    if not args.input:
        parser.error("input is required unless --status is passed")

    setup_logging(run_id, log_dir=log_dir)
    _install_sigterm_handler()
    print(f"run  {run_id}", flush=True)
    print(f"logs {log_dir}", flush=True)

    if args.input == "-":
        content = sys.stdin.read()
        tmp = state_dir / "stdin_input.txt"
        tmp.write_text(content)
        input_path = str(tmp)
    else:
        input_path = args.input

    cfg = load_config()

    if args.concurrency is not None:
        cfg.concurrency.defa...[truncated]
```
</details>

**[48] tool→Bash:** `grep -n "args\." /Users/cyan/ccr/know-expand/know_expand/cli.py; echo ----; grep -rn "user_profile" /Users/cyan/ccr/know` — Verify which CLI args are actually used

<details><summary>[49] tool result (874 chars)</summary>

```
110:    if _input_file and not args.input:
111:        args.input = _input_file
114:    if args.command == "tail":
116:        cmd_tail(args.run_id, args.runs_dir)
119:    if args.command == "serve":
121:        cmd_serve(args.runs_dir, port=args.port)
124:    runs_dir: Path = args.runs_dir
125:    run_id = args.run_id or new_run_id()
134:    if args.status:
136:        if not args.run_id:
143:    if not args.input:
151:    if args.input == "-":
157:        input_path = args.input
161:    if args.concurrency is not None:
162:        cfg.concurrency.default = args.concurrency
170:        depth=args.depth,
172:        auto_taxonomy=args.auto_taxonomy,
173:        primary_model=args.primary_model,
174:        resume_stage=int(args.resume) if args.resume else None,
----
----
/Users/cyan/ccr/know-expand/know_expand/pipeline.py:110:    resume_stage: int | None = None,
```
</details>

**[51] tool→Bash:** `grep -n "dependencies" -A 30 /Users/cyan/ccr/know-expand/pyproject.toml | head -40; echo ----; grep -rn "gap_analysis\|g` — Check dependencies, S5 gap consumption, test coverage of S0-S2

<details><summary>[52] tool result (1786 chars)</summary>

```
9:dependencies = [
10-    # Document parsing
11-    "docling>=2.0.0",
12-    # NLP / extraction
13-    "spacy>=3.7.0",
14-    "keybert",
15-    "sentence-transformers>=3.0.0",   # BGE-M3
16-    "numpy>=1.26.0",
17-    # LLM routing + structured outputs
18-    "litellm>=1.40.0",
19-    "anthropic>=0.30.0",
20-    "instructor>=1.3.0",
21-    "pydantic>=2.7.0",
22-    # Pipeline orchestration + agent loops
23-    "langgraph>=0.2.0",
24-    # Observability (serve renders markdown in browser)
25-    "markdown>=3.6",
26-    # Rate limiting
27-    "aiolimiter>=1.1.0",
28-    # HTTP
29-    "httpx>=0.27.0",
30-    # PDF integrity check
31-    "pypdf>=4.2.0",
32-    # .env loading
33-    "python-dotenv>=1.0.0",
34-]
35-
36-[project.scripts]
37-know-expand = "know_expand.cli:main"
38-
39:[project.optional-dependencies]
40-dev = [
41-    "pytest>=8.0.0",
42-    "pytest-asyncio>=0.23.0",
43-    "pytest-httpx>=0.30.0",
44-]
45-
46-[tool.pytest.ini_options]
47-asyncio_mode = "auto"
48-testpaths = ["tests"]
----
/Users/cyan/ccr/know-expand/know_expand/stages/s5_research.py:94:{gap_analysis}
/Users/cyan/ccr/know-expand/know_expand/stages/s5_research.py:296:{gap_analysis}
/Users/cyan/ccr/know-expand/know_expand/stages/s5_research.py:315:9. Gap analysis coverage: real gaps identified in gap_analysis that are not \
/Users/cyan/ccr/know-expand/know_expand/stages/s5_research.py:551:    """Extract gap findings relevant to this domain from gap_analysis.md."""
/Users/cyan/ccr/know-expand/know_expand/stages/s5_research.py:745:            gap_analysis=gap_context,
/Users/cyan/ccr/know-expand/know_expand/stages/s5_research.py:839:            gap_analysis=gap_context,
/Users/cyan/ccr/know-expand/know_expand/stages/s5_research.py:961:    gap_md_path = audit_dir / "gap_analysis.md"
----
```
</details>

**[55] tool→Bash:** `grep -rn "critique_\|round" /Users/cyan/ccr/know-expand/know_expand/stages/s4_audit.py | head -5; echo ----; grep -n "co` — Confirm no round persistence in S4 and conflicts never populated in S3

<details><summary>[56] tool result (591 chars)</summary>

```
/Users/cyan/ccr/know-expand/know_expand/stages/s4_audit.py:97:    rounds: int = 3,
/Users/cyan/ccr/know-expand/know_expand/stages/s4_audit.py:103:    termination_reason = "max_rounds"
/Users/cyan/ccr/know-expand/know_expand/stages/s4_audit.py:108:    for rnd in range(1, rounds + 1):
/Users/cyan/ccr/know-expand/know_expand/stages/s4_audit.py:109:        emit({"event": "gap_finder_start", "domain_id": domain_id, "round": rnd, "term_count": len(graph_terms), "anchor_count": len(anchors)})
/Users/cyan/ccr/know-expand/know_expand/stages/s4_audit.py:123:            "round": rnd,
----
----
1
```
</details>

## Structured Output
```json
{
  "issues": [
    {
      "layer": "objective",
      "severity": "critical",
      "title": "Mandatory human interview (S1) with broken bypass makes the default pipeline human-gated \u2014 incompatible with unattended/multi-user operation",
      "detail": "Design principle 11 (README:649-655) and the CLI reference (README:188) promise S1 is skippable via --user-profile and 'skipped by default in autonomous mode' (README:88). In code, --user-profile is parsed (cli.py:85) but never read anywhere \u2014 grep over cli.py/pipeline.py shows args.user_profile is never referenced, and s1_assess.run() has no profile-injection path. So S1 always runs. In headless mode (use_web_ipc = not sys.stdin.isatty(), s1_assess.py:685), each question blocks on a 300s file-poll timeout (_WEB_TIMEOUT, s1_assess.py:38, 369-403); a cron/CI/batch run stalls ~40-75 minutes and then synthesizes a profile from empty answers. Similarly S3's web review blocks 600s then SILENTLY defaults to lumper (s3_graph.py:54, 78-101) \u2014 README:388 instead claims the pipeline 'emits pipeline_paused and exits cleanly', which it never does. For a product run concurrently by many users, the human gates must be an async pause/persist/notify/resume job model, not in-process polling that fabricates defaults on timeout.",
      "file_refs": [
        "/Users/cyan/ccr/know-expand/know_expand/stages/s1_assess.py:38",
        "/Users/cyan/ccr/know-expand/know_expand/stages/s1_assess.py:369-403",
        "/Users/cyan/ccr/know-expand/know_expand/stages/s1_assess.py:685",
        "/Users/cyan/ccr/know-expand/know_expand/cli.py:85",
        "/Users/cyan/ccr/know-expand/know_expand/stages/s3_graph.py:54-104"
      ]
    },
    {
      "layer": "architecture",
      "severity": "critical",
      "title": "No cross-run caching and no cross-process rate-limit coordination \u2014 external API layer cannot scale past one run per host",
      "detail": "All external data (Wikipedia, PyPI, arXiv, OpenAlex, Semantic Scholar) is cached only inside a run's state dir (e.g. audit/sources/sources_{domain_id}.json, s4_audit.py:226-258); re-running the same paper refetches everything. README:869 claims DiskCache ('SQLite-backed for deterministic API calls') is part of the stack \u2014 it is not in pyproject.toml dependencies and is never imported. Rate limiters are module-level globals (_ss_limiter in bibliography.py:65-90, _OPENALEX_LIMITER in sources.py:40-47) scoped to one process; each pipeline run is a separate subprocess (spawned by observe.py), so N concurrent runs each get their own 'serialized' limiter and collectively send N\u00d7 the per-IP budget to Semantic Scholar \u2014 guaranteed 429 cascades exactly when concurrency matters. SS unauthenticated limits are per-IP, so a multi-tenant deployment shares one budget across all users with no queue, broker, or shared cache to mediate it. This is the binding throughput ceiling for the SaaS goal, and nothing in the architecture addresses it.",
      "file_refs": [
        "/Users/cyan/ccr/know-expand/know_expand/bibliography.py:62-90",
        "/Users/cyan/ccr/know-expand/know_expand/sources.py:40-47",
        "/Users/cyan/ccr/know-expand/know_expand/stages/s4_audit.py:226-258",
        "/Users/cyan/ccr/know-expand/pyproject.toml:9-34"
      ]
    },
    {
      "layer": "execution",
      "severity": "high",
      "title": "SS rate limit configured for an authenticated key but never conditioned on key presence",
      "detail": "config.yaml:21-23 sets semantic_scholar max_rate 1/time_period 1 with the comment 'authenticated key: 1 req/sec'. _get_ss_limiter (bibliography.py:77-90) reads this unconditionally; nothing checks whether SS_API_KEY is actually set (it is optional per README:305). Unauthenticated SS allows roughly 100 req/5 min (~0.33 req/s; CLAUDE.md says 0.1 req/s) \u2014 so the default configuration overruns the unauthenticated limit 3-10\u00d7, and the elaborate cooldown circuit breaker (_ss_cooldown_until, 30s\u2192120s backoff) exists largely to absorb a self-inflicted overrun. Additionally _SS_API_KEY is read once at module import (bibliography.py:62), so a key injected later (e.g. via the web UI session env) inside the same process is ignored. The limiter should derive its rate from key presence and re-read the key per request.",
      "file_refs": [
        "/Users/cyan/ccr/know-expand/know_expand/bibliography.py:62",
        "/Users/cyan/ccr/know-expand/know_expand/bibliography.py:77-90",
        "/Users/cyan/ccr/know-expand/config.yaml:20-29"
      ]
    },
    {
      "layer": "architecture",
      "severity": "high",
      "title": "S4 adversarial gap loop is a stateless re-roll: rounds share no memory, termination condition can essentially never fire, last round wins",
      "detail": "_run_gap_loop (s4_audit.py:91-198) runs finder\u2192defender\u2192rebuttal up to 3 rounds, but each round's _GAP_FINDER_PROMPT is byte-identical (only domain/terms/anchors \u2014 no prior verdicts, no prior gaps). The stall check compares set equality of free-text gap_description strings across independent LLM generations (s4_audit.py:176-185), which will almost never match \u2014 so the loop runs max rounds (9 LLM calls/domain at deep) and then returns only the LAST round's rebuttal_result, discarding rounds 1-2 entirely. The extra rounds add cost without information gain. README:636 also claims 'Every round is persisted to disk before scoring \u2014 a crash at round 2 of 3 restarts from round 1' \u2014 no round artifacts are written anywhere in s4_audit.py; gap results exist only in memory until gap_analysis.md is written at stage end.",
      "file_refs": [
        "/Users/cyan/ccr/know-expand/know_expand/stages/s4_audit.py:91-198",
        "/Users/cyan/ccr/know-expand/know_expand/stages/s4_audit.py:176-185"
      ]
    },
    {
      "layer": "planning",
      "severity": "high",
      "title": "Phantom CLI/feature surface: six advertised flags do nothing; README documents subsystems that don't exist",
      "detail": "Parsed but never used: --user-profile (cli.py:85), --stage (cli.py:84), --no-bibliography-fetch (cli.py:87, zero other references \u2014 README:190 advertises 'air-gapped mode'), --no-pdf (cli.py:76, never forwarded to run_pipeline at cli.py:165-175 whose no_pdf defaults False), --human (cli.py:75). --resume is forwarded to resume_stage (pipeline.py:110) which is never read \u2014 resume works only via stage_is_complete markers, so '--resume 4' is a no-op token. --renderer typst (README:182, 857) does not exist in cli.py at all. README further claims a Crossref tier (README:137, 525, 848 \u2014 'crossref' appears nowhere in know_expand/), DiskCache, and map_a/map_b dual map outputs (README:954-957 \u2014 S2 writes a single map_{chunk_id}.json, s2_extract.py:62). For a product, every one of these is a user-facing lie; the README is a roadmap masquerading as documentation and needs a hard reconciliation pass before any external user touches it.",
      "file_refs": [
        "/Users/cyan/ccr/know-expand/know_expand/cli.py:75-87",
        "/Users/cyan/ccr/know-expand/know_expand/cli.py:165-175",
        "/Users/cyan/ccr/know-expand/know_expand/pipeline.py:101-111",
        "/Users/cyan/ccr/know-expand/README.md:160-201"
      ]
    },
    {
      "layer": "execution",
      "severity": "high",
      "title": "URL ingestion writes raw HTML straight into the pipeline; no content extraction, content-type check, size cap, or SSRF guard",
      "detail": "s0_ingest.py:49-69: for http(s) inputs, resp.text (raw HTML including <script>, nav, boilerplate) is written to source.txt and word-split into 800-word chunks; spaCy noun-chunks the first 50k chars of markup as 'structural zones'. Every downstream stage (term extraction, interview concepts, taxonomy) then operates on tag soup. There is no content-type check (a URL to a PDF is decoded as text), no readability/Docling pass, no response size limit (memory exhaustion on large responses), no User-Ag
```

<details><summary>[60] tool result (39 chars)</summary>

```
Structured output provided successfully
```
</details>

**[61] assistant:** Audit complete. The structured findings have been submitted, covering 17 issues across all four layers (2 critical, 6 high), 7 production gaps, and 6 strengths for the S0–S4 subsystem.
