# Agent: verify__geminicli_subprocess_provider:_orphaned_processes_on_timeout,_unpinned
**ID:** `agent-ab6debb208096096e`  **Turns:** 39

## Prompt
```
You are an adversarial verifier. A code auditor claims the following about the repo at /Users/cyan/ccr/know-expand:

TITLE: geminicli subprocess provider: orphaned processes on timeout, unpinned npx supply chain, prompt in argv
LAYER: execution
SEVERITY: high
CLAIM: Three distinct problems in _do_geminicli_call/_do_geminicli_tool_call: (1) `asyncio.wait_for(proc.communicate(), timeout=600)` (base.py:138-141, 221-224) never kills the child on TimeoutError — the except block (base.py:175-185) only emits and re-raises, so every timeout leaks a live npx→node→gemini-cli process tree; at 8 concurrent calls this accumulates orphans for hours. (2) `npx --yes @google/gemini-cli` (base.py:131-134) resolves and potentially auto-installs the latest npm version on the operator's machine per call — unpinned supply chain plus 1-3s npx resolution latency on every single LLM call on the default routing path. (3) The full prompt — which contains the user's document content — is passed as an argv parameter (`-p full_prompt`), visible to every local user via `ps`, and bounded by ARG_MAX. For the provider that fronts every role, this is not production-robust.
FILES: /Users/cyan/ccr/know-expand/know_expand/agents/base.py:131-141, /Users/cyan/ccr/know-expand/know_expand/agents/base.py:175-185, /Users/cyan/ccr/know-expand/know_expand/agents/base.py:214-224

Read the actual code and try to REFUTE this claim. Check: does the cited code actually behave as claimed? Is the severity inflated? Is there mitigating code elsewhere (search the repo)? If the claim is materially correct, confirm it. Default to refuted if the evidence is ambiguous.
```

**[4] tool→Bash:** `grep -n "geminicli\|wait_for\|communicate\|npx\|kill\|terminate" /Users/cyan/ccr/know-expand/know_expand/agents/base.py ` — Find geminicli subprocess handling lines in base.py

<details><summary>[5] tool result (2781 chars)</summary>

```
21:# Gemini CLI provider  (prefix "geminicli/")
23:# Uses `npx @google/gemini-cli` with cached OAuth — no API key required.
27:_GEMINICLI_PREFIX = "geminicli/"
34:def _is_geminicli(model: str) -> bool:
42:def _geminicli_model_name(model: str) -> str:
103:async def _do_geminicli_call(
110:    cli_model = _geminicli_model_name(model)
125:        "model": f"geminicli/{cli_model}",
132:            "npx", "--yes", "@google/gemini-cli",
138:        stdout_b, stderr_b = await asyncio.wait_for(
139:            proc.communicate(),
140:            timeout=cfg.timeouts.get("geminicli_timeout_s", 120),
147:            is_quota, retry_s = _parse_geminicli_stderr(err_text)
150:                # (from models.yaml, e.g. "geminicli/gemini-3.5-flash") so
152:                # here would add the wrong key ("geminicli/gemini-3.1-pro-preview")
156:                raise RuntimeError(f"geminicli quota exhausted{retry_info}")
166:            "model": f"geminicli/{cli_model}",
180:            "model": f"geminicli/{cli_model}",
188:async def _do_geminicli_tool_call(
196:    cli_model = _geminicli_model_name(model)
209:    reported_model = f"geminicli/{cli_model}"
215:            "npx", "--yes", "@google/gemini-cli",
221:        stdout_b, stderr_b = await asyncio.wait_for(
222:            proc.communicate(),
223:            timeout=cfg.timeouts.get("geminicli_timeout_s", 120),
228:            is_quota, retry_s = _parse_geminicli_stderr(err_text)
232:                raise RuntimeError(f"geminicli quota exhausted{retry_info}")
379:def _parse_geminicli_stderr(stderr_text: str) -> tuple[bool, float | None]:
384:    waiting within a run will fix it.  We detect it and mark geminicli
423:def _geminicli_is_available() -> bool:
424:    """True if npx exists and the Gemini CLI OAuth credentials are cached."""
426:    if not shutil.which("npx"):
435:    if _is_geminicli(model):
436:        return _geminicli_is_available()
474:        return f"geminicli/{_GEMINICLI_CLI_MODEL}" if _is_geminicli(m) else m
...[truncated]
```
</details>

**[6] tool→Read:** `/Users/cyan/ccr/know-expand/know_expand/agents/base.py` 

<details><summary>[7] tool result (6682 chars)</summary>

```
100	    return text  # fall back to raw text; Pydantic will error cleanly
101	
102	
103	async def _do_geminicli_call(
104	    model: str,
105	    messages: list[dict],
106	    schema: type[T],
107	    cfg: Config,
108	    role: str = "?",
109	) -> "T":
110	    cli_model = _geminicli_model_name(model)
111	    schema_json = json.dumps(schema.model_json_schema(), indent=2)
112	    base_prompt = _messages_to_prompt(messages)
113	    full_prompt = (
114	        f"{base_prompt}\n\n"
115	        f"---\n"
116	        f"Respond with a single JSON object that strictly matches this JSON Schema "
117	        f"(no extra keys, no markdown, no explanation — raw JSON only):\n"
118	        f"{schema_json}"
119	    )
120	
121	    t0 = time.monotonic()
122	    emit({
123	        "event": "llm_call_start",
124	        "role": role,
125	        "model": f"geminicli/{cli_model}",
126	        "schema": schema.__name__,
127	        "msg_chars": len(full_prompt),
128	    })
129	
130	    try:
131	        proc = await asyncio.create_subprocess_exec(
132	            "npx", "--yes", "@google/gemini-cli",
133	            "-m", cli_model,
134	            "-p", full_prompt,
135	            stdout=asyncio.subprocess.PIPE,
136	            stderr=asyncio.subprocess.PIPE,
137	        )
138	        stdout_b, stderr_b = await asyncio.wait_for(
139	            proc.communicate(),
140	            timeout=cfg.timeouts.get("geminicli_timeout_s", 120),
141	        )
142	        raw = stdout_b.decode(errors="replace").strip()
143	        # The CLI exits non-zero even on success when MCP warnings are present.
144	        # Treat empty stdout as the real failure signal; non-empty stdout = success.
145	        if not raw:
146	            err_text = stderr_b.decode(errors="replace")
147	            is_quota, retry_s = _parse_geminicli_stderr(err_text)
148	            if is_quota:
149	                # Terminal daily-cap exhausted — mark the original model string
150	                # (from models.yaml, e.g. "gem...[truncated]
```
</details>

**[10] tool→Read:** `/Users/cyan/ccr/know-expand/know_expand/agents/base.py` 

<details><summary>[11] tool result (4747 chars)</summary>

```
770	                        self._fail_counts.pop(model, None)
771	                        await asyncio.sleep(int(retry_after))
772	                        continue
773	                    # Headerless failure: increment counter and backoff before skipping.
774	                    count = self._fail_counts.get(model, 0) + 1
775	                    self._fail_counts[model] = count
776	                    if count < 3:
777	                        sleep_s = _BURST_BACKOFF[min(count - 1, len(_BURST_BACKOFF) - 1)]
778	                        emit({
779	                            "event": "model_burst_retry",
780	                            "role": self.role,
781	                            "model": model,
782	                            "attempt": count,
783	                            "sleep_s": sleep_s,
784	                        })
785	                        await asyncio.sleep(sleep_s)
786	                        continue
787	                    # 3 consecutive failures — permanently skip this model.
788	                    self._fail_counts.pop(model, None)
789	                    async with self._lock:
790	                        self._skip.add(model)
791	                        next_model = self._next_model()
792	                    emit({
793	                        "event": "model_quota_switch",
794	                        "role": self.role,
795	                        "exhausted_model": model,
796	                        "next_model": next_model,
797	                        "reason": str(transient_err),
798	                    })
799	                else:
800	                    # geminicli errors are always terminal for this run (quota,
801	                    # session expired, empty output) — skip and try next model.
802	                    if _is_geminicli(model):
803	                        async with self._lock:
804	                            self._skip.add(model)
805	                            next_model = self._next_model()
806	                   ...[truncated]
```
</details>

**[12] tool→Bash:** `grep -rn "kill\|terminate\|SIGKILL\|psutil\|process_group\|killpg" /Users/cyan/ccr/know-expand/know_expand/ | grep -v ".` — Search repo for process kill/cleanup code

<details><summary>[13] tool result (682 chars)</summary>

```
/Users/cyan/ccr/know-expand/know_expand/observe.py:1558:  bar.placeholder = d.killed ? '■ run stopped' : '■ stop sent (no process found)';
/Users/cyan/ccr/know-expand/know_expand/observe.py:2015:        os.kill(pid, 0)   # signal 0 = probe only
/Users/cyan/ccr/know-expand/know_expand/observe.py:2698:                killed = False
/Users/cyan/ccr/know-expand/know_expand/observe.py:2705:                        os.killpg(os.getpgid(pid), signal.SIGTERM)
/Users/cyan/ccr/know-expand/know_expand/observe.py:2706:                        killed = True
/Users/cyan/ccr/know-expand/know_expand/observe.py:2713:                payload = json.dumps({"ok": True, "killed": killed}).encode()
```
</details>

**[14] tool→Bash:** `grep -rn "@google/gemini-cli" /Users/cyan/ccr/know-expand/ --include="*.py" --include="*.yaml" --include="*.md" | grep -` — Find all gemini-cli invocations and version pins

<details><summary>[15] tool result (812 chars)</summary>

```
/Users/cyan/ccr/know-expand/CLAUDE.md:78:Spawns `npx @google/gemini-cli -m gemini-3.1-pro-preview -p <prompt>` as a subprocess. No API key — uses cached OAuth from `~/.gemini/oauth_creds.json`.
/Users/cyan/ccr/know-expand/README.md:699:Spawns `npx @google/gemini-cli` as a subprocess. No API key — uses cached OAuth from `~/.gemini/oauth_creds.json`. Timeout: `cfg.timeouts.get("geminicli_timeout_s", 120)` — do not use `getattr()`, `Config.timeouts` is a `dict`.
/Users/cyan/ccr/know-expand/know_expand/agents/base.py:23:# Uses `npx @google/gemini-cli` with cached OAuth — no API key required.
/Users/cyan/ccr/know-expand/know_expand/agents/base.py:132:            "npx", "--yes", "@google/gemini-cli",
/Users/cyan/ccr/know-expand/know_expand/agents/base.py:215:            "npx", "--yes", "@google/gemini-cli",
```
</details>

**[16] tool→Bash:** `cat /Users/cyan/ccr/know-expand/models.yaml` — Show model routing order per role

<details><summary>[17] tool result (3252 chars)</summary>

```
roles:
  # Slot 0: geminicli/gemini-3.5-flash  — free OAuth CLI (daily cap, ~1500 req/day)
  # Slot 1: gemini/gemini-2.5-flash     — $0.30/$2.50 per M — cheapest quality paid fallback
  # Slot 2: gemini/gemini-3.5-flash     — $1.50/$9.00 per M — quality backstop if 2.5-flash quota hits
  # No Chinese API providers. Local = llamacpp/glm4 on foiba (192.168.1.117:8080).
  #
  # WARNING: gemini/gemini-3.5-flash (Google I/O 2026) ≠ gemini/gemini-2.5-flash.
  # 3.5-flash costs 5× more. Keep it as a backstop, not the primary paid fallback.
  #
  # Effective cost order (slot 0 exhausted):
  #   gemini/gemini-2.5-flash  $0.30/$2.50  ← use this first
  #   mistral-small-latest     $0.15/$0.60  256K ctx, MoE, European
  #   groq/llama-3.1-8b        $0.05/$0.08  bulk floor (extractor/classifier only)
  #   groq/llama-3.3-70b       $0.59/$0.79  free tier: ~500K tok/day aggregate cap
  #   gpt-4o-mini              $0.15/$0.60
  #   gemini/gemini-3.5-flash  $1.50/$9.00  ← quality backstop only
  #   claude-sonnet-4-6        $3.00/$15.00
  #   claude-opus-4-7          $5.00/$25.00 ← researcher/synthesizer only
  #
  # Expected cost per standard run (geminicli fully exhausted, 2.5-flash in slot 1):
  #   ~$0.73/run   (vs ~$3.59 if 3.5-flash were slot 1)
  # Deep run: ~$1.60/run

  # Tool calling, multi-turn ReAct — correctness over cost, low volume.
  agent:
    - "geminicli/gemini-3.5-flash"
    - "gemini/gemini-2.5-flash"
    - "gemini/gemini-3.5-flash"
    - "groq/llama-3.3-70b-versatile"
    - "claude-sonnet-4-6"
    - "llamacpp/glm4"

  # Long output, quality IS the product — no cheap shortcuts.
  researcher:
    - "geminicli/gemini-3.5-flash"
    - "gemini/gemini-2.5-flash"
    - "mistral/mistral-small-latest"
    - "gemini/gemini-3.5-flash"
    - "groq/llama-3.3-70b-versatile"
    - "claude-opus-4-7"
    - "claude-sonnet-4-6"
    - "gpt-4o"
    - "llamacpp/glm4"

  synthesizer:
    - "geminicli/gemini-3.5-flash"
    - "gemini/gemini-2.5-flash"
    - "mistral/mistral-small-lat...[truncated]
```
</details>

**[20] tool→Read:** `/Users/cyan/ccr/know-expand/know_expand/agents/base.py` 

<details><summary>[21] tool result (7408 chars)</summary>

```
585	    return _wrapper
586	
587	
588	async def _do_call(
589	    model: str,
590	    messages: list[dict],
591	    schema: type[T],
592	    cfg: Config,
593	    role: str = "?",
594	) -> T:
595	    if _is_geminicli(model):
596	        return await _do_geminicli_call(model, messages, schema, cfg, role)
597	    if _is_llamacpp(model):
598	        return await _do_llamacpp_call(model, messages, schema, cfg, role)
599	
600	    import instructor
601	    client = instructor.from_litellm(_make_sanitized_completion(), mode=_instructor_mode(model))
602	    t0 = time.monotonic()
603	    emit({
604	        "event": "llm_call_start",
605	        "role": role,
606	        "model": model,
607	        "schema": schema.__name__,
608	        "msg_chars": _msg_chars(messages),
609	    })
610	    try:
611	        result, completion = await client.chat.completions.create_with_completion(
612	            model=model,
613	            messages=messages,
614	            response_model=schema,
615	        )
616	        elapsed = time.monotonic() - t0
617	        usage = getattr(completion, "usage", None)
618	        tok_out = getattr(usage, "completion_tokens", None)
619	        tok_in = getattr(usage, "prompt_tokens", None)
620	        tok_s = round(tok_out / elapsed, 1) if tok_out and elapsed > 0 else None
621	        cache_write = getattr(usage, "cache_creation_input_tokens", None)
622	        cache_read = getattr(usage, "cache_read_input_tokens", None)
623	        emit({
624	            "event": "llm_call_done",
625	            "role": role,
626	            "model": model,
627	            "schema": schema.__name__,
628	            "elapsed_s": round(elapsed, 2),
629	            "tok_in": tok_in,
630	            "tok_out": tok_out,
631	            "tok_s": tok_s,
632	            "cache_write_tok": cache_write,
633	            "cache_read_tok": cache_read,
634	        })
635	        return result
636	    except Exception as exc:
637	        elapsed = time.monotonic() - t0
638	        emi...[truncated]
```
</details>

**[22] tool→Bash:** `grep -n "start_new_session\|setsid\|preexec_fn\|Popen" /Users/cyan/ccr/know-expand/know_expand/observe.py` — Check pipeline spawn process-group setup

<details><summary>[23] tool result (229 chars)</summary>

```
2521:    proc = subprocess.Popen(
2523:        start_new_session=True,
2527:        preexec_fn=lambda: _sig.signal(_sig.SIGCHLD, _sig.SIG_DFL),
2584:    # Pipeline subprocesses are spawned with start_new_session=True so they live
```
</details>

**[26] tool→Bash:** `grep -rn "QuotaAwareRouter(" /Users/cyan/ccr/know-expand/know_expand/ | grep -v ".pyc"; echo ---; grep -rn "get_router\|` — Find QuotaAwareRouter instantiation sites

<details><summary>[27] tool result (168 chars)</summary>

```
/Users/cyan/ccr/know-expand/know_expand/agents/base.py:960:    return QuotaAwareRouter(role=role, models=cfg.models[role], cfg=cfg, thinking_budget=thinking_budget)
---
```
</details>

**[28] tool→Bash:** `grep -rn "proc.kill\|proc.terminate\|\.kill()\|\.terminate()" /Users/cyan/ccr/know-expand/know_expand/ --include="*.py";` — Confirm absence of subprocess kill calls in package

<details><summary>[29] tool result (9 chars)</summary>

```
---exit 1
```
</details>

**[30] tool→Read:** `/Users/cyan/ccr/know-expand/know_expand/agents/base.py` 

<details><summary>[31] tool result (567 chars)</summary>

```
950	                            "skipped_model": model,
951	                            "next_model": next_model,
952	                            "reason": str(e)[:200],
953	                        })
954	                        continue
955	                    raise
956	        raise RuntimeError(f"All models exhausted for agent role: {self.role}")
957	
958	
959	def make_router(role: str, cfg: Config, thinking_budget: int = 0) -> QuotaAwareRouter:
960	    return QuotaAwareRouter(role=role, models=cfg.models[role], cfg=cfg, thinking_budget=thinking_budget)
961	
```
</details>

**[32] tool→Bash:** `grep -rn "make_router\|get_router" /Users/cyan/ccr/know-expand/know_expand/stages/*.py /Users/cyan/ccr/know-expand/know_` — Find router creation call sites in stages

<details><summary>[33] tool result (2259 chars)</summary>

```
/Users/cyan/ccr/know-expand/know_expand/stages/s1_assess.py:24:from know_expand.agents.base import make_router
/Users/cyan/ccr/know-expand/know_expand/stages/s1_assess.py:704:        router = make_router("agent", cfg)
/Users/cyan/ccr/know-expand/know_expand/stages/s5_research.py:12:from know_expand.agents.base import make_router
/Users/cyan/ccr/know-expand/know_expand/stages/s5_research.py:965:    router = make_router("researcher", cfg)
/Users/cyan/ccr/know-expand/know_expand/stages/s5_research.py:966:    critic_router = make_router("researcher", cfg, thinking_budget=cfg.critic_thinking.budget_tokens)
/Users/cyan/ccr/know-expand/know_expand/stages/s4_audit.py:11:from know_expand.agents.base import make_router
/Users/cyan/ccr/know-expand/know_expand/stages/s4_audit.py:400:    router = make_router("critic", cfg)
/Users/cyan/ccr/know-expand/know_expand/stages/s3_graph.py:11:from know_expand.agents.base import make_router
/Users/cyan/ccr/know-expand/know_expand/stages/s3_graph.py:283:    router = make_router("classifier", cfg)
/Users/cyan/ccr/know-expand/know_expand/stages/s9_prereq.py:15:from know_expand.agents.base import make_router
/Users/cyan/ccr/know-expand/know_expand/stages/s9_prereq.py:191:    router = make_router("agent", cfg)
/Users/cyan/ccr/know-expand/know_expand/stages/s2_extract.py:14:from know_expand.agents.base import make_router
/Users/cyan/ccr/know-expand/know_expand/stages/s2_extract.py:384:    router = make_router("extractor", cfg)
/Users/cyan/ccr/know-expand/know_expand/stages/s2_extract.py:463:    classifier_router = make_router("classifier", cfg)
/Users/cyan/ccr/know-expand/know_expand/stages/s8_verify.py:8:from know_expand.agents.base import make_router
/Users/cyan/ccr/know-expand/know_expand/stages/s8_verify.py:252:    classifier_router = make_router("classifier", cfg)
/Users/cyan/ccr/know-expand/know_expand/stages/s7_synthesize.py:9:from know_expand.agents.base import make_router
/Users/cyan/ccr/know-expand/know_expand/stages/s7_synthesize.py:...[truncated]
```
</details>

## Structured Output
```json
{
  "verdict": "partially_confirmed",
  "reasoning": "All three cited code behaviors are real and the line numbers are exact. (1) /Users/cyan/ccr/know-expand/know_expand/agents/base.py:138-141 and 221-224 use `asyncio.wait_for(proc.communicate(), timeout=cfg.timeouts.get(\"geminicli_timeout_s\", 120))` with no kill on timeout; the except blocks (175-185, and the tool-call equivalent) only emit an event and re-raise, and a repo-wide grep finds zero `.kill()`/`.terminate()` calls in the package \u2014 so a timed-out npx\u2192node\u2192gemini-cli child is indeed left running. (2) base.py:132 and 215 spawn `npx --yes @google/gemini-cli` with no version pin anywhere in the repo, and models.yaml places geminicli/gemini-3.5-flash at slot 0 for all seven roles, so it is the default routing path for every call. (3) The full prompt, built from message content including document text, is passed as `-p full_prompt` in argv (base.py:134, 217) \u2014 ps-visible and ARG_MAX-bounded; gemini-cli's stdin mode is not used. However, the impact framing of problem (1) is overstated: the auditor missed QuotaAwareRouter.call()'s else-branch at base.py:799-813, where ANY geminicli exception (including TimeoutError) permanently adds the model to that router's `_skip` set (\"geminicli errors are always terminal for this run\"). Routers are created per stage (make_router per stage entry), so timeouts cannot recur for hours \u2014 leaks are bounded to roughly one in-flight concurrent batch per stage before geminicli is abandoned. Additionally, gemini-cli `-p` is one-shot (leaked children usually run to completion and exit rather than persisting indefinitely), and the web UI stop path uses os.killpg (observe.py:2705), which sweeps same-process-group children on manual stop. The missing proc.kill() is still a genuine defect worth fixing, the unpinned npx and argv-prompt findings stand as written, but \"accumulates orphans for hours at 8 concurrent calls\" does not match the actual fallback behavior, which slightly inflates the headline severity.",
  "correction": "The code defects are real, but the orphan-accumulation scenario is bounded, not open-ended: on the first geminicli TimeoutError, QuotaAwareRouter permanently skips geminicli for that router instance (base.py:799-813, per-stage routers), so at most one concurrent batch of calls (~8) can leak per stage per run \u2014 not continuous accumulation for hours. Leaked gemini-cli one-shot processes also typically self-terminate after completing their single request, and os.killpg on manual stop (observe.py:2705) cleans same-group survivors. Severity is closer to medium-high: the unpinned `npx --yes @google/gemini-cli` supply chain and prompt-in-argv exposure are accurately described and unmitigated."
}
```

<details><summary>[37] tool result (39 chars)</summary>

```
Structured output provided successfully
```
</details>

**[38] assistant:** Verification complete.
