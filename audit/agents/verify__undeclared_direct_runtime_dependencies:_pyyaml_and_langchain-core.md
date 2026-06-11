# Agent: verify__undeclared_direct_runtime_dependencies:_pyyaml_and_langchain-core
**ID:** `agent-a6c66db0b9835cb54`  **Turns:** 39

## Prompt
```
You are an adversarial verifier. A code auditor claims the following about the repo at /Users/cyan/ccr/know-expand:

TITLE: Undeclared direct runtime dependencies: PyYAML and langchain-core
LAYER: execution
SEVERITY: high
CLAIM: know_expand/config.py:4 does `import yaml` and know_expand/agents/lc_adapter.py:17-26 imports langchain_core directly, but pyproject.toml:9-34 declares neither PyYAML nor langchain-core. Both currently resolve only transitively (via litellm/docling and langgraph respectively). A routine upstream dependency refactor would break a fresh install of this package with ImportError at startup — config loading is the first thing cli.py does. This is exactly the class of breakage a lockfile does not protect end users from, since pip consumers install from pyproject metadata, not uv.lock.
FILES: /Users/cyan/ccr/know-expand/pyproject.toml, /Users/cyan/ccr/know-expand/know_expand/config.py, /Users/cyan/ccr/know-expand/know_expand/agents/lc_adapter.py

Read the actual code and try to REFUTE this claim. Check: does the cited code actually behave as claimed? Is the severity inflated? Is there mitigating code elsewhere (search the repo)? If the claim is materially correct, confirm it. Default to refuted if the evidence is ambiguous.
```

**[4] tool→Read:** `/Users/cyan/ccr/know-expand/pyproject.toml` 

<details><summary>[5] tool result (1177 chars)</summary>

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

**[6] tool→Read:** `/Users/cyan/ccr/know-expand/know_expand/config.py` 

<details><summary>[7] tool result (941 chars)</summary>

```
1	from dataclasses import dataclass, field
2	from pathlib import Path
3	
4	import yaml
5	
6	
7	@dataclass
8	class BibliographyConfig:
9	    foundational_fraction: float = 0.65
10	    frontier_months: int = 24
11	    depth_totals: dict[str, int] = field(default_factory=lambda: {
12	        "survey": 10, "standard": 30, "deep": 50
13	    })
14	    ss_max_retries: int = 5
15	    ss_retry_initial_delay: float = 30.0
16	    ss_max_backoff: float = 120.0
17	    ss_anchors_n: int = 3
18	
19	
20	@dataclass
21	class CentralityConfig:
22	    structural_weight: float = 2.0
23	    core_effective_threshold: float = 5.0
24	    core_chunk_fraction: float = 0.3
25	    supporting_effective_threshold: float = 2.0
26	    alias_cosine_threshold: float = 0.92
27	
28	
29	@dataclass
30	class RateLimitConfig:
31	    max_rate: float
32	    time_period: float
33	
34	
35	@dataclass
36	class ConcurrencyConfig:
37	    default: int = 8
38	
39	
40	@dataclass
```
</details>

**[8] tool→Read:** `/Users/cyan/ccr/know-expand/know_expand/agents/lc_adapter.py` 

<details><summary>[9] tool result (2146 chars)</summary>

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

**[12] tool→Bash:** `grep -rn "import yaml\|langchain" /Users/cyan/ccr/know-expand/know_expand/ | grep -v ".pyc"` — Find all yaml and langchain imports in package

<details><summary>[13] tool result (838 chars)</summary>

```
/Users/cyan/ccr/know-expand/know_expand/config.py:4:import yaml
/Users/cyan/ccr/know-expand/know_expand/agents/lc_adapter.py:17:from langchain_core.language_models.chat_models import BaseChatModel
/Users/cyan/ccr/know-expand/know_expand/agents/lc_adapter.py:18:from langchain_core.messages import (
/Users/cyan/ccr/know-expand/know_expand/agents/lc_adapter.py:25:from langchain_core.messages import ToolCall as LCToolCall
/Users/cyan/ccr/know-expand/know_expand/agents/lc_adapter.py:26:from langchain_core.outputs import ChatGeneration, ChatResult
/Users/cyan/ccr/know-expand/know_expand/agents/lc_adapter.py:96:        from langchain_core.utils.function_calling import convert_to_openai_tool
/Users/cyan/ccr/know-expand/know_expand/agents/lc_adapter.py:170:    from langchain_core.messages import SystemMessage as _SM, ToolMessage as _TM
```
</details>

**[14] tool→Bash:** `grep -n "pyyaml\|PyYAML\|langchain" /Users/cyan/ccr/know-expand/uv.lock | head -40` — Check uv.lock for pyyaml and langchain entries

<details><summary>[15] tool result (9480 chars)</summary>

```
22:    { name = "pyyaml" },
615:    { name = "pyyaml" },
1033:    { name = "pyyaml" },
1338:name = "langchain-core"
1343:    { name = "langchain-protocol" },
1347:    { name = "pyyaml" },
1352:sdist = { url = "https://files.pythonhosted.org/packages/59/de/679a53472c25860837e32c0442c962fa86e95317a36460e2c9d5c91b17c2/langchain_core-1.4.0.tar.gz", hash = "sha256:1dc341eed802ed9c117c0df3923c991e5e9e226571e5725c194eeb5bd93d1a7f", size = 920260, upload-time = "2026-05-11T18:42:35.919Z" }
1354:    { url = "https://files.pythonhosted.org/packages/0f/1a/86c38c27b81913a1c6c12448cab55defb5a1097c7dc9a4cea83f55477a2d/langchain_core-1.4.0-py3-none-any.whl", hash = "sha256:23cbbdb46e38ddd1dd5247e6167e96013eae74bea4c5949c550809970a9e565c", size = 548120, upload-time = "2026-05-11T18:42:33.992Z" },
1358:name = "langchain-protocol"
1364:sdist = { url = "https://files.pythonhosted.org/packages/4f/24/9777489d6fbbee64af0c8f96d4f840239c408cf694f3394672807dafc490/langchain_protocol-0.0.15.tar.gz", hash = "sha256:9ab2d11ee73944754f10e037e717098d3a6796f0e58afa9cadda6154e7655ade", size = 5862, upload-time = "2026-05-01T22:30:04.748Z" }
1366:    { url = "https://files.pythonhosted.org/packages/1d/7a/9c97a7b9cbe4c5dc6a44cdb1545450c28f0c8ce89b9c1f0ee7fbad896263/langchain_protocol-0.0.15-py3-none-any.whl", hash = "sha256:461eb794358f83d5e42635a5797799ffec7b4702314e34edf73ac21e75d3ef79", size = 6982, upload-time = "2026-05-01T22:30:03.877Z" },
1374:    { name = "langchain-core" },
1391:    { name = "langchain-core" },
1404:    { name = "langchain-core" },
2159:    { name = "pyyaml" },
3032:name = "pyyaml"
3035:sdist = { url = "https://files.pythonhosted.org/packages/05/8e/961c0007c59b8dd7729d542c61a4d537767a59645b82a0b521206e1e25c2/pyyaml-6.0.3.tar.gz", hash = "sha256:d76623373421df22fb4cf8817020cbb7ef15c725b9d5e45f17e189bfc384190f", size = 130960, upload-time = "2025-09-25T21:33:16.546Z" }
3037:    { url = "https://files.pythonhosted.org/packages/6d/16/a95b6757765b7b031c9374925bb718d55e0a9ba8a1b...[truncated]
```
</details>

**[16] tool→Bash:** `grep -rn "lc_adapter\|import yaml" /Users/cyan/ccr/know-expand/know_expand/cli.py /Users/cyan/ccr/know-expand/know_expan` — Find where lc_adapter and yaml are imported

<details><summary>[17] tool result (31 chars)</summary>

```
(Bash completed with no output)
```
</details>

**[20] tool→Bash:** `grep -rn "lc_adapter" /Users/cyan/ccr/know-expand --include="*.py" --include="*.md" --include="*.toml" | grep -v ".venv"` — Find all references to lc_adapter repo-wide

<details><summary>[21] tool result (31 chars)</summary>

```
(Bash completed with no output)
```
</details>

**[22] tool→Bash:** `python3 - <<'EOF'
import re
text = open('/Users/cyan/ccr/know-expand/uv.lock').read()
blocks = text.split('[[package]]')` — List which lockfile packages depend on pyyaml and langchain-core

<details><summary>[23] tool result (264 chars)</summary>

```
pyyaml <- accelerate
pyyaml <- docling-core
pyyaml <- huggingface-hub
pyyaml <- langchain-core
pyyaml <- omegaconf
pyyaml <- rapidocr
pyyaml <- transformers
---
langchain-core <- langgraph
langchain-core <- langgraph-checkpoint
langchain-core <- langgraph-prebuilt
```
</details>

**[26] tool→Bash:** `grep -rn "lc_adapter\|BaseChatModel\|RouterChatModel\|langchain" /Users/cyan/ccr/know-expand --include="*.py" -l | grep ` — Find files referencing lc_adapter or langchain symbols; show agents __init__

<details><summary>[27] tool result (369 chars)</summary>

```
/Users/cyan/ccr/know-expand/know_expand/agents/lc_adapter.py
---agents/__init__---
---tests dir---
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
```
</details>

**[28] tool→Bash:** `head -60 /Users/cyan/ccr/know-expand/know_expand/cli.py` — Show top of cli.py import/startup path

<details><summary>[29] tool result (1896 chars)</summary>

```
import argparse
import asyncio
import signal
import sys
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

from know_expand.config import load_config
from know_expand.state import emit, load_pipeline_json, new_run_id, setup_logging


def _install_sigterm_handler() -> None:
    """Emit run_stopped and exit cleanly when SIGTERM arrives (e.g. from web UI stop)."""
    def _handler(signum, frame):
        emit({"event": "run_stopped", "reason": "sigterm"})
        sys.exit(0)
    signal.signal(signal.SIGTERM, _handler)


def _status(state_dir: Path) -> None:
    data = load_pipeline_json(state_dir)
    emit({"event": "pipeline_status", **data})


def _find_latest_state_dir(runs_dir: Path) -> Path | None:
    """Scan runs_dir for the most recently modified subdir that has state/pipeline.json."""
    if not runs_dir.exists():
        return None
    candidates = sorted(
        (d for d in runs_dir.iterdir() if d.is_dir()),
        key=lambda d: d.stat().st_mtime,
        reverse=True,
    )
    for run_dir in candidates:
        sd = run_dir / "state"
        if (sd / "pipeline.json").exists():
            return sd
    return None


_SUBCOMMANDS = {"tail", "serve"}


def main() -> None:
    # Pre-scan argv: if the first positional arg is not a known subcommand
    # (and not a flag), it's the pipeline input file. Extract it before
    # argparse consumes it as an invalid subcommand choice.
    raw_argv = sys.argv[1:]
    _input_file: str | None = None
    if raw_argv and not raw_argv[0].startswith("-") and raw_argv[0] not in _SUBCOMMANDS:
        _input_file = raw_argv[0]
        raw_argv = raw_argv[1:]

    parser = argparse.ArgumentParser(
        prog="know-expand",
        description="Expand a technical document into a research-grade knowledge document.",
    )
    parser.add_argument("input", nargs="?", help="File path, URL, or - for stdin")
```
</details>

**[31] assistant:** One last check — make sure there's no other dependency declaration mechanism (requirements files, tool.uv sources) and confirm `yaml` is actually exercised in `load_config`.

**[32] tool→Bash:** `ls /Users/cyan/ccr/know-expand/ | head -30; grep -rn "pyyaml\|PyYAML" /Users/cyan/ccr/know-expand --include="*.toml" --i` — Check for other dependency declarations and yaml usage in config.py

<details><summary>[33] tool result (226 chars)</summary>

```
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
120:    raw = yaml.safe_load(config_path.read_text())
121:    models_raw = yaml.safe_load(models_path.read_text())
```
</details>

## Structured Output
```json
{
  "verdict": "partially_confirmed",
  "reasoning": "The raw facts are accurate: pyproject.toml (deps lines 9-34) declares neither pyyaml nor langchain-core; know_expand/config.py:4 does a bare `import yaml` (used via yaml.safe_load at lines 120-121) and cli.py:10 imports know_expand.config at module level, so PyYAML truly is an undeclared direct dependency on the startup path; lc_adapter.py:17-26 does import langchain_core at module top. No other dependency declaration exists (no requirements.txt, no tool.uv deps). However, the impact analysis is substantially overstated. (1) lc_adapter.py is dead code: a repo-wide grep finds zero imports/references to it in cli.py, pipeline.py, any stage, the empty agents/__init__.py, or any test file \u2014 its langchain_core imports are never executed, so they cannot cause ImportError at startup or runtime. (2) langchain-core is required by langgraph's own package metadata (confirmed in uv.lock: langgraph, langgraph-checkpoint, langgraph-prebuilt all list it), and langgraph IS declared, so pip consumers get langchain-core regardless. (3) The pyyaml chain is misattributed: litellm does NOT depend on pyyaml; it arrives via seven independent transitive providers (docling-core, huggingface-hub, transformers, omegaconf, rapidocr, accelerate, langchain-core), so the 'routine upstream refactor breaks fresh install' scenario requires all of these to drop PyYAML at once \u2014 implausible. The packaging hygiene defect is real and the pyyaml fix is warranted, but severity is low/medium, not high.",
  "correction": "PyYAML is genuinely an undeclared direct dependency on the startup path and should be added to pyproject.toml, but it resolves through seven independent transitive providers (docling-core, huggingface-hub, transformers, omegaconf, rapidocr, accelerate, langchain-core), not litellm, making the breakage scenario remote. The langchain-core half of the finding has no runtime impact at all: lc_adapter.py is never imported anywhere in the codebase (dead code), and langchain-core is pulled in by the declared langgraph dependency's own metadata anyway. Correct severity is low-to-medium packaging hygiene, not high."
}
```

<details><summary>[37] tool result (39 chars)</summary>

```
Structured output provided successfully
```
</details>

**[38] assistant:** Verification complete.
