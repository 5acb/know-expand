# Agent: verify__web-ui_resume_silently_downgrades_a_deep_run_to_standard_depth
**ID:** `agent-a0f066dcaf4cb68e3`  **Turns:** 34

## Prompt
```
You are an adversarial verifier. A code auditor claims the following about the repo at /Users/cyan/ccr/know-expand:

TITLE: Web-UI resume silently downgrades a deep run to standard depth
LAYER: execution
SEVERITY: high
CLAIM: observe.py JS hardcodes depth='standard' and noPdf=true on resume, with the comment 'the pipeline already has them baked into state' (observe.py:~1709-1713). That is false: depth is never persisted — run_pipeline writes only run_id and input_path into pipeline.json (pipeline.py:128-131), and depth re-enters PipelineState from the CLI --depth flag on every invocation (cli.py:170, pipeline.py:138). So resuming a crashed --depth deep run re-executes all remaining stages (S5 research caps, bibliography totals, prompt sizing) at standard depth with no warning, producing a document that silently mixes depths. The forced no_pdf=true on resume only fails to break PDF output because of the separate cli.py bug that drops --no-pdf entirely — two bugs cancelling.
FILES: /Users/cyan/ccr/know-expand/know_expand/observe.py:1709-1713, /Users/cyan/ccr/know-expand/know_expand/pipeline.py:128-140

Read the actual code and try to REFUTE this claim. Check: does the cited code actually behave as claimed? Is the severity inflated? Is there mitigating code elsewhere (search the repo)? If the claim is materially correct, confirm it. Default to refuted if the evidence is ambiguous.
```

**[4] tool→Bash:** `grep -n "depth" /Users/cyan/ccr/know-expand/know_expand/observe.py | head -50` — Find depth references in observe.py

<details><summary>[5] tool result (542 chars)</summary>

```
87:        return f"{ts}  run {evt.get('run_id')}  depth={evt.get('depth')}"
780:            <select id="run-depth" class="rf-select">
1117:    ['effective depth', p.effective_depth],
1709:  // In resume mode depth/autoTax/noPdf come from the prior run — pass
1711:  const depth = isResume ? 'standard' : $('run-depth').value;
1724:        input, depth, auto_taxonomy: autoTax, no_pdf: noPdf,
2507:    depth = params.get("depth", "standard")
2510:    args = [input_path, "--runs-dir", str(runs_dir), "--run-id", run_id, "--depth", str(depth)]
```
</details>

**[6] tool→Read:** `/Users/cyan/ccr/know-expand/know_expand/pipeline.py` 

<details><summary>[7] tool result (1972 chars)</summary>

```
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
147	        "state_dir": str(state_dir),
148	    })
149	
150	    from know_expand.agents.base import probe_models
151	    await probe_models(cfg)
152	
153	    graph = build_graph(cfg, auto_taxonomy=auto_taxonomy, no_pdf=no_pdf)
154	    compiled = graph.compile()
155	    await compiled.ainvoke(state)
156	
```
</details>

**[8] tool→Read:** `/Users/cyan/ccr/know-expand/know_expand/observe.py` 

<details><summary>[9] tool result (2080 chars)</summary>

```
1690	    const d = await (await fetch('/api/keys')).json();
1691	    renderProvGrid(d);
1692	  } catch {}
1693	}
1694	
1695	async function startRun() {
1696	  const isResume = $('run-resume').value === '1';
1697	  const input = isResume ? _resumeInputPath : $('run-input').value.trim();
1698	  if (!input) {
1699	    if (isResume) {
1700	      // Prior run predates input_path tracking — fall back to text field
1701	      setResumeMode(false);
1702	      $('run-input').focus();
1703	      $('run-input').placeholder = 'enter input path to resume…';
1704	    } else {
1705	      $('run-input').focus();
1706	    }
1707	    return;
1708	  }
1709	  // In resume mode depth/autoTax/noPdf come from the prior run — pass
1710	  // sensible defaults; the pipeline already has them baked into state.
1711	  const depth = isResume ? 'standard' : $('run-depth').value;
1712	  const autoTax = isResume ? true : $('run-auto-tax').checked;
1713	  const noPdf = isResume ? true : $('run-no-pdf').checked;
1714	  const resume = isResume;
1715	
1716	  $('run-start-btn').disabled = true;
1717	  $('run-start-btn').textContent = 'starting…';
1718	
1719	  try {
1720	    await fetch('/api/run', {
1721	      method: 'POST',
1722	      headers: {'Content-Type': 'application/json'},
1723	      body: JSON.stringify({
1724	        input, depth, auto_taxonomy: autoTax, no_pdf: noPdf,
1725	        resume,
1726	      }),
1727	    });
1728	
1729	    // Invalidate status cache so stage list refreshes
1730	    lastStatusEtag = '';
1731	
1732	    // Always start in running mode; pollQa/pollTaxonomy switch reactively
1733	    setRunMode('running');
1734	
1735	    // Show all-events view so user sees live log immediately
1736	    $('sl-all-events').click();
1737	  } catch(e) {
1738	    $('run-start-btn').disabled = false;
1739	    $('run-start-btn').textContent = isResume ? '▶ Resume Run' : '▶ Start Run';
1740	  }
1741	}
1742	
1743	// File browser
1744	let fbOpen = false;
1745	let fbCwd = null;
1746	
1747	function...[truncated]
```
</details>

**[10] tool→Bash:** `grep -n "no_pdf\|no-pdf\|noPdf" /Users/cyan/ccr/know-expand/know_expand/cli.py /Users/cyan/ccr/know-expand/know_expand/o` — Find no_pdf handling across CLI/observe/pipeline

<details><summary>[11] tool result (1270 chars)</summary>

```
/Users/cyan/ccr/know-expand/know_expand/pipeline.py:25:    no_pdf: bool = False,
/Users/cyan/ccr/know-expand/know_expand/pipeline.py:70:        await s10_assemble.run(state, cfg, no_pdf=no_pdf)
/Users/cyan/ccr/know-expand/know_expand/pipeline.py:109:    no_pdf: bool = False,
/Users/cyan/ccr/know-expand/know_expand/pipeline.py:153:    graph = build_graph(cfg, auto_taxonomy=auto_taxonomy, no_pdf=no_pdf)
/Users/cyan/ccr/know-expand/know_expand/cli.py:76:    parser.add_argument("--no-pdf", action="store_true", help="Skip PDF build")
/Users/cyan/ccr/know-expand/know_expand/observe.py:804:                <input type="checkbox" id="run-no-pdf" checked />
/Users/cyan/ccr/know-expand/know_expand/observe.py:1709:  // In resume mode depth/autoTax/noPdf come from the prior run — pass
/Users/cyan/ccr/know-expand/know_expand/observe.py:1713:  const noPdf = isResume ? true : $('run-no-pdf').checked;
/Users/cyan/ccr/know-expand/know_expand/observe.py:1724:        input, depth, auto_taxonomy: autoTax, no_pdf: noPdf,
/Users/cyan/ccr/know-expand/know_expand/observe.py:2509:    no_pdf = params.get("no_pdf", False)
/Users/cyan/ccr/know-expand/know_expand/observe.py:2513:    if no_pdf:
/Users/cyan/ccr/know-expand/know_expand/observe.py:2514:        args.append("--no-pdf")
```
</details>

**[13] tool→Read:** `/Users/cyan/ccr/know-expand/know_expand/cli.py` 

<details><summary>[14] tool result (6752 chars)</summary>

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

**[17] tool→Read:** `/Users/cyan/ccr/know-expand/know_expand/observe.py` 

<details><summary>[18] tool result (2966 chars)</summary>

```
2480	    # Resume run: preserve markers so only incomplete stages run.
2481	    if not is_resume:
2482	        pipeline_path = state_dir / "pipeline.json"
2483	        if pipeline_path.exists():
2484	            try:
2485	                data = json.loads(pipeline_path.read_text())
2486	                data["stages"] = {}
2487	                pipeline_path.write_text(json.dumps(data, indent=2))
2488	            except Exception:
2489	                pass
2490	
2491	    cli_path = Path(sys.executable).parent / "know-expand"
2492	    if cli_path.exists():
2493	        cmd: list = [str(cli_path)]
2494	    else:
2495	        cmd = [sys.executable, "-m", "know_expand.cli"]
2496	
2497	    input_path = params.get("input", "")
2498	    # For resume, fall back to input_path stored in pipeline.json if not supplied
2499	    if is_resume and not input_path:
2500	        pipeline_path = state_dir / "pipeline.json"
2501	        if pipeline_path.exists():
2502	            try:
2503	                input_path = json.loads(pipeline_path.read_text()).get("input_path", "")
2504	            except Exception:
2505	                pass
2506	
2507	    depth = params.get("depth", "standard")
2508	    auto_taxonomy = params.get("auto_taxonomy", False)
2509	    no_pdf = params.get("no_pdf", False)
2510	    args = [input_path, "--runs-dir", str(runs_dir), "--run-id", run_id, "--depth", str(depth)]
2511	    if auto_taxonomy:
2512	        args.append("--auto-taxonomy")
2513	    if no_pdf:
2514	        args.append("--no-pdf")
2515	
2516	    # Reset SIGCHLD to SIG_DFL in the child before exec.  The server sets
2517	    # SIGCHLD=SIG_IGN to prevent zombie accumulation, but SIG_IGN survives
2518	    # fork+exec (POSIX), so without this reset pandoc can't wait() for xelatex
2519	    # and fails with "No child processes".
2520	    import signal as _sig
2521	    proc = subprocess.Popen(
2522	        cmd + args,
2523	        start_new_session=True,
2524	        stdout=subprocess.DEVNULL,
2525	        s...[truncated]
```
</details>

**[19] tool→Bash:** `grep -rn "depth" /Users/cyan/ccr/know-expand/know_expand/state.py /Users/cyan/ccr/know-expand/know_expand/pipeline.py | ` — Search for depth persistence in state and stages

<details><summary>[20] tool result (1057 chars)</summary>

```
/Users/cyan/ccr/know-expand/know_expand/pipeline.py:105:    depth: str,
/Users/cyan/ccr/know-expand/know_expand/pipeline.py:138:        "depth": depth,
/Users/cyan/ccr/know-expand/know_expand/pipeline.py:146:        "depth": depth,
/Users/cyan/ccr/know-expand/know_expand/state.py:103:    depth: str           # survey | standard | deep
/Users/cyan/ccr/know-expand/know_expand/state.py:217:            return f"{ts}     domain {ev.get('domain_id')}: fetching bibliography (depth={ev.get('depth')})"
---
/Users/cyan/ccr/know-expand/know_expand/stages/s1_assess.py:244:                                "30 min", "2 hr", "days", "depth"],
/Users/cyan/ccr/know-expand/know_expand/stages/s5_research.py:964:    depth = state["depth"]
/Users/cyan/ccr/know-expand/know_expand/stages/s4_audit.py:284:    emit({"event": "domain_fetch_start", "domain_id": domain_id, "depth": depth})
/Users/cyan/ccr/know-expand/know_expand/stages/s4_audit.py:399:    depth = state["depth"]
/Users/cyan/ccr/know-expand/know_expand/stages/s7_synthesize.py:303:    depth = state["depth"]
```
</details>

**[23] tool→Bash:** `grep -rn "depth" /Users/cyan/ccr/know-expand/know_expand/ --include="*.py" | grep -v "test" | grep -iv "in-depth\|depth-` — Check for depth persistence and resume_stage usage

<details><summary>[24] tool result (107 chars)</summary>

```
=== exit 1 ===
/Users/cyan/ccr/know-expand/know_expand/pipeline.py:110:    resume_stage: int | None = None,
```
</details>

**[27] tool→Bash:** `grep -n "skip\|exists\|already\|summary_" /Users/cyan/ccr/know-expand/know_expand/stages/s5_research.py | head -25; echo` — Check S5 per-domain skip logic and depth usage in S4/S5

<details><summary>[28] tool result (3527 chars)</summary>

```
123:   One concrete real-world example. Why it exists and what problem it solves.
442:        _logger.warning("s4 bibliography expansion: SS unavailable, skipping (%s)", exc)
443:        emit({"event": "s5_bibliography_expansion_skipped", "domain_id": domain_id, "reason": str(exc)[:120]})
500:    if new_papers and bib_path.exists():
533:def _domain_summary_to_markdown(summary: DomainSummary) -> str:
612:                 "Define specialist terms, skip basic general computing concepts. "
615:                        "academic terms but skip obvious practitioner knowledge. "
641:        calibration += f" The reader already understands {known_str} — do not re-explain these."
664:    if sentinel.exists():
668:            "skipped": True,
669:            "reason": "sentinel_exists",
671:        summary_path = summaries_dir / f"summary_{domain_id}.json"
672:        if summary_path.exists():
673:            return DomainSummary.model_validate(json.loads(summary_path.read_text()))
674:        # Reconstruct minimal summary if only sentinel exists
687:    if bib_path.exists():
694:    if sources_cache.exists():
920:    summary_path = summaries_dir / f"summary_{domain_id}.json"
921:    atomic_write(summary_path, final_summary.model_dump_json(indent=2))
950:        emit({"event": "stage_skipped", "stage": 5, "reason": "already_complete"})
962:    gap_md = gap_md_path.read_text() if gap_md_path.exists() else ""
970:    if profile_path.exists():
---
    taxonomy = json.loads((state_dir / "taxonomy.json").read_text())
    domains = taxonomy["domains"]

    graph_data = json.loads((state_dir / "graph.json").read_text())
    nodes = graph_data.get("nodes", [])

    gap_md_path = audit_dir / "gap_analysis.md"
    gap_md = gap_md_path.read_text() if gap_md_path.exists() else ""

    depth = state["depth"]
    router = make_router("researcher", cfg)
    critic_router = make_router("researcher", cfg, thinking_budget=cfg.critic_thinking.budget_tokens)

    profile_path = state_dir / "user_...[truncated]
```
</details>

## Structured Output
```json
{
  "verdict": "confirmed",
  "reasoning": "Every element of the claim checks out against the code. (1) observe.py:1709-1713 hardcodes depth='standard', autoTax=true, noPdf=true when isResume, with exactly the quoted comment; the server handler (observe.py:2507-2514) forwards the POSTed depth straight into the spawned CLI as `--depth standard`. (2) The comment's premise is false: pipeline.py:128-131 persists only run_id and input_path into pipeline.json; a repo-wide search finds no persistence of depth anywhere; state[\"depth\"] is rebuilt from the CLI --depth flag on every invocation (cli.py:80/170, pipeline.py:138), and graph.compile() uses no LangGraph checkpointer, so nothing is \"baked into state\". The resume_stage parameter (pipeline.py:110) is dead code; resume is purely stage markers. (3) Depth materially drives remaining stages: bibliography.py:363 (cfg.bibliography.depth_totals[depth]), s4_audit.py:273/319 (adversarial rounds), s5_research.py:964+ (research caps per domain), s7_synthesize.py:303. The mixed-depth outcome is even stronger than claimed: S5 has per-domain sentinel skips (s5_research.py:664-673), so a deep run crashing mid-S5 and resumed via web UI produces some deep domains and some standard domains in one document, with no warning or event. (4) The two-bugs-cancelling detail is verified: cli.py:76 defines --no-pdf but the run_pipeline call at cli.py:166-175 omits no_pdf=args.no_pdf, so the flag is a no-op and the forced no_pdf=true on resume has no effect. No mitigating code exists anywhere. Severity high is appropriate: CLAUDE.md documents the web UI Resume button as a primary resume mechanism, and the bug silently degrades the core output quality. Bonus confirmation of the same pattern: autoTax forced true on resume would also skip human S3 taxonomy review if resuming before S3.",
  "correction": ""
}
```

<details><summary>[32] tool result (39 chars)</summary>

```
Structured output provided successfully
```
</details>

**[33] assistant:** Verification complete. The claim was confirmed on all points: the resume path in observe.py hardcodes depth='standard' based on a false premise (depth is never persisted anywhere), depth materially drives S4/S5/S7 behavior, S5's per-domain sentinels make the mixed-depth outcome concrete, and the no_pdf cancellation by the dropped `--no-pdf` flag in cli.py is real.
