# Agent: verify__error_taxonomy_incomplete:_500_529_timeout_connection_errors_crash_ins
**ID:** `agent-af91f68e0ce85ee1d`  **Turns:** 81

## Prompt
```
You are an adversarial verifier. A code auditor claims the following about the repo at /Users/cyan/ccr/know-expand:

TITLE: Error taxonomy incomplete: 500/529/timeout/connection errors crash instead of backoff/fallback
LAYER: execution
SEVERITY: high
CLAIM: The comment at base.py:727 says the failure counter is 'for headerless 429/500/529', but the handlers (base.py:750-751, 901-902) only catch litellm.RateLimitError and litellm.ServiceUnavailableError. litellm maps provider 500s and Anthropic 529 'overloaded' to InternalServerError, and network blips to APIConnectionError/Timeout — none are referenced anywhere in the package (grep confirms), so they fall through to `raise` at base.py:814 and kill the call. A transient Anthropic overload during a 3-hour deep run crashes a stage rather than backing off or switching models, directly violating README design principle 13 ('Distinguish failure modes precisely'). No explicit timeout is passed to litellm.acompletion either, so non-geminicli calls rely on library defaults.
FILES: /Users/cyan/ccr/know-expand/know_expand/agents/base.py:727, /Users/cyan/ccr/know-expand/know_expand/agents/base.py:750-751, /Users/cyan/ccr/know-expand/know_expand/agents/base.py:814

Read the actual code and try to REFUTE this claim. Check: does the cited code actually behave as claimed? Is the severity inflated? Is there mitigating code elsewhere (search the repo)? If the claim is materially correct, confirm it. Default to refuted if the evidence is ambiguous.
```

**[4] tool→Bash:** `grep -n "RateLimitError\|ServiceUnavailableError\|InternalServerError\|APIConnectionError\|Timeout\|APIError\|consecutiv` — Grep error handling identifiers in base.py

<details><summary>[5] tool result (633 chars)</summary>

```
375:# Exponential backoff delays (seconds) for consecutive headerless failures.
726:        # Per-model consecutive-failure counter for headerless 429/500/529.
750:                rate_err = _find_in_chain(e, litellm.RateLimitError)
751:                server_err = _find_in_chain(e, litellm.ServiceUnavailableError)
787:                    # 3 consecutive failures — permanently skip this model.
901:                rate_err = _find_in_chain(e, litellm.RateLimitError)
902:                server_err = _find_in_chain(e, litellm.ServiceUnavailableError)
930:                    # 3 consecutive failures — permanently skip this model.
```
</details>

**[6] tool→Read:** `/Users/cyan/ccr/know-expand/know_expand/agents/base.py` 

<details><summary>[7] tool result (11316 chars)</summary>

```
700	            "cache_read_tok": cache_read,
701	            "thinking_budget": budget,
702	        })
703	        return result
704	    except Exception as exc:
705	        elapsed = time.monotonic() - t0
706	        emit({
707	            "event": "llm_call_error",
708	            "role": role,
709	            "model": model,
710	            "schema": schema.__name__,
711	            "elapsed_s": round(elapsed, 2),
712	            "error": str(exc)[:200],
713	        })
714	        raise
715	
716	
717	class QuotaAwareRouter:
718	    def __init__(self, role: str, models: list[str], cfg: Config, thinking_budget: int = 0) -> None:
719	        self.role = role
720	        self.models = models
721	        self.cfg = cfg
722	        self.thinking_budget = thinking_budget
723	        # Seed from probe results so unavailable models are never attempted.
724	        self._skip: set[str] = set(_PROBED_UNAVAILABLE)
725	        self._lock = asyncio.Lock()
726	        # Per-model consecutive-failure counter for headerless 429/500/529.
727	        self._fail_counts: dict[str, int] = {}
728	
729	    def _next_model(self) -> str | None:
730	        for m in self.models:
731	            if m not in self._skip and m not in _PROBED_UNAVAILABLE:
732	                return m
733	        return None
734	
735	    async def call(self, messages: list[dict], schema: type[T]) -> T:
736	        while True:
737	            model = self._next_model()
738	            if model is None:
739	                break
740	            sem = _get_semaphore(model, self.cfg)
741	            try:
742	                async with sem:
743	                    result = await _do_call_with_thinking(
744	                        model, messages, schema, self.cfg, role=self.role, budget=self.thinking_budget
745	                    )
746	                self._fail_counts.pop(model, None)
747	                return result
748	            except Exception as e:
749	                auth_err = _is_auth_error(e)
750	     ...[truncated]
```
</details>

**[10] tool→Bash:** `grep -rn "InternalServerError\|APIConnectionError\|APIError\|Timeout\b\|num_retries\|request_timeout" /Users/cyan/ccr/kn` — Search package for other error handling and retry config

<details><summary>[11] tool result (402 chars)</summary>

```
/Users/cyan/ccr/know-expand/know_expand/observe.py:1381:      setTimeout(() => { if ($('top-status').textContent === 'reattached') $('top-status').textContent = ''; }, 2000);
/Users/cyan/ccr/know-expand/know_expand/observe.py:1559:  setTimeout(() => { bar.placeholder = prev; }, 3000);
/Users/cyan/ccr/know-expand/know_expand/observe.py:1904:          if (show) setTimeout(() => otherInput.focus(), 0);
```
</details>

**[12] tool→Bash:** `grep -rn "acompletion\|litellm.retry\|litellm.num" /Users/cyan/ccr/know-expand/know_expand/agents/base.py | head -30` — Find all acompletion call sites in base.py

<details><summary>[13] tool result (607 chars)</summary>

```
/Users/cyan/ccr/know-expand/know_expand/agents/base.py:324:    client = instructor.from_litellm(litellm.acompletion)
/Users/cyan/ccr/know-expand/know_expand/agents/base.py:548:    """Wrap litellm.acompletion to repair invalid JSON escape sequences.
/Users/cyan/ccr/know-expand/know_expand/agents/base.py:574:        resp = await litellm.acompletion(*args, **kwargs)
/Users/cyan/ccr/know-expand/know_expand/agents/base.py:857:                        response = await litellm.acompletion(
/Users/cyan/ccr/know-expand/know_expand/agents/base.py:867:                        response = await litellm.acompletion(
```
</details>

**[16] tool→Bash:** `uv run python -c "
import litellm
print('litellm version:', litellm.__version__)
print('InternalServerError MRO:', [c.__` — Inspect litellm exception class hierarchy

**[17] tool→Bash:** `grep -n "_find_in_chain\|_is_auth_error" /Users/cyan/ccr/know-expand/know_expand/agents/base.py | head` — Locate _find_in_chain and _is_auth_error definitions

<details><summary>[18] tool result (871 chars)</summary>

```
Exit code 1
[92m18:24:24 - LiteLLM:WARNING[0m: common_utils.py:979 - litellm: could not pre-load bedrock-runtime response stream shape — Bedrock event-stream decoding will be unavailable. Error: No module named 'botocore'
[92m18:24:24 - LiteLLM:WARNING[0m: common_utils.py:24 - litellm: could not pre-load sagemaker-runtime response stream shape — SageMaker event-stream decoding will be unavailable. Error: No module named 'botocore'
Traceback (most recent call last):
  File "<string>", line 3, in <module>
    print('litellm version:', litellm.__version__)
                              ^^^^^^^^^^^^^^^^^^^
  File "/Users/cyan/ccr/know-expand/.venv/lib/python3.13/site-packages/litellm/__init__.py", line 2223, in __getattr__
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
AttributeError: module 'litellm' has no attribute '__version__'
```
</details>

<details><summary>[19] tool result (683 chars)</summary>

```
394:def _find_in_chain(exc: BaseException, *types) -> BaseException | None:
406:def _is_auth_error(exc: BaseException) -> BaseException | None:
409:    root = _find_in_chain(exc, litellm.AuthenticationError)
412:    root = _find_in_chain(exc, openai.AuthenticationError, openai.PermissionDeniedError)
417:    root = _find_in_chain(exc, openai.OpenAIError)
749:                auth_err = _is_auth_error(e)
750:                rate_err = _find_in_chain(e, litellm.RateLimitError)
751:                server_err = _find_in_chain(e, litellm.ServiceUnavailableError)
900:                auth_err = _is_auth_error(e)
901:                rate_err = _find_in_chain(e, litellm.RateLimitError)
```
</details>

**[20] tool→Bash:** `uv run python -c "
import litellm
print('InternalServerError MRO:', [c.__name__ for c in litellm.InternalServerError.__m` — Inspect litellm exception class hierarchy and version

**[21] tool→Read:** `/Users/cyan/ccr/know-expand/know_expand/agents/base.py` 

<details><summary>[22] tool result (737 chars)</summary>

```
InternalServerError MRO: ['InternalServerError', 'InternalServerError', 'APIStatusError', 'APIError', 'OpenAIError', 'Exception', 'BaseException', 'object']
ServiceUnavailableError MRO: ['ServiceUnavailableError', 'APIStatusError', 'APIError', 'OpenAIError', 'Exception', 'BaseException', 'object']
APIConnectionError MRO: ['APIConnectionError', 'APIConnectionError', 'APIError', 'OpenAIError', 'Exception', 'BaseException', 'object']
Timeout MRO: ['Timeout', 'APITimeoutError', 'APIConnectionError', 'APIError', 'OpenAIError', 'Exception', 'BaseException', 'object']
issubclass(InternalServerError, ServiceUnavailableError): False
issubclass(ServiceUnavailableError, InternalServerError): False
num_retries: None
litellm version: 1.85.0
```
</details>

<details><summary>[23] tool result (2765 chars)</summary>

```
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
398	    while current is not None and id(current) not in seen:
399	        seen.add(id(current))
400	        if isinstance(current, types):
401	            return current
402	        current = current.__cause__ or current.__context__
403	    return None
404	
405	
406	def _is_auth_error(exc: BaseException) -> BaseException | None:
407	    """Return the root auth exception if exc (or its chain) is a credentials failure."""
408	    import openai
409	    root = _find_in_chain(exc, litellm.AuthenticationError)
410	    if root:
411	        return root
412	    root = _find_in_chain(exc, openai.AuthenticationError, openai.PermissionD...[truncated]
```
</details>

**[26] tool→Bash:** `grep -n "529\|Overloaded\|overloaded" /Users/cyan/ccr/know-expand/.venv/lib/python3.13/site-packages/litellm/litellm_cor` — Check litellm mapping for 529 overloaded errors

<details><summary>[27] tool result (223 chars)</summary>

```
664:                elif "overloaded_error" in error_str or "Overloaded" in error_str:
736:                        or original_exception.status_code == 529
1413:                    or "The model is overloaded." in error_str
```
</details>

**[28] tool→Bash:** `grep -rn "max_retries\|timeout" /Users/cyan/ccr/know-expand/know_expand/agents/base.py | grep -v geminicli | head -20` — Check max_retries and timeout usage in base.py

<details><summary>[29] tool result (119 chars)</summary>

```
/Users/cyan/ccr/know-expand/know_expand/agents/base.py:291:        async with httpx.AsyncClient(timeout=3.0) as client:
```
</details>

**[30] tool→Bash:** `sed -n 655,680p /Users/cyan/ccr/know-expand/.venv/lib/python3.13/site-packages/litellm/litellm_core_utils/exception_mapp` — Read litellm anthropic exception mapping context

<details><summary>[31] tool result (2458 chars)</summary>

```
                or custom_llm_provider == "anthropic_text"
            ):  # one of the anthropics
                if "prompt is too long" in error_str or "prompt: length" in error_str:
                    exception_mapping_worked = True
                    raise ContextWindowExceededError(
                        message="AnthropicError - {}".format(error_str),
                        model=model,
                        llm_provider="anthropic",
                    )
                elif "overloaded_error" in error_str or "Overloaded" in error_str:
                    exception_mapping_worked = True
                    raise InternalServerError(
                        message="AnthropicError - {}".format(error_str),
                        model=model,
                        llm_provider="anthropic",
                    )
                if "Invalid API Key" in error_str:
                    exception_mapping_worked = True
                    raise AuthenticationError(
                        message="AnthropicError - {}".format(error_str),
                        model=model,
                        llm_provider="anthropic",
                    )
                if "content filtering policy" in error_str:
                    exception_mapping_worked = True
                    raise ContentPolicyViolationError(
---
                            llm_provider="anthropic",
                        )
                    elif original_exception.status_code == 429:
                        exception_mapping_worked = True
                        raise RateLimitError(
                            message=f"AnthropicException - {error_str}",
                            llm_provider="anthropic",
                            model=model,
                        )
                    elif (
                        original_exception.status_code == 500
                        or original_exception.status_code == 529
                    ):
                        exception_mapping_...[truncated]
```
</details>

**[32] tool→Bash:** `grep -rn "max_retries\|InstructorRetry" /Users/cyan/ccr/know-expand/know_expand/agents/base.py | head; echo ---; grep -r` — Check instructor retry config and stage call sites

<details><summary>[33] tool result (3724 chars)</summary>

```
---
/Users/cyan/ccr/know-expand/know_expand/stages/s1_assess.py:165:        result: ConceptList = await router.call(messages, ConceptList)
/Users/cyan/ccr/know-expand/know_expand/stages/s1_assess.py:453:        decision: InterviewDecision = await router.call(messages, InterviewDecision)
/Users/cyan/ccr/know-expand/know_expand/stages/s1_assess.py:566:    return await router.call(messages, UserProfile)
/Users/cyan/ccr/know-expand/know_expand/stages/s2_extract.py:94:    llm_result: TermInventory = await router.call(messages, TermInventory)
/Users/cyan/ccr/know-expand/know_expand/stages/s2_extract.py:261:            result: TermKindBatch = await router.call(msg, TermKindBatch)
/Users/cyan/ccr/know-expand/know_expand/stages/s3_graph.py:45:    explanation = await router.call(msg, TaxonomyExplanation)
/Users/cyan/ccr/know-expand/know_expand/stages/s3_graph.py:146:            subprocess.call([editor, str(taxonomy_path)])
/Users/cyan/ccr/know-expand/know_expand/stages/s3_graph.py:174:    merged = await router.call(merge_msg, _DomainList)
/Users/cyan/ccr/know-expand/know_expand/stages/s3_graph.py:293:            router.call(lumper_msg, TaxonomyProposal),
/Users/cyan/ccr/know-expand/know_expand/stages/s3_graph.py:294:            router.call(splitter_msg, TaxonomyProposal),
/Users/cyan/ccr/know-expand/know_expand/stages/s4_audit.py:119:        finder_result: GapAnalysisResult = await router.call(finder_msg, GapAnalysisResult)
/Users/cyan/ccr/know-expand/know_expand/stages/s4_audit.py:146:        defender_result: GapAnalysisResult = await router.call(defender_msg, GapAnalysisResult)
/Users/cyan/ccr/know-expand/know_expand/stages/s4_audit.py:166:        rebuttal_result = await router.call(rebuttal_msg, GapAnalysisResult)
/Users/cyan/ccr/know-expand/know_expand/stages/s6_align.py:277:        plan: AlignmentPlan = await router.call(messages, AlignmentPlan)
/Users/cyan/ccr/know-expand/know_expand/stages/s6_align.py:302:            plan = await router.call(messages2, AlignmentPlan)
/...[truncated]
```
</details>

**[36] tool→Read:** `/Users/cyan/ccr/know-expand/know_expand/agents/base.py` 

<details><summary>[37] tool result (2386 chars)</summary>

```
300	    total = 0
301	    for m in messages:
302	        content = m.get("content", "")
303	        if isinstance(content, list):
304	            total += sum(len(b.get("text", "")) for b in content if isinstance(b, dict))
305	        else:
306	            total += len(content)
307	    return total
308	
309	
310	async def _do_llamacpp_call(
311	    model: str,
312	    messages: list[dict],
313	    schema: type[T],
314	    cfg,
315	    role: str = "?",
316	) -> T:
317	    """Structured call to a llama-server via OpenAI-compat API (instructor + litellm)."""
318	    import instructor
319	
320	    model_name = model[len(_LLAMACPP_PREFIX):]
321	    base_url: str = cfg.llamacpp.base_url
322	    api_base = base_url.rstrip("/") + "/v1"
323	
324	    client = instructor.from_litellm(litellm.acompletion)
325	    t0 = time.monotonic()
326	    emit({
327	        "event": "llm_call_start",
328	        "role": role,
329	        "model": model,
330	        "schema": schema.__name__,
331	        "msg_chars": _msg_chars(messages),
332	    })
333	    try:
334	        result, completion = await client.chat.completions.create_with_completion(
335	            model=f"openai/{model_name}",
336	            api_base=api_base,
337	            api_key="not-needed",
338	            messages=messages,
339	            response_model=schema,
340	        )
341	        elapsed = time.monotonic() - t0
342	        usage = getattr(completion, "usage", None)
343	        tok_out = getattr(usage, "completion_tokens", None)
344	        tok_in = getattr(usage, "prompt_tokens", None)
345	        tok_s = round(tok_out / elapsed, 1) if tok_out and elapsed > 0 else None
346	        emit({
347	            "event": "llm_call_done",
348	            "role": role,
349	            "model": model,
350	            "schema": schema.__name__,
351	            "elapsed_s": round(elapsed, 2),
352	            "tok_in": tok_in,
353	            "tok_out": tok_out,
354	            "tok_s": tok_s,
355	        })
356	        ret...[truncated]
```
</details>

**[38] tool→Bash:** `grep -rn -B2 -A6 "except Exception" /Users/cyan/ccr/know-expand/know_expand/stages/s5_research.py | head -50; echo ===; ` — Check exception handling in s5_research stage

<details><summary>[39] tool result (3975 chars)</summary>

```
/Users/cyan/ccr/know-expand/know_expand/stages/s5_research.py-423-        try:
/Users/cyan/ccr/know-expand/know_expand/stages/s5_research.py-424-            return await _ss_search(query, http, cfg, limit=5)
/Users/cyan/ccr/know-expand/know_expand/stages/s5_research.py:425:        except Exception as exc:
/Users/cyan/ccr/know-expand/know_expand/stages/s5_research.py-426-            _logger.warning("s4 bib expansion: SS search failed for %r: %s", query[:60], exc)
/Users/cyan/ccr/know-expand/know_expand/stages/s5_research.py-427-            return []
/Users/cyan/ccr/know-expand/know_expand/stages/s5_research.py-428-
/Users/cyan/ccr/know-expand/know_expand/stages/s5_research.py-429-    try:
/Users/cyan/ccr/know-expand/know_expand/stages/s5_research.py-430-        async with httpx.AsyncClient(timeout=15.0) as http:
/Users/cyan/ccr/know-expand/know_expand/stages/s5_research.py-431-            # Search for missing citation IDs
--
/Users/cyan/ccr/know-expand/know_expand/stages/s5_research.py-439-                *missing_id_tasks.values(), *nc_tasks, return_exceptions=True
/Users/cyan/ccr/know-expand/know_expand/stages/s5_research.py-440-            )
/Users/cyan/ccr/know-expand/know_expand/stages/s5_research.py:441:    except Exception as exc:
/Users/cyan/ccr/know-expand/know_expand/stages/s5_research.py-442-        _logger.warning("s4 bibliography expansion: SS unavailable, skipping (%s)", exc)
/Users/cyan/ccr/know-expand/know_expand/stages/s5_research.py-443-        emit({"event": "s5_bibliography_expansion_skipped", "domain_id": domain_id, "reason": str(exc)[:120]})
/Users/cyan/ccr/know-expand/know_expand/stages/s5_research.py-444-        return bibliography, personas
/Users/cyan/ccr/know-expand/know_expand/stages/s5_research.py-445-
/Users/cyan/ccr/know-expand/know_expand/stages/s5_research.py-446-    # Process missing-ID results
/Users/cyan/ccr/know-expand/know_expand/stages/s5_research.py-447-    for mid, task in missing_id_tasks.items():
--
/Users/cyan/ccr/know-expa...[truncated]
```
</details>

**[41] tool→Read:** `/Users/cyan/ccr/know-expand/know_expand/stages/s5_research.py` 

<details><summary>[42] tool result (2899 chars)</summary>

```
765	    ))
766	    engineer_msg = _persona_msg(_ENGINEER_TASK.format(
767	        domain_label=domain_label,
768	        domain_id=domain_id,
769	        drift_guard=_PERSONA_DRIFT_GUARD.format(persona_name="ENGINEER"),
770	    ))
771	    practitioner_msg = _persona_msg(_PRACTITIONER_TASK.format(
772	        domain_label=domain_label,
773	        domain_id=domain_id,
774	        drift_guard=_PERSONA_DRIFT_GUARD.format(persona_name="PRACTITIONER"),
775	    ))
776	
777	    # Run all three personas in parallel
778	    theoretician_result, engineer_result, practitioner_result = await asyncio.gather(
779	        router.call(theoretician_msg, PersonaOutput),
780	        router.call(engineer_msg, PersonaOutput),
781	        router.call(practitioner_msg, PersonaOutput),
782	    )
783	
784	    emit({"event": "s5_personas_done", "domain_id": domain_id,
785	          "theoretician_sections": len(theoretician_result.sections),
786	          "engineer_sections": len(engineer_result.sections),
787	          "practitioner_sections": len(practitioner_result.sections)})
788	
789	    # Bibliography expansion (A-3): fill citation gaps before reconciler sees the text
790	    _expanded_bib, _expanded_personas = await _expand_bibliography(
791	        domain_id=domain_id,
792	        personas=[theoretician_result, engineer_result, practitioner_result],
793	        bibliography=bibliography,
794	        bib_path=bib_path,
795	        cfg=cfg,
796	    )
797	    bibliography = _expanded_bib
798	    theoretician_result, engineer_result, practitioner_result = _expanded_personas
799	    # Refresh derived bibliography strings after expansion
800	    bib_str = _bib_summary(bibliography)
801	    bib_ids = [b.get("id", "") for b in bibliography]
802	
803	    # Reconciler: merge all three persona outputs
804	    reconciler_msg = [{"role": "user", "content": _RECONCILER_PROMPT.format(
805	        domain_label=domain_label,
806	        domain_id=domain_id,
807	        reader_profile=reader_profile_st...[truncated]
```
</details>

**[43] tool→Bash:** `grep -n "max_retries" /Users/cyan/ccr/know-expand/know_expand/agents/base.py; echo ===; sed -n 540,640p /Users/cyan/ccr/` — Read patched acompletion wrapper and main call path

<details><summary>[44] tool result (34 chars)</summary>

```
Exit code 1
(eval):1: == not found
```
</details>

**[45] tool→Bash:** `grep -n "max_retries" /Users/cyan/ccr/know-expand/know_expand/agents/base.py` — Check for instructor max_retries in base.py

<details><summary>[46] tool result (31 chars)</summary>

```
(Bash completed with no output)
```
</details>

**[47] tool→Bash:** `sed -n '540,680p' /Users/cyan/ccr/know-expand/know_expand/agents/base.py` — Read main structured-call path in base.py

<details><summary>[48] tool result (4937 chars)</summary>

```
        # gemini/ API models route through litellm, not the native Google SDK.
        # GEMINI_TOOLS mode requires model set at client-patch time (native SDK only);
        # JSON mode works fine through litellm's openai-compat translation layer.
        return instructor.Mode.JSON
    return instructor.Mode.TOOLS


def _make_sanitized_completion():
    """Wrap litellm.acompletion to repair invalid JSON escape sequences.

    Gemini in JSON mode emits bare LaTeX backslashes (\\frac, \\mid, etc.)
    inside JSON string values. These are invalid JSON escapes and cause
    instructor's pydantic validation to fail with 'Invalid JSON: invalid escape'.
    We repair them by doubling any backslash not already part of a valid JSON
    escape sequence before instructor parses the response.
    """
    _valid_escapes = set('"\\' + '/bfnrtu')

    def _repair(text: str) -> str:
        # Replace \X where X is not a valid JSON escape char with \\X.
        out: list[str] = []
        i = 0
        while i < len(text):
            if text[i] == '\\' and i + 1 < len(text):
                nxt = text[i + 1]
                if nxt not in _valid_escapes:
                    out.append('\\\\')
                    i += 1
                    continue
            out.append(text[i])
            i += 1
        return ''.join(out)

    async def _wrapper(*args, **kwargs):
        resp = await litellm.acompletion(*args, **kwargs)
        try:
            for choice in getattr(resp, 'choices', []):
                msg = getattr(choice, 'message', None)
                if msg is not None:
                    content = getattr(msg, 'content', None)
                    if content:
                        msg.content = _repair(content)
        except Exception:
            pass  # never block on sanitization failure
        return resp
    return _wrapper


async def _do_call(
    model: str,
    messages: list[dict],
    schema: type[T],
    cfg: Config,
    role: str = "?",
) -> T:
    if _i...[truncated]
```
</details>

**[50] tool→Bash:** `grep -rn "return_exceptions" /Users/cyan/ccr/know-expand/know_expand/ | grep -v pyc; echo ===; grep -n -B3 -A12 "def ret` — Check gather return_exceptions and instructor retry behavior

<details><summary>[51] tool result (749 chars)</summary>

```
Exit code 1
/Users/cyan/ccr/know-expand/know_expand/sources.py:436:    await asyncio.gather(*[_fetch_one(t, tt) for t, tt in terms], return_exceptions=True)
/Users/cyan/ccr/know-expand/know_expand/stages/s2_extract.py:302:                results_list = await asyncio.gather(*grounding_tasks, return_exceptions=True)
/Users/cyan/ccr/know-expand/know_expand/stages/s6_align.py:214:    results = await asyncio.gather(*tasks, return_exceptions=True)
/Users/cyan/ccr/know-expand/know_expand/stages/s5_research.py:439:                *missing_id_tasks.values(), *nc_tasks, return_exceptions=True
/Users/cyan/ccr/know-expand/know_expand/stages/s5_research.py:990:    raw_results = await asyncio.gather(*coros, return_exceptions=True)
(eval):1: == not found
```
</details>

**[52] tool→Bash:** `grep -rn -B5 -A15 "for stage" /Users/cyan/ccr/know-expand/know_expand/pipeline.py | head -60; echo ===; grep -n "except"` — Check pipeline-level stage exception handling

<details><summary>[53] tool result (34 chars)</summary>

```
Exit code 1
(eval):1: == not found
```
</details>

**[55] assistant:** `s5_research.py:990` gathers domain coroutines with `return_exceptions=True` — possible mitigation. Let me see how those exceptions are handled.

**[56] tool→Read:** `/Users/cyan/ccr/know-expand/know_expand/stages/s5_research.py` 

<details><summary>[57] tool result (2030 chars)</summary>

```
960	
961	    gap_md_path = audit_dir / "gap_analysis.md"
962	    gap_md = gap_md_path.read_text() if gap_md_path.exists() else ""
963	
964	    depth = state["depth"]
965	    router = make_router("researcher", cfg)
966	    critic_router = make_router("researcher", cfg, thinking_budget=cfg.critic_thinking.budget_tokens)
967	
968	    profile_path = state_dir / "user_profile.json"
969	    user_profile: dict = {}
970	    if profile_path.exists():
971	        user_profile = json.loads(profile_path.read_text())
972	
973	    coros = [
974	        _research_domain(
975	            domain=domain,
976	            audit_dir=audit_dir,
977	            sections_dir=sections_dir,
978	            summaries_dir=summaries_dir,
979	            gap_md=gap_md,
980	            graph_nodes=nodes,
981	            depth=depth,
982	            cfg=cfg,
983	            router=router,
984	            critic_router=critic_router,
985	            user_profile=user_profile,
986	        )
987	        for domain in domains
988	    ]
989	
990	    raw_results = await asyncio.gather(*coros, return_exceptions=True)
991	
992	    failed = 0
993	    succeeded = 0
994	    failed_ids: list[str] = []
995	    for domain, result in zip(domains, raw_results):
996	        if isinstance(result, Exception):
997	            failed += 1
998	            failed_ids.append(domain["id"])
999	            emit({
1000	                "event": "s5_domain_failed",
1001	                "domain_id": domain["id"],
1002	                "error": str(result)[:200],
1003	            })
1004	        else:
1005	            succeeded += 1
1006	
1007	    if failed_ids:
1008	        _logger.warning("s5: %d domain(s) failed to produce content: %s", len(failed_ids), ", ".join(failed_ids))
1009	
1010	    mark_stage_complete(state_dir, 5)
1011	    emit({
1012	        "event": "s5_research_complete",
1013	        "stage": 5,
1014	        "domains_succeeded": succeeded,
1015	        "domains_failed": failed,
1016	        "domains_failed_ids":...[truncated]
```
</details>

**[58] tool→Bash:** `grep -n "except" /Users/cyan/ccr/know-expand/know_expand/pipeline.py /Users/cyan/ccr/know-expand/know_expand/cli.py | he` — Check exception handling in pipeline.py and cli.py

<details><summary>[59] tool result (31 chars)</summary>

```
(Bash completed with no output)
```
</details>

**[62] tool→Bash:** `grep -n -B2 -A4 "Distinguish failure\|principle" /Users/cyan/ccr/know-expand/README.md | head -40` — Find design principle 13 in README

<details><summary>[63] tool result (3080 chars)</summary>

```
35-`know-expand` takes that document and produces a **navigable, ground-up knowledge expansion** — a structured field guide covering everything needed to understand the paper deeply, go beyond it, and do independent research.
36-
37:**Not a summary. Not a chatbot answer. Not a Q&A wrapper.** A full-stack knowledge construction pipeline that identifies every substantive concept in a document, maps those concepts into a principled domain taxonomy, augments each domain from first principles to the current research frontier, synthesizes cross-domain insights invisible within any single domain, and grounds every claim in verifiable, real citations.
38-
39-The reference output: a 480KB, 241-page, 81-node skill-tree document with verified citations, LaTeX mathematics, and technical diagrams — produced automatically from a single source paper.
40-
41-### Cost and latency
--
464-| Practitioner | Real-world trade-offs, benchmarks, gotchas, Where to Go Next | From usage to mechanism: practitioner workflow → underlying reasons |
465-
466:**Each agent receives:** domain nodes from `graph.json`, gap analysis, `bibliography_{domain}.json` (only permitted citations), reader profile (`unknown_concepts` get first-principles treatment, `known_concepts` are assumed), math protocol, and per-persona drift guard.
467-
468-**Adversarial critic loop:** terminates when issue set is empty, identical to prior round (stalled), or critic accepts. Hard cap: 1/2/3 rounds for survey/standard/deep.
469-
470-**Prompt sizing and caching:** persona prompts are split into a shared context block (domain info, reader profile, graph nodes, gap analysis, bibliography, knowledge sources — marked `cache_control: {type: ephemeral}`) and a task block (persona-specific role and output instruction). The shared block exceeds Anthropic's 4,096-token caching threshold at deep depth, so resume/retry runs benefit from the 90% cache-read discount.
--
572----
573-
574:## Design principles
575-
576:These principles are n...[truncated]
```
</details>

**[64] tool→Bash:** `grep -rn "ainvoke\|graph.invoke\|compiled" /Users/cyan/ccr/know-expand/know_expand/pipeline.py | head; grep -c "try:" /U` — Check pipeline invocation and try counts in s4

<details><summary>[65] tool result (291 chars)</summary>

```
/Users/cyan/ccr/know-expand/know_expand/pipeline.py:154:    compiled = graph.compile()
/Users/cyan/ccr/know-expand/know_expand/pipeline.py:155:    await compiled.ainvoke(state)
/Users/cyan/ccr/know-expand/know_expand/pipeline.py:0
/Users/cyan/ccr/know-expand/know_expand/stages/s4_audit.py:3
```
</details>

**[66] tool→Bash:** `sed -n '663,690p' /Users/cyan/ccr/know-expand/README.md` — Read design principle 13 failure mode table

<details><summary>[67] tool result (1528 chars)</summary>

```
### 13. Distinguish failure modes precisely. Handle them differently.

| Failure | Cause | Correct response |
|---------|-------|-----------------|
| HTTP 429 + `Retry-After` | Transient rate limit | Sleep `Retry-After` seconds; retry same model |
| HTTP 429, no `Retry-After` | True quota exhaustion | Switch to next model in fallback list |
| All models exhausted | Full quota drain | Emit `quota_exhausted`; preserve progress; wait for `--resume` |
| xelatex exit 0, tiny PDF | Silent corruption | Explicit `pypdf` check; raise before checkpoint is written |
| Concurrent 429s from fan-out | Rate limit misread as exhaustion | Per-provider semaphore prevents the fan-out |

Conflating any two rows wastes the fallback list on recoverable errors or misses real failures.

### 14. The frontier is not optional.

Sorting bibliography by citation count descending is a recency tax: a 2017 survey with 4,000 citations outranks a 2025 breakthrough with 12. The 65% foundational / 35% frontier bibliography split is a design invariant. The frontier window (default: last 24 months) is configurable, but the existence of a frontier bucket is not.

---

## LLM routing

### QuotaAwareRouter (`agents/base.py`)

Tries models in order from `models.yaml` for the given role:

| Error | Action |
|-------|--------|
| `litellm.AuthenticationError` (may be wrapped in `InstructorRetryException`) | Permanently skip model; walk exception chain with `_find_in_chain()` |
| 429 + `Retry-After` header | Sleep exact duration; retry same model |
```
</details>

**[68] tool→Bash:** `grep -n -B3 -A8 "try:" /Users/cyan/ccr/know-expand/know_expand/stages/s4_audit.py | head -50` — Check what s4_audit try blocks protect

<details><summary>[69] tool result (1732 chars)</summary>

```
216-    terms_path = audit_dir.parent / "terms.json"
217-    term_type_map: dict[str, str] = {}
218-    if terms_path.exists():
219:        try:
220-            for t in json.loads(terms_path.read_text()):
221-                term_type_map[t["name"]] = t.get("term_type", "academic")
222-        except Exception as exc:
223-            _logger.warning("s4 term_type_map load failed: %s", exc)
224-
225-    # Fetch knowledge sources for non-trivial terms in this domain (best-effort)
226-    sources_dir = audit_dir / "sources"
227-    sources_dir.mkdir(exist_ok=True)
228-    sources_cache = sources_dir / f"sources_{domain_id}.json"
229-
230-    if not sources_cache.exists():
231:        try:
232-            from know_expand.sources import fetch_sources_for_terms  # noqa: PLC0415
233-            # Only fetch core + supporting terms, not incidental
234-            nodes_for_fetch = domain_nodes or []
235-            terms_to_fetch = [
236-                (n["name"], term_type_map.get(n["name"], "academic"))
237-                for n in nodes_for_fetch
238-                if n.get("centrality") in ("core", "supporting")
239-            ]
--
421-                if any(n["name"] == t and n.get("domain") == domain_id for n in nodes)
422-            ]
423-            all_terms = list(dict.fromkeys(graph_terms + gap_candidates))
424:            try:
425-                result = await _process_domain(
426-                    domain, all_terms, audit_dir, depth, cfg, router, http,
427-                    domain_nodes=domain_node_list,
428-                )
429-                gap_results.append(result)
430-            except Exception as exc:
431-                emit({
432-                    "event": "domain_failed",
```
</details>

**[71] assistant:** S4 also has per-domain try/except. Let me check S7, S8, S9, S2 protections.

**[72] tool→Bash:** `grep -n -B6 "router.call" /Users/cyan/ccr/know-expand/know_expand/stages/s7_synthesize.py | sed -n 1,40p` — Check try wrapping around s7 router calls

<details><summary>[73] tool result (1199 chars)</summary>

```
351-            domain_summaries=full_summaries_str,
352-            graph_edges=edges_str,
353-        ),
354-    }]
355-
356-    structural_draft, semantic_draft, connector_output = await asyncio.gather(
357:        router.call(structural_msg, SynthesisDraft),
358:        router.call(semantic_msg, SynthesisDraft),
359:        router.call(connector_msg, ConnectorOutput),
--
374-            "content": _SYNTHESIS_CRITIC_PROMPT.format(
375-                structural_narrative=current_structural.narrative,
376-                semantic_narrative=current_semantic.narrative,
377-                domain_ids=json.dumps(domain_ids),
378-            ),
379-        }]
380:        critique: SynthesisCritique = await router.call(critic_msg, SynthesisCritique)
--
417-                semantic_narrative=current_semantic.narrative,
418-                trivial_connections=json.dumps(critique.trivial_connections),
419-                unsupported_connections=json.dumps(critique.unsupported_connections),
420-                missing_cross_domain=json.dumps(critique.missing_cross_domain),
421-            ),
422-        }]
423:        revised: SynthesisDraft = await router.call(revise_msg, SynthesisDraft)
```
</details>

**[74] tool→Bash:** `grep -n -B8 "await router.call" /Users/cyan/ccr/know-expand/know_expand/stages/s8_verify.py /Users/cyan/ccr/know-expand/` — Check try wrapping in s8 s9 s2

<details><summary>[75] tool result (773 chars)</summary>

```
8:/Users/cyan/ccr/know-expand/know_expand/stages/s8_verify.py-154-        try:
9:/Users/cyan/ccr/know-expand/know_expand/stages/s8_verify.py:155:            check: CitationCheck = await router.call(
17:/Users/cyan/ccr/know-expand/know_expand/stages/s9_prereq.py-130-    try:
18:/Users/cyan/ccr/know-expand/know_expand/stages/s9_prereq.py:131:        plan: PrimerPlan = await router.call(messages, PrimerPlan)
27:/Users/cyan/ccr/know-expand/know_expand/stages/s2_extract.py:94:    llm_result: TermInventory = await router.call(messages, TermInventory)
33:/Users/cyan/ccr/know-expand/know_expand/stages/s2_extract.py-257-        try:
37:/Users/cyan/ccr/know-expand/know_expand/stages/s2_extract.py:261:            result: TermKindBatch = await router.call(msg, TermKindBatch)
```
</details>

## Structured Output
```json
{
  "verdict": "confirmed",
  "reasoning": "Every load-bearing technical assertion verified against the code and installed litellm 1.85.0.\n\n1. Comment/code mismatch confirmed. /Users/cyan/ccr/know-expand/know_expand/agents/base.py:726-727 says the counter is \"for headerless 429/500/529\", but both handlers (lines 750-751 in call(), 901-902 in call_with_tools()) only do _find_in_chain(e, litellm.RateLimitError) and _find_in_chain(e, litellm.ServiceUnavailableError). _find_in_chain uses isinstance (base.py:394-403), and in litellm 1.85.0 InternalServerError is NOT a subclass of ServiceUnavailableError (verified via MRO inspection: both descend directly from APIStatusError; issubclass returns False both directions). So 500/529 never enter the backoff path despite the comment.\n\n2. litellm mapping confirmed. exception_mapping_utils.py in the installed venv maps Anthropic \"overloaded_error\"/\"Overloaded\" strings and status codes 500 and 529 to litellm.InternalServerError. Timeout subclasses APIConnectionError, not ServiceUnavailableError.\n\n3. Grep confirms InternalServerError, APIConnectionError, and litellm Timeout are referenced nowhere in the know_expand package (only JS setTimeout hits in observe.py). litellm.num_retries is None (no global retry), and instructor's retry only re-asks on validation errors, so these exceptions reach the router's else branch and, for non-geminicli models, hit the bare `raise` at base.py:814 \u2014 no backoff, no model switch. This contradicts both the base.py comment and README.md:663 design principle 13.\n\n4. Timeout claim confirmed: _do_call/_do_call_with_thinking/call_with_tools pass no timeout kwarg to litellm/instructor (only the geminicli subprocess path and a 3s httpx health probe have explicit timeouts); non-geminicli calls rely on litellm's default request_timeout.\n\nOne refinement to the impact narrative (does not reduce severity): the propagated exception does not always literally \"crash a stage\". S5 gathers per-domain coroutines with return_exceptions=True (s5_research.py:990-1010) and S4 has a per-domain try/except (s4_audit.py:424-432), so in those stages a transient 529 silently drops an entire domain's content, emits s5_domain_failed/domain_failed, and still marks the stage complete \u2014 meaning resume will NOT regenerate the lost domain. That is arguably worse than a crash. In S7 (gather at s7_synthesize.py:356-359 without return_exceptions), S2's main extraction call, S1, and S3 the exception is unhandled and does crash the run (pipeline.py and cli.py contain zero try/except). High severity stands either way: a single transient Anthropic overload either crashes the run mid-stage or permanently loses a domain from the final document."
}
```

<details><summary>[79] tool result (39 chars)</summary>

```
Structured output provided successfully
```
</details>

**[80] assistant:** Verification complete. Verdict: confirmed.
