# Agent: verify__phantom_cli_feature_surface:_six_advertised_flags_do_nothing;_readme_d
**ID:** `agent-a493a64d7edcad60f`  **Turns:** 28

## Prompt
```
You are an adversarial verifier. A code auditor claims the following about the repo at /Users/cyan/ccr/know-expand:

TITLE: Phantom CLI/feature surface: six advertised flags do nothing; README documents subsystems that don't exist
LAYER: planning
SEVERITY: high
CLAIM: Parsed but never used: --user-profile (cli.py:85), --stage (cli.py:84), --no-bibliography-fetch (cli.py:87, zero other references — README:190 advertises 'air-gapped mode'), --no-pdf (cli.py:76, never forwarded to run_pipeline at cli.py:165-175 whose no_pdf defaults False), --human (cli.py:75). --resume is forwarded to resume_stage (pipeline.py:110) which is never read — resume works only via stage_is_complete markers, so '--resume 4' is a no-op token. --renderer typst (README:182, 857) does not exist in cli.py at all. README further claims a Crossref tier (README:137, 525, 848 — 'crossref' appears nowhere in know_expand/), DiskCache, and map_a/map_b dual map outputs (README:954-957 — S2 writes a single map_{chunk_id}.json, s2_extract.py:62). For a product, every one of these is a user-facing lie; the README is a roadmap masquerading as documentation and needs a hard reconciliation pass before any external user touches it.
FILES: /Users/cyan/ccr/know-expand/know_expand/cli.py:75-87, /Users/cyan/ccr/know-expand/know_expand/cli.py:165-175, /Users/cyan/ccr/know-expand/know_expand/pipeline.py:101-111, /Users/cyan/ccr/know-expand/README.md:160-201

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

**[6] tool→Bash:** `grep -rn "crossref\|Crossref\|CrossRef" /Users/cyan/ccr/know-expand --include="*.py" --include="*.yaml" --include="*.md"` — Search repo for crossref references

<details><summary>[7] tool result (1557 chars)</summary>

```
/Users/cyan/ccr/know-expand/README.md:137:│ Crossref → Semantic Scholar → arXiv (CS/Math only)    │
/Users/cyan/ccr/know-expand/README.md:190:  --no-bibliography-fetch   Skip Semantic Scholar / Crossref fetches (air-gapped mode)
/Users/cyan/ccr/know-expand/README.md:438:**External API rate limiting:** 8 concurrent domain fetches hit Semantic Scholar (100 req/5 min unauthenticated), Crossref, and OpenAlex simultaneously. `aiolimiter` (async token bucket) serializes at the API call level without blocking domain-level parallelism.
/Users/cyan/ccr/know-expand/README.md:524:1. Crossref REST API — journals, proceedings, books, all fields, no auth
/Users/cyan/ccr/know-expand/README.md:598:Agents without web access will hallucinate plausible-sounding papers. Verifying hallucinated titles against Crossref/Semantic Scholar yields 100% failure. The fix is preventing the hallucination by providing a bounded real bibliography before writing begins.
/Users/cyan/ccr/know-expand/README.md:606:*Consequence:* Crossref REST API and Semantic Scholar are the global citation oracles. arXiv is a fallback for CS/Math/Physics only.
/Users/cyan/ccr/know-expand/README.md:813:With `--profile local`, no API keys are required. Semantic Scholar, Crossref, and OpenAlex calls still require internet for bibliography fetch.
/Users/cyan/ccr/know-expand/README.md:848:| S4 | Bibliography fetch | Semantic Scholar + Crossref | Two-bucket (65% foundational / 35% frontier). Anchor-neighbor expansion via `/citations`. |
/Users/cyan/ccr/know-expand/config.yaml:24:  crossref:
```
</details>

**[9] tool→Read:** `/Users/cyan/ccr/know-expand/know_expand/pipeline.py` 

<details><summary>[10] tool result (5220 chars)</summary>

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

**[11] tool→Bash:** `grep -rn "no_pdf\|no-pdf\|args.human\|user_profile\|user-profile\|no_bibliography\|no-bibliography\|resume_stage\|args.s` — Search code for usage of claimed phantom flags and features

<details><summary>[12] tool result (3338 chars)</summary>

```
/Users/cyan/ccr/know-expand/know_expand/pipeline.py:25:    no_pdf: bool = False,
/Users/cyan/ccr/know-expand/know_expand/pipeline.py:70:        await s10_assemble.run(state, cfg, no_pdf=no_pdf)
/Users/cyan/ccr/know-expand/know_expand/pipeline.py:109:    no_pdf: bool = False,
/Users/cyan/ccr/know-expand/know_expand/pipeline.py:110:    resume_stage: int | None = None,
/Users/cyan/ccr/know-expand/know_expand/pipeline.py:153:    graph = build_graph(cfg, auto_taxonomy=auto_taxonomy, no_pdf=no_pdf)
/Users/cyan/ccr/know-expand/know_expand/cli.py:76:    parser.add_argument("--no-pdf", action="store_true", help="Skip PDF build")
/Users/cyan/ccr/know-expand/know_expand/cli.py:85:    parser.add_argument("--user-profile", type=Path, metavar="PATH")
/Users/cyan/ccr/know-expand/know_expand/cli.py:87:    parser.add_argument("--no-bibliography-fetch", action="store_true")
/Users/cyan/ccr/know-expand/know_expand/cli.py:174:        resume_stage=int(args.resume) if args.resume else None,
/Users/cyan/ccr/know-expand/know_expand/observe.py:804:                <input type="checkbox" id="run-no-pdf" checked />
/Users/cyan/ccr/know-expand/know_expand/observe.py:1713:  const noPdf = isResume ? true : $('run-no-pdf').checked;
/Users/cyan/ccr/know-expand/know_expand/observe.py:1724:        input, depth, auto_taxonomy: autoTax, no_pdf: noPdf,
/Users/cyan/ccr/know-expand/know_expand/observe.py:2149:        profile_path = state_dir / "user_profile.json"
/Users/cyan/ccr/know-expand/know_expand/observe.py:2509:    no_pdf = params.get("no_pdf", False)
/Users/cyan/ccr/know-expand/know_expand/observe.py:2513:    if no_pdf:
/Users/cyan/ccr/know-expand/know_expand/observe.py:2514:        args.append("--no-pdf")
/Users/cyan/ccr/know-expand/know_expand/stages/s9_prereq.py:47:def _load_user_profile(state_dir: Path) -> dict:
/Users/cyan/ccr/know-expand/know_expand/stages/s9_prereq.py:48:    """Load user_profile.json; return empty dict on any error."""
/Users/cyan/ccr/know-expand/know_expand/stages/s9_prere...[truncated]
```
</details>

**[14] tool→Bash:** `grep -n "renderer\|typst\|Typst\|diskcache\|DiskCache\|disk_cache\|map_a\|map_b" /Users/cyan/ccr/know-expand/README.md` — Search README for typst, diskcache, map_a/map_b claims

<details><summary>[15] tool result (589 chars)</summary>

```
181:  --renderer                xelatex | typst  (default: xelatex)
857:| S10 | PDF build (optional) | `Typst` v0.14+ | ~27× faster than xelatex; no TeX dependency; `--renderer typst` |
869:| Response caching | `DiskCache` | SQLite-backed for deterministic API calls; faster than Redis in local scenarios |
884:| `Redis` | Requires external process; DiskCache is sufficient |
954:│   │   ├── map_a_{chunk_id}.json               S2 Map Signal A+C
955:│   │   ├── map_a_{chunk_id}.done
956:│   │   ├── map_b_{chunk_id}.json               S2 Map Signal B
957:│   │   └── map_b_{chunk_id}.done
```
</details>

**[16] tool→Read:** `/Users/cyan/ccr/know-expand/README.md` 

<details><summary>[17] tool result (2112 chars)</summary>

```
160	## CLI reference
161	
162	```
163	know-expand <input> [options]
164	know-expand tail [run_id] [--runs-dir DIR]
165	know-expand serve [--runs-dir DIR] [--port PORT]
166	
167	Arguments:
168	  <input>                   File path (.txt .md .pdf), URL, or - (stdin)
169	
170	Subcommands:
171	  tail [run_id]             Stream pipeline events to stdout. Reads
172	                            runs/<run_id>/logs/events.jsonl; uses latest
173	                            run if run_id omitted. Pipe-friendly, greppable.
174	  serve                     Launch HTTP dashboard at localhost:7842.
175	                            Left stage rail + full-width detail pane + run
176	                            drawer. Interview Q&A and taxonomy review IPC
177	                            handled in-browser. ETag-based 2s polling.
178	
179	Output / mode:
180	  --no-pdf                  Skip PDF build, output Markdown only
181	  --renderer                xelatex | typst  (default: xelatex)
182	
183	Pipeline control:
184	  --depth                   survey | standard | deep  (default: standard)
185	  --status                  Emit pipeline_status from pipeline.json; no work runs
186	  --resume STAGE            Resume from stage N (e.g. --resume 4)
187	  --stage STAGE             Run only stage N then stop
188	  --user-profile <path>     Load pre-computed UserProfile JSON; skips S1
189	  --auto-taxonomy           Skip editor review; orchestrating LLM decides taxonomy
190	  --no-bibliography-fetch   Skip Semantic Scholar / Crossref fetches (air-gapped mode)
191	  --primary-model MODEL     Prepend MODEL to every role's fallback list
192	
193	Paths:
194	  --runs-dir DIR            Base directory for all runs  (default: ./runs)
195	
196	Concurrency:
197	  --concurrency N           Override config concurrency.default (default: 8)
198	
199	Run identity:
200	  --run-id ID               Reuse an existing run ID (for --resume)
201	```
202	
203	Every stage emits structured JSON Lines to stdout. The o...[truncated]
```
</details>

**[18] tool→Bash:** `sed -n '55,70p' /Users/cyan/ccr/know-expand/know_expand/stages/s2_extract.py; echo ---; grep -rn "semanticscholar\|api.s` — Check s2_extract map output, bibliography sources, config crossref section

<details><summary>[19] tool result (1075 chars)</summary>

```
) -> list[TermOccurrence]:
    chunk = json.loads(chunk_path.read_text())
    chunk_id = chunk["chunk_id"]
    text = chunk["text"]
    nlp_text = _strip_code_from_text(text)  # code-free version for spaCy + KeyBERT
    t0 = time.monotonic()

    out_path = map_dir / f"map_{chunk_id}.json"
    done_path = out_path.with_suffix(".json.done")
    if done_path.exists():
        cached = json.loads(out_path.read_text())
        emit({"event": "chunk_map_cached", "chunk_id": chunk_id, "term_count": len(cached)})
        return cached

    emit({"event": "chunk_map_start", "chunk_id": chunk_id, "text_len": len(text)})

---
/Users/cyan/ccr/know-expand/know_expand/bibliography.py:92:_SS_BASE = "https://api.semanticscholar.org/graph/v1/paper/search"
/Users/cyan/ccr/know-expand/know_expand/bibliography.py:93:_SS_PAPER_BASE = "https://api.semanticscholar.org/graph/v1/paper"
---
rate_limits:
  semantic_scholar:
    max_rate: 1
    time_period: 1    # authenticated key: 1 req/sec
  crossref:
    max_rate: 1
    time_period: 1
  openalex:
    max_rate: 10
    time_period: 1
```
</details>

**[21] tool→Bash:** `grep -n "diskcache\|typst" /Users/cyan/ccr/know-expand/pyproject.toml; echo ---; sed -n '2495,2540p' /Users/cyan/ccr/kno` — Check pyproject deps, web UI pipeline spawn args, rate limiter usage

<details><summary>[22] tool result (2823 chars)</summary>

```
---
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
---
/Users/cyan/ccr/know-expand/know_expand/bibliography.py:10:from aiolimiter import AsyncLimiter
/Users/cyan/ccr/know-expand/know_expand/bibliography.py:65:_ss_limiter: AsyncLimiter | None = None
/Users/cyan/c...[truncated]
```
</details>

## Structured Output
```json
{
  "verdict": "confirmed",
  "reasoning": "Every sub-claim verified against the code. (1) cli.py parses --human (75), --no-pdf (76), --stage (84), --user-profile (85), --no-bibliography-fetch (87); repo-wide grep shows args.human/args.stage/args.user_profile/args.no_bibliography_fetch consumed nowhere. (2) The run_pipeline() call at cli.py:166-175 omits no_pdf, and pipeline.py:109 defaults it False \u2014 the downstream s10_assemble plumbing exists but is unreachable from the CLI. This is worse than claimed: observe.py:2509-2514 spawns the CLI with --no-pdf when the dashboard checkbox (checked by default, observe.py:804) is set, so the web UI's skip-PDF option is silently broken too. (3) resume_stage is accepted at pipeline.py:110 but never read in the function body (111-155); resume works solely via pipeline.json stage markers, making the --resume stage number a no-op token as claimed. (4) --renderer/typst (README:181, 857) does not exist in cli.py, code, or pyproject.toml. (5) Crossref appears in README at 137/190/438/524/598/606/813/848 but nowhere in know_expand/*.py; bibliography.py uses only api.semanticscholar.org (config.yaml:24 has an unused rate_limits.crossref stanza \u2014 dead config, not mitigation). (6) DiskCache (README:869, 884) absent from code and dependencies. (7) README:954-957 documents map_a_/map_b_ dual outputs; s2_extract.py:62 writes a single map_{chunk_id}.json. Severity 'high' at the planning/documentation layer is justified: half the documented CLI surface is inert and one gap breaks a default-on web UI option. Only cosmetic imprecision: Crossref citation is README:524, not 525."
}
```

<details><summary>[26] tool result (39 chars)</summary>

```
Structured output provided successfully
```
</details>

**[27] assistant:** Verification complete — verdict: confirmed.
