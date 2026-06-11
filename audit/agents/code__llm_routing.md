# Agent: agent__unknown_05
**ID:** `agent-ab0a955f2d8ea2ea6`  **Turns:** 49

## Prompt
```
You are auditing the codebase at /Users/cyan/ccr/know-expand (a Python multi-agent CLI called know-expand that turns a technical document into a long "knowledge expansion" PDF via a LangGraph pipeline of 11 stages, with a QuotaAwareRouter over many LLM providers and a single-file web dashboard). Read the actual files with Read/Grep/Bash. Be a rigorous senior staff engineer: every issue must cite specific files/lines and be classified by layer — "objective" (is the goal itself right / coherent), "architecture" (structural design decisions), "planning" (roadmap, sequencing, what's prioritized), "execution" (bugs, code quality, correctness). Judge against the goal of making this a production-grade product that many users run concurrently (SaaS or self-hosted), not a personal tool. Do NOT pad with trivia; do report real critical/high problems. Also read /Users/cyan/ccr/know-expand/CLAUDE.md and /Users/cyan/ccr/know-expand/README.md for stated intent so you can flag intent-vs-code mismatches.

Your subsystem: LLM routing — /Users/cyan/ccr/know-expand/know_expand/agents/base.py (960 lines), agents/lc_adapter.py, agents/schemas.py, models.yaml. Assess: QuotaAwareRouter design (global mutable _PROBED_UNAVAILABLE set, per-router _skip, semaphores), the geminicli subprocess provider (spawning npx per call — latency, security, robustness), instructor mode selection, cost tracking accuracy, retry/backoff correctness (race conditions under asyncio concurrency?), testability, and whether routing free-tier/OAuth-CLI models first is sane for a product whose pitch is research-grade quality. Check models.yaml ordering vs the README claim "quality IS the product — no cheap shortcuts": cheapest models are tried FIRST for the researcher role — evaluate that contradiction. Also evaluate vendor lock-in/abstraction quality of litellm+instructor+langchain mixed together.
```

**[4] tool→Read:** `/Users/cyan/ccr/know-expand/know_expand/agents/base.py` 

<details><summary>[5] tool result (40385 chars)</summary>

```
1	import asyncio
2	import json
3	import logging
4	import os
5	import re
6	import time
7	from typing import TypeVar
8	
9	import httpx
10	import litellm
11	from pydantic import BaseModel
12	
13	from know_expand.config import Config
14	from know_expand.state import emit
15	
16	_logger = logging.getLogger("know_expand.router")
17	
18	T = TypeVar("T", bound=BaseModel)
19	
20	# ---------------------------------------------------------------------------
21	# Gemini CLI provider  (prefix "geminicli/")
22	# ---------------------------------------------------------------------------
23	# Uses `npx @google/gemini-cli` with cached OAuth — no API key required.
24	# Structured output is achieved by injecting the JSON schema into the prompt
25	# and extracting the first JSON object/array from the response.
26	
27	_GEMINICLI_PREFIX = "geminicli/"
28	# Model passed to the CLI binary (free OAuth path).
29	_GEMINICLI_CLI_MODEL = "gemini-3.1-pro-preview"
30	
31	_LLAMACPP_PREFIX = "llamacpp/"
32	
33	
34	def _is_geminicli(model: str) -> bool:
35	    return model.startswith(_GEMINICLI_PREFIX)
36	
37	
38	def _is_llamacpp(model: str) -> bool:
39	    return model.startswith(_LLAMACPP_PREFIX)
40	
41	
42	def _geminicli_model_name(model: str) -> str:
43	    """Return the model name to pass to the CLI binary."""
44	    return _GEMINICLI_CLI_MODEL
45	
46	
47	def _messages_to_prompt(messages: list[dict]) -> str:
48	    """Flatten a messages list into a single text prompt for the CLI."""
49	    parts: list[str] = []
50	    for m in messages:
51	        role = m.get("role", "user")
52	        content = m.get("content", "")
53	        if isinstance(content, list):
54	            # multi-part content blocks
55	            content = "\n".join(
56	                block.get("text", "") for block in content
57	                if isinstance(block, dict) and block.get("type") == "text"
58	            )
59	        if role == "system":
60	            parts.append(f"[System]\n{content}")
61	        elif role =...[truncated]
```
</details>

**[6] tool→Read:** `/Users/cyan/ccr/know-expand/models.yaml` 

<details><summary>[7] tool result (3523 chars)</summary>

```
1	roles:
2	  # Slot 0: geminicli/gemini-3.5-flash  — free OAuth CLI (daily cap, ~1500 req/day)
3	  # Slot 1: gemini/gemini-2.5-flash     — $0.30/$2.50 per M — cheapest quality paid fallback
4	  # Slot 2: gemini/gemini-3.5-flash     — $1.50/$9.00 per M — quality backstop if 2.5-flash quota hits
5	  # No Chinese API providers. Local = llamacpp/glm4 on foiba (192.168.1.117:8080).
6	  #
7	  # WARNING: gemini/gemini-3.5-flash (Google I/O 2026) ≠ gemini/gemini-2.5-flash.
8	  # 3.5-flash costs 5× more. Keep it as a backstop, not the primary paid fallback.
9	  #
10	  # Effective cost order (slot 0 exhausted):
11	  #   gemini/gemini-2.5-flash  $0.30/$2.50  ← use this first
12	  #   mistral-small-latest     $0.15/$0.60  256K ctx, MoE, European
13	  #   groq/llama-3.1-8b        $0.05/$0.08  bulk floor (extractor/classifier only)
14	  #   groq/llama-3.3-70b       $0.59/$0.79  free tier: ~500K tok/day aggregate cap
15	  #   gpt-4o-mini              $0.15/$0.60
16	  #   gemini/gemini-3.5-flash  $1.50/$9.00  ← quality backstop only
17	  #   claude-sonnet-4-6        $3.00/$15.00
18	  #   claude-opus-4-7          $5.00/$25.00 ← researcher/synthesizer only
19	  #
20	  # Expected cost per standard run (geminicli fully exhausted, 2.5-flash in slot 1):
21	  #   ~$0.73/run   (vs ~$3.59 if 3.5-flash were slot 1)
22	  # Deep run: ~$1.60/run
23	
24	  # Tool calling, multi-turn ReAct — correctness over cost, low volume.
25	  agent:
26	    - "geminicli/gemini-3.5-flash"
27	    - "gemini/gemini-2.5-flash"
28	    - "gemini/gemini-3.5-flash"
29	    - "groq/llama-3.3-70b-versatile"
30	    - "claude-sonnet-4-6"
31	    - "llamacpp/glm4"
32	
33	  # Long output, quality IS the product — no cheap shortcuts.
34	  researcher:
35	    - "geminicli/gemini-3.5-flash"
36	    - "gemini/gemini-2.5-flash"
37	    - "mistral/mistral-small-latest"
38	    - "gemini/gemini-3.5-flash"
39	    - "groq/llama-3.3-70b-versatile"
40	    - "claude-opus-4-7"
41	    - "claude-sonnet-4-6"
42	    - "gpt-4o"
43	    - "llamacpp/g...[truncated]
```
</details>

**[8] tool→Read:** `/Users/cyan/ccr/know-expand/know_expand/agents/lc_adapter.py` 

<details><summary>[9] tool result (8085 chars)</summary>

```
1	"""LangChain BaseChatModel adapter around QuotaAwareRouter.
2	
3	Allows QuotaAwareRouter (litellm-backed, with model fallback) to be used
4	with langgraph's StateGraph + ToolNode ReAct pattern and any other
5	LangChain component that expects a BaseChatModel.
6	
7	Message conversion:
8	  LangChain messages → OpenAI dicts  (for litellm)
9	  litellm response dict             → LangChain AIMessage (for langgraph)
10	"""
11	
12	from __future__ import annotations
13	
14	import json
15	from typing import Any, Iterator
16	
17	from langchain_core.language_models.chat_models import BaseChatModel
18	from langchain_core.messages import (
19	    AIMessage,
20	    BaseMessage,
21	    HumanMessage,
22	    SystemMessage,
23	    ToolMessage,
24	)
25	from langchain_core.messages import ToolCall as LCToolCall
26	from langchain_core.outputs import ChatGeneration, ChatResult
27	from langgraph.graph import END, MessagesState, StateGraph
28	from langgraph.prebuilt import ToolNode
29	
30	
31	def _to_openai_dict(msg: BaseMessage) -> dict:
32	    """Convert a LangChain message to an OpenAI-format dict for litellm."""
33	    if isinstance(msg, SystemMessage):
34	        return {"role": "system", "content": msg.content or ""}
35	    if isinstance(msg, HumanMessage):
36	        return {"role": "user", "content": msg.content or ""}
37	    if isinstance(msg, ToolMessage):
38	        return {
39	            "role": "tool",
40	            "tool_call_id": msg.tool_call_id,
41	            "content": msg.content or "",
42	        }
43	    if isinstance(msg, AIMessage):
44	        d: dict = {"role": "assistant", "content": msg.content or ""}
45	        if msg.tool_calls:
46	            d["tool_calls"] = [
47	                {
48	                    "id": tc["id"],
49	                    "type": "function",
50	                    "function": {
51	                        "name": tc["name"],
52	                        "arguments": json.dumps(tc["args"]),
53	                    },
54	                }
55...[truncated]
```
</details>

**[11] tool→Read:** `/Users/cyan/ccr/know-expand/know_expand/agents/schemas.py` 

<details><summary>[12] tool result (12509 chars)</summary>

```
1	from typing import Literal
2	
3	from pydantic import BaseModel, Field
4	
5	
6	# ---------------------------------------------------------------------------
7	# Stage 1 — Extract
8	# ---------------------------------------------------------------------------
9	
10	class TermOccurrence(BaseModel):
11	    name: str
12	    aliases: list[str] = Field(default_factory=list)
13	    co_occurring_terms: list[str] = Field(default_factory=list)
14	    context_snippet: str = ""
15	    occurrence_count: int = 1
16	
17	
18	class TermInventory(BaseModel):
19	    chunk_id: str
20	    terms: list[TermOccurrence]
21	
22	
23	class CanonicalTerm(BaseModel):
24	    name: str
25	    aliases: list[str]
26	    centrality: str          # core | supporting | incidental
27	    occurrence_count: int
28	    in_structural_zones: bool
29	    grounded: bool = True    # False if SS title search returned no confident results
30	    term_type: Literal["academic", "tool_library", "concept"] = "academic"
31	
32	
33	class TermKindItem(BaseModel):
34	    term: str
35	    term_type: Literal["academic", "tool_library", "concept"]
36	
37	
38	class TermKindBatch(BaseModel):
39	    classifications: list[TermKindItem]
40	
41	
42	# ---------------------------------------------------------------------------
43	# Stage 2 — Graph
44	# ---------------------------------------------------------------------------
45	
46	class DomainProposal(BaseModel):
47	    id: str                  # snake_case identifier
48	    label: str               # human-readable name
49	    definition: str
50	    example_terms: list[str]
51	    openalex_concept_id: str | None = None
52	    openalex_level: int | None = None    # L0–L5; L0-L2 = lumper, L3-L5 = splitter
53	
54	
55	class TaxonomyProposal(BaseModel):
56	    strategy: str            # "lumper" or "splitter"
57	    domains: list[DomainProposal]
58	    rationale: str
59	
60	
61	class TaxonomyExplanation(BaseModel):
62	    body: str                # plain-text explanation for the us...[truncated]
```
</details>

**[13] tool→Read:** `/Users/cyan/ccr/know-expand/README.md` 

<details><summary>[14] tool result (59584 chars)</summary>

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

**[15] tool→Bash:** `ls /Users/cyan/ccr/know-expand/tests/ 2>/dev/null; ls /Users/cyan/ccr/know-expand/know_expand/agents/; grep -rn "cost" /` — List tests and agents dir, grep for cost tracking

<details><summary>[16] tool result (303 chars)</summary>

```
__init__.py
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
__init__.py
base.py
lc_adapter.py
schemas.py
```
</details>

**[18] tool→Bash:** `grep -rn "model_usage" /Users/cyan/ccr/know-expand --include="*.py" | head; grep -rn "cost_usd\|completion_cost\|cost" /` — Check whether model_usage.jsonl / cost tracking exists in code

<details><summary>[19] tool result (31 chars)</summary>

```
(Bash completed with no output)
```
</details>

**[20] tool→Bash:** `grep -rn "make_router\|QuotaAwareRouter(" /Users/cyan/ccr/know-expand/know_expand --include="*.py" | head -30; echo ---;` — Find router instantiation sites and --primary-model implementation

<details><summary>[21] tool result (3324 chars)</summary>

```
/Users/cyan/ccr/know-expand/know_expand/stages/s9_prereq.py:15:from know_expand.agents.base import make_router
/Users/cyan/ccr/know-expand/know_expand/stages/s9_prereq.py:191:    router = make_router("agent", cfg)
/Users/cyan/ccr/know-expand/know_expand/stages/s1_assess.py:24:from know_expand.agents.base import make_router
/Users/cyan/ccr/know-expand/know_expand/stages/s1_assess.py:704:        router = make_router("agent", cfg)
/Users/cyan/ccr/know-expand/know_expand/stages/s7_synthesize.py:9:from know_expand.agents.base import make_router
/Users/cyan/ccr/know-expand/know_expand/stages/s7_synthesize.py:304:    router = make_router("researcher", cfg)
/Users/cyan/ccr/know-expand/know_expand/stages/s2_extract.py:14:from know_expand.agents.base import make_router
/Users/cyan/ccr/know-expand/know_expand/stages/s2_extract.py:384:    router = make_router("extractor", cfg)
/Users/cyan/ccr/know-expand/know_expand/stages/s2_extract.py:463:    classifier_router = make_router("classifier", cfg)
/Users/cyan/ccr/know-expand/know_expand/stages/s3_graph.py:11:from know_expand.agents.base import make_router
/Users/cyan/ccr/know-expand/know_expand/stages/s3_graph.py:283:    router = make_router("classifier", cfg)
/Users/cyan/ccr/know-expand/know_expand/stages/s6_align.py:29:from know_expand.agents.base import make_router
/Users/cyan/ccr/know-expand/know_expand/stages/s6_align.py:358:    router = make_router("agent", cfg)
/Users/cyan/ccr/know-expand/know_expand/stages/s4_audit.py:11:from know_expand.agents.base import make_router
/Users/cyan/ccr/know-expand/know_expand/stages/s4_audit.py:400:    router = make_router("critic", cfg)
/Users/cyan/ccr/know-expand/know_expand/stages/s8_verify.py:8:from know_expand.agents.base import make_router
/Users/cyan/ccr/know-expand/know_expand/stages/s8_verify.py:252:    classifier_router = make_router("classifier", cfg)
/Users/cyan/ccr/know-expand/know_expand/stages/s5_research.py:12:from know_expand.agents.base import make_router
/Users/cyan/ccr/kn...[truncated]
```
</details>

**[22] tool→Read:** `/Users/cyan/ccr/know-expand/tests/test_base.py` 

<details><summary>[23] tool result (15182 chars)</summary>

```
1	"""Tests for know_expand.agents.base — _msg_chars and QuotaAwareRouter."""
2	
3	import asyncio
4	from unittest.mock import AsyncMock, patch
5	
6	import pytest
7	
8	from know_expand.agents.base import (
9	    QuotaAwareRouter,
10	    _msg_chars,
11	    _PROBED_UNAVAILABLE,
12	    _parse_geminicli_stderr,
13	    _find_in_chain,
14	    _instructor_mode,
15	)
16	from know_expand.config import (
17	    BibliographyConfig,
18	    CentralityConfig,
19	    ConcurrencyConfig,
20	    Config,
21	    RateLimitConfig,
22	)
23	
24	
25	# ---------------------------------------------------------------------------
26	# Helpers
27	# ---------------------------------------------------------------------------
28	
29	def _make_cfg(models: dict[str, list[str]] | None = None) -> Config:
30	    return Config(
31	        bibliography=BibliographyConfig(),
32	        centrality=CentralityConfig(),
33	        rate_limits={"semantic_scholar": RateLimitConfig(max_rate=100, time_period=300)},
34	        concurrency=ConcurrencyConfig(default=4),
35	        timeouts={"default": 30, "geminicli_timeout_s": 600},
36	        adversarial_rounds={"survey": 1, "standard": 2, "deep": 3},
37	        boilerplate_stop_list=[],
38	        models=models or {"researcher": ["claude-3-haiku"]},
39	    )
40	
41	
42	# ---------------------------------------------------------------------------
43	# _msg_chars — character counter
44	# ---------------------------------------------------------------------------
45	
46	def test_msg_chars_string_content():
47	    """Single string content — returns len of that string."""
48	    msgs = [{"role": "user", "content": "hello world"}]
49	    assert _msg_chars(msgs) == len("hello world")
50	
51	
52	def test_msg_chars_list_content():
53	    """List-block content — sums text blocks, ignores non-text blocks."""
54	    msgs = [
55	        {
56	            "role": "user",
57	            "content": [
58	                {"type": "text", "text": "foo bar"},
59	                {"type":...[truncated]
```
</details>

**[25] tool→Bash:** `grep -rn "model_consistency\|switched_from\|switch_reason" /Users/cyan/ccr/know-expand/know_expand --include="*.py" | he` — Check model_consistency claim, ConcurrencyConfig fields, config.yaml concurrency

<details><summary>[26] tool result (22 chars)</summary>

```
(eval):1: == not found
```
</details>

**[27] tool→Bash:** `grep -n "litellm\|instructor\|langchain\|langgraph" /Users/cyan/ccr/know-expand/pyproject.toml; echo ===; grep -rn "drop` — Check dependency pins and litellm usage elsewhere

<details><summary>[28] tool result (103 chars)</summary>

```
18:    "litellm>=1.40.0",
20:    "instructor>=1.3.0",
23:    "langgraph>=0.2.0",
(eval):1: == not found
```
</details>

**[29] tool→Bash:** `grep -rn "model_consistency\|switched_from\|switch_reason" /Users/cyan/ccr/know-expand/know_expand --include="*.py" | he` — Check model_consistency implementation

<details><summary>[30] tool result (31 chars)</summary>

```
(Bash completed with no output)
```
</details>

**[31] tool→Bash:** `grep -n -A 8 "class ConcurrencyConfig" /Users/cyan/ccr/know-expand/know_expand/config.py` — Show ConcurrencyConfig fields

<details><summary>[32] tool result (212 chars)</summary>

```
36:class ConcurrencyConfig:
37-    default: int = 8
38-
39-
40-@dataclass
41-class NlpModelsConfig:
42-    spacy: str = "en_core_web_sm"
43-    embedding: str = "BAAI/bge-m3"
44-    tokenizer: str = "cl100k_base"
```
</details>

**[33] tool→Bash:** `grep -n "concurrency" -A 4 /Users/cyan/ccr/know-expand/config.yaml` — Show concurrency section of config.yaml

<details><summary>[34] tool result (83 chars)</summary>

```
31:concurrency:
32-  default: 8
33-
34-timeouts:
35-  domain_research_seconds: 2700
```
</details>

**[36] tool→Bash:** `sed -n 25,60p /Users/cyan/ccr/know-expand/config.yaml` — Show config.yaml timeouts section

<details><summary>[37] tool result (615 chars)</summary>

```
    max_rate: 1
    time_period: 1
  openalex:
    max_rate: 10
    time_period: 1

concurrency:
  default: 8

timeouts:
  domain_research_seconds: 2700
  http_fetch_seconds: 30
  openalex_seconds: 10
  http_async_seconds: 30
  geminicli_timeout_s: 600   # allow 10 min for long-form persona generation

adversarial_rounds:
  survey: 1
  standard: 2
  deep: 3

nlp_models:
  spacy: "en_core_web_sm"
  embedding: "BAAI/bge-m3"
  tokenizer: "cl100k_base"

chunking:
  word_size: 800
  max_tokens: 4000
  spacy_char_limit: 50000
  llm_map_char_limit: 6000

keyword_extraction:
  top_n: 20
  ngram_min: 1
  ngram_max: 3
```
</details>

**[39] tool→Bash:** `ls /Users/cyan/ccr/know-expand/uv.lock 2>/dev/null && echo LOCK_EXISTS; grep -rn "probe_models" /Users/cyan/ccr/know-exp` — Check lockfile, probe_models call site, error class coverage

<details><summary>[40] tool result (240 chars)</summary>

```
/Users/cyan/ccr/know-expand/uv.lock
LOCK_EXISTS
/Users/cyan/ccr/know-expand/know_expand/pipeline.py:150:    from know_expand.agents.base import probe_models
/Users/cyan/ccr/know-expand/know_expand/pipeline.py:151:    await probe_models(cfg)
```
</details>

**[42] tool→Bash:** `grep -n -B2 -A4 "critic_thinking" /Users/cyan/ccr/know-expand/config.yaml /Users/cyan/ccr/know-expand/know_expand/config` — Check thinking budget config and max_tokens handling

<details><summary>[43] tool result (1578 chars)</summary>

```
/Users/cyan/ccr/know-expand/config.yaml-87-    deep: 500
/Users/cyan/ccr/know-expand/config.yaml-88-
/Users/cyan/ccr/know-expand/config.yaml:89:critic_thinking:
/Users/cyan/ccr/know-expand/config.yaml-90-  budget_tokens: 0        # 0 = disabled; 8192 = recommended for deep critic runs
/Users/cyan/ccr/know-expand/config.yaml-91-
/Users/cyan/ccr/know-expand/config.yaml-92-boilerplate_stop_list:
/Users/cyan/ccr/know-expand/config.yaml-93-  - introduction
/Users/cyan/ccr/know-expand/know_expand/config.py-111-    llamacpp: LlamaCppConfig = field(default_factory=LlamaCppConfig)
/Users/cyan/ccr/know-expand/know_expand/config.py-112-    research_context: ResearchContextConfig = field(default_factory=ResearchContextConfig)
/Users/cyan/ccr/know-expand/know_expand/config.py:113:    critic_thinking: CriticThinkingConfig = field(default_factory=CriticThinkingConfig)
/Users/cyan/ccr/know-expand/know_expand/config.py-114-
/Users/cyan/ccr/know-expand/know_expand/config.py-115-
/Users/cyan/ccr/know-expand/know_expand/config.py-116-def load_config(
/Users/cyan/ccr/know-expand/know_expand/config.py-117-    config_path: Path = Path("config.yaml"),
--
/Users/cyan/ccr/know-expand/know_expand/config.py-143-        llamacpp=_section("llamacpp", LlamaCppConfig),
/Users/cyan/ccr/know-expand/know_expand/config.py-144-        research_context=_section("research_context", ResearchContextConfig),
/Users/cyan/ccr/know-expand/know_expand/config.py:145:        critic_thinking=_section("critic_thinking", CriticThinkingConfig),
/Users/cyan/ccr/know-expand/know_expand/config.py-146-    )
```
</details>

## Structured Output
```json
{
  "issues": [
    {
      "layer": "objective",
      "severity": "critical",
      "title": "Routing order directly contradicts the product's core quality promise",
      "detail": "models.yaml:33 labels the researcher role 'Long output, quality IS the product \u2014 no cheap shortcuts', yet the list (models.yaml:34-43) is ordered cheapest-first: free OAuth geminicli, then gemini-2.5-flash ($0.30/M), mistral-small, 3.5-flash, free-tier groq 70B \u2014 claude-opus-4-7 is 6th and will essentially never run unless five cheaper providers all fail. README.md:43 tells users to 'expect $50\u2013150 per deep run with Opus as primary researcher' while models.yaml:20-22 computes the actual expected cost at ~$0.73/run because the design intent is to drain free tiers first. The pitch is 'research-publication-grade'; the router optimizes for $0. These are not reconcilable: output quality is whatever model happens to have quota left, and varies nondeterministically mid-run as fallbacks fire. There is no quality floor (e.g., 'researcher must be at least X'), no per-role minimum tier, and no record of which model wrote which domain (see cost-tracking issue).",
      "file_refs": [
        "/Users/cyan/ccr/know-expand/models.yaml:20-43",
        "/Users/cyan/ccr/know-expand/README.md:43",
        "/Users/cyan/ccr/know-expand/README.md:262"
      ]
    },
    {
      "layer": "execution",
      "severity": "critical",
      "title": "Capability failures (schema validation) never trigger fallback \u2014 they crash the call",
      "detail": "QuotaAwareRouter.call() only falls back on auth errors, RateLimitError, ServiceUnavailableError, or any geminicli error (base.py:748-814). Everything else hits the bare `raise` at base.py:814. When instructor's Pydantic validation fails (InstructorRetryException with no auth/rate-limit in the chain) \u2014 i.e., the model couldn't produce the schema \u2014 the router re-raises instead of escalating to the next, stronger model. Combined with cheapest-first ordering, the weakest models attempt the hardest schemas (DomainSummary, ReconcilerOutput) first, making schema noncompliance the single most likely failure mode, and it is the one failure the fallback chain does not handle. This inverts the router's stated purpose. Note the asymmetry: for geminicli, even a one-off JSON parse error permanently skips the provider for the router's lifetime (base.py:799-813), while for API models the same class of error kills the call outright.",
      "file_refs": [
        "/Users/cyan/ccr/know-expand/know_expand/agents/base.py:748-814"
      ]
    },
    {
      "layer": "architecture",
      "severity": "critical",
      "title": "Single-tenant by construction: process-env keys, personal OAuth creds, module-global mutable state",
      "detail": "For a product 'many users run concurrently': (1) credentials are process-wide env vars only (_probe_one, base.py:433-454) \u2014 no per-request/tenant key injection; (2) the slot-0 model for every role is geminicli, which authenticates via the operator's personal ~/.gemini/oauth_creds.json (base.py:423-430) \u2014 a single individual free-tier OAuth identity cannot back multiple users, likely violates the CLI's individual-use terms, and on Google's free tier user content may be used for training \u2014 i.e., customers' uploaded documents are routed by default through a free tier with no data-privacy posture; (3) router state is module-global (_PROBED_UNAVAILABLE base.py:373, lazy global semaphores base.py:500-520), so any future move to a multi-run server process shares quota/skip state across tenants. Today each run is its own subprocess which masks this, but nothing in the layer is designed for the stated deployment model.",
      "file_refs": [
        "/Users/cyan/ccr/know-expand/know_expand/agents/base.py:373",
        "/Users/cyan/ccr/know-expand/know_expand/agents/base.py:423-430",
        "/Users/cyan/ccr/know-expand/know_expand/agents/base.py:500-520"
      ]
    },
    {
      "layer": "execution",
      "severity": "high",
      "title": "README-documented cost tracking does not exist anywhere in the code",
      "detail": "README.md:723-729 documents `state/audit/model_usage.jsonl` with per-call `cost_usd`, `switched_from`/`switch_reason`, and a `model_consistency: mixed` flag in pipeline.json plus frontmatter recording which model wrote which domain. grep finds zero occurrences of model_usage, cost_usd, cost, model_consistency, or switched_from in know_expand/*.py. The only telemetry is tok_in/tok_out in events.jsonl (base.py:623-634), and the slot-0 provider (geminicli) emits tok_in/tok_out as None (base.py:169-171), so the default path has no usage data at all. litellm ships completion_cost() \u2014 it is simply never called. For a product whose fallback chain can silently escalate from $0 to Opus at $25/M output, having no cost accounting and no spend cap is a real operational hazard, and the README actively misleads users into believing it exists ('Monitor state/audit/model_usage.jsonl \u2014 every call is logged with cost_usd', README.md:43).",
      "file_refs": [
        "/Users/cyan/ccr/know-expand/README.md:43",
        "/Users/cyan/ccr/know-expand/README.md:723-729",
        "/Users/cyan/ccr/know-expand/know_expand/agents/base.py:163-172"
      ]
    },
    {
      "layer": "execution",
      "severity": "high",
      "title": "Error taxonomy incomplete: 500/529/timeout/connection errors crash instead of backoff/fallback",
      "detail": "The comment at base.py:727 says the failure counter is 'for headerless 429/500/529', but the handlers (base.py:750-751, 901-902) only catch litellm.RateLimitError and litellm.ServiceUnavailableError. litellm maps provider 500s and Anthropic 529 'overloaded' to InternalServerError, and network blips to APIConnectionError/Timeout \u2014 none are referenced anywhere in the package (grep confirms), so they fall through to `raise` at base.py:814 and kill the call. A transient Anthropic overload during a 3-hour deep run crashes a stage rather than backing off or switching models, directly violating README design principle 13 ('Distinguish failure modes precisely'). No explicit timeout is passed to litellm.acompletion either, so non-geminicli calls rely on library defaults.",
      "file_refs": [
        "/Users/cyan/ccr/know-expand/know_expand/agents/base.py:727",
        "/Users/cyan/ccr/know-expand/know_expand/agents/base.py:750-751",
        "/Users/cyan/ccr/know-expand/know_expand/agents/base.py:814"
      ]
    },
    {
      "layer": "execution",
      "severity": "high",
      "title": "Retry-After path: unbounded retry loop, uncapped sleep, and int() crash on HTTP-date",
      "detail": "base.py:764-772 (duplicated at 908-915): when a 429 carries Retry-After, the code pops the failure counter, sleeps `int(retry_after)`, and retries the same model forever \u2014 there is no attempt cap on this branch, so a provider that always includes Retry-After on quota-exhausted 429s (Anthropic does) is never failed over, contradicting the README's own table ('429 without Retry-After = exhaustion; switch model' assumes exhausted quotas omit the header, which is not reliably true). The sleep is uncapped (Retry-After: 86400 stalls a coroutine for a day; only S5 has an outer 2700s timeout). And Retry-After may legally be an HTTP-date string; `int()` then raises ValueError from inside the except handler, escaping call() as an unrelated crash.",
      "file_refs": [
        "/Users/cyan/ccr/know-expand/know_expand/agents/base.py:764-772",
        "/Users/cyan/ccr/know-expand/know_expand/agents/base.py:908-915"
      ]
    },
    {
      "layer": "execution",
      "severity": "high",
      "title": "geminicli subprocess provider: orphaned processes on timeout, unpinned npx supply chain, prompt in argv",
      "detail": "Three distinct problems in _do_geminicli_call/_do_geminicli_tool_call: (1) `asyncio.wait_for(proc.communicate(), timeout=600)` (base.py:138-
```

<details><summary>[47] tool result (39 chars)</summary>

```
Structured output provided successfully
```
</details>

**[48] assistant:** Audit complete. Findings returned via structured output.
