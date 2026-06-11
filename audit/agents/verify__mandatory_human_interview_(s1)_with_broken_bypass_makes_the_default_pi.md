# Agent: verify__mandatory_human_interview_(s1)_with_broken_bypass_makes_the_default_pi
**ID:** `agent-a75417819abef1b39`  **Turns:** 40

## Prompt
```
You are an adversarial verifier. A code auditor claims the following about the repo at /Users/cyan/ccr/know-expand:

TITLE: Mandatory human interview (S1) with broken bypass makes the default pipeline human-gated — incompatible with unattended/multi-user operation
LAYER: objective
SEVERITY: critical
CLAIM: Design principle 11 (README:649-655) and the CLI reference (README:188) promise S1 is skippable via --user-profile and 'skipped by default in autonomous mode' (README:88). In code, --user-profile is parsed (cli.py:85) but never read anywhere — grep over cli.py/pipeline.py shows args.user_profile is never referenced, and s1_assess.run() has no profile-injection path. So S1 always runs. In headless mode (use_web_ipc = not sys.stdin.isatty(), s1_assess.py:685), each question blocks on a 300s file-poll timeout (_WEB_TIMEOUT, s1_assess.py:38, 369-403); a cron/CI/batch run stalls ~40-75 minutes and then synthesizes a profile from empty answers. Similarly S3's web review blocks 600s then SILENTLY defaults to lumper (s3_graph.py:54, 78-101) — README:388 instead claims the pipeline 'emits pipeline_paused and exits cleanly', which it never does. For a product run concurrently by many users, the human gates must be an async pause/persist/notify/resume job model, not in-process polling that fabricates defaults on timeout.
FILES: /Users/cyan/ccr/know-expand/know_expand/stages/s1_assess.py:38, /Users/cyan/ccr/know-expand/know_expand/stages/s1_assess.py:369-403, /Users/cyan/ccr/know-expand/know_expand/stages/s1_assess.py:685, /Users/cyan/ccr/know-expand/know_expand/cli.py:85, /Users/cyan/ccr/know-expand/know_expand/stages/s3_graph.py:54-104

Read the actual code and try to REFUTE this claim. Check: does the cited code actually behave as claimed? Is the severity inflated? Is there mitigating code elsewhere (search the repo)? If the claim is materially correct, confirm it. Default to refuted if the evidence is ambiguous.
```

**[4] tool→Read:** `/Users/cyan/ccr/know-expand/know_expand/cli.py` 

<details><summary>[5] tool result (6752 chars)</summary>

```
1	import argparse
2	import asyncio
3	import signal
4	import sys
5	from pathlib import Path
6	
7	from dotenv import load_dotenv
8	load_dotenv()
9	
10	from know_expand.config import load_config
11	from know_expand.state import emit, load_pipeline_json, new_run_id, setup_logging
12	
13	
14	def _install_sigterm_handler() -> None:
15	    """Emit run_stopped and exit cleanly when SIGTERM arrives (e.g. from web UI stop)."""
16	    def _handler(signum, frame):
17	        emit({"event": "run_stopped", "reason": "sigterm"})
18	        sys.exit(0)
19	    signal.signal(signal.SIGTERM, _handler)
20	
21	
22	def _status(state_dir: Path) -> None:
23	    data = load_pipeline_json(state_dir)
24	    emit({"event": "pipeline_status", **data})
25	
26	
27	def _find_latest_state_dir(runs_dir: Path) -> Path | None:
28	    """Scan runs_dir for the most recently modified subdir that has state/pipeline.json."""
29	    if not runs_dir.exists():
30	        return None
31	    candidates = sorted(
32	        (d for d in runs_dir.iterdir() if d.is_dir()),
33	        key=lambda d: d.stat().st_mtime,
34	        reverse=True,
35	    )
36	    for run_dir in candidates:
37	        sd = run_dir / "state"
38	        if (sd / "pipeline.json").exists():
39	            return sd
40	    return None
41	
42	
43	_SUBCOMMANDS = {"tail", "serve"}
44	
45	
46	def main() -> None:
47	    # Pre-scan argv: if the first positional arg is not a known subcommand
48	    # (and not a flag), it's the pipeline input file. Extract it before
49	    # argparse consumes it as an invalid subcommand choice.
50	    raw_argv = sys.argv[1:]
51	    _input_file: str | None = None
52	    if raw_argv and not raw_argv[0].startswith("-") and raw_argv[0] not in _SUBCOMMANDS:
53	        _input_file = raw_argv[0]
54	        raw_argv = raw_argv[1:]
55	
56	    parser = argparse.ArgumentParser(
57	        prog="know-expand",
58	        description="Expand a technical document into a research-grade knowledge document.",
59	    )
60	    parser.add_...[truncated]
```
</details>

**[6] tool→Read:** `/Users/cyan/ccr/know-expand/know_expand/stages/s1_assess.py` 

<details><summary>[7] tool result (2041 chars)</summary>

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

**[8] tool→Bash:** `grep -rn "user_profile\|user-profile" /Users/cyan/ccr/know-expand/know_expand/ | grep -v ".pyc"` — Search for user_profile references in source

<details><summary>[9] tool result (1501 chars)</summary>

```
/Users/cyan/ccr/know-expand/know_expand/observe.py:2149:        profile_path = state_dir / "user_profile.json"
/Users/cyan/ccr/know-expand/know_expand/cli.py:85:    parser.add_argument("--user-profile", type=Path, metavar="PATH")
/Users/cyan/ccr/know-expand/know_expand/stages/s9_prereq.py:47:def _load_user_profile(state_dir: Path) -> dict:
/Users/cyan/ccr/know-expand/know_expand/stages/s9_prereq.py:48:    """Load user_profile.json; return empty dict on any error."""
/Users/cyan/ccr/know-expand/know_expand/stages/s9_prereq.py:49:    path = state_dir / "user_profile.json"
/Users/cyan/ccr/know-expand/know_expand/stages/s9_prereq.py:187:    profile = _load_user_profile(state_dir)
/Users/cyan/ccr/know-expand/know_expand/stages/s1_assess.py:676:    profile_path = state_dir / "user_profile.json"
/Users/cyan/ccr/know-expand/know_expand/stages/s5_research.py:657:    user_profile: dict | None = None,
/Users/cyan/ccr/know-expand/know_expand/stages/s5_research.py:727:    reader_profile_str, reader_profile_note = _reader_profile_strings(user_profile or {})
/Users/cyan/ccr/know-expand/know_expand/stages/s5_research.py:968:    profile_path = state_dir / "user_profile.json"
/Users/cyan/ccr/know-expand/know_expand/stages/s5_research.py:969:    user_profile: dict = {}
/Users/cyan/ccr/know-expand/know_expand/stages/s5_research.py:971:        user_profile = json.loads(profile_path.read_text())
/Users/cyan/ccr/know-expand/know_expand/stages/s5_research.py:985:            user_profile=user_profile,
```
</details>

**[10] tool→Read:** `/Users/cyan/ccr/know-expand/know_expand/stages/s1_assess.py` 

<details><summary>[11] tool result (4626 chars)</summary>

```
340	
341	# ---------------------------------------------------------------------------
342	# Terminal and web question delivery
343	# ---------------------------------------------------------------------------
344	
345	def _ask_terminal(q: dict) -> str:
346	    """Print question, read answer from stdin. Returns "" if stdin is not a TTY."""
347	    if not sys.stdin.isatty():
348	        return ""
349	
350	    print()
351	    print(q["text"])
352	    options = q.get("options", [])
353	    if options:
354	        for i, opt in enumerate(options, 1):
355	            print(f"  {i}. {opt}")
356	        raw = input("Your answer (number or text): ").strip()
357	        # If they typed a number, resolve it to the option text
358	        try:
359	            idx = int(raw)
360	            if 1 <= idx <= len(options):
361	                return options[idx - 1]
362	        except ValueError:
363	            pass
364	        return raw
365	    else:
366	        return input("Your answer: ").strip()
367	
368	
369	async def _ask_web(q: dict, state_dir: Path) -> str:
370	    """Append question to qa_queue.jsonl; poll qa_answers.jsonl for matching answer.
371	
372	    Returns the answer string, or "" on timeout (300s).
373	    """
374	    queue_path = state_dir / "qa_queue.jsonl"
375	    answers_path = state_dir / "qa_answers.jsonl"
376	
377	    # Append question to the queue
378	    with queue_path.open("a") as fh:
379	        fh.write(json.dumps(q) + "\n")
380	
381	    # Poll for the answer
382	    deadline = time.monotonic() + _WEB_TIMEOUT
383	    seen_ids: set[str] = set()
384	
385	    while time.monotonic() < deadline:
386	        await asyncio.sleep(_WEB_POLL_INTERVAL)
387	        if not answers_path.exists():
388	            continue
389	        try:
390	            lines = answers_path.read_text().splitlines()
391	        except OSError:
392	            continue
393	        for line in lines:
394	            line = line.strip()
395	            if not line:
396	             ...[truncated]
```
</details>

**[13] tool→Read:** `/Users/cyan/ccr/know-expand/know_expand/stages/s1_assess.py` 

<details><summary>[14] tool result (5765 chars)</summary>

```
600	        emit({"event": "stage1_question_asked", "turn": q["turn"], "question": q["text"]})
601	        if use_web_ipc:
602	            answer = await _ask_web(q, state_dir)
603	        else:
604	            answer = _ask_terminal(q)
605	        qa_pairs.append({"question": q["text"], "answer": answer})
606	    emit({"event": "stage1_interview_complete", "turns_completed": len(qa_pairs), "reason": "fallback_complete"})
607	    return qa_pairs
608	
609	
610	def _profile_from_fallback(qa_pairs: list[dict]) -> UserProfile:
611	    """Build a UserProfile from the 3-question fallback answers."""
612	    familiarity = "practitioner"
613	    background = "unknown"
614	    math_comfort = "engage"
615	
616	    if len(qa_pairs) >= 1:
617	        ans0 = qa_pairs[0]["answer"].lower()
618	        if "novice" in ans0 or "just starting" in ans0:
619	            familiarity = "novice"
620	        elif "aware" in ans0 or "basics" in ans0:
621	            familiarity = "aware"
622	        elif "expert" in ans0 or "deeply" in ans0:
623	            familiarity = "expert"
624	
625	    if len(qa_pairs) >= 2:
626	        background = qa_pairs[1]["answer"].strip() or "unknown"
627	
628	    if len(qa_pairs) >= 3:
629	        ans2 = qa_pairs[2]["answer"].lower()
630	        if "intuition" in ans2 or "diagram" in ans2:
631	            math_comfort = "intuition_only"
632	        elif "skim" in ans2:
633	            math_comfort = "skim"
634	        elif "formal" in ans2 or "derivation" in ans2:
635	            math_comfort = "formal"
636	
637	    depth_map = {"novice": "survey", "aware": "survey", "practitioner": "standard", "expert": "deep"}
638	    math_map = {"intuition_only": "intuition", "skim": "equations_explained", "engage": "equations_explained", "formal": "full_derivations"}
639	
640	    return UserProfile(
641	        familiarity_level=familiarity,
642	        background_field=background,
643	        math_comfort=math_comfort,
644	        learning_goal="apply",
645	        known_...[truncated]
```
</details>

**[15] tool→Read:** `/Users/cyan/ccr/know-expand/know_expand/stages/s3_graph.py` 

<details><summary>[16] tool result (2795 chars)</summary>

```
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
65	    """Web IPC variant: write proposals to state dir, poll for user's choice."""
66	    state_dir = taxonomy_path.parent
67	
68	    review = {
69	        "lumper": lumper.model_dump(),
70	        "splitter": splitter.model_dump(),
71	        "issues": issues,
72	    }
73	    (state_dir / _TAXONOMY_REVIEW_FILE).write_text(json.dumps(review, indent=2))
74	    emit({"event": "stage3_review_pending", "lumper_domains": len(lumper.domains),
75	          "splitter_domains": len(splitter.domains)})
76	
77	    choice_path = state_dir / _TAXONOMY_CHOICE_FILE
78	    deadline = time.monotonic() + _TAXONOMY_WEB_TIMEOUT
79	    choice = None
80	    while time.monotonic() < deadline:
81	        await asyncio.sleep(1.0)
82	        if choice_path.exists():
83	            try:
84	                choice = json.loads(choice_path.read_text()).get("choice", "l")
85	            except Exception:
86	                pass
87	            break
88	
89	    # Clean up IPC files
90	    for f in (state_dir / _TAXONOMY_REVIEW_FILE, choice_path):
91	        try:
92	            f.unlink(missing_ok=True)
93	        except Exception:
94	            pass
95	
96	    if choice == "s":
97	        mode, domains = "splitter", spl...[truncated]
```
</details>

**[18] tool→Bash:** `grep -rn "pipeline_paused\|auto_taxonomy" /Users/cyan/ccr/know-expand/know_expand/*.py /Users/cyan/ccr/know-expand/know_` — Search for pipeline_paused and auto_taxonomy in source

<details><summary>[19] tool result (1196 chars)</summary>

```
/Users/cyan/ccr/know-expand/know_expand/cli.py:172:        auto_taxonomy=args.auto_taxonomy,
/Users/cyan/ccr/know-expand/know_expand/observe.py:1724:        input, depth, auto_taxonomy: autoTax, no_pdf: noPdf,
/Users/cyan/ccr/know-expand/know_expand/observe.py:2508:    auto_taxonomy = params.get("auto_taxonomy", False)
/Users/cyan/ccr/know-expand/know_expand/observe.py:2511:    if auto_taxonomy:
/Users/cyan/ccr/know-expand/know_expand/pipeline.py:24:    auto_taxonomy: bool,
/Users/cyan/ccr/know-expand/know_expand/pipeline.py:42:        await s3_graph.run(state, cfg, auto_taxonomy=auto_taxonomy)
/Users/cyan/ccr/know-expand/know_expand/pipeline.py:107:    auto_taxonomy: bool = False,
/Users/cyan/ccr/know-expand/know_expand/pipeline.py:153:    graph = build_graph(cfg, auto_taxonomy=auto_taxonomy, no_pdf=no_pdf)
/Users/cyan/ccr/know-expand/know_expand/state.py:179:    warn_events = {"ss_429_retry", "pipeline_paused"}
/Users/cyan/ccr/know-expand/know_expand/state.py:287:        case "pipeline_paused":
/Users/cyan/ccr/know-expand/know_expand/stages/s3_graph.py:267:    auto_taxonomy: bool = False,
/Users/cyan/ccr/know-expand/know_expand/stages/s3_graph.py:312:        if auto_taxonomy:
```
</details>

**[20] tool→Bash:** `sed -n '80,95p;180,195p;380,395p;640,660p' /Users/cyan/ccr/know-expand/README.md` — Show README lines cited in claim

<details><summary>[21] tool result (4468 chars)</summary>

```
                            │  state/terms.json
                            ▼
┌───────────────────────────────────────────────────────┐
│ S1: ASSESS (User Calibration)                         │
│ 10–15 turn interview using real extracted terms       │
│ → UserProfile (depth, math mode, known concepts)      │
│ ← Human interaction #1 (web UI Q&A or TTY;           │
│   skipped by default in autonomous mode)              │
└───────────────────────────┬───────────────────────────┘
                            │  state/user_profile.json
                            ▼
┌───────────────────────────────────────────────────────┐
│ S3: GRAPH (Two-Phase Lock)                            │
│ Phase 1: taxonomy proposals (Lumper + Splitter)       │
│          → web UI review OR --auto-taxonomy           │
│          ← Human interaction #2 (skippable)           │
  --no-pdf                  Skip PDF build, output Markdown only
  --renderer                xelatex | typst  (default: xelatex)

Pipeline control:
  --depth                   survey | standard | deep  (default: standard)
  --status                  Emit pipeline_status from pipeline.json; no work runs
  --resume STAGE            Resume from stage N (e.g. --resume 4)
  --stage STAGE             Run only stage N then stop
  --user-profile <path>     Load pre-computed UserProfile JSON; skips S1
  --auto-taxonomy           Skip editor review; orchestrating LLM decides taxonomy
  --no-bibliography-fetch   Skip Semantic Scholar / Crossref fetches (air-gapped mode)
  --primary-model MODEL     Prepend MODEL to every role's fallback list

Paths:
  --runs-dir DIR            Base directory for all runs  (default: ./runs)


- **Lumper:** fewest domains that cleanly partition core terms (max 8; each must map to a Semantic Scholar `fieldsOfStudy`)
- **Splitter:** finest-grained distinctions the core terms support

Each proposed domain is validated against the OpenAlex concept API. Domains that resolve to no OpenAlex concept are flagged `...[truncated]
```
</details>

**[23] tool→Read:** `/Users/cyan/ccr/know-expand/know_expand/stages/s1_assess.py` 

<details><summary>[24] tool result (4445 chars)</summary>

```
455	        if decision.interview_complete or decision.next_question is None:
456	            covered = _detect_covered_fields(qa_history)
457	            uncovered = [f for f in _CRITICAL_FIELDS if f not in covered]
458	            if uncovered and turn < _MIN_TURNS:
459	                # Agent declared complete but critical signals are missing.
460	                # Override — the next iteration rebuilds the prompt with accurate
461	                # remaining_fields so the agent knows what it still needs to ask.
462	                emit({
463	                    "event": "stage1_coverage_override",
464	                    "turn": turn,
465	                    "uncovered_fields": uncovered,
466	                })
467	                # Synthesise a stub answer so the loop can continue; the agent
468	                # declared complete rather than asking, so there is no real answer.
469	                # Next iteration's prompt will list the uncovered fields explicitly.
470	                qa_history.append({"question": "(coverage check)", "answer": "(agent skipped)"})
471	                turn += 1
472	                continue
473	            emit({"event": "stage1_interview_complete", "turns_completed": turn,
474	                  "reason": "agent_declared_complete",
475	                  "uncovered_fields": [f for f in _CRITICAL_FIELDS if f not in covered]})
476	            break
477	
478	        q = decision.next_question
479	        clean_text, options = _extract_embedded_options(q.text, q.options)
480	
481	        # LLMs reliably set question_type but often forget to populate options.
482	        # For the three fixed structured turns we know exactly what the options
483	        # should be. For later unstructured turns, downgrade to open.
484	        if not options:
485	            fallback = _fallback_options(turn, key_concepts)
486	            if fallback:
487	                options = fallback
488	            else:
489	                q.question_type = "ope...[truncated]
```
</details>

**[25] tool→Read:** `/Users/cyan/ccr/know-expand/know_expand/stages/s1_assess.py` 

<details><summary>[26] tool result (545 chars)</summary>

```
729	        _create_qa_complete_sentinel(state_dir)
730	
731	        profile = await _synthesize_profile(qa_pairs, document_context, router)
732	        emit({"event": "stage1_profile_synthesized", "mode": "llm_synthesis", "qa_turns": len(qa_pairs)})
733	
734	    atomic_write(profile_path, profile.model_dump_json(indent=2))
735	    mark_stage_complete(state_dir, 1)
736	    emit({
737	        "event": "stage_complete",
738	        "stage": 1,
739	        "mode": "agentic_interview",
740	        "artifact": str(profile_path),
741	    })
742	
```
</details>

**[27] tool→Read:** `/Users/cyan/ccr/know-expand/know_expand/stages/s3_graph.py` 

<details><summary>[28] tool result (3359 chars)</summary>

```
107	async def _prompt_taxonomy(
108	    lumper: TaxonomyProposal,
109	    splitter: TaxonomyProposal,
110	    taxonomy_path: Path,
111	    router,
112	    issues: list[str],
113	    cfg,
114	) -> dict:
115	    """
116	    yay-style stdin prompt. Returns an approved taxonomy dict.
117	    Falls back to auto-merge if stdin is not a TTY.
118	    """
119	    if not sys.stdin.isatty():
120	        return await _prompt_taxonomy_web(lumper, splitter, taxonomy_path, router, issues, cfg)
121	
122	    _print_proposal("Lumper  — broad domains", lumper)
123	    _print_proposal("Splitter — fine-grained", splitter)
124	
125	    loop = asyncio.get_event_loop()
126	    while True:
127	        print(f"\n{_BOLD}==>{_RESET} l/s/m/e/x (lumper/splitter/merge/edit/explain) [l]: ", end="", flush=True)
128	        choice = (await loop.run_in_executor(None, sys.stdin.readline)).strip().lower() or "l"
129	
130	        if choice in ("x", "explain"):
131	            print(f"{_CYAN}==>{_RESET} Asking LLM to explain...", flush=True)
132	            await _explain_taxonomy(lumper, splitter, router)
133	            _print_proposal("Lumper  — broad domains", lumper)
134	            _print_proposal("Splitter — fine-grained", splitter)
135	            continue
136	
137	        if choice in ("s", "splitter"):
138	            domains = splitter.domains
139	            mode = "splitter"
140	        elif choice in ("e", "edit"):
141	            taxonomy_path.write_text(json.dumps(
142	                {"status": "pending", "domains": [d.model_dump() for d in lumper.domains]},
143	                indent=2,
144	            ))
145	            editor = os.environ.get("EDITOR", "vi")
146	            subprocess.call([editor, str(taxonomy_path)])
147	            data = json.loads(taxonomy_path.read_text())
148	            data["status"] = "approved"
149	            data.setdefault("mode", "edited")
150	            return data
151	        elif choice in ("m", "merge"):
152	            return await _auto_merge(lum...[truncated]
```
</details>

**[30] tool→Bash:** `grep -rn "_DEFAULT_PROFILE" /Users/cyan/ccr/know-expand/know_expand/ ; grep -rn "emit.*pipeline_paused" /Users/cyan/ccr/` — Check _DEFAULT_PROFILE usage, pipeline_paused emitters, resume logic

<details><summary>[31] tool result (1842 chars)</summary>

```
/Users/cyan/ccr/know-expand/know_expand/stages/s1_assess.py:40:_DEFAULT_PROFILE = UserProfile(
---

async def run_pipeline(
    input_path: str,
    state_dir: Path,
    output_dir: Path,
    depth: str,
    cfg: Config,
    auto_taxonomy: bool = False,
    primary_model: str | None = None,
    no_pdf: bool = False,
    resume_stage: int | None = None,
) -> None:
    import uuid

    if primary_model:
        for role in cfg.models:
            models = cfg.models[role]
            if primary_model not in models:
                cfg.models[role] = [primary_model] + models
            else:
                cfg.models[role] = [primary_model] + [m for m in models if m != primary_model]

    pipeline_json = load_pipeline_json(state_dir)
    run_id = pipeline_json.get("run_id") or f"run_{uuid.uuid4().hex[:8]}"

    # Persist run_id and input_path into pipeline.json immediately so the
    # web UI can show them and use input_path for resume without waiting
    # for the first stage to complete.
    pipeline_json["run_id"] = run_id
    pipeline_json.setdefault("input_path", input_path)
    from know_expand.state import save_pipeline_json
    save_pipeline_json(state_dir, pipeline_json)

    state: PipelineState = {
        "run_id": run_id,
        "state_dir": str(state_dir),
        "output_dir": str(output_dir),
        "input_path": input_path,
        "depth": depth,
        "domain_ids": pipeline_json.get("domain_ids", []),
    }

    emit({
        "event": "pipeline_init",
        "run_id": run_id,
        "input": input_path,
        "depth": depth,
        "state_dir": str(state_dir),
    })

    from know_expand.agents.base import probe_models
    await probe_models(cfg)

    graph = build_graph(cfg, auto_taxonomy=auto_taxonomy, no_pdf=no_pdf)
    compiled = graph.compile()
    await compiled.ainvoke(state)
```
</details>

**[33] tool→Bash:** `grep -rn "user.profile\|--user-profile" /Users/cyan/ccr/know-expand/tests/ 2>/dev/null | head; echo ---; grep -n "resume` — Check tests for user-profile coverage and resume_stage usage

<details><summary>[34] tool result (44 chars)</summary>

```
---
110:    resume_stage: int | None = None,
```
</details>

## Structured Output
```json
{
  "verdict": "confirmed",
  "reasoning": "Every cited code fact verifies. (1) cli.py:85 parses --user-profile but args.user_profile is never read; run_pipeline() (pipeline.py:101-110) has no profile parameter and s1_assess.run() (s1_assess.py:674-741) only skips via the pipeline.json stage marker \u2014 there is no profile-injection path. _DEFAULT_PROFILE (s1_assess.py:40) is dead code, never referenced. README:188, README:~88, and design principle 11 (README:649-655) all promise an S1 bypass that does not exist. (2) Headless mode confirmed: use_web_ipc = not sys.stdin.isatty() (s1_assess.py:685); _ask_web (s1_assess.py:369-403) blocks up to _WEB_TIMEOUT=300s per question and returns \"\" on timeout; the coverage-override loop (455-472) forces ~10-15 turns, so an unattended run stalls ~50-75 min then LLM-synthesizes a profile from empty answers (line 731) and marks the stage complete. (3) S3 confirmed: _prompt_taxonomy routes to web IPC when no TTY (s3_graph.py:119-120), polls 600s (_TAXONOMY_WEB_TIMEOUT, line 54), and on timeout defaults to lumper (line 100). No code emits pipeline_paused (state.py only has display formatting for that event name), so README:388's \"emits pipeline_paused and exits cleanly\" is false. Mitigations searched: --auto-taxonomy works for S3 (claim doesn't dispute), but no working S1 bypass exists anywhere (web UI /api/run passes no profile; no tests cover --user-profile). Trivial nuances only: the S3 timeout emits stage3_review_complete (mode=lumper) so it is \"silent\" only in that a timeout is indistinguishable from an explicit choice, and the stall floor is ~50 min rather than 40. Severity is not inflated for the objective layer: the README itself advertises autonomous/unattended operation, and the advertised bypasses are broken or fictional."
}
```

<details><summary>[38] tool result (39 chars)</summary>

```
Structured output provided successfully
```
</details>

**[39] assistant:** Verification complete — verdict submitted as confirmed.
