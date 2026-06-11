# Agent: verify__single-threaded_http.server_+_2s_full-file_polling_does_not_scale_to_m
**ID:** `agent-aab009292104673ca`  **Turns:** 47

## Prompt
```
You are an adversarial verifier. A code auditor claims the following about the repo at /Users/cyan/ccr/know-expand:

TITLE: Single-threaded http.server + 2s full-file polling does not scale to multiple clients
LAYER: architecture
SEVERITY: high
CLAIM: The server is plain `http.server.HTTPServer` (line 2763), not ThreadingHTTPServer — all requests are serialized. The client polls every 2s (line 1464) hitting /api/status and /api/events, and /api/events returns the ENTIRE events.jsonl every call (_load_events, line 2387). Worse, _load_state and several _load_stage_artifacts branches (S5/S6/S8/S9, e.g. lines 2226-2247, 2338-2348) each re-read and re-parse the whole events.jsonl on every poll. events.jsonl grows unbounded during a run, so per-poll cost is O(file size) and a long run plus a few open tabs will saturate the single thread; one slow handler (large events file or markdown render) blocks the QA poll for every client. The ETag short-circuit (line 2599) still builds the full payload before comparing, so it saves bandwidth but not the read/parse/serialize cost. This needs SSE/websocket push with incremental tailing and a concurrent server.
FILES: /Users/cyan/ccr/know-expand/know_expand/observe.py:2763, /Users/cyan/ccr/know-expand/know_expand/observe.py:2387, /Users/cyan/ccr/know-expand/know_expand/observe.py:1464, /Users/cyan/ccr/know-expand/know_expand/observe.py:2226

Read the actual code and try to REFUTE this claim. Check: does the cited code actually behave as claimed? Is the severity inflated? Is there mitigating code elsewhere (search the repo)? If the claim is materially correct, confirm it. Default to refuted if the evidence is ambiguous.
```

**[4] tool→Bash:** `grep -n "HTTPServer\|ThreadingHTTPServer\|socketserver\|ThreadingMixIn" /Users/cyan/ccr/know-expand/know_expand/observe.` — Search for server class usage in observe.py

<details><summary>[5] tool result (70 chars)</summary>

```
2763:    server = http.server.HTTPServer(("127.0.0.1", port), Handler)
```
</details>

**[6] tool→Bash:** `grep -n "setInterval\|setTimeout\|poll" /Users/cyan/ccr/know-expand/know_expand/observe.py | head -50` — Find polling intervals in dashboard JS

<details><summary>[7] tool result (955 chars)</summary>

```
1370:async function poll() {
1381:      setTimeout(() => { if ($('top-status').textContent === 'reattached') $('top-status').textContent = ''; }, 2000);
1382:      return;  // let the next poll cycle fetch with cleared ETags
1438:    // Refresh selected stage artifacts every 5th poll while pipeline is running
1459:  await pollQa();
1460:  await pollTaxonomy();
1463:poll();
1464:setInterval(poll, 2000);
1550:  // Don't call setRunMode here — the next poll will detect pipeline_running=false
1559:  setTimeout(() => { bar.placeholder = prev; }, 3000);
1591:  // never re-open it on repeated polls of the same mode.
1598:async function pollTaxonomy() {
1732:    // Always start in running mode; pollQa/pollTaxonomy switch reactively
1799:// Q&A polling (called from main poll loop)
1800:async function pollQa() {
1810:    // which would cause running→qa→running oscillation on every poll.
1904:          if (show) setTimeout(() => otherInput.focus(), 0);
```
</details>

**[8] tool→Read:** `/Users/cyan/ccr/know-expand/know_expand/observe.py` 

<details><summary>[9] tool result (2163 chars)</summary>

```
2370	            text = output_path.read_text(errors="replace")
2371	            out["word_count"] = len(text.split())
2372	            out["output_path"] = str(output_path)
2373	        # bibliography count across all domains
2374	        audit_dir = state_dir / "audit"
2375	        total = 0
2376	        if audit_dir.exists():
2377	            for bib in audit_dir.glob("bibliography_*.json"):
2378	                try:
2379	                    total += len(json.loads(bib.read_text()))
2380	                except Exception:
2381	                    pass
2382	        out["bibliography_count"] = total or None
2383	
2384	    return out
2385	
2386	
2387	def _load_events(state_dir: Path) -> list[dict]:
2388	    ef = state_dir.parent / "logs" / "events.jsonl"
2389	    if not ef.exists():
2390	        return []
2391	    events = []
2392	    for line in ef.read_text(errors="replace").splitlines():
2393	        try:
2394	            events.append(json.loads(line))
2395	        except Exception:
2396	            pass
2397	    return events
2398	
2399	
2400	def _render_section(section_path: Path) -> str:
2401	    if not section_path.exists():
2402	        return ""
2403	    text = section_path.read_text(errors="replace")
2404	    try:
2405	        import markdown as _md_lib
2406	        return _md_lib.markdown(text, extensions=["tables", "fenced_code"])
2407	    except ImportError:
2408	        import html as _h
2409	        return "<pre>" + _h.escape(text) + "</pre>"
2410	
2411	
2412	
2413	
2414	def _list_files(dir_param: str) -> dict:
2415	    """Return directory listing for the file browser."""
2416	    try:
2417	        target = Path(dir_param).expanduser().resolve()
2418	    except Exception:
2419	        target = Path.cwd()
2420	    if not target.is_dir():
2421	        target = target.parent if target.parent.is_dir() else Path.cwd()
2422	
2423	    entries = []
2424	    try:
2425	        for p in sorted(target.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
24...[truncated]
```
</details>

**[10] tool→Read:** `/Users/cyan/ccr/know-expand/know_expand/observe.py` 

<details><summary>[11] tool result (8054 chars)</summary>

```
2200	                    did = d["id"]
2201	                    gf = audit_dir / f"gap_{did}.json" if audit_dir.exists() else Path("/nonexistent")
2202	                    gap_list: list = []
2203	                    if gf.exists():
2204	                        try:
2205	                            gap_list = json.loads(gf.read_text())
2206	                        except Exception:
2207	                            pass
2208	                    gaps[did] = {"label": d.get("label", did), "gaps": gap_list}
2209	            except Exception:
2210	                pass
2211	        out["gaps"] = gaps
2212	
2213	    elif stage_id == "5":
2214	        tax_path = state_dir / "taxonomy.json"
2215	        sections_dir = state_dir / "sections"
2216	        summaries_dir = state_dir / "summaries"
2217	        domains = []
2218	        if tax_path.exists():
2219	            try:
2220	                tax = json.loads(tax_path.read_text())
2221	                # Parse events for per-domain status
2222	                personas_done: set = set()
2223	                reconciled: set = set()
2224	                critic_done: set = set()
2225	                section_written: set = set()
2226	                ef = state_dir.parent / "logs" / "events.jsonl"
2227	                if ef.exists():
2228	                    try:
2229	                        for line in ef.read_text(errors="replace").splitlines():
2230	                            try:
2231	                                evt = json.loads(line)
2232	                            except Exception:
2233	                                continue
2234	                            ev = evt.get("event", "")
2235	                            did = evt.get("domain_id", "")
2236	                            if not did:
2237	                                continue
2238	                            if ev == "s5_personas_done":
2239	                                personas_done.add(did)
2240	                            elif ev == "s5_reconciler_don...[truncated]
```
</details>

**[13] assistant:** The first four citations check out. Now let me verify the poll loop behavior, `_load_state`, and the ETag handling.

**[14] tool→Read:** `/Users/cyan/ccr/know-expand/know_expand/observe.py` 

<details><summary>[15] tool result (4677 chars)</summary>

```
1360	  }).join('');
1361	  if (atBottom) log.scrollTop = log.scrollHeight;
1362	}
1363	$('evt-filter-input').addEventListener('input', renderAllEvents);
1364	
1365	// ── Polling ───────────────────────────────────────────────────────────────────
1366	let lastStatusEtag = '', lastEventsEtag = '';
1367	let _serverDown = false;  // true while server is unreachable
1368	let _artifactPollCount = 0;
1369	
1370	async function poll() {
1371	  try {
1372	    // Status
1373	    const sr = await fetch('/api/status', {headers: lastStatusEtag ? {'If-None-Match': lastStatusEtag} : {}});
1374	
1375	    // Server came back after a disconnect — clear stale ETags so we get fresh data
1376	    if (_serverDown) {
1377	      _serverDown = false;
1378	      lastStatusEtag = '';
1379	      lastEventsEtag = '';
1380	      $('top-status').textContent = 'reattached';
1381	      setTimeout(() => { if ($('top-status').textContent === 'reattached') $('top-status').textContent = ''; }, 2000);
1382	      return;  // let the next poll cycle fetch with cleared ETags
1383	    }
1384	
1385	    if (sr.status !== 304) {
1386	      lastStatusEtag = sr.headers.get('ETag') || '';
1387	      const s = await sr.json();
1388	      const wasRunning = _pipelineRunning;
1389	      _pipelineRunning = s.pipeline_running;
1390	      renderStageList(s.stages || {}, s.pipeline_running);
1391	      domainData = s.domains || [];
1392	      _runId = s.run_id || '';
1393	      _stateDir = s.state_dir || '';
1394	      $('run-label').textContent = s.run_id || '';
1395	      $('top-status').textContent = s.summary || '';
1396	      updateResumeCard(s.stages || {}, s.input_path || '', s.run_id || '');
1397	      // Sync run pane mode with actual pipeline state
1398	      if (s.pipeline_running && runPaneMode === 'setup') {
1399	        setRunMode('running');
1400	      } else if (!s.pipeline_running && runPaneMode === 'running') {
1401	        // Pipeline finished — return to setup but keep resume selected if prior run exi...[truncated]
```
</details>

**[16] tool→Read:** `/Users/cyan/ccr/know-expand/know_expand/observe.py` 

<details><summary>[17] tool result (5598 chars)</summary>

```
2560	        if qid in answered_ids:
2561	            history.append({"question": q.get("text", ""), "answer": answered_ids[qid]})
2562	
2563	    if complete_flag.exists():
2564	        return {"question": None, "interview_complete": True, "history": history}
2565	
2566	    first_unanswered = None
2567	    for q in questions:
2568	        if q.get("id", "") not in answered_ids:
2569	            first_unanswered = q
2570	            break
2571	
2572	    return {"question": first_unanswered, "interview_complete": False, "history": history}
2573	
2574	
2575	def _post_answer(state_dir: Path, qid: str, answer: str) -> None:
2576	    """Append an answer to qa_answers.jsonl."""
2577	    answers_file = state_dir / "qa_answers.jsonl"
2578	    record = json.dumps({"id": qid, "answer": answer, "ts": time.time()})
2579	    with answers_file.open("a") as f:
2580	        f.write(record + "\n")
2581	
2582	
2583	def cmd_serve(runs_dir: Path, port: int = 7842) -> None:
2584	    # Pipeline subprocesses are spawned with start_new_session=True so they live
2585	    # in their own process group and session — server exit / Ctrl-C never propagates
2586	    # to them.  SIG_IGN on SIGCHLD prevents zombie accumulation when they exit.
2587	    signal.signal(signal.SIGCHLD, signal.SIG_IGN)
2588	    # Do NOT forward SIGTERM to children — the pipeline survives server restarts.
2589	    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
2590	
2591	    import hashlib
2592	
2593	    def _etag(data: bytes) -> str:
2594	        return '"' + hashlib.md5(data).hexdigest() + '"'
2595	
2596	    def _json_response(handler, obj: object) -> None:
2597	        payload = json.dumps(obj, default=str).encode()
2598	        tag = _etag(payload)
2599	        if handler.headers.get("If-None-Match", "") == tag:
2600	            handler.send_response(304)
2601	            handler.end_headers()
2602	            return
2603	        handler.send_response(200)
2604	        handler.send_header("Content-Type", "applica...[truncated]
```
</details>

**[20] tool→Bash:** `grep -n "_load_state\|st_mtime\|mtime\|lru_cache\|_cache\|functools" /Users/cyan/ccr/know-expand/know_expand/observe.py` — Search for _load_state and any caching in observe.py

<details><summary>[21] tool result (202 chars)</summary>

```
34:        key=lambda d: d.stat().st_mtime,
2027:        key=lambda d: d.stat().st_mtime,
2043:def _load_state(state_dir: Path) -> dict:
2626:                _json_response(self, _load_state(state_dir))
```
</details>

**[22] tool→Bash:** `grep -rn "ThreadingHTTPServer\|ThreadingMixIn\|asyncio.*server\|aiohttp\|uvicorn\|sse\|EventSource\|websocket" /Users/cy` — Search repo for concurrent server or push mechanisms

<details><summary>[23] tool result (2128 chars)</summary>

```
/Users/cyan/ccr/know-expand/know_expand/config.py:1:from dataclasses import dataclass, field
/Users/cyan/ccr/know-expand/know_expand/sources.py:93:    # Possessive forms — refer to something else, not a standalone concept
/Users/cyan/ccr/know-expand/know_expand/sources.py:368:    domain_label: passed to arXiv fetcher to enrich short/generic queries.
/Users/cyan/ccr/know-expand/know_expand/cli.py:144:        parser.error("input is required unless --status is passed")
/Users/cyan/ccr/know-expand/know_expand/pipeline.py:9:    s1_assess,
/Users/cyan/ccr/know-expand/know_expand/pipeline.py:18:    s10_assemble,
/Users/cyan/ccr/know-expand/know_expand/pipeline.py:33:    async def node_assess(state: PipelineState) -> PipelineState:
/Users/cyan/ccr/know-expand/know_expand/pipeline.py:34:        await s1_assess.run(state, cfg)
/Users/cyan/ccr/know-expand/know_expand/pipeline.py:69:    async def node_assemble(state: PipelineState) -> PipelineState:
/Users/cyan/ccr/know-expand/know_expand/pipeline.py:70:        await s10_assemble.run(state, cfg, no_pdf=no_pdf)
/Users/cyan/ccr/know-expand/know_expand/pipeline.py:74:    graph.add_node("assess", node_assess)
/Users/cyan/ccr/know-expand/know_expand/pipeline.py:83:    graph.add_node("assemble", node_assemble)
/Users/cyan/ccr/know-expand/know_expand/pipeline.py:87:    graph.add_edge("extract", "assess")
/Users/cyan/ccr/know-expand/know_expand/pipeline.py:88:    graph.add_edge("assess", "graph")
/Users/cyan/ccr/know-expand/know_expand/pipeline.py:95:    graph.add_edge("prereq", "assemble")
/Users/cyan/ccr/know-expand/know_expand/pipeline.py:96:    graph.add_edge("assemble", END)
/Users/cyan/ccr/know-expand/know_expand/observe.py:137:    "0": "Ingest", "1": "Assess", "2": "Extract", "3": "Graph",
/Users/cyan/ccr/know-expand/know_expand/observe.py:139:    "8": "Verify", "9": "Prereq", "10": "Assemble",
/Users/cyan/ccr/know-expand/know_expand/observe.py:891:  '0':'Ingest','1':'Assess','2':'Extract','3':'Graph','4':'Audit',
/Users/cyan/cc...[truncated]
```
</details>

**[26] tool→Read:** `/Users/cyan/ccr/know-expand/know_expand/observe.py` 

<details><summary>[27] tool result (4215 chars)</summary>

```
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
2070	        try:
2071	            g = json.loads(graph_path.read_text())
2072	            for n in g.get("nodes", []):
2073	                did = n.get("domain", "")
2074	                node_counts[did] = node_counts.get(did, 0) + 1
2075	        except Exception:
2076	            pass
2077	
2078	    sections_dir = state_dir / "sections"
2079	    checklist_by_domain: dict[str, dict] = {}
2080	    ef = state_dir.parent / "logs" / "events.jsonl"
2081	    if ef.exists():
2082	        try:
2083	            for line in ef.read_text(errors="replace").splitlines():
2084	                try:
2085	                    evt = json.loads(line)
2086	                except Exception:
2087	                    continue
2088	                if evt.get("event") in ("s6_domain_aligned", "s4_5_domain_aligned"):
2089	                    did = evt.get("domain_id", "")
2090	                    cl = evt.get("checklist", {})
2091	           ...[truncated]
```
</details>

**[30] tool→Read:** `/Users/cyan/ccr/know-expand/know_expand/observe.py` 

<details><summary>[31] tool result (826 chars)</summary>

```
2750	                answer = data.get("answer", "")
2751	                _post_answer(state_dir, qid, answer)
2752	                payload = json.dumps({"ok": True}).encode()
2753	                self.send_response(200)
2754	                self.send_header("Content-Type", "application/json; charset=utf-8")
2755	                self.send_header("Content-Length", str(len(payload)))
2756	                self.end_headers()
2757	                self.wfile.write(payload)
2758	
2759	            else:
2760	                self.send_response(404)
2761	                self.end_headers()
2762	
2763	    server = http.server.HTTPServer(("127.0.0.1", port), Handler)
2764	    print(f"http://localhost:{port}", flush=True)
2765	    try:
2766	        server.serve_forever()
2767	    except KeyboardInterrupt:
2768	        pass
2769	
```
</details>

**[32] tool→Bash:** `find /Users/cyan/ccr/know-expand/runs -name "events.jsonl" -exec ls -la {} \; 2>/dev/null; echo "---"; find /Users/cyan/` — Check actual events.jsonl file sizes in runs directory

<details><summary>[33] tool result (3 chars)</summary>

```
---
```
</details>

**[35] tool→Bash:** `grep -n "def emit" /Users/cyan/ccr/know-expand/know_expand/state.py` — Locate emit() in state.py

<details><summary>[36] tool result (76 chars)</summary>

```
157:def emit(event: dict) -> None:
367:def emit_human(message: str) -> None:
```
</details>

**[37] tool→Bash:** `grep -rn "emit(" /Users/cyan/ccr/know-expand/know_expand/agents/base.py | head -20` — Check event emission frequency in LLM router

<details><summary>[38] tool result (1564 chars)</summary>

```
/Users/cyan/ccr/know-expand/know_expand/agents/base.py:122:    emit({
/Users/cyan/ccr/know-expand/know_expand/agents/base.py:163:        emit({
/Users/cyan/ccr/know-expand/know_expand/agents/base.py:177:        emit({
/Users/cyan/ccr/know-expand/know_expand/agents/base.py:211:    emit({"event": "agent_tool_call_start", "role": role, "model": reported_model})
/Users/cyan/ccr/know-expand/know_expand/agents/base.py:236:        emit({
/Users/cyan/ccr/know-expand/know_expand/agents/base.py:270:        emit({
/Users/cyan/ccr/know-expand/know_expand/agents/base.py:326:    emit({
/Users/cyan/ccr/know-expand/know_expand/agents/base.py:346:        emit({
/Users/cyan/ccr/know-expand/know_expand/agents/base.py:359:        emit({
/Users/cyan/ccr/know-expand/know_expand/agents/base.py:476:    emit({
/Users/cyan/ccr/know-expand/know_expand/agents/base.py:494:    emit({
/Users/cyan/ccr/know-expand/know_expand/agents/base.py:603:    emit({
/Users/cyan/ccr/know-expand/know_expand/agents/base.py:623:        emit({
/Users/cyan/ccr/know-expand/know_expand/agents/base.py:638:        emit({
/Users/cyan/ccr/know-expand/know_expand/agents/base.py:668:    emit({
/Users/cyan/ccr/know-expand/know_expand/agents/base.py:690:        emit({
/Users/cyan/ccr/know-expand/know_expand/agents/base.py:706:        emit({
/Users/cyan/ccr/know-expand/know_expand/agents/base.py:757:                    emit({
/Users/cyan/ccr/know-expand/know_expand/agents/base.py:778:                        emit({
/Users/cyan/ccr/know-expand/know_expand/agents/base.py:792:                    emit({
```
</details>

**[40] tool→Read:** `/Users/cyan/ccr/know-expand/know_expand/state.py` 

<details><summary>[41] tool result (1513 chars)</summary>

```
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
167	        _event_fh.write(json.dumps(stamped) + "\n")
168	
169	    name = event.get("event", "")
170	    _route_to_log(name, event)
171	    line = _progress_line(ts, name, event)
172	    if line:
173	        print(line, flush=True)
174	
175	
176	def _route_to_log(name: str, ev: dict) -> None:
177	    """Send event to the Python logger at the appropriate level."""
178	    error_events = {"domain_failed", "quota_exhausted", "ss_exhausted", "llm_call_error"}
179	    warn_events = {"ss_429_retry", "pipeline_paused"}
180	    debug_events = {
181	        "ss_rate_wait", "ss_request", "ss_response",
182	        "llm_call_start", "chunk_map_start", "chunk_map_cached",
183	        "source_refs_skipped", "model_auth_skip", "model_quota_switch",
184	    }
185	
186	    if name in error_events:
187	        _logger.error("%s %s", name, _kv(ev))
188	    elif name in warn_events:
189	        _logger.warning("%s %s", name, _kv(ev))
190	    elif name in debug_events:
191	        _logger.debug("%s %s", name, _kv(ev))
192	    else:
193	        _logger.info("%s %s", name, _kv(ev))
194	
195	
196	def _progress_line(ts: str, name: str, ev: dict) -> str | None:
```
</details>

## Structured Output
```json
{
  "verdict": "confirmed",
  "reasoning": "Every factual assertion verified against /Users/cyan/ccr/know-expand/know_expand/observe.py. Line 2763 is plain http.server.HTTPServer (single-threaded, serialized requests). Line 1464 is setInterval(poll, 2000), and each cycle actually hits FOUR endpoints (/api/status, /api/events, /api/qa, /api/taxonomy) plus /api/stage/:id every 5th poll \u2014 the claim understates request volume. _load_events (line 2387) reads and parses the entire events.jsonl per call with no tailing. _load_state (lines 2080-2099) re-parses the full events.jsonl on every /api/status once taxonomy.json exists. _load_stage_artifacts re-parses it again in the S5 (2226-2247), S6 (2286-2305), S8 (2338-2348), and S9 (2353+) branches. The ETag check (line 2599) compares If-None-Match only AFTER json.dumps builds the full payload (line 2597), so it saves bandwidth, not read/parse/serialize cost \u2014 and during an active run the events ETag changes nearly every poll, so full payloads are re-sent every 2s per tab. emit() in state.py appends unboundedly with no rotation; LLM-call and SS request events produce thousands of lines per deep run. Repo-wide search found zero mitigation: no ThreadingHTTPServer/ThreadingMixIn, no mtime caching, no lru_cache, no SSE/websocket. _render_section (line 2400) also does synchronous markdown rendering inside the single-threaded handler, making the \"one slow handler blocks the QA poll\" mechanism structurally real. The only soft spot is the saturation prediction: events lines are small (~100-300 bytes), so typical runs produce low-MB files where 1-2 tabs stay responsive; true saturation requires a long deep run (10+ MB) plus multiple tabs. Severity 'high' is at the upper bound for a localhost single-operator dashboard, but is defensible because the degradation directly hits the latency-sensitive QA interview poll (the only human-in-the-loop path, S1/S3) and per-poll cost provably grows O(file size) with no cap."
}
```

<details><summary>[45] tool result (39 chars)</summary>

```
Structured output provided successfully
```
</details>

**[46] assistant:** Verification complete — verdict returned via StructuredOutput.
