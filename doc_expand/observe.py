"""Observability commands: `doc-expand tail` and `doc-expand serve`.

tail  — streams formatted event lines to stdout (pipe-friendly, greppable)
serve — split-pane dashboard at localhost: stage list + stage detail (progress + results)
"""

from __future__ import annotations

import http.server
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _find_events_file(run_id: str | None, log_base: Path) -> Path | None:
    if run_id:
        p = log_base / run_id / "events.jsonl"
        return p if p.exists() else None
    dirs = sorted(
        (d for d in log_base.iterdir() if d.is_dir()),
        key=lambda d: d.stat().st_mtime,
        reverse=True,
    )
    for d in dirs:
        p = d / "events.jsonl"
        if p.exists():
            return p
    return None


# ---------------------------------------------------------------------------
# tail — plain text stream
# ---------------------------------------------------------------------------

def _fmt(evt: dict) -> str | None:
    e = evt.get("event", "")
    ts = evt.get("ts", "")[:19].replace("T", " ")

    if e == "stage_start":
        return f"{ts}  stage {evt.get('stage')} starting"
    if e == "stage_complete":
        extra = "  ".join(f"{k}={v}" for k, v in evt.items() if k not in ("event", "ts", "stage"))
        return f"{ts}  stage {evt.get('stage')} done  {extra}"
    if e == "stage_skipped":
        return None
    if e in ("s6_domain_start", "s4_5_domain_start", "domain_start"):
        return f"{ts}    domain {evt.get('domain_id')} starting"
    if e in ("s6_domain_complete", "s4_5_domain_complete", "domain_complete"):
        return f"{ts}    domain {evt.get('domain_id')} done  {evt.get('elapsed_s')}s"
    if e in ("s6_domain_aligned", "s4_5_domain_aligned"):
        patches = evt.get("patches") or []
        return f"{ts}    aligned {evt.get('domain_id')}  patches={len(patches)}"
    if e in ("s9_primer_inserted", "s6_5_primer_inserted"):
        return f"{ts}    primer  {evt.get('section_id')}  term={evt.get('term')}"
    if e == "llm_call_done":
        return (f"{ts}    llm [{evt.get('role')}] {evt.get('model')}  "
                f"{evt.get('tok_out')}tok  {evt.get('tok_s')}tok/s  {evt.get('elapsed_s')}s")
    if e == "agent_tool_call_done":
        return (f"{ts}    agent [{evt.get('role')}] {evt.get('model')}  "
                f"calls={evt.get('tool_calls')}  {evt.get('elapsed_s')}s")
    if e in ("model_quota_switch", "model_auth_skip"):
        return f"{ts}  model switch  {evt.get('skipped_model')} -> {evt.get('next_model')}"
    if e == "ss_request":
        return f"{ts}    ss search  {evt.get('query', '')[:60]}"
    if e == "ss_429_retry":
        return f"{ts}    ss 429  retry in {evt.get('wait_s')}s"
    if e == "pipeline_init":
        return f"{ts}  run {evt.get('run_id')}  depth={evt.get('depth')}"
    if e == "model_probe_done":
        avail = " ".join(evt.get("available", []))
        return f"{ts}  models  {avail}"
    return None


def cmd_tail(run_id: str | None, log_base: Path) -> None:
    events_file = _find_events_file(run_id, log_base)
    if not events_file:
        print(f"no events.jsonl found under {log_base}", file=sys.stderr)
        sys.exit(1)

    print(f"# {events_file}", flush=True)
    offset = 0
    try:
        while True:
            text = events_file.read_text(errors="replace")
            lines = text.splitlines()
            for line in lines[offset:]:
                line = line.strip()
                if not line:
                    continue
                try:
                    evt = json.loads(line)
                except json.JSONDecodeError:
                    continue
                msg = _fmt(evt)
                if msg:
                    print(msg, flush=True)
                if evt.get("event") == "stage_complete" and str(evt.get("stage")) == "10":
                    return
            offset = len(lines)
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass


# ---------------------------------------------------------------------------
# serve — split-pane dashboard
# ---------------------------------------------------------------------------

_STAGE_META = {
    "0": "Ingest", "1": "Assess", "2": "Extract", "3": "Graph",
    "4": "Audit", "5": "Research", "6": "Align", "7": "Synthesize",
    "8": "Verify", "9": "Prereq", "10": "Assemble",
}

_STAGE_ORDER = ["0", "1", "2", "3", "4", "5", "6", "7", "8", "9", "10"]

_PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>doc-expand</title>
<style>
:root {
  --bg: #0f1117; --surface: #1a1d27; --surface2: #1e2133; --border: #2a2d3a;
  --text: #c9cdd4; --muted: #555a6e; --accent: #4e9eff;
  --green: #3fb950; --yellow: #d29922; --red: #f85149;
  --font-mono: 'JetBrains Mono', 'Fira Code', monospace;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body { background: var(--bg); color: var(--text); font-family: system-ui, sans-serif;
       font-size: 14px; height: 100vh; display: flex; flex-direction: column; overflow: hidden; }

/* topbar */
#topbar { display: flex; align-items: center; gap: 14px; padding: 0 16px;
          height: 42px; background: var(--surface); border-bottom: 1px solid var(--border); flex-shrink: 0; }
#topbar h1 { font-size: 13px; font-weight: 600; color: var(--accent); letter-spacing: .04em; }
.run-id { font-family: var(--font-mono); font-size: 11px; color: var(--muted); }
#pulse { width: 7px; height: 7px; border-radius: 50%; background: var(--muted); flex-shrink: 0; }
#pulse.live { background: var(--green); animation: blink 1.4s infinite; }
@keyframes blink { 0%,100%{opacity:1} 50%{opacity:.3} }
.topbar-spacer { flex: 1; }
.top-status { font-size: 11px; color: var(--muted); font-family: var(--font-mono); }
#stop-btn { display: none; background: #3a1a1a; border: 1px solid var(--red); color: var(--red);
            border-radius: 3px; padding: 3px 10px; font-size: 11px; font-weight: 600;
            cursor: pointer; font-family: var(--font-mono); }
#stop-btn:hover { background: var(--red); color: #fff; }

/* main split */
#main { flex: 1; overflow: hidden; display: flex; }

/* stage list (lhs) */
#stage-list { width: 190px; flex-shrink: 0; border-right: 1px solid var(--border);
              overflow-y: auto; display: flex; flex-direction: column; }
.sl-item { display: flex; align-items: center; gap: 9px; padding: 9px 12px;
           cursor: pointer; border-bottom: 1px solid var(--border); user-select: none;
           border-left: 2px solid transparent; transition: background .1s; }
.sl-item:hover { background: var(--surface); }
.sl-item.active { background: var(--surface2); border-left-color: var(--accent); }
.sl-num { font-family: var(--font-mono); font-size: 10px; color: var(--muted);
          width: 22px; flex-shrink: 0; text-align: right; }
.sl-name { flex: 1; font-size: 12px; font-weight: 500; }
.sl-dot { width: 6px; height: 6px; border-radius: 50%; background: var(--border); flex-shrink: 0; }
.sl-clear { color: var(--border); font-size: 14px; line-height: 1; cursor: pointer;
            flex-shrink: 0; padding: 0 1px; transition: color .1s; }
.sl-item:hover .sl-clear { color: var(--muted); }
.sl-clear:hover { color: var(--red) !important; }
.sl-dot.done { background: var(--green); }
.sl-dot.running { background: var(--green); animation: blink 1.4s infinite; }
.sl-dot.skipped { background: var(--accent); }
.sl-dot.error { background: var(--red); }
.sl-divider { height: 1px; background: var(--border); margin: 4px 0; }
.sl-meta { display: flex; align-items: center; gap: 9px; padding: 8px 12px;
           cursor: pointer; border-left: 2px solid transparent; user-select: none; }
.sl-meta:hover { background: var(--surface); }
.sl-meta.active { background: var(--surface2); border-left-color: var(--muted); }
.sl-meta-label { font-size: 11px; color: var(--muted); padding-left: 31px; }

/* detail pane (rhs) */
#detail-pane { flex: 1; overflow: hidden; display: flex; flex-direction: column; }
#detail-header { padding: 12px 20px; border-bottom: 1px solid var(--border);
                 background: var(--surface); flex-shrink: 0; }
.dh-title { font-size: 14px; font-weight: 600; margin-bottom: 4px; }
.dh-meta { display: flex; gap: 14px; font-size: 11px; font-family: var(--font-mono); color: var(--muted); flex-wrap: wrap; }
.badge { font-size: 10px; font-family: var(--font-mono); padding: 2px 7px; border-radius: 3px; }
.badge-done { background: #1a3a1a; color: var(--green); }
.badge-running { background: #1a3a1a; color: var(--green); }
.badge-pending { background: var(--border); color: var(--muted); }
.badge-error { background: #3a1a1a; color: var(--red); }
.badge-skipped { background: #1a2a3a; color: var(--accent); }

#detail-body { flex: 1; overflow-y: auto; padding: 16px 20px; display: flex; flex-direction: column; gap: 16px; }

/* section headers */
.sec-hdr { font-size: 10px; font-weight: 600; letter-spacing: .1em; text-transform: uppercase;
           color: var(--muted); margin-bottom: 8px; display: flex; align-items: center; gap: 8px; }
.sec-hdr-line { flex: 1; height: 1px; background: var(--border); }

/* progress log */
#progress-log { background: var(--surface); border: 1px solid var(--border); border-radius: 5px;
                overflow-y: auto; max-height: 260px; font-family: var(--font-mono); font-size: 11px; }
.pl-row { display: flex; gap: 10px; padding: 3px 12px; border-bottom: 1px solid #1a1d27; }
.pl-row:hover { background: var(--surface2); }
.pl-ts { color: var(--muted); flex-shrink: 0; width: 90px; }
.pl-evt { flex-shrink: 0; width: 190px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.pl-evt.stage { color: var(--accent); }
.pl-evt.llm { color: #b392f0; }
.pl-evt.agent { color: #79c0ff; }
.pl-evt.error { color: var(--red); }
.pl-evt.ss { color: var(--yellow); }
.pl-body { color: var(--muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex: 1; }
.pl-empty { padding: 14px 12px; color: var(--muted); font-size: 11px; font-family: var(--font-mono); }

/* results area */
#results-area { display: flex; flex-direction: column; gap: 12px; }

/* generic kv table */
.kv-table { width: 100%; border-collapse: collapse; font-size: 12px; font-family: var(--font-mono); }
.kv-table tr { border-bottom: 1px solid var(--border); }
.kv-table tr:last-child { border-bottom: none; }
.kv-table td { padding: 5px 10px; }
.kv-table td:first-child { color: var(--muted); width: 200px; }

/* term chips */
.term-chips { display: flex; flex-wrap: wrap; gap: 6px; }
.term-chip { font-size: 11px; font-family: var(--font-mono); padding: 3px 8px;
             border-radius: 3px; background: var(--surface); border: 1px solid var(--border); color: var(--text); }
.term-chip .cnt { color: var(--accent); margin-left: 5px; font-size: 10px; }

/* domain/section cards */
.card-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(175px, 1fr)); gap: 8px; }
.card { background: var(--surface); border: 1px solid var(--border); border-radius: 5px; padding: 10px 12px; }
.card.clickable { cursor: pointer; }
.card.clickable:hover { border-color: var(--accent); }
.card.selected { border-color: var(--accent); background: var(--surface2); }
.card.done { border-color: #2a3a2a; }
.card-label { font-weight: 600; font-size: 12px; margin-bottom: 4px; }
.card-meta { font-size: 11px; color: var(--muted); font-family: var(--font-mono); }

/* checklist row */
.check-row { display: flex; align-items: center; gap: 10px; padding: 6px 10px;
             border-bottom: 1px solid var(--border); font-size: 12px; }
.check-row:last-child { border-bottom: none; }
.check-label { flex: 1; }
.check-dot { width: 7px; height: 7px; border-radius: 50%; background: var(--border); flex-shrink: 0; }
.check-dot.pass { background: var(--green); }
.check-dot.fail { background: var(--red); }

/* gap items */
.gap-item { padding: 8px 10px; border-bottom: 1px solid var(--border); font-size: 12px; }
.gap-item:last-child { border-bottom: none; }
.gap-desc { margin-bottom: 3px; }
.gap-evidence { font-size: 10px; color: var(--muted); font-family: var(--font-mono); }
.v-real { color: var(--red); font-weight: 600; }
.v-ambiguous { color: var(--yellow); }
.v-not { color: var(--muted); }

/* inline section renderer */
#inline-section { background: var(--surface); border: 1px solid var(--border); border-radius: 5px;
                  padding: 20px 26px; overflow-y: auto; max-height: 55vh; display: none; }
#inline-section.visible { display: block; }
#inline-section h1,#inline-section h2,#inline-section h3,#inline-section h4
  { margin-top: 1.2rem; margin-bottom: .4rem; color: #e8eaed; }
#inline-section p { color: var(--text); line-height: 1.7; margin-bottom: .8rem; }
#inline-section pre { background: #11131b; border: 1px solid var(--border); border-radius: 4px;
                      padding: .6rem 1rem; overflow-x: auto; font-family: var(--font-mono); font-size: 12px; }
#inline-section code { background: #11131b; border-radius: 3px; padding: .1em .35em;
                        font-family: var(--font-mono); font-size: 12px; }
#inline-section pre code { background: none; padding: 0; }
#inline-section blockquote { border-left: 3px solid var(--accent); padding-left: 1rem;
                              color: var(--muted); font-style: italic; margin: .8rem 0; }
#inline-section table { border-collapse: collapse; width: 100%; margin: .8rem 0; font-size: 12px; }
#inline-section th,#inline-section td { border: 1px solid var(--border); padding: .35rem .7rem; }
#inline-section th { background: var(--border); }

/* all-events view */
#all-events-view { flex: 1; display: flex; flex-direction: column; gap: 8px; }
#all-events-filter { display: flex; gap: 8px; align-items: center; }
#all-events-filter input { flex: 1; background: var(--bg); border: 1px solid var(--border);
                            border-radius: 4px; padding: 5px 10px; color: var(--text);
                            font-family: var(--font-mono); font-size: 12px; }
#all-events-log { background: var(--surface); border: 1px solid var(--border); border-radius: 5px;
                  overflow-y: auto; flex: 1; min-height: 200px; font-family: var(--font-mono); font-size: 11px; }

/* empty / loading */
.empty { color: var(--muted); font-size: 12px; padding: 24px 0; font-family: var(--font-mono); }

/* run pane */
#run-pane { width: 300px; flex-shrink: 0; border-left: 1px solid var(--border);
            display: flex; flex-direction: column; overflow: hidden; }
#run-header { padding: 10px 14px; background: var(--surface); border-bottom: 1px solid var(--border);
              display: flex; align-items: center; gap: 10px; flex-shrink: 0; }
#run-pane-title { font-size: 13px; font-weight: 600; flex: 1; }
#run-body { flex: 1; overflow-y: auto; display: flex; flex-direction: column; }
#run-setup { display: flex; flex-direction: column; gap: 10px; padding: 14px; }
.rf-label { font-size: 10px; color: var(--muted); font-family: var(--font-mono);
            text-transform: uppercase; letter-spacing: .06em; margin-bottom: 2px; }
.rf-input { background: var(--bg); border: 1px solid var(--border); border-radius: 4px;
            padding: 6px 10px; color: var(--text); font-family: var(--font-mono);
            font-size: 12px; width: 100%; outline: none; }
.rf-input:focus { border-color: var(--accent); }
.rf-select { background: var(--bg); border: 1px solid var(--border); border-radius: 4px;
             padding: 6px 10px; color: var(--text); font-size: 12px; width: 100%; outline: none; }
.rf-check { display: flex; align-items: center; gap: 8px; font-size: 12px; cursor: pointer; }
.rf-check input { accent-color: var(--accent); }
#run-start-btn { background: var(--accent); color: #fff; border: none; border-radius: 4px;
                 padding: 9px 14px; font-size: 13px; font-weight: 600; cursor: pointer; width: 100%; margin-top: 4px; }
#run-start-btn:hover { filter: brightness(1.1); }
#run-start-btn:disabled { background: var(--border); color: var(--muted); cursor: not-allowed; }
/* models & keys panel */
.mk-section { border: 1px solid var(--border); border-radius: 5px; overflow: hidden; }
.mk-header { display: flex; align-items: center; gap: 8px; padding: 7px 10px;
             background: var(--surface); cursor: pointer; user-select: none; font-size: 12px; font-weight: 600; }
.mk-header:hover { background: var(--surface2); }
.mk-chevron { font-size: 10px; color: var(--muted); transition: transform .15s; margin-left: auto; }
.mk-chevron.open { transform: rotate(90deg); }
.mk-body { display: none; padding: 10px; display: flex; flex-direction: column; gap: 9px; }
.mk-body.collapsed { display: none; }
.key-row { display: flex; align-items: center; gap: 5px; }
.key-row .rf-input { flex: 1; font-size: 11px; padding: 5px 8px; }
.key-dot { width: 7px; height: 7px; border-radius: 50%; background: var(--border); flex-shrink: 0; }
.key-dot.set { background: var(--green); }
.key-eye { background: none; border: none; color: var(--muted); cursor: pointer; font-size: 12px;
           padding: 0 3px; flex-shrink: 0; }
.key-eye:hover { color: var(--text); }
.mk-divider { height: 1px; background: var(--border); margin: 2px 0; }
.model-role-row { display: flex; flex-direction: column; gap: 3px; }
.model-role-label { font-size: 10px; color: var(--muted); font-family: var(--font-mono);
                    text-transform: uppercase; letter-spacing: .05em; }
.model-chips { display: flex; flex-wrap: wrap; gap: 4px; }
.model-chip { background: var(--bg); border: 1px solid var(--border); border-radius: 3px;
              padding: 3px 7px; font-size: 10px; font-family: var(--font-mono); cursor: pointer;
              color: var(--muted); white-space: nowrap; }
.model-chip:hover { border-color: var(--accent); color: var(--accent); }
.model-chip.active { border-color: var(--accent); color: var(--accent); background: #1a2a3a; }
.provider-section { border: 1px solid var(--border); border-radius: 4px; overflow: hidden; margin-bottom: 5px; }
.provider-section-hdr { background: var(--surface2); padding: 3px 8px; font-size: 9px; color: var(--muted);
                        font-family: var(--font-mono); text-transform: uppercase; letter-spacing: .09em; font-weight: 600; }
.provider-section-chips { padding: 6px 8px; display: flex; flex-wrap: wrap; gap: 4px; }
/* file browser */
.rf-input-row { display: flex; gap: 5px; align-items: center; }
.rf-input-row .rf-input { flex: 1; }
#browse-btn { background: var(--surface); border: 1px solid var(--border); border-radius: 4px;
              padding: 6px 9px; color: var(--muted); font-size: 11px; cursor: pointer;
              white-space: nowrap; flex-shrink: 0; }
#browse-btn:hover { border-color: var(--accent); color: var(--accent); }
#file-browser { background: var(--surface); border: 1px solid var(--border); border-radius: 5px;
                overflow: hidden; display: none; flex-direction: column; max-height: 240px; }
#fb-crumb { padding: 5px 8px; font-size: 10px; font-family: var(--font-mono); color: var(--muted);
            border-bottom: 1px solid var(--border); white-space: nowrap; overflow: hidden;
            text-overflow: ellipsis; flex-shrink: 0; }
#fb-list { overflow-y: auto; flex: 1; }
.fb-entry { display: flex; align-items: center; gap: 7px; padding: 5px 10px;
            font-size: 12px; cursor: pointer; border-bottom: 1px solid var(--border); }
.fb-entry:last-child { border-bottom: none; }
.fb-entry:hover { background: var(--surface2); }
.fb-icon { font-size: 11px; flex-shrink: 0; width: 14px; }
.fb-name { flex: 1; font-family: var(--font-mono); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.fb-name.dir { color: var(--accent); }
.fb-size { font-size: 10px; color: var(--muted); flex-shrink: 0; font-family: var(--font-mono); }

/* Chat UI */
#chat-messages { flex: 1; overflow-y: auto; padding: 12px; display: flex; flex-direction: column; gap: 10px; min-height: 0; }
.chat-msg { display: flex; flex-direction: column; gap: 4px; }
.chat-msg.agent { align-items: flex-start; }
.chat-msg.user-msg { align-items: flex-end; }
.chat-bubble { max-width: 90%; border-radius: 8px; padding: 8px 11px; font-size: 12px; line-height: 1.5; }
.chat-bubble.agent { background: var(--surface); border: 1px solid var(--border); color: var(--text); }
.chat-bubble.user-bub { background: var(--accent); color: #fff; }
.chat-options { display: flex; flex-direction: column; gap: 4px; margin-top: 4px; max-width: 90%; }
.chat-opt { background: var(--bg); border: 1px solid var(--border); border-radius: 4px;
            padding: 5px 10px; font-size: 11px; cursor: pointer; text-align: left; color: var(--text); }
.chat-opt:hover { border-color: var(--accent); color: var(--accent); }
.chat-opt:disabled { opacity: 0.5; cursor: default; }
.chat-opt.chosen { background: #1a2a3a; border-color: var(--accent); color: var(--accent); }
#chat-input-area { padding: 10px; border-top: 1px solid var(--border); display: flex; gap: 6px; flex-shrink: 0; }
#chat-input { flex: 1; background: var(--bg); border: 1px solid var(--border); border-radius: 4px;
              padding: 6px 10px; color: var(--text); font-family: var(--font-mono); font-size: 12px; outline: none; }
#chat-input:focus { border-color: var(--accent); }
#chat-send { background: var(--accent); border: none; border-radius: 4px; padding: 6px 10px;
             color: #fff; cursor: pointer; font-size: 13px; font-weight: 600; }
</style>
</head>
<body>
<div id="topbar">
  <div id="pulse"></div>
  <h1>doc-expand</h1>
  <span class="run-id" id="run-label">—</span>
  <span class="topbar-spacer"></span>
  <button id="stop-btn" onclick="stopRun()">■ Stop</button>
  <span class="top-status" id="top-status">loading…</span>
</div>

<div id="main">
  <!-- Left: stage list -->
  <div id="stage-list">
    <div id="sl-stages"></div>
    <div class="sl-divider"></div>
    <div class="sl-meta" id="sl-all-events" data-view="all-events">
      <span class="sl-meta-label">all events</span>
    </div>
  </div>

  <!-- Right: detail pane -->
  <div id="detail-pane">
    <div id="detail-header">
      <div class="dh-title" id="dh-title">Select a stage</div>
      <div class="dh-meta" id="dh-meta"></div>
    </div>
    <div id="detail-body">
      <div class="empty" id="detail-placeholder">← click a stage</div>

      <!-- Progress section (shown for stage views) -->
      <div id="progress-section" style="display:none">
        <div class="sec-hdr"><span>Progress</span><span class="sec-hdr-line"></span></div>
        <div id="progress-log"></div>
      </div>

      <!-- Results section (shown for stage views) -->
      <div id="results-section" style="display:none">
        <div class="sec-hdr"><span>Results</span><span class="sec-hdr-line"></span></div>
        <div id="results-area"></div>
        <div id="inline-section"></div>
      </div>

      <!-- All-events view -->
      <div id="all-events-view" style="display:none">
        <div id="all-events-filter">
          <input id="evt-filter-input" placeholder="filter by event name…" />
        </div>
        <div id="all-events-log"></div>
      </div>
    </div>
  </div>

  <!-- Far right: run pane -->
  <div id="run-pane">
    <div id="run-header">
      <span id="run-pane-title">Run</span>
      <span id="run-state-badge" class="badge badge-pending">idle</span>
    </div>
    <div id="run-body">
      <!-- mode: setup -->
      <div id="run-setup">
        <div>
          <div class="rf-label">Input (file path or URL)</div>
          <div class="rf-input-row">
            <input id="run-input" class="rf-input" placeholder="./paper.pdf or https://…" />
            <button id="browse-btn" onclick="toggleBrowser()">⋯</button>
          </div>
          <div id="file-browser">
            <div id="fb-crumb">/</div>
            <div id="fb-list"></div>
          </div>
        </div>
        <div>
          <div class="rf-label">Depth</div>
          <select id="run-depth" class="rf-select">
            <option value="standard" selected>standard</option>
            <option value="survey">survey</option>
            <option value="deep">deep</option>
          </select>
        </div>
        <label class="rf-check">
          <input type="checkbox" id="run-auto-tax" checked />
          Auto-taxonomy (skip manual review)
        </label>
        <label class="rf-check">
          <input type="checkbox" id="run-no-pdf" checked />
          Skip PDF render
        </label>
        <label class="rf-check">
          <input type="checkbox" id="run-resume" />
          Resume (keep completed stages)
        </label>

        <!-- Models & Keys collapsible -->
        <div class="mk-section">
          <div class="mk-header" onclick="toggleMkPanel()">
            <span>⚙</span> Models &amp; Keys
            <span class="mk-chevron" id="mk-chevron">▶</span>
          </div>
          <div class="mk-body collapsed" id="mk-body">
            <div class="rf-label">API Keys</div>
            <div class="key-row">
              <span class="key-dot" id="dot-anthropic"></span>
              <input class="rf-input" id="key-anthropic" type="password" placeholder="Anthropic sk-ant-…"
                     oninput="updateKeyDot('anthropic')" />
              <button class="key-eye" onclick="toggleKeyVis('key-anthropic')">👁</button>
            </div>
            <div class="key-row">
              <span class="key-dot" id="dot-openai"></span>
              <input class="rf-input" id="key-openai" type="password" placeholder="OpenAI sk-…"
                     oninput="updateKeyDot('openai')" />
              <button class="key-eye" onclick="toggleKeyVis('key-openai')">👁</button>
            </div>
            <div class="key-row">
              <span class="key-dot" id="dot-gemini"></span>
              <input class="rf-input" id="key-gemini" type="password" placeholder="Gemini AIza…"
                     oninput="updateKeyDot('gemini')" />
              <button class="key-eye" onclick="toggleKeyVis('key-gemini')">👁</button>
            </div>
            <div class="mk-divider"></div>
            <div class="rf-label">Primary model (first tried for all roles)</div>
            <div id="provider-sections">
              <span style="color:var(--muted);font-size:11px">loading…</span>
            </div>
            <input class="rf-input" id="custom-model-input" placeholder="or type any model ID…"
                   style="font-size:11px;padding:5px 8px;margin-top:2px"
                   oninput="onCustomModelInput(this.value)" />
          </div>
        </div>

        <button id="run-start-btn" onclick="startRun()">▶ Start Run</button>
      </div>
      <!-- mode: qa (chat interface) -->
      <div id="run-qa" style="display:none; flex-direction:column; flex:1">
        <div id="chat-messages"></div>
        <div id="chat-input-area" style="display:none">
          <input id="chat-input" placeholder="type your answer…" />
          <button id="chat-send">→</button>
        </div>
      </div>
      <!-- mode: running -->
      <div id="run-active" style="display:none; padding:14px">
        <div style="color:var(--muted); font-size:12px; font-family:var(--font-mono)">pipeline running…</div>
      </div>
    </div>
  </div>
</div>

<script>
const $ = id => document.getElementById(id);
const STAGE_ORDER = ['0','1','2','3','4','5','6','7','8','9','10'];
const STAGE_NAMES = {
  '0':'Ingest','1':'Assess','2':'Extract','3':'Graph','4':'Audit',
  '5':'Research','6':'Align','7':'Synthesize','8':'Verify','9':'Prereq','10':'Assemble'
};

let allEvents = [];
let stageData = {};       // { [stageId]: status + timing from /api/status }
let domainData = [];
let selectedView = null;  // stageId string or 'all-events'
let stageArtifacts = {};  // cache { [stageId]: artifact data }

// ── Stage list ──────────────────────────────────────────────────────────────
function renderStageList(stages) {
  stageData = stages;
  const container = $('sl-stages');
  const anyRunning = Object.values(stages).some(s => s.status === 'running');
  $('stop-btn').style.display = anyRunning ? 'inline-block' : 'none';

  container.innerHTML = STAGE_ORDER.map(sid => {
    const s = stages[sid] || {};
    const status = s.status || 'pending';
    const dotCls = {complete:'done', running:'running', error:'error', skipped:'skipped'}[status] || '';
    const active = selectedView === sid ? ' active' : '';
    const canClear = status === 'complete' || status === 'error' || status === 'skipped';
    const clearBtn = canClear
      ? `<span class="sl-clear" onclick="event.stopPropagation();clearStage('${sid}')" title="Clear stage">×</span>`
      : `<span class="sl-clear" style="visibility:hidden">×</span>`;
    return `<div class="sl-item${active}" data-stage="${sid}" onclick="selectStage('${sid}')">
      <span class="sl-num">S${sid}</span>
      <span class="sl-name">${STAGE_NAMES[sid]}</span>
      <span class="sl-dot ${dotCls}"></span>
      ${clearBtn}
    </div>`;
  }).join('');

  $('sl-all-events').className = 'sl-meta' + (selectedView === 'all-events' ? ' active' : '');
}

// ── Build stage timeline from events ────────────────────────────────────────
function buildStageWindows(events) {
  const windows = {};
  for (const evt of events) {
    const stage = String(evt.stage ?? '');
    if (!stage) continue;
    if (evt.event === 'stage_start') {
      if (!windows[stage]) windows[stage] = { start: evt.ts, end: null };
    } else if (evt.event === 'stage_complete' || evt.event === 'stage_skipped') {
      if (!windows[stage]) windows[stage] = { start: null, end: evt.ts };
      else windows[stage].end = evt.ts;
    }
  }
  return windows;
}

function eventsForStage(sid, windows) {
  const win = windows[sid];
  if (!win) return allEvents.filter(e => String(e.stage ?? '') === sid);

  const start = win.start;
  const end = win.end;
  return allEvents.filter(evt => {
    if (String(evt.stage ?? '') === sid) return true;
    const ts = evt.ts || '';
    if (start && ts < start) return false;
    if (end && ts > end) return false;
    if (!start && !end) return false;
    return true;
  });
}

// ── Detail header ────────────────────────────────────────────────────────────
function renderDetailHeader(sid) {
  const s = stageData[sid] || {};
  const status = s.status || 'pending';
  const name = STAGE_NAMES[sid] || sid;
  $('dh-title').textContent = `Stage ${sid} — ${name}`;

  const badgeCls = {complete:'badge-done', running:'badge-running', error:'badge-error',
                    skipped:'badge-skipped', pending:'badge-pending'}[status] || 'badge-pending';
  let meta = `<span class="badge ${badgeCls}">${status}</span>`;
  if (s.started_at) meta += `<span>started ${s.started_at.slice(11,19)}</span>`;
  if (s.completed_at) meta += `<span>done ${s.completed_at.slice(11,19)}</span>`;
  if (s.elapsed_s != null) meta += `<span>${s.elapsed_s}s</span>`;
  $('dh-meta').innerHTML = meta;
}

// ── Progress log ─────────────────────────────────────────────────────────────
function renderProgress(events) {
  const log = $('progress-log');
  if (!events.length) {
    log.innerHTML = '<div class="pl-empty">no events yet</div>';
    return;
  }
  const atBottom = log.scrollTop + log.clientHeight >= log.scrollHeight - 20;
  log.innerHTML = events.slice(-400).map(evt => {
    const e = evt.event || '';
    const ts = (evt.ts || '').slice(11, 23);
    let cls = '';
    if (e.startsWith('stage')) cls = 'stage';
    else if (e.startsWith('llm')) cls = 'llm';
    else if (e.startsWith('agent')) cls = 'agent';
    else if (e.includes('error') || e.includes('fail')) cls = 'error';
    else if (e.startsWith('ss')) cls = 'ss';
    const body = Object.entries(evt)
      .filter(([k]) => !['event', 'ts', 'stage'].includes(k))
      .map(([k, v]) => `${k}=${JSON.stringify(v)}`).join('  ');
    return `<div class="pl-row">
      <span class="pl-ts">${ts}</span>
      <span class="pl-evt ${cls}">${e}</span>
      <span class="pl-body">${body}</span>
    </div>`;
  }).join('');
  if (atBottom) log.scrollTop = log.scrollHeight;
}

// ── Results area ──────────────────────────────────────────────────────────────
function renderResults(sid, artifacts) {
  const area = $('results-area');
  $('inline-section').className = '';
  $('inline-section').innerHTML = '';
  area.innerHTML = '';

  if (!artifacts || artifacts.error) {
    area.innerHTML = '<div class="empty">no results yet</div>';
    return;
  }

  if (sid === '0') renderIngest(area, artifacts);
  else if (sid === '1') renderAssess(area, artifacts);
  else if (sid === '2') renderExtract(area, artifacts);
  else if (sid === '3') renderGraph(area, artifacts);
  else if (sid === '4') renderAudit(area, artifacts);
  else if (sid === '5') renderResearch(area, artifacts);
  else if (sid === '6') renderAlign(area, artifacts);
  else if (sid === '7') renderSynthesize(area, artifacts);
  else if (sid === '8') renderVerify(area, artifacts);
  else if (sid === '9') renderPrereq(area, artifacts);
  else if (sid === '10') renderAssemble(area, artifacts);
  else area.innerHTML = '<div class="empty">no results view for this stage</div>';
}

function kv(rows) {
  return `<div style="background:var(--surface);border:1px solid var(--border);border-radius:5px;overflow:hidden">
    <table class="kv-table">${rows.map(([k,v]) =>
      `<tr><td>${k}</td><td style="color:var(--text)">${v ?? '—'}</td></tr>`
    ).join('')}</table></div>`;
}

function renderIngest(area, a) {
  const m = a.meta || {};
  area.innerHTML = kv([
    ['title', m.title || '—'],
    ['source', m.url || m.file || '—'],
    ['type', m.source_type || '—'],
    ['chunks', a.chunk_count ?? '—'],
    ['tokens (est)', m.token_count != null ? m.token_count.toLocaleString() : '—'],
  ]);
}

function renderAssess(area, a) {
  const p = a.profile || {};
  area.innerHTML = kv([
    ['familiarity', p.familiarity_level],
    ['background', p.background_field],
    ['effective depth', p.effective_depth],
    ['math mode', p.math_mode],
    ['learning goal', p.learning_goal],
    ['known concepts', (p.known_concepts || []).join(', ') || '—'],
    ['unknown concepts', (p.unknown_concepts || []).join(', ') || '—'],
  ]);
}

function renderExtract(area, a) {
  const terms = a.terms || [];
  if (!terms.length) { area.innerHTML = '<div class="empty">no terms yet</div>'; return; }
  area.innerHTML = `<div class="term-chips">${terms.map(t =>
    `<span class="term-chip">${t.name}<span class="cnt">×${t.occurrence_count}</span></span>`
  ).join('')}</div>`;
}

function renderGraph(area, a) {
  const domains = (a.taxonomy || {}).domains || [];
  const strategy = (a.taxonomy || {}).strategy || '';
  let html = kv([
    ['strategy', strategy],
    ['domains', domains.length],
    ['nodes', a.node_count ?? '—'],
    ['edges', a.edge_count ?? '—'],
  ]);
  if (domains.length) {
    html += `<div class="card-grid" style="margin-top:8px">${domains.map(d =>
      `<div class="card">
        <div class="card-label">${d.label}</div>
        <div class="card-meta">${(d.example_terms || []).slice(0,4).join(', ')}</div>
      </div>`
    ).join('')}</div>`;
  }
  area.innerHTML = html;
}

function renderAudit(area, a) {
  let html = kv([['total papers', a.bibliography_count ?? '—']]);
  const gaps = a.gaps || {};
  for (const [did, info] of Object.entries(gaps)) {
    const gapList = info.gaps || [];
    if (!gapList.length) continue;
    html += `<div style="background:var(--surface);border:1px solid var(--border);border-radius:5px;overflow:hidden;margin-top:8px">
      <div style="padding:8px 12px;font-size:12px;font-weight:600;border-bottom:1px solid var(--border)">${info.label}</div>
      ${gapList.map(g => {
        const v = g.verdict || 'pending';
        const vc = v==='real_gap'?'v-real':v==='ambiguous'?'v-ambiguous':'v-not';
        return `<div class="gap-item">
          <div class="gap-desc"><span class="${vc}">[${v}]</span>  ${g.gap_description||''}</div>
          ${g.evidence_anchor_ids?.length ? `<div class="gap-evidence">evidence: ${g.evidence_anchor_ids.join(', ')}</div>` : ''}
        </div>`;
      }).join('')}
    </div>`;
  }
  area.innerHTML = html;
}

let selectedSectionId = null;
function renderResearch(area, a) {
  const domains = a.domains || [];
  if (!domains.length) { area.innerHTML = '<div class="empty">no sections yet</div>'; return; }
  area.innerHTML = `<div class="card-grid">${domains.map(d => {
    const hasSec = d.word_count > 0;
    const cls = hasSec ? 'card done clickable' : 'card';
    const sel = d.id === selectedSectionId ? ' selected' : '';
    return `<div class="${cls}${sel}" onclick="${hasSec ? `loadSection('${d.id}')` : ''}" data-domain="${d.id}">
      <div class="card-label">${d.label}</div>
      <div class="card-meta">${hasSec ? d.word_count.toLocaleString() + ' words' : 'pending'}</div>
    </div>`;
  }).join('')}</div>`;
}

function renderAlign(area, a) {
  const domains = a.domains || [];
  if (!domains.length) { area.innerHTML = '<div class="empty">no alignment data yet</div>'; return; }
  const CHECKS = [
    ['what_is', '"What is X?" intro'],
    ['symbols', 'Symbol tables before equations'],
    ['examples', 'Worked examples'],
    ['where_next', '"Where to Go Next" section'],
    ['citations', '[NEEDS_CITATION] resolved'],
  ];
  area.innerHTML = domains.map(d => {
    const cl = d.checklist || {};
    return `<div style="background:var(--surface);border:1px solid var(--border);border-radius:5px;overflow:hidden;margin-bottom:8px">
      <div style="padding:8px 12px;font-size:12px;font-weight:600;border-bottom:1px solid var(--border)">
        ${d.label}${d.aligned ? ' <span style="color:var(--green);font-weight:400;font-size:11px">aligned</span>' : ''}
      </div>
      ${CHECKS.map(([key, label]) => {
        const pass = cl[key];
        const dotCls = pass === true ? 'pass' : pass === false ? 'fail' : '';
        return `<div class="check-row"><span class="check-label">${label}</span><span class="check-dot ${dotCls}"></span></div>`;
      }).join('')}
    </div>`;
  }).join('');
}

function renderSynthesize(area, a) {
  let html = kv([
    ['word count', a.word_count != null ? a.word_count.toLocaleString() : '—'],
  ]);
  if (a.reading_roadmap?.length) {
    html += `<div style="background:var(--surface);border:1px solid var(--border);border-radius:5px;padding:10px 14px;margin-top:8px">
      <div style="font-size:11px;color:var(--muted);margin-bottom:8px;font-weight:600;letter-spacing:.05em;text-transform:uppercase">reading roadmap</div>
      ${a.reading_roadmap.map((step, i) =>
        `<div style="font-size:12px;padding:4px 0;color:var(--text)"><span style="color:var(--muted);font-family:var(--font-mono);margin-right:8px">${i+1}.</span>${step}</div>`
      ).join('')}
    </div>`;
  }
  area.innerHTML = html;
  if (a.word_count > 0) {
    loadSection('synthesis');
  }
}

function renderVerify(area, a) {
  area.innerHTML = kv([
    ['[NEEDS_CITATION] remaining', a.needs_citation_count ?? '—'],
    ['unknown citation keys', a.unknown_keys ?? '—'],
    ['verified citations', a.verified ?? '—'],
  ]);
}

function renderPrereq(area, a) {
  area.innerHTML = kv([
    ['primers inserted', a.primers_inserted ?? '—'],
    ['domains processed', a.domains_processed ?? '—'],
  ]);
}

function renderAssemble(area, a) {
  area.innerHTML = kv([
    ['output', a.output_path || '—'],
    ['total words', a.word_count != null ? a.word_count.toLocaleString() : '—'],
    ['bibliography entries', a.bibliography_count ?? '—'],
  ]);
}

// ── Section inline loader ─────────────────────────────────────────────────────
async function loadSection(id) {
  const prev = selectedSectionId;
  selectedSectionId = id;
  // re-render cards so selected state updates
  if (selectedView === '5') renderResearch($('results-area'), stageArtifacts['5']);

  const view = $('inline-section');
  view.className = 'visible';
  view.innerHTML = '<p style="color:var(--muted);padding:1rem;font-size:12px">loading…</p>';
  try {
    const r = await fetch('/api/section/' + id);
    const d = await r.json();
    view.innerHTML = d.html || '<p class="empty">not available yet</p>';
  } catch {
    view.innerHTML = '<p class="empty">failed to load</p>';
  }
  view.scrollTop = 0;
}

// ── Select stage ──────────────────────────────────────────────────────────────
async function selectStage(sid) {
  selectedView = sid;
  selectedSectionId = null;
  $('sl-all-events').classList.remove('active');

  $('detail-placeholder').style.display = 'none';
  $('progress-section').style.display = 'block';
  $('results-section').style.display = 'block';
  $('all-events-view').style.display = 'none';
  $('inline-section').className = '';
  $('inline-section').innerHTML = '';

  renderDetailHeader(sid);
  const windows = buildStageWindows(allEvents);
  renderProgress(eventsForStage(sid, windows));

  // Load artifacts (cached)
  if (stageArtifacts[sid]) {
    renderResults(sid, stageArtifacts[sid]);
  } else {
    $('results-area').innerHTML = '<div class="empty">loading…</div>';
    try {
      const r = await fetch('/api/stage/' + sid);
      const a = await r.json();
      stageArtifacts[sid] = a;
      renderResults(sid, a);
    } catch {
      $('results-area').innerHTML = '<div class="empty">failed to load</div>';
    }
  }
}

// ── All-events view ───────────────────────────────────────────────────────────
$('sl-all-events').addEventListener('click', () => {
  selectedView = 'all-events';
  $('detail-placeholder').style.display = 'none';
  $('progress-section').style.display = 'none';
  $('results-section').style.display = 'none';
  $('all-events-view').style.display = 'flex';
  $('dh-title').textContent = 'All Events';
  $('dh-meta').innerHTML = '';
  $('sl-all-events').classList.add('active');
  document.querySelectorAll('.sl-item').forEach(el => el.classList.remove('active'));
  renderAllEvents();
});

function renderAllEvents() {
  const filter = $('evt-filter-input').value.trim().toLowerCase();
  const log = $('all-events-log');
  const atBottom = log.scrollTop + log.clientHeight >= log.scrollHeight - 20;
  const filtered = filter
    ? allEvents.filter(e => (e.event||'').includes(filter) || JSON.stringify(e).toLowerCase().includes(filter))
    : allEvents;
  log.innerHTML = filtered.slice(-500).map(evt => {
    const e = evt.event || '';
    const ts = (evt.ts || '').slice(11, 23);
    let cls = '';
    if (e.startsWith('stage')) cls = 'stage';
    else if (e.startsWith('llm')) cls = 'llm';
    else if (e.startsWith('agent')) cls = 'agent';
    else if (e.includes('error') || e.includes('fail')) cls = 'error';
    else if (e.startsWith('ss')) cls = 'ss';
    const body = Object.entries(evt)
      .filter(([k]) => !['event','ts'].includes(k))
      .map(([k,v]) => `${k}=${JSON.stringify(v)}`).join('  ');
    return `<div class="pl-row">
      <span class="pl-ts">${ts}</span>
      <span class="pl-evt ${cls}">${e}</span>
      <span class="pl-body">${body}</span>
    </div>`;
  }).join('');
  if (atBottom) log.scrollTop = log.scrollHeight;
}
$('evt-filter-input').addEventListener('input', renderAllEvents);

// ── Polling ───────────────────────────────────────────────────────────────────
let lastStatusEtag = '', lastEventsEtag = '';

async function poll() {
  try {
    // Status
    const sr = await fetch('/api/status', {headers: lastStatusEtag ? {'If-None-Match': lastStatusEtag} : {}});
    if (sr.status !== 304) {
      lastStatusEtag = sr.headers.get('ETag') || '';
      const s = await sr.json();
      renderStageList(s.stages || {});
      domainData = s.domains || [];
      $('run-label').textContent = s.run_id || '';
      $('top-status').textContent = s.summary || '';
      const alive = !s.stages?.['10']?.status || s.stages?.['10']?.status === 'running';
      $('pulse').className = 'pulse' + (alive ? ' live' : '');

      // Invalidate artifact cache for stages that are now running
      for (const [sid, st] of Object.entries(s.stages || {})) {
        if (st.status === 'running' || st.status === 'complete') {
          delete stageArtifacts[sid];
        }
      }
    }

    // Events
    const er = await fetch('/api/events', {headers: lastEventsEtag ? {'If-None-Match': lastEventsEtag} : {}});
    if (er.status !== 304) {
      lastEventsEtag = er.headers.get('ETag') || '';
      const ed = await er.json();
      allEvents = ed.events || [];

      // Refresh active view
      if (selectedView && selectedView !== 'all-events') {
        const windows = buildStageWindows(allEvents);
        renderProgress(eventsForStage(selectedView, windows));
      } else if (selectedView === 'all-events') {
        renderAllEvents();
      }
    }
  } catch {
    $('top-status').textContent = 'disconnected';
  }
  await pollQa();
}

poll();
setInterval(poll, 2000);

// Initialize key dots from server env on load
(async () => {
  try {
    const d = await (await fetch('/api/keys')).json();
    ['anthropic', 'openai', 'gemini'].forEach(p => {
      _envKeysSet[p] = !!d[p];
      $('dot-' + p).classList.toggle('set', !!d[p]);
    });
  } catch {}
})();

// ── Stop / clear ─────────────────────────────────────────────────────────
async function stopRun() {
  await fetch('/api/stop', {method: 'POST'});
  lastStatusEtag = '';
  $('stop-btn').style.display = 'none';
}

async function clearStage(sid) {
  await fetch(`/api/stage/${sid}/clear`, {method: 'POST'});
  delete stageArtifacts[sid];
  lastStatusEtag = '';
  // If we're viewing this stage, refresh header
  if (selectedView === sid) renderDetailHeader(sid);
}

// ── Run pane ──────────────────────────────────────────────────────────────────
let runPaneMode = 'setup'; // 'setup' | 'qa' | 'running'
let currentQaId = null;
let qaHistory = []; // [{question: str, answer: str}]

function setRunMode(mode) {
  runPaneMode = mode;
  $('run-setup').style.display = mode === 'setup' ? 'flex' : 'none';
  $('run-qa').style.display = mode === 'qa' ? 'flex' : 'none';
  $('run-active').style.display = mode === 'running' ? 'block' : 'none';
  const badge = $('run-state-badge');
  badge.className = 'badge ' + {setup:'badge-pending', qa:'badge-running', running:'badge-running'}[mode];
  badge.textContent = {setup:'idle', qa:'interview', running:'running'}[mode];
}

// Models & Keys panel
let mkOpen = false;
let selectedModel = null;

function toggleMkPanel() {
  mkOpen = !mkOpen;
  $('mk-body').classList.toggle('collapsed', !mkOpen);
  $('mk-chevron').classList.toggle('open', mkOpen);
  if (mkOpen && $('provider-sections').children.length <= 1) loadModels();
}

function toggleKeyVis(id) {
  const el = $(id);
  el.type = el.type === 'password' ? 'text' : 'password';
}

const _envKeysSet = {};  // populated by /api/keys on load

function updateKeyDot(provider) {
  const val = $('key-' + provider).value.trim();
  $('dot-' + provider).classList.toggle('set', val.length > 0 || !!_envKeysSet[provider]);
}

async function loadModels() {
  try {
    const r = await fetch('/api/models');
    const d = await r.json();

    // Pre-green dots for keys already set in server env
    if (d.keys_set) {
      ['anthropic', 'openai', 'gemini'].forEach(p => {
        if (d.keys_set[p]) $('dot-' + p).classList.add('set');
      });
    }

    const container = $('provider-sections');
    container.innerHTML = '';

    // "auto" chip at top (no model override)
    const autoRow = document.createElement('div');
    autoRow.style.cssText = 'padding-bottom:5px';
    const auto = document.createElement('span');
    auto.className = 'model-chip active';
    auto.textContent = 'auto';
    auto.dataset.model = '';
    auto.onclick = () => selectModel('', auto);
    autoRow.appendChild(auto);
    container.appendChild(autoRow);

    const PROVIDER_LABEL = {anthropic: 'Anthropic', openai: 'OpenAI', gemini: 'Google Gemini'};

    // Group by provider, then float providers with env keys to top
    const sections = {};
    const order = [];
    for (const m of d.models) {
      if (!sections[m.provider]) { sections[m.provider] = []; order.push(m.provider); }
      sections[m.provider].push(m);
    }
    const keysSet = d.keys_set || {};
    order.sort((a, b) => (!!keysSet[b] - !!keysSet[a]));

    for (const prov of order) {
      const section = document.createElement('div');
      section.className = 'provider-section';
      const hdr = document.createElement('div');
      hdr.className = 'provider-section-hdr';
      hdr.textContent = PROVIDER_LABEL[prov] || prov;
      section.appendChild(hdr);
      const chips = document.createElement('div');
      chips.className = 'provider-section-chips';
      for (const m of sections[prov]) {
        const chip = document.createElement('span');
        chip.className = 'model-chip';
        chip.textContent = m.label;
        chip.title = m.id;
        chip.dataset.model = m.id;
        chip.onclick = () => selectModel(m.id, chip);
        chips.appendChild(chip);
      }
      section.appendChild(chips);
      container.appendChild(section);
    }
  } catch(e) {
    $('provider-sections').innerHTML = '<span style="color:var(--muted);font-size:11px">unavailable</span>';
  }
}

function selectModel(modelId, chipEl) {
  selectedModel = modelId || null;
  document.querySelectorAll('.model-chip').forEach(c => c.classList.remove('active'));
  chipEl.classList.add('active');
  if (modelId !== '') $('custom-model-input').value = '';
}

function onCustomModelInput(val) {
  val = val.trim();
  if (val) {
    selectedModel = val;
    document.querySelectorAll('.model-chip').forEach(c => c.classList.remove('active'));
  } else {
    // revert to auto
    selectedModel = null;
    const autoChip = document.querySelector('.model-chip[data-model=""]');
    if (autoChip) autoChip.classList.add('active');
  }
}

async function startRun() {
  const input = $('run-input').value.trim();
  if (!input) { $('run-input').focus(); return; }
  const depth = $('run-depth').value;
  const autoTax = $('run-auto-tax').checked;
  const noPdf = $('run-no-pdf').checked;
  const resume = $('run-resume').checked;

  const apiKeys = {
    anthropic: $('key-anthropic').value.trim(),
    openai: $('key-openai').value.trim(),
    gemini: $('key-gemini').value.trim(),
  };

  $('run-start-btn').disabled = true;
  $('run-start-btn').textContent = 'starting…';

  try {
    await fetch('/api/run', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        input, depth, auto_taxonomy: autoTax, no_pdf: noPdf,
        resume,
        api_keys: apiKeys,
        primary_model: selectedModel || '',
      }),
    });

    // Invalidate status cache so stage list refreshes
    lastStatusEtag = '';

    if (!autoTax) {
      setRunMode('qa');
      $('run-qa').style.flexDirection = 'column';
    } else {
      setRunMode('running');
    }
  } catch(e) {
    $('run-start-btn').disabled = false;
    $('run-start-btn').textContent = '▶ Start Run';
  }
}

// File browser
let fbOpen = false;
let fbCwd = null;

function toggleBrowser() {
  const el = $('file-browser');
  fbOpen = !fbOpen;
  el.style.display = fbOpen ? 'flex' : 'none';
  if (fbOpen && fbCwd === null) loadDir('.');
}

async function loadDir(dir) {
  const r = await fetch('/api/files?dir=' + encodeURIComponent(dir));
  if (!r.ok) return;
  const d = await r.json();
  fbCwd = d.cwd;
  $('fb-crumb').textContent = fbCwd;
  const list = $('fb-list');
  list.innerHTML = '';

  // Parent dir entry
  if (d.parent) {
    const row = document.createElement('div');
    row.className = 'fb-entry';
    row.innerHTML = '<span class="fb-icon">↑</span><span class="fb-name dir">..</span>';
    row.onclick = () => loadDir(d.parent);
    list.appendChild(row);
  }

  for (const e of d.entries) {
    const row = document.createElement('div');
    row.className = 'fb-entry';
    const sizeStr = e.type === 'file' ? _fmtBytes(e.size) : '';
    row.innerHTML = `<span class="fb-icon">${e.type === 'dir' ? '▶' : '·'}</span>` +
      `<span class="fb-name ${e.type === 'dir' ? 'dir' : ''}">${e.name}</span>` +
      `<span class="fb-size">${sizeStr}</span>`;
    if (e.type === 'dir') {
      row.onclick = () => loadDir(e.path);
    } else {
      row.onclick = () => {
        $('run-input').value = e.path;
        fbOpen = false;
        $('file-browser').style.display = 'none';
      };
    }
    list.appendChild(row);
  }
}

function _fmtBytes(n) {
  if (n == null) return '';
  if (n < 1024) return n + 'B';
  if (n < 1048576) return (n/1024).toFixed(0) + 'K';
  return (n/1048576).toFixed(1) + 'M';
}

// Q&A polling (called from main poll loop)
async function pollQa() {
  if (runPaneMode !== 'qa') return;

  try {
    const r = await fetch('/api/qa');
    const d = await r.json();

    // Rebuild history messages if history changed
    const hist = d.history || [];
    if (hist.length !== qaHistory.length) {
      qaHistory = hist;
      rebuildChatHistory();
    }

    if (d.interview_complete) {
      // Remove thinking indicator, show completion
      removeThinking();
      appendAgentBubble('Profile complete. Running pipeline…');
      setRunMode('running');
      return;
    }

    if (d.question && d.question.id !== currentQaId) {
      removeThinking();
      currentQaId = d.question.id;
      renderQuestion(d.question);
    } else if (!d.question && currentQaId) {
      // Waiting for next question
      ensureThinking();
    }
  } catch(e) {}
}

function rebuildChatHistory() {
  // Only add bubbles for history items not already shown
  // (Simple: clear and re-add all — but that loses in-progress state)
  // Better: track which IDs we've shown
}

function renderQuestion(q) {
  const area = $('chat-messages');

  // Agent bubble
  const msgEl = document.createElement('div');
  msgEl.className = 'chat-msg agent';
  const bubble = document.createElement('div');
  bubble.className = 'chat-bubble agent';
  bubble.textContent = q.text;
  msgEl.appendChild(bubble);

  if (q.question_type === 'mc' && q.options?.length) {
    const opts = document.createElement('div');
    opts.className = 'chat-options';
    q.options.forEach(opt => {
      const btn = document.createElement('button');
      btn.className = 'chat-opt';
      btn.textContent = opt;
      btn.onclick = () => submitAnswer(q.id, opt, msgEl);
      opts.appendChild(btn);
    });
    msgEl.appendChild(opts);
    $('chat-input-area').style.display = 'none';
  } else {
    $('chat-input-area').style.display = 'flex';
    $('chat-input').focus();
    $('chat-send').onclick = () => {
      const val = $('chat-input').value.trim();
      if (val) submitAnswer(q.id, val, msgEl);
    };
    $('chat-input').onkeydown = (e) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        const val = $('chat-input').value.trim();
        if (val) submitAnswer(q.id, val, msgEl);
      }
    };
  }

  area.appendChild(msgEl);
  area.scrollTop = area.scrollHeight;
}

async function submitAnswer(qid, answer, questionEl) {
  // Disable options
  questionEl.querySelectorAll('.chat-opt').forEach(b => {
    b.disabled = true;
    if (b.textContent === answer) b.classList.add('chosen');
  });
  $('chat-input').value = '';
  $('chat-input-area').style.display = 'none';

  // User bubble
  const area = $('chat-messages');
  const userEl = document.createElement('div');
  userEl.className = 'chat-msg user-msg';
  const bub = document.createElement('div');
  bub.className = 'chat-bubble user-bub';
  bub.textContent = answer;
  userEl.appendChild(bub);
  area.appendChild(userEl);
  area.scrollTop = area.scrollHeight;

  ensureThinking();
  currentQaId = null;

  await fetch('/api/qa/answer', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({id: qid, answer}),
  });
}

function appendAgentBubble(text) {
  const area = $('chat-messages');
  const el = document.createElement('div');
  el.className = 'chat-msg agent';
  el.innerHTML = `<div class="chat-bubble agent">${text}</div>`;
  area.appendChild(el);
  area.scrollTop = area.scrollHeight;
}

let thinkingEl = null;
function ensureThinking() {
  if (thinkingEl) return;
  const area = $('chat-messages');
  thinkingEl = document.createElement('div');
  thinkingEl.className = 'chat-msg agent';
  thinkingEl.innerHTML = '<div class="chat-bubble agent" style="color:var(--muted)">thinking…</div>';
  area.appendChild(thinkingEl);
  area.scrollTop = area.scrollHeight;
}
function removeThinking() {
  if (thinkingEl) { thinkingEl.remove(); thinkingEl = null; }
}
</script>
</body>
</html>
"""


def _load_state(state_dir: Path) -> dict:
    result: dict = {"stages": {}, "domains": [], "run_id": "", "summary": ""}

    pipeline_path = state_dir / "pipeline.json"
    if pipeline_path.exists():
        try:
            data = json.loads(pipeline_path.read_text())
            result["stages"] = data.get("stages", {})
            result["run_id"] = data.get("run_id", "")
        except Exception:
            pass

    tax_path = state_dir / "taxonomy.json"
    if not tax_path.exists():
        return result

    try:
        tax = json.loads(tax_path.read_text())
    except Exception:
        return result

    graph_path = state_dir / "graph.json"
    node_counts: dict[str, int] = {}
    if graph_path.exists():
        try:
            g = json.loads(graph_path.read_text())
            for n in g.get("nodes", []):
                did = n.get("domain", "")
                node_counts[did] = node_counts.get(did, 0) + 1
        except Exception:
            pass

    sections_dir = state_dir / "sections"
    checklist_by_domain: dict[str, dict] = {}
    logs_dir = state_dir.parent / "logs"
    if logs_dir.exists():
        run_dirs = sorted(logs_dir.iterdir(), key=lambda d: d.stat().st_mtime, reverse=True)
        for rd in run_dirs[:3]:
            ef = rd / "events.jsonl"
            if not ef.exists():
                continue
            try:
                for line in ef.read_text(errors="replace").splitlines():
                    try:
                        evt = json.loads(line)
                    except Exception:
                        continue
                    if evt.get("event") in ("s6_domain_aligned", "s4_5_domain_aligned"):
                        did = evt.get("domain_id", "")
                        cl = evt.get("checklist", {})
                        checklist_by_domain[did] = {
                            "what_is": cl.get("what_is_section"),
                            "symbols": cl.get("symbol_tables"),
                            "examples": cl.get("worked_examples"),
                            "where_next": cl.get("where_to_go_next"),
                            "citations": cl.get("citations_resolved"),
                        }
            except Exception:
                pass
            if checklist_by_domain:
                break

    domains = []
    done_stages = result.get("stages", {})
    n_done = sum(1 for v in done_stages.values() if v.get("status") == "complete")
    for d in tax.get("domains", []):
        did = d["id"]
        section_path = sections_dir / f"section_{did}.md"
        word_count = 0
        if section_path.exists():
            word_count = len(section_path.read_text().split())
        aligned = (sections_dir / f"section_{did}.aligned").exists()
        domains.append({
            "id": did,
            "label": d.get("label", did),
            "node_count": node_counts.get(did, 0),
            "word_count": word_count,
            "aligned": aligned,
            "checklist": checklist_by_domain.get(did, {}),
        })

    stage10_done = done_stages.get("10", {}).get("status") == "complete"
    total_words = sum(d["word_count"] for d in domains)
    result["domains"] = domains
    result["summary"] = (
        f"{n_done}/11 stages · {total_words:,} words · "
        f"{'complete' if stage10_done else 'running'}"
    )
    return result


def _load_stage_artifacts(stage_id: str, state_dir: Path) -> dict:
    """Load stage-specific artifacts for the detail pane results section."""
    out: dict = {}

    if stage_id == "0":
        meta_path = state_dir / "source_meta.json"
        if meta_path.exists():
            try:
                out["meta"] = json.loads(meta_path.read_text())
            except Exception:
                pass
        chunks_dir = state_dir / "chunks"
        if chunks_dir.exists():
            out["chunk_count"] = len(list(chunks_dir.glob("*.json")))

    elif stage_id == "1":
        profile_path = state_dir / "user_profile.json"
        if profile_path.exists():
            try:
                out["profile"] = json.loads(profile_path.read_text())
            except Exception:
                pass

    elif stage_id == "2":
        terms_path = state_dir / "terms.json"
        if terms_path.exists():
            try:
                data = json.loads(terms_path.read_text())
                terms = data.get("terms", [])
                terms.sort(key=lambda t: t.get("occurrence_count", 0), reverse=True)
                out["terms"] = terms[:40]
            except Exception:
                pass

    elif stage_id == "3":
        tax_path = state_dir / "taxonomy.json"
        if tax_path.exists():
            try:
                out["taxonomy"] = json.loads(tax_path.read_text())
            except Exception:
                pass
        graph_path = state_dir / "graph.json"
        if graph_path.exists():
            try:
                g = json.loads(graph_path.read_text())
                out["node_count"] = len(g.get("nodes", []))
                out["edge_count"] = len(g.get("edges", []))
            except Exception:
                pass

    elif stage_id == "4":
        audit_dir = state_dir / "audit"
        total_papers = 0
        if audit_dir.exists():
            for bib in audit_dir.glob("bibliography_*.json"):
                try:
                    total_papers += len(json.loads(bib.read_text()))
                except Exception:
                    pass
        out["bibliography_count"] = total_papers

        tax_path = state_dir / "taxonomy.json"
        gaps: dict = {}
        if tax_path.exists():
            try:
                tax = json.loads(tax_path.read_text())
                for d in tax.get("domains", []):
                    did = d["id"]
                    gf = audit_dir / f"gap_{did}.json" if audit_dir.exists() else Path("/nonexistent")
                    gap_list: list = []
                    if gf.exists():
                        try:
                            gap_list = json.loads(gf.read_text())
                        except Exception:
                            pass
                    gaps[did] = {"label": d.get("label", did), "gaps": gap_list}
            except Exception:
                pass
        out["gaps"] = gaps

    elif stage_id == "5":
        tax_path = state_dir / "taxonomy.json"
        sections_dir = state_dir / "sections"
        domains = []
        if tax_path.exists():
            try:
                tax = json.loads(tax_path.read_text())
                for d in tax.get("domains", []):
                    did = d["id"]
                    sf = sections_dir / f"section_{did}.md"
                    wc = len(sf.read_text().split()) if sf.exists() else 0
                    domains.append({"id": did, "label": d.get("label", did), "word_count": wc})
            except Exception:
                pass
        out["domains"] = domains

    elif stage_id == "6":
        tax_path = state_dir / "taxonomy.json"
        sections_dir = state_dir / "sections"
        domains = []
        if tax_path.exists():
            try:
                tax = json.loads(tax_path.read_text())
                logs_dir = state_dir.parent / "logs"
                checklist_by_domain: dict = {}
                if logs_dir.exists():
                    run_dirs = sorted(logs_dir.iterdir(), key=lambda d: d.stat().st_mtime, reverse=True)
                    for rd in run_dirs[:3]:
                        ef = rd / "events.jsonl"
                        if not ef.exists():
                            continue
                        try:
                            for line in ef.read_text(errors="replace").splitlines():
                                try:
                                    evt = json.loads(line)
                                except Exception:
                                    continue
                                if evt.get("event") in ("s6_domain_aligned", "s4_5_domain_aligned"):
                                    did = evt.get("domain_id", "")
                                    cl = evt.get("checklist", {})
                                    checklist_by_domain[did] = {
                                        "what_is": cl.get("what_is_section"),
                                        "symbols": cl.get("symbol_tables"),
                                        "examples": cl.get("worked_examples"),
                                        "where_next": cl.get("where_to_go_next"),
                                        "citations": cl.get("citations_resolved"),
                                    }
                        except Exception:
                            pass
                        if checklist_by_domain:
                            break
                for d in tax.get("domains", []):
                    did = d["id"]
                    aligned = (sections_dir / f"section_{did}.aligned").exists()
                    domains.append({
                        "id": did,
                        "label": d.get("label", did),
                        "aligned": aligned,
                        "checklist": checklist_by_domain.get(did, {}),
                    })
            except Exception:
                pass
        out["domains"] = domains

    elif stage_id == "7":
        sf = state_dir / "sections" / "section_synthesis.md"
        if sf.exists():
            out["word_count"] = len(sf.read_text().split())
        syn_summary = state_dir / "summaries" / "summary_synthesis.json"
        if syn_summary.exists():
            try:
                s = json.loads(syn_summary.read_text())
                out["reading_roadmap"] = s.get("reading_roadmap", [])
                out["boss_nodes"] = s.get("boss_nodes", [])
            except Exception:
                pass

    elif stage_id == "8":
        nc = state_dir / "audit" / "needs_citation.md"
        if nc.exists():
            text = nc.read_text()
            out["needs_citation_count"] = text.count("[NEEDS_CITATION]")
        # Parse verify events for stats
        logs_dir = state_dir.parent / "logs"
        if logs_dir.exists():
            run_dirs = sorted(logs_dir.iterdir(), key=lambda d: d.stat().st_mtime, reverse=True)
            for rd in run_dirs[:1]:
                ef = rd / "events.jsonl"
                if ef.exists():
                    for line in ef.read_text(errors="replace").splitlines():
                        try:
                            evt = json.loads(line)
                        except Exception:
                            continue
                        if evt.get("event") == "stage_complete" and str(evt.get("stage")) == "8":
                            out["needs_citation_count"] = evt.get("total_needs_citation", out.get("needs_citation_count"))
                            out["unknown_keys"] = evt.get("total_unknown_keys")
                            out["verified"] = evt.get("total_verified")
                    break

    elif stage_id == "9":
        logs_dir = state_dir.parent / "logs"
        primers = 0
        domains_seen: set = set()
        if logs_dir.exists():
            run_dirs = sorted(logs_dir.iterdir(), key=lambda d: d.stat().st_mtime, reverse=True)
            for rd in run_dirs[:1]:
                ef = rd / "events.jsonl"
                if ef.exists():
                    for line in ef.read_text(errors="replace").splitlines():
                        try:
                            evt = json.loads(line)
                        except Exception:
                            continue
                        if evt.get("event") in ("s9_primer_inserted", "s6_5_primer_inserted"):
                            primers += 1
                            if evt.get("section_id"):
                                domains_seen.add(evt["section_id"])
                    break
        out["primers_inserted"] = primers
        out["domains_processed"] = len(domains_seen) or None

    elif stage_id == "10":
        output_path = state_dir.parent / "output" / "expanded.md"
        if output_path.exists():
            text = output_path.read_text(errors="replace")
            out["word_count"] = len(text.split())
            out["output_path"] = str(output_path)
        # bibliography count across all domains
        audit_dir = state_dir / "audit"
        total = 0
        if audit_dir.exists():
            for bib in audit_dir.glob("bibliography_*.json"):
                try:
                    total += len(json.loads(bib.read_text()))
                except Exception:
                    pass
        out["bibliography_count"] = total or None

    return out


def _load_events(state_dir: Path) -> list[dict]:
    logs_dir = state_dir.parent / "logs"
    if not logs_dir.exists():
        return []
    run_dirs = sorted(logs_dir.iterdir(), key=lambda d: d.stat().st_mtime, reverse=True)
    for rd in run_dirs[:1]:
        ef = rd / "events.jsonl"
        if ef.exists():
            events = []
            for line in ef.read_text(errors="replace").splitlines():
                try:
                    events.append(json.loads(line))
                except Exception:
                    pass
            return events
    return []


def _render_section(section_path: Path) -> str:
    if not section_path.exists():
        return ""
    text = section_path.read_text(errors="replace")
    try:
        import markdown as _md_lib
        return _md_lib.markdown(text, extensions=["tables", "fenced_code"])
    except ImportError:
        import html as _h
        return "<pre>" + _h.escape(text) + "</pre>"


_models_cache: list[dict] | None = None
_models_cache_ts: float = 0.0
_MODELS_CACHE_TTL = 3600.0  # 1 hour
_LITELLM_PRICES_URL = (
    "https://raw.githubusercontent.com/BerriAI/litellm/main"
    "/model_prices_and_context_window.json"
)

# Prefixes that map a model id → provider key.
# Ordered so longer/more-specific prefixes match first.
_PROVIDER_PREFIXES: list[tuple[str, str]] = [
    ("claude-",          "anthropic"),
    ("anthropic/",       "anthropic"),
    ("gpt-",             "openai"),
    ("o1",               "openai"),
    ("o3",               "openai"),
    ("o4",               "openai"),
    ("openai/",          "openai"),
    ("gemini/",          "gemini"),
    ("google/",          "gemini"),
]

# Substrings that disqualify a model
_BLOCKLIST = (
    "vision", "embed", "audio", "tts", "whisper", "dall-e",
    "instruct", "realtime", "search", "computer-use",
    "image-generation", "container",
    "gpt-3.5",                    # too old
    "gpt-4-0",                    # dated GPT-4 snapshots (gpt-4-0314, gpt-4-0613)
    "gpt-4-3",                    # gpt-4-32k variants
    "gpt-4-vision",
    "1106-preview", "0125-preview", "turbo-preview", "gpt-4-preview",
    "robotics", "learnlm", "lyria", "gemma",  # non-chat Gemini models
    "/gemini-exp-",                            # experimental snapshot variants
)

import re as _re
_DATE_SUFFIX = _re.compile(r'(-\d{8}|-\d{4}-\d{2}-\d{2})$')   # YYYYMMDD or YYYY-MM-DD snapshot suffix


def _provider_of(model_id: str) -> str | None:
    for prefix, provider in _PROVIDER_PREFIXES:
        if model_id.startswith(prefix):
            return provider
    return None


def _fetch_litellm_models() -> list[dict]:
    """Fetch LiteLLM's authoritative model list and return [{id, label, provider}]."""
    import urllib.request
    try:
        with urllib.request.urlopen(_LITELLM_PRICES_URL, timeout=8) as r:
            data: dict = json.loads(r.read().decode())
    except Exception:
        return []

    out: list[dict] = []
    for model_id, meta in data.items():
        if not isinstance(meta, dict):
            continue
        provider = _provider_of(model_id)
        if provider is None:
            continue
        low = model_id.lower()
        if any(b in low for b in _BLOCKLIST):
            continue
        if _DATE_SUFFIX.search(model_id):
            continue
        if ":" in model_id:          # Bedrock ARN variants
            continue
        # Strip provider prefix for the label
        label = model_id
        for prefix, _ in _PROVIDER_PREFIXES:
            if label.startswith(prefix):
                label = label[len(prefix):]
                break
        # Only keep chat models (excludes embedding, image_generation, audio, etc.)
        mode = meta.get("mode", "")
        if mode and mode != "chat":
            continue

        out.append({"id": model_id, "label": label, "provider": provider})

    # Sort: provider order (anthropic → openai → gemini), then by id
    order = {"anthropic": 0, "openai": 1, "gemini": 2}
    out.sort(key=lambda m: (order.get(m["provider"], 9), m["id"]))
    return out


def _get_models() -> list[dict]:
    global _models_cache, _models_cache_ts
    now = time.time()
    if _models_cache is not None and now - _models_cache_ts < _MODELS_CACHE_TTL:
        return _models_cache
    fetched = _fetch_litellm_models()
    if fetched:
        _models_cache = fetched
        _models_cache_ts = now
    elif _models_cache is not None:
        pass  # keep stale cache on network failure
    else:
        _models_cache = []
    return _models_cache


def _list_files(dir_param: str) -> dict:
    """Return directory listing for the file browser."""
    try:
        target = Path(dir_param).expanduser().resolve()
    except Exception:
        target = Path.cwd()
    if not target.is_dir():
        target = target.parent if target.parent.is_dir() else Path.cwd()

    entries = []
    try:
        for p in sorted(target.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
            if p.name.startswith("."):
                continue
            try:
                size = p.stat().st_size if p.is_file() else None
            except OSError:
                size = None
            entries.append({
                "name": p.name,
                "type": "dir" if p.is_dir() else "file",
                "path": str(p),
                "size": size,
            })
    except PermissionError:
        pass

    parent = str(target.parent) if target != target.parent else None
    return {"cwd": str(target), "parent": parent, "entries": entries}


def _spawn_pipeline(state_dir: Path, params: dict) -> int:
    """Clear Q&A state (and stage markers unless resuming), spawn pipeline subprocess, return PID."""
    for fname in ("qa_queue.jsonl", "qa_answers.jsonl", "qa_complete"):
        p = state_dir / fname
        if p.exists():
            p.unlink()

    # Fresh run: wipe stage completion markers so every stage re-runs.
    # Resume run: preserve markers so only incomplete stages run.
    if not params.get("resume", False):
        pipeline_path = state_dir / "pipeline.json"
        if pipeline_path.exists():
            try:
                data = json.loads(pipeline_path.read_text())
                data["stages"] = {}
                pipeline_path.write_text(json.dumps(data, indent=2))
            except Exception:
                pass

    cli_path = Path(sys.executable).parent / "doc-expand"
    if cli_path.exists():
        cmd: list = [str(cli_path)]
    else:
        cmd = [sys.executable, "-m", "doc_expand.cli"]

    input_path = params.get("input", "")
    depth = params.get("depth", "standard")
    auto_taxonomy = params.get("auto_taxonomy", False)
    no_pdf = params.get("no_pdf", False)
    primary_model = params.get("primary_model", "").strip()
    api_keys: dict = params.get("api_keys", {})

    args = [input_path, "--state-dir", str(state_dir), "--depth", str(depth)]
    if auto_taxonomy:
        args.append("--auto-taxonomy")
    if no_pdf:
        args.append("--no-pdf")
    if primary_model:
        args += ["--primary-model", primary_model]

    env = os.environ.copy()
    _KEY_MAP = {
        "anthropic": "ANTHROPIC_API_KEY",
        "openai":    "OPENAI_API_KEY",
        "gemini":    "GEMINI_API_KEY",
    }
    for provider, key_val in api_keys.items():
        if key_val and provider in _KEY_MAP:
            env[_KEY_MAP[provider]] = key_val

    proc = subprocess.Popen(
        cmd + args,
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
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
            return []
        out = []
        for line in path.read_text(errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
        return out

    questions = _read_jsonl(queue_file)
    answers = _read_jsonl(answers_file)
    answered_ids = {a["id"]: a["answer"] for a in answers}

    history = []
    for q in questions:
        qid = q.get("id", "")
        if qid in answered_ids:
            history.append({"question": q.get("text", ""), "answer": answered_ids[qid]})

    if complete_flag.exists():
        return {"question": None, "interview_complete": True, "history": history}

    first_unanswered = None
    for q in questions:
        if q.get("id", "") not in answered_ids:
            first_unanswered = q
            break

    return {"question": first_unanswered, "interview_complete": False, "history": history}


def _post_answer(state_dir: Path, qid: str, answer: str) -> None:
    """Append an answer to qa_answers.jsonl."""
    answers_file = state_dir / "qa_answers.jsonl"
    record = json.dumps({"id": qid, "answer": answer, "ts": time.time()})
    with answers_file.open("a") as f:
        f.write(record + "\n")


def cmd_serve(state_dir: Path, port: int = 7842) -> None:
    # Auto-reap any child processes (pipeline subprocesses) so they don't
    # accumulate as zombies when they exit.
    signal.signal(signal.SIGCHLD, signal.SIG_IGN)

    import hashlib

    def _etag(data: bytes) -> str:
        return '"' + hashlib.md5(data).hexdigest() + '"'

    def _json_response(handler, obj: object) -> None:
        payload = json.dumps(obj, default=str).encode()
        tag = _etag(payload)
        if handler.headers.get("If-None-Match", "") == tag:
            handler.send_response(304)
            handler.end_headers()
            return
        handler.send_response(200)
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        handler.send_header("Content-Length", str(len(payload)))
        handler.send_header("ETag", tag)
        handler.end_headers()
        handler.wfile.write(payload)

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            if self.path == "/":
                body = _PAGE.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            elif self.path == "/api/status":
                _json_response(self, _load_state(state_dir))

            elif self.path == "/api/events":
                _json_response(self, {"events": _load_events(state_dir)})

            elif self.path.startswith("/api/stage/"):
                sid = self.path[len("/api/stage/"):].split("?")[0].strip("/")
                _json_response(self, _load_stage_artifacts(sid, state_dir))

            elif self.path.startswith("/api/section/"):
                sid = self.path[len("/api/section/"):].split("?")[0].strip("/")
                path = state_dir / "sections" / f"section_{sid}.md"
                _json_response(self, {"html": _render_section(path)})

            elif self.path == "/api/qa":
                _json_response(self, _get_qa_state(state_dir))

            elif self.path.startswith("/api/files"):
                from urllib.parse import urlparse, parse_qs
                qs = parse_qs(urlparse(self.path).query)
                dir_param = qs.get("dir", ["."])[0]
                _json_response(self, _list_files(dir_param))

            elif self.path == "/api/keys":
                _json_response(self, {
                    "anthropic": bool(os.environ.get("ANTHROPIC_API_KEY")),
                    "openai":    bool(os.environ.get("OPENAI_API_KEY")),
                    "gemini":    bool(os.environ.get("GEMINI_API_KEY")),
                })

            elif self.path == "/api/models":
                keys_set = {
                    "anthropic": bool(os.environ.get("ANTHROPIC_API_KEY")),
                    "openai":    bool(os.environ.get("OPENAI_API_KEY")),
                    "gemini":    bool(os.environ.get("GEMINI_API_KEY")),
                }
                _json_response(self, {"models": _get_models(), "keys_set": keys_set})

            else:
                self.send_response(404)
                self.end_headers()

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                data = json.loads(body)
            except json.JSONDecodeError:
                data = {}

            if self.path == "/api/run":
                pid = _spawn_pipeline(state_dir, data)
                payload = json.dumps({"ok": True, "pid": pid}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            elif self.path == "/api/stop":
                killed = False
                pid_file = state_dir / "pipeline_pid"
                if pid_file.exists():
                    try:
                        pid = int(pid_file.read_text().strip())
                        os.killpg(os.getpgid(pid), signal.SIGTERM)
                        killed = True
                    except Exception:
                        pass
                    try:
                        pid_file.unlink()
                    except Exception:
                        pass
                payload = json.dumps({"ok": True, "killed": killed}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            elif self.path.startswith("/api/stage/") and self.path.endswith("/clear"):
                sid = self.path[len("/api/stage/"):-len("/clear")]
                pipeline_path = state_dir / "pipeline.json"
                if sid and pipeline_path.exists():
                    try:
                        pipe = json.loads(pipeline_path.read_text())
                        pipe.setdefault("stages", {}).pop(str(sid), None)
                        pipeline_path.write_text(json.dumps(pipe, indent=2))
                    except Exception:
                        pass
                payload = json.dumps({"ok": True}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            elif self.path == "/api/qa/answer":
                qid = data.get("id", "")
                answer = data.get("answer", "")
                _post_answer(state_dir, qid, answer)
                payload = json.dumps({"ok": True}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            else:
                self.send_response(404)
                self.end_headers()

    server = http.server.HTTPServer(("127.0.0.1", port), Handler)
    print(f"http://localhost:{port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
