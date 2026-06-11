# Agent: verify__dead_and_broken_cli_surface:_--resume_is_a_no-op,_--no-pdf_is_silently
**ID:** `agent-a27b035893277419b`  **Turns:** 37

## Prompt
```
You are an adversarial verifier. A code auditor claims the following about the repo at /Users/cyan/ccr/know-expand:

TITLE: Dead and broken CLI surface: --resume is a no-op, --no-pdf is silently dropped, four more flags do nothing
LAYER: execution
SEVERITY: high
CLAIM: (1) --resume STAGE: cli.py:174 passes resume_stage to run_pipeline, which accepts it (pipeline.py:110) and never references it — the parameter is dead. Worse, README:22-24 and 186 present 'know-expand --resume 4' as the canonical recovery command, but without --run-id it generates a fresh UUID (cli.py:125) and starts from scratch in a new directory. Actual resume = --run-id + the per-stage skip checks; --resume itself changes nothing. (2) --no-pdf: parsed (cli.py:76) but never forwarded — the run_pipeline call at cli.py:166-175 omits no_pdf, so it defaults False. The web UI dutifully passes --no-pdf (observe.py:2513-2514) and it is dropped on the floor; PDF always builds. (3) --stage, --user-profile, --no-bibliography-fetch, --human (cli.py:75-87): parsed, documented in README:187+, referenced nowhere in the codebase (grep confirms). Six documented flags, zero or broken implementations.
FILES: /Users/cyan/ccr/know-expand/know_expand/cli.py:74-107, /Users/cyan/ccr/know-expand/know_expand/cli.py:166-175, /Users/cyan/ccr/know-expand/know_expand/pipeline.py:101-155

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

**[6] tool→Read:** `/Users/cyan/ccr/know-expand/know_expand/pipeline.py` 

<details><summary>[7] tool result (2302 chars)</summary>

```
90	    graph.add_edge("audit", "research")
91	    graph.add_edge("research", "align")
92	    graph.add_edge("align", "synthesize")
93	    graph.add_edge("synthesize", "verify")
94	    graph.add_edge("verify", "prereq")
95	    graph.add_edge("prereq", "assemble")
96	    graph.add_edge("assemble", END)
97	
98	    return graph
99	
100	
101	async def run_pipeline(
102	    input_path: str,
103	    state_dir: Path,
104	    output_dir: Path,
105	    depth: str,
106	    cfg: Config,
107	    auto_taxonomy: bool = False,
108	    primary_model: str | None = None,
109	    no_pdf: bool = False,
110	    resume_stage: int | None = None,
111	) -> None:
112	    import uuid
113	
114	    if primary_model:
115	        for role in cfg.models:
116	            models = cfg.models[role]
117	            if primary_model not in models:
118	                cfg.models[role] = [primary_model] + models
119	            else:
120	                cfg.models[role] = [primary_model] + [m for m in models if m != primary_model]
121	
122	    pipeline_json = load_pipeline_json(state_dir)
123	    run_id = pipeline_json.get("run_id") or f"run_{uuid.uuid4().hex[:8]}"
124	
125	    # Persist run_id and input_path into pipeline.json immediately so the
126	    # web UI can show them and use input_path for resume without waiting
127	    # for the first stage to complete.
128	    pipeline_json["run_id"] = run_id
129	    pipeline_json.setdefault("input_path", input_path)
130	    from know_expand.state import save_pipeline_json
131	    save_pipeline_json(state_dir, pipeline_json)
132	
133	    state: PipelineState = {
134	        "run_id": run_id,
135	        "state_dir": str(state_dir),
136	        "output_dir": str(output_dir),
137	        "input_path": input_path,
138	        "depth": depth,
139	        "domain_ids": pipeline_json.get("domain_ids", []),
140	    }
141	
142	    emit({
143	        "event": "pipeline_init",
144	        "run_id": run_id,
145	        "input": input_path,
146	        "depth": depth,
147...[truncated]
```
</details>

**[10] tool→Bash:** `grep -rn "resume_stage\|no_pdf\|no-pdf" --include="*.py" know_expand/ | grep -v ".pyc"` — Find all references to resume_stage and no_pdf in Python code

<details><summary>[11] tool result (1266 chars)</summary>

```
know_expand/observe.py:804:                <input type="checkbox" id="run-no-pdf" checked />
know_expand/observe.py:1713:  const noPdf = isResume ? true : $('run-no-pdf').checked;
know_expand/observe.py:1724:        input, depth, auto_taxonomy: autoTax, no_pdf: noPdf,
know_expand/observe.py:2509:    no_pdf = params.get("no_pdf", False)
know_expand/observe.py:2513:    if no_pdf:
know_expand/observe.py:2514:        args.append("--no-pdf")
know_expand/cli.py:76:    parser.add_argument("--no-pdf", action="store_true", help="Skip PDF build")
know_expand/cli.py:174:        resume_stage=int(args.resume) if args.resume else None,
know_expand/pipeline.py:25:    no_pdf: bool = False,
know_expand/pipeline.py:70:        await s10_assemble.run(state, cfg, no_pdf=no_pdf)
know_expand/pipeline.py:109:    no_pdf: bool = False,
know_expand/pipeline.py:110:    resume_stage: int | None = None,
know_expand/pipeline.py:153:    graph = build_graph(cfg, auto_taxonomy=auto_taxonomy, no_pdf=no_pdf)
know_expand/stages/s10_assemble.py:395:async def run(state: PipelineState, cfg: Config, no_pdf: bool = False) -> None:
know_expand/stages/s10_assemble.py:555:    if no_pdf:
know_expand/stages/s10_assemble.py:556:        emit({"event": "s7_pdf_skipped", "reason": "no_pdf_flag"})
```
</details>

**[12] tool→Bash:** `grep -rn "args\.stage\b\|args\.user_profile\|args\.no_bibliography_fetch\|args\.human\b\|user_profile_path\|no_bibliogra` — Find usages of stage, user_profile, no_bibliography_fetch, human flags

<details><summary>[13] tool result (31 chars)</summary>

```
(Bash completed with no output)
```
</details>

**[16] tool→Bash:** `sed -n '15,30p' /Users/cyan/ccr/know-expand/README.md; echo ---; sed -n '180,200p' /Users/cyan/ccr/know-expand/README.md` — Read README lines 15-30 and 180-200

<details><summary>[17] tool result (1320 chars)</summary>

```
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

---
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

Concurrency:
  --concurrency N           Override config concurrency.default (default: 8)

Run identity:
  --run-id ID               Reuse an existing run ID (for --resume)
```
</details>

**[18] tool→Bash:** `grep -rn "resume\|--stage\|user-profile\|no-bibliography\|--human\|--no-pdf" /Users/cyan/ccr/know-expand/README.md | hea` — Find flag documentation in README

<details><summary>[19] tool result (4318 chars)</summary>

```
/Users/cyan/ccr/know-expand/README.md:24:uv run know-expand --resume 4
/Users/cyan/ccr/know-expand/README.md:156:Stages skip automatically if already complete (`stage_is_complete()` checks `pipeline.json`). Resume a stopped run via the web UI Resume button or `--resume N`.
/Users/cyan/ccr/know-expand/README.md:180:  --no-pdf                  Skip PDF build, output Markdown only
/Users/cyan/ccr/know-expand/README.md:186:  --resume STAGE            Resume from stage N (e.g. --resume 4)
/Users/cyan/ccr/know-expand/README.md:187:  --stage STAGE             Run only stage N then stop
/Users/cyan/ccr/know-expand/README.md:188:  --user-profile <path>     Load pre-computed UserProfile JSON; skips S1
/Users/cyan/ccr/know-expand/README.md:190:  --no-bibliography-fetch   Skip Semantic Scholar / Crossref fetches (air-gapped mode)
/Users/cyan/ccr/know-expand/README.md:200:  --run-id ID               Reuse an existing run ID (for --resume)
/Users/cyan/ccr/know-expand/README.md:208:{"event": "quota_exhausted", "stage": 5, "domain_id": "inference_systems", "resume_command": "know-expand --resume 5"}
/Users/cyan/ccr/know-expand/README.md:359:**Position:** After S2, before S3. Runs automatically — answered via the web UI (`serve`) or TTY. Pass `--user-profile <path>` to skip entirely with a pre-computed profile.
/Users/cyan/ccr/know-expand/README.md:388:*Default:* pipeline emits `pipeline_paused` and exits cleanly. User reviews proposals in `runs/{id}/state/audit/taxonomy_a.json` and `taxonomy_b.json`, edits `taxonomy.json`, and resumes. In the web UI, taxonomy proposals appear in a dedicated review pane.
/Users/cyan/ccr/know-expand/README.md:470:**Prompt sizing and caching:** persona prompts are split into a shared context block (domain info, reader profile, graph nodes, gap analysis, bibliography, knowledge sources — marked `cache_control: {type: ephemeral}`) and a task block (persona-specific role and output instruction). The shared block exceeds Anthropic's 4,096-token caching th...[truncated]
```
</details>

**[20] tool→Bash:** `ls /Users/cyan/ccr/know-expand/tests/ 2>/dev/null; grep -rln "resume\|no_pdf\|no-pdf" /Users/cyan/ccr/know-expand/tests/` — List tests and find resume/no_pdf coverage

<details><summary>[21] tool result (324 chars)</summary>

```
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
/Users/cyan/ccr/know-expand/tests/test_s7_assemble.py
```
</details>

**[24] tool→Bash:** `sed -n '2490,2540p' /Users/cyan/ccr/know-expand/know_expand/observe.py` — Read _spawn_pipeline arg construction in observe.py

<details><summary>[25] tool result (1915 chars)</summary>

```
    cli_path = Path(sys.executable).parent / "know-expand"
    if cli_path.exists():
        cmd: list = [str(cli_path)]
    else:
        cmd = [sys.executable, "-m", "know_expand.cli"]

    input_path = params.get("input", "")
    # For resume, fall back to input_path stored in pipeline.json if not supplied
    if is_resume and not input_path:
        pipeline_path = state_dir / "pipeline.json"
        if pipeline_path.exists():
            try:
                input_path = json.loads(pipeline_path.read_text()).get("input_path", "")
            except Exception:
                pass

    depth = params.get("depth", "standard")
    auto_taxonomy = params.get("auto_taxonomy", False)
    no_pdf = params.get("no_pdf", False)
    args = [input_path, "--runs-dir", str(runs_dir), "--run-id", run_id, "--depth", str(depth)]
    if auto_taxonomy:
        args.append("--auto-taxonomy")
    if no_pdf:
        args.append("--no-pdf")

    # Reset SIGCHLD to SIG_DFL in the child before exec.  The server sets
    # SIGCHLD=SIG_IGN to prevent zombie accumulation, but SIG_IGN survives
    # fork+exec (POSIX), so without this reset pandoc can't wait() for xelatex
    # and fails with "No child processes".
    import signal as _sig
    proc = subprocess.Popen(
        cmd + args,
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=os.environ.copy(),
        preexec_fn=lambda: _sig.signal(_sig.SIGCHLD, _sig.SIG_DFL),
    )
    (state_dir / "pipeline_pid").write_text(str(proc.pid))
    return proc.pid


def _get_qa_state(state_dir: Path) -> dict:
    """Return current Q&A state: current question, history, completion flag."""
    complete_flag = state_dir / "qa_complete"
    queue_file = state_dir / "qa_queue.jsonl"
    answers_file = state_dir / "qa_answers.jsonl"

    def _read_jsonl(path: Path) -> list[dict]:
        if not path.exists():
```
</details>

**[26] tool→Bash:** `grep -n "stage_is_complete" /Users/cyan/ccr/know-expand/know_expand/state.py /Users/cyan/ccr/know-expand/know_expand/pip` — Check stage skip mechanism and web UI resume JS

<details><summary>[27] tool result (1141 chars)</summary>

```
/Users/cyan/ccr/know-expand/know_expand/state.py:148:def stage_is_complete(state_dir: Path, stage: str | int) -> bool:
      // Prior run predates input_path tracking — fall back to text field
      setResumeMode(false);
      $('run-input').focus();
      $('run-input').placeholder = 'enter input path to resume…';
    } else {
      $('run-input').focus();
    }
    return;
  }
  // In resume mode depth/autoTax/noPdf come from the prior run — pass
  // sensible defaults; the pipeline already has them baked into state.
  const depth = isResume ? 'standard' : $('run-depth').value;
  const autoTax = isResume ? true : $('run-auto-tax').checked;
  const noPdf = isResume ? true : $('run-no-pdf').checked;
  const resume = isResume;

  $('run-start-btn').disabled = true;
  $('run-start-btn').textContent = 'starting…';

  try {
    await fetch('/api/run', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        input, depth, auto_taxonomy: autoTax, no_pdf: noPdf,
        resume,
      }),
    });

    // Invalidate status cache so stage list refreshes
    lastStatusEtag = '';
```
</details>

**[30] tool→Bash:** `sed -n '170,182p' /Users/cyan/ccr/know-expand/README.md; echo ---; grep -rn "human" /Users/cyan/ccr/know-expand/README.m` — Check README --human docs and stage_is_complete usage

<details><summary>[31] tool result (3229 chars)</summary>

```
Subcommands:
  tail [run_id]             Stream pipeline events to stdout. Reads
                            runs/<run_id>/logs/events.jsonl; uses latest
                            run if run_id omitted. Pipe-friendly, greppable.
  serve                     Launch HTTP dashboard at localhost:7842.
                            Left stage rail + full-width detail pane + run
                            drawer. Interview Q&A and taxonomy review IPC
                            handled in-browser. ETag-based 2s polling.

Output / mode:
  --no-pdf                  Skip PDF build, output Markdown only
  --renderer                xelatex | typst  (default: xelatex)

---
/Users/cyan/ccr/know-expand/README.md:392:This is the highest-consequence single decision in the pipeline — every downstream stage is partitioned by the taxonomy. One minute of review (human or LLM) here saves hours of downstream garbage.
/Users/cyan/ccr/know-expand/README.md:528:`[NEEDS_CITATION]` markers are logged to `state/audit/needs_citation.md` and flagged for human review. They do not block the build.
/Users/cyan/ccr/know-expand/README.md:588:*Consequence:* S3 is two phases with a human (or LLM) checkpoint between them.
/Users/cyan/ccr/know-expand/README.md:627:| S3 Graph | No critic | Decision handled by human review or `--auto-taxonomy` |
/Users/cyan/ccr/know-expand/README.md:650:Exactly two human-facing interactions are permitted, both at the very beginning of the pipeline, both skippable via flags:
---
/Users/cyan/ccr/know-expand/know_expand/stages/s1_assess.py:32:    stage_is_complete,
/Users/cyan/ccr/know-expand/know_expand/stages/s1_assess.py:678:    if stage_is_complete(state_dir, 1):
/Users/cyan/ccr/know-expand/know_expand/stages/s0_ingest.py:11:from know_expand.state import PipelineState, emit, mark_stage_complete, stage_is_complete
/Users/cyan/ccr/know-expand/know_expand/stages/s0_ingest.py:36:    if stage_is_complete(state_dir, 0):
/Users/cyan/ccr/know-expand/know_expand/stages/s10_assembl...[truncated]
```
</details>

## Structured Output
```json
{
  "verdict": "confirmed",
  "reasoning": "Every material assertion verifies. (1) --resume: cli.py:174 forwards resume_stage to run_pipeline (pipeline.py:110), which never references it \u2014 repo-wide grep finds only the call site and the parameter declaration, so it is dead. Without --run-id, cli.py:125 (run_id = args.run_id or new_run_id()) creates a fresh run directory with an empty pipeline.json, so README:24's canonical 'know-expand --resume 4' silently starts the whole pipeline over in a new directory. Actual resume is --run-id plus the per-stage stage_is_complete() guards (state.py:148, called at the top of every stage), exactly as the claim states; the web UI resume works only because observe.py:2510 passes --run-id. (2) --no-pdf: parsed at cli.py:76 but the run_pipeline call at cli.py:166-175 omits it, so it defaults False and the PDF always builds; downstream plumbing (pipeline.py:109/153, s10_assemble.py:555-556) is functional but unreachable from the CLI. observe.py:2513-2514 appends --no-pdf to the spawned subprocess, and observe.py:1713 hardcodes noPdf=true on every web resume \u2014 all dropped, making the impact slightly worse than claimed. (3) --human, --stage, --user-profile, --no-bibliography-fetch are parsed (cli.py:75-87) and referenced nowhere (zero grep hits for args.human/args.stage/args.user_profile/args.no_bibliography_fetch; run_pipeline has no matching parameters); README:187-190 documents --stage/--user-profile/--no-bibliography-fetch as functional. Sole imprecision: --human is parsed-and-dead but not actually listed in the README flag reference; offsetting this, README:181 documents a --renderer flag that is not parsed at all, so the doc/CLI mismatch is broader than claimed. Severity 'high' is justified: the documented recovery command for an expensive multi-hour LLM pipeline silently re-runs everything from scratch, and no mitigating code exists anywhere in the repo."
}
```

<details><summary>[35] tool result (39 chars)</summary>

```
Structured output provided successfully
```
</details>

**[36] assistant:** Verification complete. The verdict and full reasoning were returned via structured output: the claim is **confirmed** on all three parts, with one trivial imprecision (`--human` is parsed-and-dead but not actually listed in the README flag reference, while README additionally documents a non-existent `--renderer` flag).
