# Agent: verify__pipeline.json_—_the_sole_resume_mechanism_—_is_written_non-atomically_
**ID:** `agent-a9da56c2e5afb84e7`  **Turns:** 44

## Prompt
```
You are an adversarial verifier. A code auditor claims the following about the repo at /Users/cyan/ccr/know-expand:

TITLE: pipeline.json — the sole resume mechanism — is written non-atomically with unsynchronized multi-writer races
LAYER: execution
SEVERITY: high
CLAIM: save_pipeline_json uses p.write_text() (state.py:134-136) even though atomic_write() exists 240 lines below in the same file (state.py:376-380). mark_stage_complete is an unlocked read-modify-write (state.py:139-145). Concurrent writers exist: the pipeline process writes markers while the web server's POST /api/stage/:id/clear does its own read-modify-write of the same file (observe.py:2720-2729), and the dashboard reads it every 2s poll. A SIGTERM arriving mid-write (the handler raises SystemExit at an arbitrary bytecode, cli.py:16-19) or a crash can truncate pipeline.json — corrupting the only resume store at exactly the moment resume matters; load_pipeline_json (state.py:127-131) would then raise on every subsequent start and status call.
FILES: /Users/cyan/ccr/know-expand/know_expand/state.py:134-145, /Users/cyan/ccr/know-expand/know_expand/state.py:376-380, /Users/cyan/ccr/know-expand/know_expand/observe.py:2720-2729

Read the actual code and try to REFUTE this claim. Check: does the cited code actually behave as claimed? Is the severity inflated? Is there mitigating code elsewhere (search the repo)? If the claim is materially correct, confirm it. Default to refuted if the evidence is ambiguous.
```

**[4] tool→Read:** `/Users/cyan/ccr/know-expand/know_expand/state.py` 

<details><summary>[5] tool result (2050 chars)</summary>

```
110	
111	def sentinel_path(base: Path) -> Path:
112	    return base.with_suffix(base.suffix + ".done")
113	
114	
115	def sentinel_exists(path: Path) -> bool:
116	    return sentinel_path(path).exists()
117	
118	
119	def write_sentinel(path: Path) -> None:
120	    sentinel_path(path).touch()
121	
122	
123	# ---------------------------------------------------------------------------
124	# pipeline.json helpers
125	# ---------------------------------------------------------------------------
126	
127	def load_pipeline_json(state_dir: Path) -> dict:
128	    p = state_dir / "pipeline.json"
129	    if p.exists():
130	        return json.loads(p.read_text())
131	    return {}
132	
133	
134	def save_pipeline_json(state_dir: Path, data: dict) -> None:
135	    p = state_dir / "pipeline.json"
136	    p.write_text(json.dumps(data, indent=2))
137	
138	
139	def mark_stage_complete(state_dir: Path, stage: str | int) -> None:
140	    data = load_pipeline_json(state_dir)
141	    data.setdefault("stages", {})[str(stage)] = {
142	        "status": "complete",
143	        "completed_at": _utc(),
144	    }
145	    save_pipeline_json(state_dir, data)
146	
147	
148	def stage_is_complete(state_dir: Path, stage: str | int) -> bool:
149	    data = load_pipeline_json(state_dir)
150	    return data.get("stages", {}).get(str(stage), {}).get("status") == "complete"
151	
152	
153	# ---------------------------------------------------------------------------
154	# emit() — structured events → file + stdout progress
155	# ---------------------------------------------------------------------------
156	
157	def emit(event: dict) -> None:
158	    """
159	    Write a timestamped JSON event to events.jsonl, log it to the appropriate
160	    log file, and print a human-readable progress line to stdout if the event
161	    warrants operator attention.
162	    """
163	    ts = _utc()
164	    stamped = {"ts": ts, **event}
165	
166	    if _event_fh is not None:
167	        _event_fh.write(json.dumps(stamped) ...[truncated]
```
</details>

**[6] tool→Read:** `/Users/cyan/ccr/know-expand/know_expand/cli.py` 

<details><summary>[7] tool result (1276 chars)</summary>

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
```
</details>

**[9] tool→Read:** `/Users/cyan/ccr/know-expand/know_expand/state.py` 

<details><summary>[10] tool result (821 chars)</summary>

```
360	
361	
362	def _kv(ev: dict, skip: frozenset[str] = frozenset({"event"})) -> str:
363	    parts = [f"{k}={v!r}" for k, v in ev.items() if k not in skip and v is not None]
364	    return " ".join(parts[:8])
365	
366	
367	def emit_human(message: str) -> None:
368	    """Interactive/human-mode output; goes to stdout."""
369	    print(message, flush=True)
370	
371	
372	# ---------------------------------------------------------------------------
373	# Atomic file write
374	# ---------------------------------------------------------------------------
375	
376	def atomic_write(path: Path, content: str) -> None:
377	    """Write content to path atomically via a tmp+rename guard (POSIX atomic)."""
378	    tmp = path.with_suffix(path.suffix + ".tmp")
379	    tmp.write_text(content)
380	    os.replace(tmp, path)
381	
```
</details>

**[11] tool→Read:** `/Users/cyan/ccr/know-expand/know_expand/observe.py` 

<details><summary>[12] tool result (2765 chars)</summary>

```
2700	                active_sd = _find_active_state_dir(runs_dir)
2701	                pid_file = (active_sd / "pipeline_pid") if active_sd else (state_dir / "pipeline_pid")
2702	                if pid_file.exists():
2703	                    try:
2704	                        pid = int(pid_file.read_text().strip())
2705	                        os.killpg(os.getpgid(pid), signal.SIGTERM)
2706	                        killed = True
2707	                    except Exception:
2708	                        pass
2709	                    try:
2710	                        pid_file.unlink()
2711	                    except Exception:
2712	                        pass
2713	                payload = json.dumps({"ok": True, "killed": killed}).encode()
2714	                self.send_response(200)
2715	                self.send_header("Content-Type", "application/json; charset=utf-8")
2716	                self.send_header("Content-Length", str(len(payload)))
2717	                self.end_headers()
2718	                self.wfile.write(payload)
2719	
2720	            elif self.path.startswith("/api/stage/") and self.path.endswith("/clear"):
2721	                sid = self.path[len("/api/stage/"):-len("/clear")]
2722	                pipeline_path = state_dir / "pipeline.json"
2723	                if sid and pipeline_path.exists():
2724	                    try:
2725	                        pipe = json.loads(pipeline_path.read_text())
2726	                        pipe.setdefault("stages", {}).pop(str(sid), None)
2727	                        pipeline_path.write_text(json.dumps(pipe, indent=2))
2728	                    except Exception:
2729	                        pass
2730	                payload = json.dumps({"ok": True}).encode()
2731	                self.send_response(200)
2732	                self.send_header("Content-Type", "application/json; charset=utf-8")
2733	                self.send_header("Content-Length", str(len(payload)))
2734	                self.end_headers()
2735	       ...[truncated]
```
</details>

**[15] tool→Bash:** `grep -rn "atomic_write\|load_pipeline_json\|save_pipeline_json\|mark_stage_complete" /Users/cyan/ccr/know-expand/know_ex` — Find all callers of pipeline.json helpers and atomic_write

<details><summary>[16] tool result (5512 chars)</summary>

```
/Users/cyan/ccr/know-expand/know_expand/cli.py:11:from know_expand.state import emit, load_pipeline_json, new_run_id, setup_logging
/Users/cyan/ccr/know-expand/know_expand/cli.py:23:    data = load_pipeline_json(state_dir)
/Users/cyan/ccr/know-expand/know_expand/pipeline.py:6:from know_expand.state import PipelineState, emit, load_pipeline_json
/Users/cyan/ccr/know-expand/know_expand/pipeline.py:122:    pipeline_json = load_pipeline_json(state_dir)
/Users/cyan/ccr/know-expand/know_expand/pipeline.py:130:    from know_expand.state import save_pipeline_json
/Users/cyan/ccr/know-expand/know_expand/pipeline.py:131:    save_pipeline_json(state_dir, pipeline_json)
/Users/cyan/ccr/know-expand/know_expand/state.py:127:def load_pipeline_json(state_dir: Path) -> dict:
/Users/cyan/ccr/know-expand/know_expand/state.py:134:def save_pipeline_json(state_dir: Path, data: dict) -> None:
/Users/cyan/ccr/know-expand/know_expand/state.py:139:def mark_stage_complete(state_dir: Path, stage: str | int) -> None:
/Users/cyan/ccr/know-expand/know_expand/state.py:140:    data = load_pipeline_json(state_dir)
/Users/cyan/ccr/know-expand/know_expand/state.py:145:    save_pipeline_json(state_dir, data)
/Users/cyan/ccr/know-expand/know_expand/state.py:149:    data = load_pipeline_json(state_dir)
/Users/cyan/ccr/know-expand/know_expand/state.py:376:def atomic_write(path: Path, content: str) -> None:
/Users/cyan/ccr/know-expand/know_expand/stages/s9_prereq.py:18:from know_expand.state import PipelineState, emit, mark_stage_complete, stage_is_complete
/Users/cyan/ccr/know-expand/know_expand/stages/s9_prereq.py:180:        mark_stage_complete(state_dir, 9)
/Users/cyan/ccr/know-expand/know_expand/stages/s9_prereq.py:224:    mark_stage_complete(state_dir, 9)
/Users/cyan/ccr/know-expand/know_expand/stages/s1_assess.py:29:    atomic_write,
/Users/cyan/ccr/know-expand/know_expand/stages/s1_assess.py:31:    mark_stage_complete,
/Users/cyan/ccr/know-expand/know_expand/stages/s1_assess.py:734:    atomic_write...[truncated]
```
</details>

**[17] tool→Bash:** `grep -n "pipeline.json\|pipeline_json\|JSONDecodeError" /Users/cyan/ccr/know-expand/know_expand/observe.py` — Find pipeline.json reads and error handling in observe.py

<details><summary>[18] tool result (636 chars)</summary>

```
119:                except json.JSONDecodeError:
911:  // (pipeline.json only records completed stages — no "running" status is ever written)
2048:    pipeline_path = state_dir / "pipeline.json"
2454:        # inside pipeline.json can differ (e.g. "run_4ed94c55" vs the UUID dir
2482:        pipeline_path = state_dir / "pipeline.json"
2498:    # For resume, fall back to input_path stored in pipeline.json if not supplied
2500:        pipeline_path = state_dir / "pipeline.json"
2549:            except json.JSONDecodeError:
2685:            except json.JSONDecodeError:
2722:                pipeline_path = state_dir / "pipeline.json"
```
</details>

**[21] tool→Read:** `/Users/cyan/ccr/know-expand/know_expand/observe.py` 

<details><summary>[22] tool result (1102 chars)</summary>

```
2040	    return None
2041	
2042	
2043	def _load_state(state_dir: Path) -> dict:
2044	    result: dict = {"stages": {}, "domains": [], "run_id": "", "summary": "",
2045	                    "pipeline_running": _pipeline_is_running(state_dir),
2046	                    "state_dir": str(state_dir)}
2047	
2048	    pipeline_path = state_dir / "pipeline.json"
2049	    if pipeline_path.exists():
2050	        try:
2051	            data = json.loads(pipeline_path.read_text())
2052	            result["stages"] = data.get("stages", {})
2053	            result["run_id"] = data.get("run_id", "")
2054	            result["input_path"] = data.get("input_path", "") or data.get("input", "")
2055	        except Exception:
2056	            pass
2057	
2058	    tax_path = state_dir / "taxonomy.json"
2059	    if not tax_path.exists():
2060	        return result
2061	
2062	    try:
2063	        tax = json.loads(tax_path.read_text())
2064	    except Exception:
2065	        return result
2066	
2067	    graph_path = state_dir / "graph.json"
2068	    node_counts: dict[str, int] = {}
2069	    if graph_path.exists():
```
</details>

**[23] tool→Read:** `/Users/cyan/ccr/know-expand/know_expand/observe.py` 

<details><summary>[24] tool result (1991 chars)</summary>

```
2475	        p = state_dir / fname
2476	        if p.exists():
2477	            p.unlink()
2478	
2479	    # Fresh run: wipe stage completion markers so every stage re-runs.
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
```
</details>

**[25] tool→Read:** `/Users/cyan/ccr/know-expand/know_expand/pipeline.py` 

<details><summary>[26] tool result (1346 chars)</summary>

```
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
```
</details>

**[29] tool→Bash:** `grep -rn "fcntl\|flock\|lockf\|filelock\|Lock(" /Users/cyan/ccr/know-expand/know_expand/ --include="*.py"` — Search for any file locking in the codebase

<details><summary>[30] tool result (94 chars)</summary>

```
/Users/cyan/ccr/know-expand/know_expand/agents/base.py:725:        self._lock = asyncio.Lock()
```
</details>

**[31] tool→Read:** `/Users/cyan/ccr/know-expand/know_expand/cli.py` 

<details><summary>[32] tool result (4634 chars)</summary>

```
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
60	    parser.add_argument("input", nargs="?", help="File path, URL, or - for stdin")
61	
62	    subparsers = parser.add_subparsers(dest="command")
63	
64	    # --- tail subcommand ---
65	    tail_p = subparsers.add_parser("tail", help="Stream pipeline events to stdout")
66	    tail_p.add_argument("run_id", nargs="?", default=None, help="Run ID (default: latest)")
67	    tail_p.add_argument("--runs-dir", type=Path, default=Path("runs"))
68	
69	    # --- serve subcommand ---
70	    serve_p = subparsers.add_parser("serve", help="Serve completed sections at localhost")
71	    serve_p.add_argument("--port", type=int, default=7842)
72	    serve_p.add_argument("--runs-dir", type=Path, default=Path("runs"))
73	
74	    # Output / mode
75	    parser.add_argument("--human", action="store_true", help="Rich terminal output")
76	    parser.add_argument("--no-pdf", action="store_true", help="Skip PDF build")
77	
78	    # Pipeline control
79	    parser.add_argument(
80	        "--depth", choices=["survey", "standard", "deep"], default="standard"
81	    )
82	    parser.add_argument("--status", action="store_true", help="Emit pipeline status and exit")
83	    parser.add_argument("--resume", type=str, metavar="STAGE", help="Resume from stage N")
84	    parser.add_argument("...[truncated]
```
</details>

**[34] assistant:** One more check — whether the web UI gates the stage-clear endpoint while the pipeline is running (which would mitigate the multi-writer race in practice).

**[35] tool→Bash:** `grep -n "clear" /Users/cyan/ccr/know-expand/know_expand/observe.py | head -40` — Find stage-clear UI references in observe.py

<details><summary>[36] tool result (1031 chars)</summary>

```
266:.sl-clear {
271:.sl-item:hover .sl-clear { color: var(--muted); }
272:.sl-clear:hover { color: var(--red) !important; background: var(--red-dim); }
925:    const clearBtn = canClear
926:      ? `<span class="sl-clear" onclick="event.stopPropagation();clearStage('${sid}')" title="Clear stage">&#215;</span>`
927:      : `<span class="sl-clear" style="visibility:hidden">&#215;</span>`;
936:      ${clearBtn}
1375:    // Server came back after a disconnect — clear stale ETags so we get fresh data
1382:      return;  // let the next poll cycle fetch with cleared ETags
1541:// ── Stop / clear ─────────────────────────────────────────────────────────
1562:async function clearStage(sid) {
1563:  await fetch(`/api/stage/${sid}/clear`, {method: 'POST'});
2446:    """Determine run_id, create run dir tree, clear Q&A state, spawn pipeline subprocess, return PID."""
2720:            elif self.path.startswith("/api/stage/") and self.path.endswith("/clear"):
2721:                sid = self.path[len("/api/stage/"):-len("/clear")]
```
</details>

**[37] tool→Read:** `/Users/cyan/ccr/know-expand/know_expand/observe.py` 

<details><summary>[38] tool result (1865 chars)</summary>

```
905	function renderStageList(stages, pipelineRunning) {
906	  stageData = stages;
907	  const container = $('sl-stages');
908	  $('stop-btn').style.display = pipelineRunning ? 'inline-block' : 'none';
909	
910	  // Infer which stage is actively running: first non-terminal stage when pipeline is alive
911	  // (pipeline.json only records completed stages — no "running" status is ever written)
912	  const terminalStatuses = new Set(['complete', 'skipped', 'error']);
913	  const activeStage = pipelineRunning
914	    ? STAGE_ORDER.find(sid => !terminalStatuses.has(stages[sid]?.status))
915	    : null;
916	
917	  container.innerHTML = STAGE_ORDER.map(sid => {
918	    const s = stages[sid] || {};
919	    const status = s.status || 'pending';
920	    const dotCls = {complete:'done', error:'error', skipped:'skipped'}[status]
921	                   || (sid === activeStage ? 'running' : '');
922	    const active = selectedView === sid ? ' active' : '';
923	    const isRunning = sid === activeStage ? ' stage-running' : '';
924	    const canClear = status === 'complete' || status === 'error' || status === 'skipped';
925	    const clearBtn = canClear
926	      ? `<span class="sl-clear" onclick="event.stopPropagation();clearStage('${sid}')" title="Clear stage">&#215;</span>`
927	      : `<span class="sl-clear" style="visibility:hidden">&#215;</span>`;
928	    const elapsed = s.elapsed_s != null ? `<span class="sl-elapsed">${s.elapsed_s}s</span>` : '';
929	    return `<div class="sl-item${active}${isRunning}" data-stage="${sid}" onclick="selectStage('${sid}')">
930	      <span class="sl-num">S${sid}</span>
931	      <span class="sl-main">
932	        <span class="sl-name">${STAGE_NAMES[sid]}</span>
933	        ${elapsed}
934	      </span>
935	      <span class="sl-dot ${dotCls}"></span>
936	      ${clearBtn}
937	    </div>`;
938	  }).join('');
939	
```
</details>

## Structured Output
```json
{
  "verdict": "partially_confirmed",
  "reasoning": "Every code citation checks out. state.py:134-136 save_pipeline_json uses Path.write_text (truncate-then-write, non-atomic); state.py:376-380 atomic_write (tmp + os.replace) exists in the same file and is used for s1/s5/s6/s7/s10 artifacts but never for pipeline.json; state.py:139-145 mark_stage_complete is an unlocked read-modify-write, and a repo-wide grep finds no file locking anywhere. The multi-writer race is real and user-reachable: observe.py:2720-2729 (/api/stage/:id/clear) performs its own non-atomic RMW from the web server process, and the UI does NOT hide the clear button while the pipeline runs (observe.py:924-927 gates only on stage status), so clicking it mid-run races the pipeline's marker writes \u2014 lost update (dropped completion marker or undone clear). The SIGTERM truncation mechanism is also real: cli.py:16-19 raises SystemExit from the handler, the web Stop button sends SIGTERM to the process group (observe.py:2705), and write_text truncates before writing, so an interrupt or crash mid-write leaves invalid JSON. load_pipeline_json (state.py:127-131) does bare json.loads with no error handling, so a corrupt file crashes pipeline startup (pipeline.py:122), --status (cli.py:23, which picks the latest run dir by existence only at cli.py:38), and stage_is_complete; the web server's guarded reads all swallow the error (observe.py:2050-2056, 2484-2489, 2502-2505) so there is no in-product repair path \u2014 Resume spawns a subprocess that dies instantly. Severity 'high' is defensible (sole resume store for hours-long paid LLM runs; trivial fix exists in-file), though the per-write corruption window is microseconds-scale. The one material overstatement: 'raise on every subsequent start' is false for fresh runs \u2014 cli.py:125-127 generates a new run_id and fresh state_dir per run, so a corrupt pipeline.json only breaks resume of that specific run and default --status while it is the latest run, not all future starts.",
  "correction": "The core claim is accurate (non-atomic writes, no locking, real user-reachable multi-writer race, SIGTERM/crash truncation possible, no recovery path), but the blast radius is narrower than stated: a corrupted pipeline.json does NOT make load_pipeline_json raise on every subsequent start. Fresh runs create a new run_id and state dir (cli.py:125-127) and are unaffected. Only (a) resume of the corrupted run (web UI Resume passes that run's --run-id; pipeline.py:122 then raises) and (b) `know-expand --status` without --run-id while the corrupted run is the most recently modified (cli.py:36-39 selects it by file existence only) crash. The corrupted run is unresumable until the file is manually deleted/repaired, but the tool as a whole keeps working for new runs."
}
```

<details><summary>[42] tool result (39 chars)</summary>

```
Structured output provided successfully
```
</details>

**[43] assistant:** Verification complete.
