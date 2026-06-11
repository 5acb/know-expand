# Agent: verify__single-tenant_by_construction:_process-env_keys,_personal_oauth_creds,
**ID:** `agent-abcd3087f7e943130`  **Turns:** 43

## Prompt
```
You are an adversarial verifier. A code auditor claims the following about the repo at /Users/cyan/ccr/know-expand:

TITLE: Single-tenant by construction: process-env keys, personal OAuth creds, module-global mutable state
LAYER: architecture
SEVERITY: critical
CLAIM: For a product 'many users run concurrently': (1) credentials are process-wide env vars only (_probe_one, base.py:433-454) — no per-request/tenant key injection; (2) the slot-0 model for every role is geminicli, which authenticates via the operator's personal ~/.gemini/oauth_creds.json (base.py:423-430) — a single individual free-tier OAuth identity cannot back multiple users, likely violates the CLI's individual-use terms, and on Google's free tier user content may be used for training — i.e., customers' uploaded documents are routed by default through a free tier with no data-privacy posture; (3) router state is module-global (_PROBED_UNAVAILABLE base.py:373, lazy global semaphores base.py:500-520), so any future move to a multi-run server process shares quota/skip state across tenants. Today each run is its own subprocess which masks this, but nothing in the layer is designed for the stated deployment model.
FILES: /Users/cyan/ccr/know-expand/know_expand/agents/base.py:373, /Users/cyan/ccr/know-expand/know_expand/agents/base.py:423-430, /Users/cyan/ccr/know-expand/know_expand/agents/base.py:500-520

Read the actual code and try to REFUTE this claim. Check: does the cited code actually behave as claimed? Is the severity inflated? Is there mitigating code elsewhere (search the repo)? If the claim is materially correct, confirm it. Default to refuted if the evidence is ambiguous.
```

**[4] tool→Bash:** `wc -l /Users/cyan/ccr/know-expand/know_expand/agents/base.py && ls /Users/cyan/ccr/know-expand` — Check file length and repo layout

<details><summary>[5] tool result (175 chars)</summary>

```
     960 /Users/cyan/ccr/know-expand/know_expand/agents/base.py
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
```
</details>

**[6] tool→Read:** `/Users/cyan/ccr/know-expand/know_expand/agents/base.py` 

<details><summary>[7] tool result (8687 chars)</summary>

```
350	            "schema": schema.__name__,
351	            "elapsed_s": round(elapsed, 2),
352	            "tok_in": tok_in,
353	            "tok_out": tok_out,
354	            "tok_s": tok_s,
355	        })
356	        return result
357	    except Exception as exc:
358	        elapsed = time.monotonic() - t0
359	        emit({
360	            "event": "llm_call_error",
361	            "role": role,
362	            "model": model,
363	            "schema": schema.__name__,
364	            "elapsed_s": round(elapsed, 2),
365	            "error": str(exc)[:200],
366	        })
367	        raise
368	
369	
370	# Populated once by probe_models() at pipeline startup.
371	# Also updated at runtime when a terminal quota error is detected so that ALL
372	# routers (not just the one that saw the error) stop trying the model immediately.
373	_PROBED_UNAVAILABLE: set[str] = set()
374	
375	# Exponential backoff delays (seconds) for consecutive headerless failures.
376	_BURST_BACKOFF = [2, 4, 8]
377	
378	
379	def _parse_geminicli_stderr(stderr_text: str) -> tuple[bool, float | None]:
380	    """Check stderr for a terminal quota error.
381	
382	    Returns (is_quota_error, retry_after_seconds_or_None).
383	    TerminalQuotaError means the free-tier daily cap is hit — no amount of
384	    waiting within a run will fix it.  We detect it and mark geminicli
385	    globally unavailable so every subsequent _next_model() call skips it.
386	    """
387	    if "TerminalQuotaError" not in stderr_text and "exhausted your capacity" not in stderr_text:
388	        return False, None
389	    m = re.search(r"retryDelayMs[:\s]+([0-9.]+)", stderr_text)
390	    retry_s = float(m.group(1)) / 1000.0 if m else None
391	    return True, retry_s
392	
393	
394	def _find_in_chain(exc: BaseException, *types) -> BaseException | None:
395	    """Walk __cause__ / __context__ chain; return first match for any of types."""
396	    seen = set()
397	    current: BaseException | None = exc
398	    while current i...[truncated]
```
</details>

**[9] assistant:** The cited lines match so far. Now let me verify the "slot-0 geminicli for every role" claim and the "many users run concurrently" premise.

**[10] tool→Read:** `/Users/cyan/ccr/know-expand/models.yaml` 

<details><summary>[11] tool result (3523 chars)</summary>

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

**[12] tool→Bash:** `grep -n -i "concurrent\|multi-user\|many users\|tenant\|multi-tenant" /Users/cyan/ccr/know-expand/README.md /Users/cyan/` — Search for multi-user/concurrency deployment claims in docs

<details><summary>[13] tool result (1305 chars)</summary>

```
/Users/cyan/ccr/know-expand/README.md:438:**External API rate limiting:** 8 concurrent domain fetches hit Semantic Scholar (100 req/5 min unauthenticated), Crossref, and OpenAlex simultaneously. `aiolimiter` (async token bucket) serializes at the API call level without blocking domain-level parallelism.
/Users/cyan/ccr/know-expand/README.md:671:| Concurrent 429s from fan-out | Rate limit misread as exhaustion | Per-provider semaphore prevents the fan-out |
/Users/cyan/ccr/know-expand/README.md:715:Free-tier providers (Groq, Mistral) get `asyncio.Semaphore(3)`. All other models share the global semaphore (`cfg.concurrency.default = 8`). Cloud APIs have concurrent request limits too — 16 simultaneous Opus requests will hit Anthropic's ceiling and generate 429s that the `QuotaAwareRouter` will misread as quota exhaustion without a semaphore.
/Users/cyan/ccr/know-expand/README.md:915:- **Applying semaphore only to local inference:** cloud APIs have concurrent request limits too. 16 simultaneous Opus calls will hit Anthropic's ceiling and generate 429s that drain the fallback list.
/Users/cyan/ccr/know-expand/README.md:916:- **Fan-out parallel inference against local Ollama without a semaphore:** concurrent requests against a single-GPU instance spike memory and trigger thermal throttling.
```
</details>

**[16] tool→Bash:** `grep -rn -i "many users\|users run\|product\|deployment\|single.user\|personal tool" /Users/cyan/ccr/know-expand/README.` — Search README for product/deployment statements

<details><summary>[17] tool result (452 chars)</summary>

```
/Users/cyan/ccr/know-expand/README.md:756:## Deployment
/Users/cyan/ccr/know-expand/README.md:831:- **LangSmith** (multi-run production traces) — native LangGraph integration activated by `LANGCHAIN_API_KEY` + `LANGCHAIN_TRACING_V2=true`. No code changes required.
/Users/cyan/ccr/know-expand/README.md:895:The following patterns are explicitly excluded from this codebase. Each is a documented footgun from production experience or prior art analysis.
```
</details>

**[18] tool→Bash:** `grep -rn "api_key\|API_KEY\|os.environ" /Users/cyan/ccr/know-expand/know_expand/agents/base.py | head -30` — Find how API keys are passed in base.py

<details><summary>[19] tool result (956 chars)</summary>

```
/Users/cyan/ccr/know-expand/know_expand/agents/base.py:337:            api_key="not-needed",
/Users/cyan/ccr/know-expand/know_expand/agents/base.py:418:    if root and any(kw in str(root).lower() for kw in ("credential", "api_key", "missing")):
/Users/cyan/ccr/know-expand/know_expand/agents/base.py:438:        return bool(os.environ.get("ANTHROPIC_API_KEY"))
/Users/cyan/ccr/know-expand/know_expand/agents/base.py:440:        return bool(os.environ.get("OPENAI_API_KEY"))
/Users/cyan/ccr/know-expand/know_expand/agents/base.py:442:        return bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))
/Users/cyan/ccr/know-expand/know_expand/agents/base.py:444:        return bool(os.environ.get("GROQ_API_KEY"))
/Users/cyan/ccr/know-expand/know_expand/agents/base.py:446:        return bool(os.environ.get("MISTRAL_API_KEY"))
/Users/cyan/ccr/know-expand/know_expand/agents/base.py:860:                            api_key="not-needed",
```
</details>

**[21] tool→Bash:** `sed -n 750,840p /Users/cyan/ccr/know-expand/README.md` — Read README deployment section

<details><summary>[22] tool result (2978 chars)</summary>

```
Before re-running any unit, partial output files are deleted — not overwritten. This prevents corrupted partial writes from a previous crash from being merged with new output.

Each domain task in S5 is wrapped with `asyncio.timeout(2700)`. A domain agent stuck in API backoff emits a clean `domain_timeout` event, allowing the rest of the group to finish so the stage can be resumed.

---

## Deployment

### Local run (no Docker)

```bash
# Install dependencies
uv sync

# Set API keys
cp .env.example .env && $EDITOR .env

# Run on a paper
uv run know-expand paper.pdf --depth standard

# Monitor in browser
uv run know-expand serve
```

### Docker Compose

```yaml
services:
  app:
    build: .
    volumes:
      - ./runs:/app/runs
      - ./input:/app/input
    environment:
      - ANTHROPIC_API_KEY
      - OPENAI_API_KEY
    command: ["know-expand", "/app/input/source.pdf", "--depth", "standard"]

  ollama:
    image: ollama/ollama:latest
    profiles: ["local"]
    ports: ["11434:11434"]
    volumes: [ollama_data:/root/.ollama]
    deploy:
      resources:
        reservations:
          devices: [{driver: nvidia, capabilities: [gpu], count: all}]

volumes:
  ollama_data:
```

```bash
# Cloud LLMs
ANTHROPIC_API_KEY=sk-... docker compose up

# Fully local (no API keys)
docker compose --profile local up
docker compose exec ollama ollama pull qwen2.5:14b
```

### Fully local mode

With `--profile local`, no API keys are required. Semantic Scholar, Crossref, and OpenAlex calls still require internet for bibliography fetch.

**Recommended local models by role:**

| Role | Recommended | Minimum |
|------|-------------|---------|
| Researcher / Synthesizer | `qwen2.5:14b` | `llama3.2:3b` |
| Extractor / Classifier / Critic | `llama3.2:3b` | `llama3.2:1b` |

Expected quality at local depth: roughly `survey` regardless of `--depth` flag. Structure, citations, and adversarial audit are all intact; narrative depth and mathematical derivations are shallower.

Concurrency: global ...[truncated]
```
</details>

**[23] tool→Bash:** `grep -n -i "users\|audience\|who.*for\|product" /Users/cyan/ccr/know-expand/README.md | head -25` — Find audience/users statements in README

<details><summary>[24] tool result (320 chars)</summary>

```
831:- **LangSmith** (multi-run production traces) — native LangGraph integration activated by `LANGCHAIN_API_KEY` + `LANGCHAIN_TRACING_V2=true`. No code changes required.
895:The following patterns are explicitly excluded from this codebase. Each is a documented footgun from production experience or prior art analysis.
```
</details>

**[27] tool→Bash:** `grep -rn "many users" . --include="*.md" --include="*.py" --include="*.yaml" --include="*.toml" 2>/dev/null; git log --a` — Search repo and git history for 'many users' phrase

<details><summary>[28] tool result (718 chars)</summary>

```
9bb2eac Bump aiohttp from 3.13.5 to 3.14.0
d27b0d9 Insert gemini-2.5-flash as cheap paid fallback; fix 5x pricing error
fd18d6e Fix geminicli PROBED_UNAVAILABLE key mismatch; fix README CLI/models accuracy
3df1514 README restructure, CLAUDE.md alignment fixes, bug fixes, and 182 tests
af0ed6b Replace impl plan + vision doc with single know-expand README
80668d8 Rename project doc-expand → know-expand; fix stage name alignment
e207c95 Merge initial GitHub stub (LICENSE, README.md, .gitignore)
e30e679 Initial commit
efc5c5d Add prompt caching, extended thinking critic, OpenAlex fallback, citation classifier, and S8 mismatch detection
cd705cc Add S4 anchor-neighbor expansion, S0 parse guard, and align vision doc
```
</details>

**[30] tool→Bash:** `git grep -il "many users" $(git rev-list --all) 2>/dev/null | head -5; echo "---"; git grep -i "concurrently" $(git rev-` — Search all git revisions for multi-user phrasing

<details><summary>[31] tool result (285 chars)</summary>

```
---
1257614df3afa8d22a8f11bfc34b9faf127320d4:TUI_UX.md:**Non-blocking.** Pipeline stages that don't require user input never pause. Interactive stages (0.5 assess, 2 graph) show the prompt immediately and block *only that stage* — other stages that could run concurrently are not held.
```
</details>

**[34] tool→Bash:** `sed -n 1,60p /Users/cyan/ccr/know-expand/README.md` — Read README intro

<details><summary>[35] tool result (2630 chars)</summary>

```
# know-expand

> Turn any technical document into a research-publication-grade knowledge expansion — automatically.

**Python 3.11+ · LangGraph · LiteLLM · Docling · asyncio**

---

## Quick start

```bash
# Install
pip install uv
uv sync

# Expand a paper
uv run know-expand path/to/paper.pdf

# Watch progress in your browser
uv run know-expand serve          # http://localhost:7842

# Resume a stopped run
uv run know-expand --status
uv run know-expand --resume 4
```

Each run writes to `runs/{run_id}/` containing `state/`, `logs/`, and `output/`.

---

## What it does

A person reads a dense technical paper. They understand the words but not the field. They cannot ask good questions, cannot evaluate the claims, cannot know what to read next, cannot build on top of it.

`know-expand` takes that document and produces a **navigable, ground-up knowledge expansion** — a structured field guide covering everything needed to understand the paper deeply, go beyond it, and do independent research.

**Not a summary. Not a chatbot answer. Not a Q&A wrapper.** A full-stack knowledge construction pipeline that identifies every substantive concept in a document, maps those concepts into a principled domain taxonomy, augments each domain from first principles to the current research frontier, synthesizes cross-domain insights invisible within any single domain, and grounds every claim in verifiable, real citations.

The reference output: a 480KB, 241-page, 81-node skill-tree document with verified citations, LaTeX mathematics, and technical diagrams — produced automatically from a single source paper.

### Cost and latency

**Cost:** a deep run is roughly 8 domains × (3 personas + 1 reconciler) × up to 3 critic rounds, plus adversarial loops in S4 and S7. ~150–300 LLM calls for the researcher/synthesizer role. At current Opus 4.7 pricing, expect **$50–150 per deep run** with Opus as primary researcher. Standard depth with Sonnet 4.6 is roughly $5–20. Monitor `state/audit/model_usa...[truncated]
```
</details>

**[36] tool→Bash:** `grep -n "_spawn_pipeline\|os.environ\|setdefault\|env=" /Users/cyan/ccr/know-expand/know_expand/observe.py | head -25` — Check how observe.py spawns pipeline and handles keys

<details><summary>[37] tool result (628 chars)</summary>

```
2445:def _spawn_pipeline(runs_dir: Path, params: dict) -> int:
2526:        env=os.environ.copy(),
2667:                    "anthropic": bool(os.environ.get("ANTHROPIC_API_KEY")),
2668:                    "openai":    bool(os.environ.get("OPENAI_API_KEY")),
2669:                    "gemini":    bool(os.environ.get("GEMINI_API_KEY")),
2670:                    "groq":      bool(os.environ.get("GROQ_API_KEY")),
2671:                    "mistral":   bool(os.environ.get("MISTRAL_API_KEY")),
2689:                pid = _spawn_pipeline(runs_dir, data)
2726:                        pipe.setdefault("stages", {}).pop(str(sid), None)
```
</details>

## Structured Output
```json
{
  "verdict": "partially_confirmed",
  "reasoning": "Every code-level observation checks out at the exact cited lines. (1) /Users/cyan/ccr/know-expand/know_expand/agents/base.py:433-454 `_probe_one` validates credentials solely via os.environ; litellm calls rely on process env (the only explicit api_key anywhere is \"not-needed\" for local llamacpp at lines 337/860); observe.py `_spawn_pipeline` propagates `env=os.environ.copy()` (line 2526) and web-UI keys live in server process env \u2014 there is genuinely no per-request/tenant key path. (2) base.py:423-430 `_geminicli_is_available` checks ~/.gemini/oauth_creds.json, and models.yaml puts geminicli/gemini-3.5-flash in slot 0 for all 7 roles (comment: \"free OAuth CLI, daily cap ~1500 req/day\"), so document content is routed through a single personal free-tier OAuth identity by default. (3) base.py:373 `_PROBED_UNAVAILABLE` is a runtime-mutated module-global set and base.py:500-520 are lazy module-global semaphores; in any single-process multi-run server these would be shared across runs. No mitigating per-tenant code exists anywhere in the repo. However, the claim's framing and severity rest on a premise the repo does not contain: the quoted phrase \"many users run concurrently\" appears nowhere in the working tree or in any commit in git history (searched all revisions). README/CLAUDE.md/pyproject consistently present a single-operator local CLI with a localhost dashboard and per-run subprocesses; there are no \"customers\" or hosted multi-tenant deployment model stated. The auditor concedes current behavior is correct (\"each run is its own subprocess\"). For the deployment model the repo actually states, process-env keys and a personal OAuth slot-0 are conventional design, not a critical defect.",
  "correction": "The three technical observations are accurate as descriptions of the code, but the \"critical\" severity and the framing are not supported: the repo nowhere states a \"many users run concurrently\" product model \u2014 that quoted premise does not exist in the codebase or its git history. know-expand is documented as a single-operator local CLI (per-run subprocesses, localhost dashboard, .env keys), for which process-wide env credentials and module-global router state are ordinary design choices. The accurate residual finding is architecture/medium at most: a forward-looking note that nothing in the credential/router layer (base.py:373, 423-454, 500-520) is tenant-aware, plus a real but single-user-scoped privacy observation that slot-0 geminicli routes document content through the operator's free-tier Google OAuth identity by default (the free tier's data-use terms are an external assertion the repo neither confirms nor denies)."
}
```

<details><summary>[41] tool result (39 chars)</summary>

```
Structured output provided successfully
```
</details>

**[42] assistant:** Verification complete.
