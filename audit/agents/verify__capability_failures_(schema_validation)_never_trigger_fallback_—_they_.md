# Agent: verify__capability_failures_(schema_validation)_never_trigger_fallback_—_they_
**ID:** `agent-af13f04c3af50a3c1`  **Turns:** 39

## Prompt
```
You are an adversarial verifier. A code auditor claims the following about the repo at /Users/cyan/ccr/know-expand:

TITLE: Capability failures (schema validation) never trigger fallback — they crash the call
LAYER: execution
SEVERITY: critical
CLAIM: QuotaAwareRouter.call() only falls back on auth errors, RateLimitError, ServiceUnavailableError, or any geminicli error (base.py:748-814). Everything else hits the bare `raise` at base.py:814. When instructor's Pydantic validation fails (InstructorRetryException with no auth/rate-limit in the chain) — i.e., the model couldn't produce the schema — the router re-raises instead of escalating to the next, stronger model. Combined with cheapest-first ordering, the weakest models attempt the hardest schemas (DomainSummary, ReconcilerOutput) first, making schema noncompliance the single most likely failure mode, and it is the one failure the fallback chain does not handle. This inverts the router's stated purpose. Note the asymmetry: for geminicli, even a one-off JSON parse error permanently skips the provider for the router's lifetime (base.py:799-813), while for API models the same class of error kills the call outright.
FILES: /Users/cyan/ccr/know-expand/know_expand/agents/base.py:748-814

Read the actual code and try to REFUTE this claim. Check: does the cited code actually behave as claimed? Is the severity inflated? Is there mitigating code elsewhere (search the repo)? If the claim is materially correct, confirm it. Default to refuted if the evidence is ambiguous.
```

**[4] tool→Read:** `/Users/cyan/ccr/know-expand/know_expand/agents/base.py` 

<details><summary>[5] tool result (7259 chars)</summary>

```
680	            response_model=schema,
681	            thinking={"type": "enabled", "budget_tokens": budget},
682	        )
683	        elapsed = time.monotonic() - t0
684	        usage = getattr(completion, "usage", None)
685	        tok_out = getattr(usage, "completion_tokens", None)
686	        tok_in = getattr(usage, "prompt_tokens", None)
687	        tok_s = round(tok_out / elapsed, 1) if tok_out and elapsed > 0 else None
688	        cache_write = getattr(usage, "cache_creation_input_tokens", None)
689	        cache_read = getattr(usage, "cache_read_input_tokens", None)
690	        emit({
691	            "event": "llm_call_done",
692	            "role": role,
693	            "model": model,
694	            "schema": schema.__name__,
695	            "elapsed_s": round(elapsed, 2),
696	            "tok_in": tok_in,
697	            "tok_out": tok_out,
698	            "tok_s": tok_s,
699	            "cache_write_tok": cache_write,
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
727	        se...[truncated]
```
</details>

**[6] tool→Bash:** `grep -n "InstructorRetryException\|ValidationError\|except\|raise" /Users/cyan/ccr/know-expand/know_expand/agents/base.p` — Find exception handling in base.py

<details><summary>[7] tool result (1203 chars)</summary>

```
156:                raise RuntimeError(f"geminicli quota exhausted{retry_info}")
157:            raise RuntimeError(f"gemini-cli returned no output (exit {proc.returncode}): {err_text[:400]}")
175:    except Exception as exc:
185:        raise
232:                raise RuntimeError(f"geminicli quota exhausted{retry_info}")
233:            raise RuntimeError(f"gemini-cli returned no output (exit {proc.returncode}): {err_text[:400]}")
262:        except Exception:
268:    except Exception as exc:
278:        raise
294:    except Exception:
357:    except Exception as exc:
367:        raise
407:    """Return the root auth exception if exc (or its chain) is a credentials failure."""
415:    # openai raises a generic OpenAIError *before* the request when credentials
582:        except Exception:
636:    except Exception as exc:
646:        raise
704:    except Exception as exc:
714:        raise
748:            except Exception as e:
814:                    raise
821:        raise RuntimeError(f"All models exhausted for role: {self.role}")
899:            except Exception as e:
955:                    raise
956:        raise RuntimeError(f"All models exhausted for agent role: {self.role}")
```
</details>

**[10] tool→Read:** `/Users/cyan/ccr/know-expand/know_expand/agents/base.py` 

<details><summary>[11] tool result (4430 chars)</summary>

```
560	        out: list[str] = []
561	        i = 0
562	        while i < len(text):
563	            if text[i] == '\\' and i + 1 < len(text):
564	                nxt = text[i + 1]
565	                if nxt not in _valid_escapes:
566	                    out.append('\\\\')
567	                    i += 1
568	                    continue
569	            out.append(text[i])
570	            i += 1
571	        return ''.join(out)
572	
573	    async def _wrapper(*args, **kwargs):
574	        resp = await litellm.acompletion(*args, **kwargs)
575	        try:
576	            for choice in getattr(resp, 'choices', []):
577	                msg = getattr(choice, 'message', None)
578	                if msg is not None:
579	                    content = getattr(msg, 'content', None)
580	                    if content:
581	                        msg.content = _repair(content)
582	        except Exception:
583	            pass  # never block on sanitization failure
584	        return resp
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
616	        e...[truncated]
```
</details>

**[12] tool→Bash:** `grep -rn "max_retries\|InstructorRetryException\|ValidationError" /Users/cyan/ccr/know-expand/know_expand/ | grep -v ".p` — Search for retry/validation handling across the package

<details><summary>[13] tool result (632 chars)</summary>

```
/Users/cyan/ccr/know-expand/know_expand/config.py:14:    ss_max_retries: int = 5
/Users/cyan/ccr/know-expand/know_expand/bibliography.py:154:    for attempt in range(cfg.bibliography.ss_max_retries):
/Users/cyan/ccr/know-expand/know_expand/bibliography.py:199:                "max_retries": cfg.bibliography.ss_max_retries,
/Users/cyan/ccr/know-expand/know_expand/bibliography.py:208:    emit({"event": "ss_exhausted", "query": query[:60], "attempts": cfg.bibliography.ss_max_retries})
/Users/cyan/ccr/know-expand/know_expand/state.py:252:                f"retry {ev.get('attempt')}/{ev.get('max_retries')} in {ev.get('sleep_s')}s "
```
</details>

**[16] tool→Bash:** `python3 -c "
import inspect, importlib
spec = importlib.util.find_spec('instructor')
print('no local instructor' if spec` — Check instructor version and default max_retries

**[17] tool→Read:** `/Users/cyan/ccr/know-expand/models.yaml` 

**[18] tool→Bash:** `grep -rn "router.call\|\.call(" /Users/cyan/ccr/know-expand/know_expand/stages/ | grep -v "call_with_tools" | head -40` — Find router.call usages in stages

<details><summary>[19] tool result (317 chars)</summary>

```
version: 1.15.1
(self, messages: 'list[ChatCompletionMessageParam]', response_model: 'type[T]', max_retries: 'int | AsyncRetrying' = 3, validation_context: 'dict[str, Any] | None' = None, context: 'dict[str, Any] | None' = None, strict: 'bool' = True, hooks: 'Hooks | None' = None, **kwargs: 'Any') -> 'tuple[T, Any]'
```
</details>

<details><summary>[20] tool result (3523 chars)</summary>

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

<details><summary>[21] tool result (3720 chars)</summary>

```
/Users/cyan/ccr/know-expand/know_expand/stages/s1_assess.py:165:        result: ConceptList = await router.call(messages, ConceptList)
/Users/cyan/ccr/know-expand/know_expand/stages/s1_assess.py:453:        decision: InterviewDecision = await router.call(messages, InterviewDecision)
/Users/cyan/ccr/know-expand/know_expand/stages/s1_assess.py:566:    return await router.call(messages, UserProfile)
/Users/cyan/ccr/know-expand/know_expand/stages/s9_prereq.py:131:        plan: PrimerPlan = await router.call(messages, PrimerPlan)
/Users/cyan/ccr/know-expand/know_expand/stages/s3_graph.py:45:    explanation = await router.call(msg, TaxonomyExplanation)
/Users/cyan/ccr/know-expand/know_expand/stages/s3_graph.py:146:            subprocess.call([editor, str(taxonomy_path)])
/Users/cyan/ccr/know-expand/know_expand/stages/s3_graph.py:174:    merged = await router.call(merge_msg, _DomainList)
/Users/cyan/ccr/know-expand/know_expand/stages/s3_graph.py:293:            router.call(lumper_msg, TaxonomyProposal),
/Users/cyan/ccr/know-expand/know_expand/stages/s3_graph.py:294:            router.call(splitter_msg, TaxonomyProposal),
/Users/cyan/ccr/know-expand/know_expand/stages/s2_extract.py:94:    llm_result: TermInventory = await router.call(messages, TermInventory)
/Users/cyan/ccr/know-expand/know_expand/stages/s2_extract.py:261:            result: TermKindBatch = await router.call(msg, TermKindBatch)
/Users/cyan/ccr/know-expand/know_expand/stages/s4_audit.py:119:        finder_result: GapAnalysisResult = await router.call(finder_msg, GapAnalysisResult)
/Users/cyan/ccr/know-expand/know_expand/stages/s4_audit.py:146:        defender_result: GapAnalysisResult = await router.call(defender_msg, GapAnalysisResult)
/Users/cyan/ccr/know-expand/know_expand/stages/s4_audit.py:166:        rebuttal_result = await router.call(rebuttal_msg, GapAnalysisResult)
/Users/cyan/ccr/know-expand/know_expand/stages/s8_verify.py:155:            check: CitationCheck = await router.call(
/Users/cyan/ccr/kn...[truncated]
```
</details>

**[24] tool→Bash:** `grep -n -B6 "await router.call(reconciler_msg\|router.call(theoretician_msg" /Users/cyan/ccr/know-expand/know_expand/sta` — Check exception wrapping around router calls in s5 and schema names

<details><summary>[25] tool result (1288 chars)</summary>

```
773-        domain_id=domain_id,
774-        drift_guard=_PERSONA_DRIFT_GUARD.format(persona_name="PRACTITIONER"),
775-    ))
776-
777-    # Run all three personas in parallel
778-    theoretician_result, engineer_result, practitioner_result = await asyncio.gather(
779:        router.call(theoretician_msg, PersonaOutput),
--
811-        engineer_sections=_persona_sections_to_text(engineer_result),
812-        practitioner_sections=_persona_sections_to_text(practitioner_result),
813-        math_protocol=_MATH_PROTOCOL,
814-        concept_protocol=_CONCEPT_PROTOCOL,
815-        anti_hallucination=_ANTI_HALLUCINATION,
816-    )}]
817:    reconciled: ReconcilerOutput = await router.call(reconciler_msg, ReconcilerOutput)
---
/Users/cyan/ccr/know-expand/know_expand/stages/s5_research.py:423:        try:
/Users/cyan/ccr/know-expand/know_expand/stages/s5_research.py:429:    try:
/Users/cyan/ccr/know-expand/know_expand/stages/s5_research.py:501:        try:
/Users/cyan/ccr/know-expand/know_expand/stages/s5_research.py:695:        try:
---
186:class DomainSummary(BaseModel):
195:class PersonaOutput(BaseModel):
203:class ReconcilerOutput(BaseModel):
206:    summary: DomainSummary      # structured summary consumed by Stage 5
222:    revised_summary: DomainSummary | None = None
```
</details>

**[26] tool→Read:** `/Users/cyan/ccr/know-expand/know_expand/agents/base.py` 

<details><summary>[27] tool result (2645 chars)</summary>

```
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
412	    root = _find_in_chain(exc, openai.AuthenticationError, openai.PermissionDeniedError)
413	    if root:
414	        return root
415	    # openai raises a generic OpenAIError *before* the request when credentials
416	    # are missing (client init fails), so it never becomes a litellm error.
417	    root = _find_in_chain(exc, openai.OpenAIError)
418	    if root and any(kw in str(root).lower() for kw in ("credential", "api_key", "missing")):
419	        return root
420	    return None
421	
422	
423	def _geminicli_is_available() -> bool:
424	    """True if npx exists and the Gemini CLI OAuth credentials are cached."""
425	    import shutil
426	    if not shutil.which("npx"):
427	        return False
428	    # Gemini CLI stores OAuth creds in ~/.gemini/oauth_creds.json
429	    creds = os.path.expanduser("~/.gemini/oauth_creds.json")
430	    return os.path.exists(creds)
431	
432	
433	async def _probe_one(model: str, cfg: Config) -> bool:
434	    """Return True if model appears usable. Checks env vars only — no LLM call."""
435	    if _is_geminicli(model):
436	        return _geminicli_is_available()
437	    if "claude" in model or "anthropic" in model:
438	        return bool(os.environ.get("ANTHROPIC_API_KEY"))
439	    if "gpt" in model or "openai" in model:
440	        return bool(os.environ.get("OPENAI_API_KEY"))
441	    if "gemini" in model or "google" in model:
442	        return bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))
443	    if "groq" in model:
444	        return bool(os.enviro...[truncated]
```
</details>

**[29] assistant:** No try/except around the s5 persona/reconciler calls. Let me check pipeline.py for stage-level retry handling and the line 695 try block context.

**[30] tool→Bash:** `grep -n -A12 "try:" /Users/cyan/ccr/know-expand/know_expand/stages/s5_research.py | sed -n '1,60p'; echo ===; grep -n "e` — Inspect try blocks in s5 and pipeline-level handling

<details><summary>[31] tool result (2181 chars)</summary>

```
Exit code 1
423:        try:
424-            return await _ss_search(query, http, cfg, limit=5)
425-        except Exception as exc:
426-            _logger.warning("s4 bib expansion: SS search failed for %r: %s", query[:60], exc)
427-            return []
428-
429:    try:
430-        async with httpx.AsyncClient(timeout=15.0) as http:
431-            # Search for missing citation IDs
432-            missing_id_tasks = {mid: asyncio.create_task(_search_one(mid, http)) for mid in missing_ids}
433-            # Search for [NEEDS_CITATION] contexts
434-            nc_tasks = [
435-                asyncio.create_task(_search_one(ctx, http))
436-                for (_, _, ctx) in needs_citation_contexts
437-            ]
438-            await asyncio.gather(
439-                *missing_id_tasks.values(), *nc_tasks, return_exceptions=True
440-            )
441-    except Exception as exc:
--
501:        try:
502-            bib_path.write_text(json.dumps(updated_bibliography, indent=2))
503-        except Exception as exc:
504-            _logger.warning("s4 bib expansion: could not write updated bib for %s: %s", domain_id, exc)
505-
506-    return updated_bibliography, updated_personas
507-
508-
509-# ---------------------------------------------------------------------------
510-# Helpers
511-# ---------------------------------------------------------------------------
512-
513-def _narrative_to_markdown(output: ReconcilerOutput) -> str:
--
695:        try:
696-            term_sources = json.loads(sources_cache.read_text())
697-        except Exception as exc:
698-            _logger.warning("s5 sources load failed for %r: %s", domain_id, exc)
699-
700-    # Depth-aware caps — keeps persona prompts under ~20k chars at all depths.
701-    # Defaults: survey≈1.5k / standard≈3.2k / deep≈15k chars of knowledge sources.
702-    ctx = cfg.research_context
703-    knowledge_sources_str = format_sources(
704-        term_sources,
705-        max_terms=ctx.sources_max_terms.g...[truncated]
```
</details>

**[32] tool→Bash:** `grep -n 'except\|retry\|try:' /Users/cyan/ccr/know-expand/know_expand/pipeline.py | head -20` — Check pipeline.py for stage-level exception handling

<details><summary>[33] tool result (31 chars)</summary>

```
(Bash completed with no output)
```
</details>

## Structured Output
```json
{
  "verdict": "confirmed",
  "reasoning": "Read /Users/cyan/ccr/know-expand/know_expand/agents/base.py:735-821 directly. The cited behavior is verbatim accurate: the except block (748) classifies errors as auth (_is_auth_error, chain-walking via _find_in_chain), rate-limit/ServiceUnavailable (with Retry-After sleep or 3-strike backoff-then-skip), or geminicli (skip+continue, lines 799-813); everything else hits the bare `raise` at line 814. An InstructorRetryException from Pydantic schema validation chains to ValidationError \u2014 not auth or rate-limit \u2014 so for non-geminicli models the router re-raises instead of trying the next model. The asymmetry claim is also correct: any non-auth/non-transient geminicli error (including one-off parse errors) is added to self._skip for the router's lifetime, while the identical error class on an API model crashes the call. Cheapest-first ordering is confirmed in models.yaml (geminicli flash \u2192 gemini-2.5-flash \u2192 mistral-small \u2192 groq llamas before claude-opus-4-7 for researcher/synthesizer), and the hardest schemas (ReconcilerOutput embedding DomainSummary, schemas.py:186/203) flow through these routers (s5_research.py:817). I searched for mitigating code and found none at the call sites or pipeline level: no try/except wraps any router.call in stages/ (the s5 asyncio.gather has no return_exceptions), and pipeline.py has zero exception handling, so the raise kills the stage/run (resumable only via pipeline.json stage markers, and a resume retries the same cheapest-first order). The verdict therefore stands as confirmed.",
  "correction": "Two softening details the claim omits (they reduce frequency, not the structural gap): (1) instructor 1.15.1's create_with_completion defaults to max_retries=3, so each model gets up to 3 self-correcting re-ask attempts with validation errors fed back before InstructorRetryException ever reaches the router \u2014 many schema failures recover on the same model; (2) _make_sanitized_completion (base.py:573-585) repairs invalid backslash escapes before parsing, mitigating one class of JSON failure. Also, \"single most likely failure mode\" is plausible inference, not something verifiable from the code alone, so \"critical\" severity rests partly on that unverified prevalence assumption; \"high\" may be more defensible given runs are resumable."
}
```

<details><summary>[37] tool result (39 chars)</summary>

```
Structured output provided successfully
```
</details>

**[38] assistant:** Verification complete. The claim is confirmed.
