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
  --bg: #0d0f18; --surface: #131621; --surface2: #191c2e; --surface3: #1f2235;
  --border: #252840; --border2: #2e3250;
  --text: #c8cdd8; --muted: #4e5470; --muted2: #6b7190;
  --accent: #4e9eff; --accent-dim: #1a2a3a;
  --green: #3fb950; --green-dim: #162416;
  --yellow: #d29922; --yellow-dim: #2a200a;
  --red: #f85149; --red-dim: #2a1015;
  --cyan: #56d0e0; --purple: #b392f0;
  --font-mono: 'JetBrains Mono', 'Fira Code', 'Cascadia Code', monospace;
  --radius: 6px; --radius-sm: 4px;
  --drawer-w: 400px;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
html, body { height: 100%; }
body { background: var(--bg); color: var(--text); font-family: system-ui, -apple-system, sans-serif;
       font-size: 14px; height: 100vh; display: flex; flex-direction: column; overflow: hidden; }

/* ── animations ───────────────────────────────────────────────────── */
@keyframes blink { 0%,100%{opacity:1} 50%{opacity:.25} }
@keyframes spin { to { transform: rotate(360deg); } }
@keyframes slideIn { from { transform: translateX(100%); opacity: 0; } to { transform: translateX(0); opacity: 1; } }
@keyframes fadeIn { from { opacity: 0; } to { opacity: 1; } }

/* ── topbar ───────────────────────────────────────────────────────── */
#topbar {
  display: flex; align-items: center; gap: 12px; padding: 0 16px;
  height: 44px; background: var(--surface); border-bottom: 1px solid var(--border);
  flex-shrink: 0; z-index: 10;
}
#topbar-logo { display: flex; align-items: center; gap: 8px; }
#topbar-logo h1 { font-size: 13px; font-weight: 700; color: var(--accent); letter-spacing: .06em; font-family: var(--font-mono); }
#pulse { width: 7px; height: 7px; border-radius: 50%; background: var(--muted); flex-shrink: 0; transition: background .3s; }
#pulse.live { background: var(--green); animation: blink 1.6s ease-in-out infinite; }
.topbar-sep { width: 1px; height: 18px; background: var(--border); }
.run-id { font-family: var(--font-mono); font-size: 11px; color: var(--muted2); }
.topbar-spacer { flex: 1; }
.top-status { font-size: 11px; color: var(--muted2); font-family: var(--font-mono); max-width: 300px;
              overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
#stop-btn {
  display: none; background: var(--red-dim); border: 1px solid var(--red); color: var(--red);
  border-radius: var(--radius-sm); padding: 4px 12px; font-size: 11px; font-weight: 700;
  cursor: pointer; font-family: var(--font-mono); letter-spacing: .04em; transition: all .15s;
}
#stop-btn:hover { background: var(--red); color: #fff; }
.inline-stop-btn {
  background: var(--red-dim); border: 1px solid var(--red); color: var(--red);
  border-radius: var(--radius-sm); padding: 5px 14px; font-size: 11px; font-weight: 700;
  cursor: pointer; font-family: var(--font-mono); letter-spacing: .04em; transition: all .15s;
}
.inline-stop-btn:hover { background: var(--red); color: #fff; }

/* Run drawer toggle button */
#run-toggle-btn {
  display: flex; align-items: center; gap: 6px; padding: 5px 12px;
  background: var(--accent); border: none; border-radius: var(--radius-sm);
  color: #fff; font-size: 12px; font-weight: 600; cursor: pointer;
  font-family: var(--font-mono); letter-spacing: .03em; transition: filter .15s;
}
#run-toggle-btn:hover { filter: brightness(1.12); }
#run-toggle-btn .btn-badge {
  background: rgba(255,255,255,.2); border-radius: 3px; padding: 1px 5px;
  font-size: 10px; font-weight: 700;
}

/* ── main layout ──────────────────────────────────────────────────── */
#main { flex: 1; overflow: hidden; display: flex; position: relative; }

/* ── stage rail (left) ────────────────────────────────────────────── */
#stage-list {
  width: 220px; flex-shrink: 0; border-right: 1px solid var(--border);
  overflow-y: auto; display: flex; flex-direction: column;
  background: var(--surface);
}

/* all-events meta row */
.sl-meta {
  display: flex; align-items: center; gap: 8px; padding: 9px 14px;
  cursor: pointer; border-left: 2px solid transparent; user-select: none;
  border-bottom: 1px solid var(--border); transition: background .1s;
}
.sl-meta:hover { background: var(--surface2); }
.sl-meta.active { background: var(--surface3); border-left-color: var(--muted2); }
.sl-meta-icon { font-size: 11px; color: var(--muted); }
.sl-meta-label { font-size: 11px; color: var(--muted2); font-family: var(--font-mono); }

.sl-section-hdr {
  padding: 8px 14px 4px; font-size: 9px; font-weight: 700; letter-spacing: .12em;
  text-transform: uppercase; color: var(--muted); font-family: var(--font-mono);
  border-bottom: 1px solid var(--border);
}

/* stage items */
.sl-item {
  display: grid; grid-template-columns: 28px 1fr auto auto; align-items: center; gap: 0;
  padding: 8px 12px 8px 0; cursor: pointer; user-select: none;
  border-left: 2px solid transparent; border-bottom: 1px solid var(--border);
  transition: background .1s; min-height: 44px;
}
.sl-item:hover { background: var(--surface2); }
.sl-item.active { background: var(--surface3); border-left-color: var(--accent); }
.sl-item.stage-running { background: color-mix(in srgb, var(--green) 8%, var(--surface)); border-left-color: var(--green); }
.sl-item.stage-running .sl-name { color: var(--green); font-weight: 600; }
.sl-num {
  font-family: var(--font-mono); font-size: 10px; color: var(--muted);
  text-align: center; flex-shrink: 0;
}
.sl-main { display: flex; flex-direction: column; gap: 2px; overflow: hidden; padding-left: 2px; }
.sl-name { font-size: 12px; font-weight: 500; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.sl-elapsed { font-size: 10px; font-family: var(--font-mono); color: var(--muted); }
.sl-dot { width: 7px; height: 7px; border-radius: 50%; background: var(--border); flex-shrink: 0; margin: 0 8px 0 4px; }
.sl-dot.done { background: var(--green); }
.sl-dot.running { background: var(--green); animation: blink 1.4s ease-in-out infinite; }
.sl-dot.skipped { background: var(--accent); opacity: .6; }
.sl-dot.error { background: var(--red); }
.sl-clear {
  color: transparent; font-size: 13px; line-height: 1; cursor: pointer;
  flex-shrink: 0; padding: 2px 4px; border-radius: 3px; transition: all .1s;
  margin-right: 4px;
}
.sl-item:hover .sl-clear { color: var(--muted); }
.sl-clear:hover { color: var(--red) !important; background: var(--red-dim); }

/* ── detail pane (center/right) ───────────────────────────────────── */
#detail-pane { flex: 1; overflow: hidden; display: flex; flex-direction: column; min-width: 0; }
#detail-header {
  padding: 14px 22px; border-bottom: 1px solid var(--border);
  background: var(--surface); flex-shrink: 0; display: flex; align-items: baseline; gap: 14px; flex-wrap: wrap;
}
.dh-title { font-size: 15px; font-weight: 600; }
.dh-meta { display: flex; gap: 10px; font-size: 11px; font-family: var(--font-mono); color: var(--muted2); flex-wrap: wrap; align-items: center; }
.badge { font-size: 10px; font-family: var(--font-mono); padding: 2px 8px; border-radius: var(--radius-sm); font-weight: 600; }
.badge-done    { background: var(--green-dim); color: var(--green); }
.badge-running { background: var(--green-dim); color: var(--green); }
.badge-pending { background: var(--border);    color: var(--muted2); }
.badge-error   { background: var(--red-dim);   color: var(--red); }
.badge-skipped { background: var(--accent-dim);color: var(--accent); }

#detail-body { flex: 1; overflow-y: auto; padding: 18px 22px; display: flex; flex-direction: column; gap: 18px; }

/* section headers */
.sec-hdr {
  font-size: 9px; font-weight: 700; letter-spacing: .12em; text-transform: uppercase;
  color: var(--muted); margin-bottom: 10px; display: flex; align-items: center; gap: 10px;
  font-family: var(--font-mono);
}
.sec-hdr-line { flex: 1; height: 1px; background: var(--border); }

/* progress log */
#progress-log {
  background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius);
  overflow-y: auto; max-height: 260px; font-family: var(--font-mono); font-size: 11px;
}
.pl-row { display: flex; gap: 10px; padding: 3px 12px; border-bottom: 1px solid var(--bg); }
.pl-row:hover { background: var(--surface2); }
.pl-ts { color: var(--muted); flex-shrink: 0; width: 90px; }
.pl-evt { flex-shrink: 0; width: 190px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.pl-evt.stage { color: var(--accent); }
.pl-evt.llm { color: var(--purple); }
.pl-evt.agent { color: #79c0ff; }
.pl-evt.error { color: var(--red); }
.pl-evt.ss { color: var(--yellow); }
.pl-body { color: var(--muted2); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex: 1; }
.pl-empty { padding: 14px 12px; color: var(--muted); font-size: 11px; }

/* results area */
#results-area { display: flex; flex-direction: column; gap: 12px; }

/* kv table */
.kv-table { width: 100%; border-collapse: collapse; font-size: 12px; font-family: var(--font-mono); }
.kv-table tr { border-bottom: 1px solid var(--border); }
.kv-table tr:last-child { border-bottom: none; }
.kv-table td { padding: 6px 12px; }
.kv-table td:first-child { color: var(--muted2); width: 200px; }

/* term chips */
.term-chips { display: flex; flex-wrap: wrap; gap: 6px; }
.term-chip {
  font-size: 11px; font-family: var(--font-mono); padding: 3px 8px;
  border-radius: var(--radius-sm); background: var(--surface); border: 1px solid var(--border); color: var(--text);
}
.term-chip .cnt { color: var(--accent); margin-left: 5px; font-size: 10px; }

/* domain cards */
.card-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: 8px; }
.card { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); padding: 11px 14px; }
.card.clickable { cursor: pointer; transition: border-color .15s; }
.card.clickable:hover { border-color: var(--accent); }
.card.selected { border-color: var(--accent); background: var(--surface2); }
.card.done { border-color: #1e3020; }
.card-label { font-weight: 600; font-size: 12px; margin-bottom: 4px; }
.card-meta { font-size: 11px; color: var(--muted2); font-family: var(--font-mono); }

/* checklist */
.check-row { display: flex; align-items: center; gap: 10px; padding: 6px 12px; border-bottom: 1px solid var(--border); font-size: 12px; }
.check-row:last-child { border-bottom: none; }
.check-label { flex: 1; }
.check-dot { width: 7px; height: 7px; border-radius: 50%; background: var(--border); flex-shrink: 0; }
.check-dot.pass { background: var(--green); }
.check-dot.fail { background: var(--red); }

/* gap items */
.gap-item { padding: 9px 12px; border-bottom: 1px solid var(--border); font-size: 12px; }
.gap-item:last-child { border-bottom: none; }
.gap-desc { margin-bottom: 3px; }
.gap-evidence { font-size: 10px; color: var(--muted2); font-family: var(--font-mono); }
.v-real { color: var(--red); font-weight: 600; }
.v-ambiguous { color: var(--yellow); }
.v-not { color: var(--muted2); }

/* inline section */
#inline-section {
  background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius);
  padding: 22px 28px; overflow-y: auto; max-height: 55vh; display: none;
}
#inline-section.visible { display: block; }
#inline-section h1,#inline-section h2,#inline-section h3,#inline-section h4
  { margin-top: 1.2rem; margin-bottom: .5rem; color: #e8eaed; }
#inline-section p { color: var(--text); line-height: 1.75; margin-bottom: .9rem; }
#inline-section pre { background: #0b0d14; border: 1px solid var(--border); border-radius: var(--radius-sm);
                      padding: .7rem 1rem; overflow-x: auto; font-family: var(--font-mono); font-size: 12px; }
#inline-section code { background: #0b0d14; border-radius: 3px; padding: .1em .35em;
                        font-family: var(--font-mono); font-size: 12px; }
#inline-section pre code { background: none; padding: 0; }
#inline-section blockquote { border-left: 3px solid var(--accent); padding-left: 1rem;
                              color: var(--muted2); font-style: italic; margin: .9rem 0; }
#inline-section table { border-collapse: collapse; width: 100%; margin: .9rem 0; font-size: 12px; }
#inline-section th,#inline-section td { border: 1px solid var(--border); padding: .4rem .8rem; }
#inline-section th { background: var(--surface2); }

/* all-events view */
#all-events-view { flex: 1; display: flex; flex-direction: column; gap: 10px; }
#all-events-filter { display: flex; gap: 8px; align-items: center; }
#all-events-filter input {
  flex: 1; background: var(--surface); border: 1px solid var(--border);
  border-radius: var(--radius-sm); padding: 6px 12px; color: var(--text);
  font-family: var(--font-mono); font-size: 12px; outline: none; transition: border-color .15s;
}
#all-events-filter input:focus { border-color: var(--accent); }
#all-events-log {
  background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius);
  overflow-y: auto; flex: 1; min-height: 200px; font-family: var(--font-mono); font-size: 11px;
}

/* empty / loading */
.empty { color: var(--muted2); font-size: 12px; padding: 28px 0; font-family: var(--font-mono); }

/* ── run drawer overlay ────────────────────────────────────────────── */
#drawer-backdrop {
  display: none; position: fixed; inset: 0; background: rgba(0,0,0,.5);
  z-index: 40; backdrop-filter: blur(2px);
}
#drawer-backdrop.open { display: block; animation: fadeIn .2s ease; }

#run-pane {
  position: fixed; right: 0; top: 44px; bottom: 0; width: var(--drawer-w);
  max-width: 96vw; z-index: 50;
  background: var(--surface); border-left: 1px solid var(--border2);
  display: flex; flex-direction: column; overflow: hidden;
  transform: translateX(100%); transition: transform .25s cubic-bezier(.4,0,.2,1);
  box-shadow: -8px 0 40px rgba(0,0,0,.5);
}
#run-pane.open { transform: translateX(0); animation: slideIn .25s cubic-bezier(.4,0,.2,1); }
/* expand when active */
#run-pane.active { --drawer-w: 480px; }

#run-header {
  padding: 12px 16px; background: var(--surface2); border-bottom: 1px solid var(--border);
  display: flex; align-items: center; gap: 10px; flex-shrink: 0;
}
#run-pane-title { font-size: 13px; font-weight: 600; flex: 1; font-family: var(--font-mono); }
#run-close-btn {
  background: none; border: none; color: var(--muted2); cursor: pointer; font-size: 16px;
  padding: 2px 6px; border-radius: var(--radius-sm); transition: all .15s; line-height: 1;
}
#run-close-btn:hover { color: var(--text); background: var(--border); }
#run-state-badge { flex-shrink: 0; }

#run-body { flex: 1; overflow-y: auto; display: flex; flex-direction: column; }

/* ── setup form ──────────────────────────────────────────────────── */
#run-setup {
  display: flex; flex-direction: column; gap: 0; padding: 0; flex: 1;
}
.setup-section {
  padding: 14px 16px; border-bottom: 1px solid var(--border);
}
.setup-section:last-child { border-bottom: none; }

.rf-label {
  font-size: 10px; color: var(--muted2); font-family: var(--font-mono);
  text-transform: uppercase; letter-spacing: .08em; margin-bottom: 6px; display: block;
}
.rf-input {
  background: var(--bg); border: 1px solid var(--border); border-radius: var(--radius-sm);
  padding: 7px 10px; color: var(--text); font-family: var(--font-mono);
  font-size: 12px; width: 100%; outline: none; transition: border-color .15s;
}
.rf-input:focus { border-color: var(--accent); }
.rf-select {
  background: var(--bg); border: 1px solid var(--border); border-radius: var(--radius-sm);
  padding: 7px 10px; color: var(--text); font-size: 12px; width: 100%; outline: none;
  cursor: pointer; transition: border-color .15s; appearance: none;
  background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='6'%3E%3Cpath d='M0 0l5 6 5-6z' fill='%234e5470'/%3E%3C/svg%3E");
  background-repeat: no-repeat; background-position: right 10px center; padding-right: 28px;
}
.rf-select:focus { border-color: var(--accent); }

/* toggle switches (replace raw checkboxes) */
.toggle-row {
  display: flex; align-items: center; justify-content: space-between;
  padding: 8px 0; gap: 12px;
}
.toggle-row + .toggle-row { border-top: 1px solid var(--border); }
.toggle-label { font-size: 12px; color: var(--text); flex: 1; }
.toggle-sub { font-size: 10px; color: var(--muted2); font-family: var(--font-mono); }
.toggle-wrap { position: relative; flex-shrink: 0; }
.toggle-wrap input[type=checkbox] { position: absolute; opacity: 0; width: 0; height: 0; }
.toggle-track {
  display: block; width: 34px; height: 18px; border-radius: 9px;
  background: var(--border); cursor: pointer; transition: background .2s; position: relative;
}
.toggle-track::after {
  content: ''; position: absolute; top: 2px; left: 2px; width: 14px; height: 14px;
  border-radius: 50%; background: var(--muted2); transition: transform .2s, background .2s;
}
.toggle-wrap input:checked + .toggle-track { background: var(--accent); }
.toggle-wrap input:checked + .toggle-track::after { transform: translateX(16px); background: #fff; }

/* resume card */
#resume-card {
  background: var(--green-dim); border: 1px solid #2a4a2a; border-radius: var(--radius);
  padding: 12px 14px; margin-bottom: 2px;
}
#resume-card.has-errors { background: var(--yellow-dim); border-color: #4a3a10; }
.rc-header { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
.rc-icon { font-size: 16px; }
.rc-title { font-size: 13px; font-weight: 600; flex: 1; }
.rc-stages { font-size: 11px; font-family: var(--font-mono); color: var(--muted2); margin-bottom: 10px; line-height: 1.6; }
.rc-path { font-size: 10px; font-family: var(--font-mono); color: var(--muted); word-break: break-all; }
.resume-toggle { display: flex; gap: 6px; margin-top: 10px; }
.resume-btn {
  flex: 1; background: rgba(0,0,0,.3); border: 1px solid rgba(255,255,255,.1);
  color: var(--muted2); border-radius: var(--radius-sm); padding: 6px 0; font-size: 11px;
  font-weight: 600; cursor: pointer; font-family: var(--font-mono); transition: all .15s; text-align: center;
}
.resume-btn:hover { border-color: var(--accent); color: var(--accent); }
.resume-btn.active { background: var(--accent); border-color: var(--accent); color: #fff; }

/* hidden compat field */
#run-resume { display: none; }

/* start button */
#run-start-btn {
  background: var(--accent); color: #fff; border: none; border-radius: var(--radius);
  padding: 11px 14px; font-size: 13px; font-weight: 700; cursor: pointer; width: 100%;
  letter-spacing: .03em; transition: filter .15s; font-family: var(--font-mono);
}
#run-start-btn:hover { filter: brightness(1.1); }
#run-start-btn:disabled { background: var(--border); color: var(--muted); cursor: not-allowed; filter: none; }

/* models & keys panel */
.mk-section { border: 1px solid var(--border); border-radius: var(--radius); overflow: hidden; }
.mk-header {
  display: flex; align-items: center; gap: 8px; padding: 9px 12px;
  background: var(--surface2); cursor: pointer; user-select: none; font-size: 12px; font-weight: 600;
  transition: background .1s;
}
.mk-header:hover { background: var(--surface3); }
.mk-chevron { font-size: 10px; color: var(--muted2); transition: transform .2s; margin-left: auto; }
.mk-chevron.open { transform: rotate(90deg); }
.mk-body { padding: 12px; display: flex; flex-direction: column; gap: 10px; }
.mk-body.collapsed { display: none; }
.key-row { display: flex; align-items: center; gap: 6px; }
.key-row .rf-input { flex: 1; font-size: 11px; padding: 5px 8px; }
.key-dot { width: 7px; height: 7px; border-radius: 50%; background: var(--border); flex-shrink: 0; }
.key-dot.set { background: var(--green); }
.key-eye { background: none; border: none; color: var(--muted2); cursor: pointer; font-size: 12px; padding: 0 3px; flex-shrink: 0; }
.key-eye:hover { color: var(--text); }
.mk-divider { height: 1px; background: var(--border); }
.model-chip {
  background: var(--bg); border: 1px solid var(--border); border-radius: var(--radius-sm);
  padding: 3px 8px; font-size: 10px; font-family: var(--font-mono); cursor: pointer;
  color: var(--muted2); white-space: nowrap; transition: all .1s;
}
.model-chip:hover { border-color: var(--accent); color: var(--accent); }
.model-chip.active { border-color: var(--accent); color: var(--accent); background: var(--accent-dim); }
.provider-section { border: 1px solid var(--border); border-radius: var(--radius-sm); overflow: hidden; margin-bottom: 5px; }
.provider-section-hdr {
  background: var(--surface3); padding: 3px 9px; font-size: 9px; color: var(--muted);
  font-family: var(--font-mono); text-transform: uppercase; letter-spacing: .1em; font-weight: 700;
}
.provider-section-chips { padding: 6px 8px; display: flex; flex-wrap: wrap; gap: 4px; }

/* file browser */
.rf-input-row { display: flex; gap: 6px; align-items: center; }
.rf-input-row .rf-input { flex: 1; }
#browse-btn {
  background: var(--surface2); border: 1px solid var(--border); border-radius: var(--radius-sm);
  padding: 7px 10px; color: var(--muted2); font-size: 11px; cursor: pointer; white-space: nowrap;
  flex-shrink: 0; transition: all .15s;
}
#browse-btn:hover { border-color: var(--accent); color: var(--accent); }
#file-browser {
  background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius);
  overflow: hidden; display: none; flex-direction: column; max-height: 240px; margin-top: 5px;
}
#fb-crumb {
  padding: 5px 10px; font-size: 10px; font-family: var(--font-mono); color: var(--muted2);
  border-bottom: 1px solid var(--border); white-space: nowrap; overflow: hidden;
  text-overflow: ellipsis; flex-shrink: 0; background: var(--surface2);
}
#fb-list { overflow-y: auto; flex: 1; }
.fb-entry { display: flex; align-items: center; gap: 7px; padding: 5px 10px; font-size: 12px; cursor: pointer; border-bottom: 1px solid var(--border); }
.fb-entry:last-child { border-bottom: none; }
.fb-entry:hover { background: var(--surface2); }
.fb-icon { font-size: 11px; flex-shrink: 0; width: 14px; }
.fb-name { flex: 1; font-family: var(--font-mono); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.fb-name.dir { color: var(--accent); }
.fb-size { font-size: 10px; color: var(--muted2); flex-shrink: 0; font-family: var(--font-mono); }

/* ── taxonomy review ──────────────────────────────────────────────── */
#run-taxonomy { display:none; flex-direction:column; flex:1; overflow:hidden; }
#tax-body { flex:1; overflow-y:auto; padding:16px; display:flex; flex-direction:column; gap:12px; }
.tax-proposal { border:1px solid var(--border); border-radius:var(--radius); overflow:hidden; }
.tax-proposal-hdr {
  padding:9px 12px; font-size:12px; font-weight:700;
  background:var(--surface2); border-bottom:1px solid var(--border); letter-spacing:.02em;
}
.tax-domains { padding:10px 12px; display:flex; flex-wrap:wrap; gap:6px; }
.tax-domain {
  background:var(--bg); border:1px solid var(--border); border-radius:var(--radius-sm);
  padding:3px 9px; font-size:10px; font-family:var(--font-mono); color:var(--text);
}
.tax-rationale { padding:0 12px 10px; font-size:11px; color:var(--muted2); line-height:1.55; }
#tax-actions {
  padding:14px 16px; border-top:1px solid var(--border); display:flex; flex-direction:column; gap:7px; flex-shrink:0;
  background: var(--surface2);
}
.tax-btn {
  border:1px solid var(--border); border-radius:var(--radius); padding:9px 14px; font-size:12px;
  font-weight:600; cursor:pointer; background:var(--surface); color:var(--text); text-align:left;
  transition: all .15s;
}
.tax-btn:hover { border-color:var(--accent); color:var(--accent); background: var(--accent-dim); }
.tax-btn.primary { background:var(--accent); color:#fff; border-color:var(--accent); }
.tax-btn.primary:hover { filter:brightness(1.1); background:var(--accent); color:#fff; }

/* ── chat / QA interface ──────────────────────────────────────────── */
#run-qa { display:none; flex-direction:column; flex:1; min-height:0; }
#chat-messages {
  flex: 1; overflow-y: auto; padding: 16px; display: flex; flex-direction: column; gap: 12px; min-height: 0;
}
.chat-msg { display: flex; flex-direction: column; gap: 5px; }
.chat-msg.agent { align-items: flex-start; }
.chat-msg.user-msg { align-items: flex-end; }
.chat-bubble {
  max-width: 88%; border-radius: 10px; padding: 9px 13px; font-size: 13px; line-height: 1.55;
}
.chat-bubble.agent { background: var(--surface2); border: 1px solid var(--border); color: var(--text); border-radius: 2px 10px 10px 10px; }
.chat-bubble.user-bub { background: var(--accent); color: #fff; border-radius: 10px 2px 10px 10px; }
.chat-options { display: flex; flex-direction: column; gap: 5px; margin-top: 5px; max-width: 92%; }
.chat-opt {
  background: var(--bg); border: 1px solid var(--border); border-radius: var(--radius);
  padding: 7px 12px; font-size: 12px; cursor: pointer; text-align: left; color: var(--text);
  transition: all .15s;
}
.chat-opt:hover { border-color: var(--accent); color: var(--accent); background: var(--accent-dim); }
.chat-opt:disabled { opacity: 0.45; cursor: default; }
.chat-opt.chosen { background: var(--accent-dim); border-color: var(--accent); color: var(--accent); }
.chat-opt-submit {
  margin-top: 5px; background: var(--accent); border: none; border-radius: var(--radius);
  padding: 7px 16px; font-size: 12px; font-weight: 700; cursor: pointer;
  color: #fff; width: 100%; text-align: center; transition: filter .15s;
}
.chat-opt-submit:hover { filter: brightness(1.15); }
.chat-other-input {
  margin-top: 5px; width: 100%; background: var(--bg); border: 1px solid var(--accent);
  border-radius: var(--radius-sm); padding: 6px 10px; color: var(--text);
  font-family: var(--font-mono); font-size: 12px; outline: none;
}
.chat-other-input:disabled { opacity: 0.45; }
#chat-input-area {
  padding: 12px 14px; border-top: 1px solid var(--border); display: flex; gap: 7px; flex-shrink: 0;
  background: var(--surface2);
}
#chat-input {
  flex: 1; background: var(--bg); border: 1px solid var(--border); border-radius: var(--radius-sm);
  padding: 7px 11px; color: var(--text); font-family: var(--font-mono); font-size: 12px; outline: none;
  transition: border-color .15s;
}
#chat-input:focus { border-color: var(--accent); }
#chat-send {
  background: var(--accent); border: none; border-radius: var(--radius-sm); padding: 7px 13px;
  color: #fff; cursor: pointer; font-size: 14px; font-weight: 700; transition: filter .15s;
}
#chat-send:hover { filter: brightness(1.15); }

/* ── running state ────────────────────────────────────────────────── */
#run-active { display: none; flex-direction: column; align-items: center; justify-content: center; flex: 1; padding: 32px 20px; gap: 20px; }
.run-active-spinner {
  width: 36px; height: 36px; border: 3px solid var(--border);
  border-top-color: var(--accent); border-radius: 50%;
  animation: spin 1s linear infinite; flex-shrink: 0;
}
.run-active-label { font-family: var(--font-mono); font-size: 12px; color: var(--muted2); letter-spacing: .05em; text-transform: uppercase; }
#run-active-info {
  font-size: 11px; font-family: var(--font-mono); color: var(--muted); word-break: break-all;
  line-height: 1.7; background: var(--surface2); border: 1px solid var(--border);
  border-radius: var(--radius-sm); padding: 10px 12px; width: 100%; text-align: center;
}
</style>
</head>
<body>

<!-- topbar -->
<div id="topbar">
  <div id="topbar-logo">
    <div id="pulse"></div>
    <h1>doc-expand</h1>
  </div>
  <div class="topbar-sep"></div>
  <span class="run-id" id="run-label"></span>
  <span class="topbar-spacer"></span>
  <span class="top-status" id="top-status">connecting…</span>
  <button id="stop-btn" onclick="stopRun()">&#9632; Stop</button>
  <button id="run-toggle-btn" onclick="toggleDrawer()">
    <span>Run</span>
    <span class="btn-badge" id="run-state-badge">idle</span>
  </button>
</div>

<!-- drawer backdrop -->
<div id="drawer-backdrop" onclick="closeDrawer()"></div>

<div id="main">
  <!-- stage rail -->
  <div id="stage-list">
    <div class="sl-meta" id="sl-all-events" data-view="all-events">
      <span class="sl-meta-icon">&#9776;</span>
      <span class="sl-meta-label">all events</span>
    </div>
    <div class="sl-section-hdr">Stages</div>
    <div id="sl-stages"></div>
  </div>

  <!-- detail pane -->
  <div id="detail-pane">
    <div id="detail-header">
      <div class="dh-title" id="dh-title">Select a stage</div>
      <div class="dh-meta" id="dh-meta"></div>
    </div>
    <div id="detail-body">
      <div class="empty" id="detail-placeholder" style="padding-left:4px">&#8592; select a stage to inspect</div>

      <div id="progress-section" style="display:none">
        <div class="sec-hdr"><span>Progress</span><span class="sec-hdr-line"></span></div>
        <div id="progress-log"></div>
      </div>

      <div id="results-section" style="display:none">
        <div class="sec-hdr"><span>Results</span><span class="sec-hdr-line"></span></div>
        <div id="results-area"></div>
        <div id="inline-section"></div>
      </div>

      <div id="all-events-view" style="display:none">
        <div id="all-events-filter">
          <input id="evt-filter-input" placeholder="filter events…" />
        </div>
        <div id="all-events-log"></div>
      </div>
    </div>
  </div>

  <!-- run drawer (fixed overlay) -->
  <div id="run-pane">
    <div id="run-header">
      <span id="run-pane-title">Run</span>
      <span style="flex:1"></span>
      <button id="run-close-btn" onclick="closeDrawer()" title="Close">&#x2715;</button>
    </div>
    <div id="run-body">

      <!-- mode: setup -->
      <div id="run-setup">

        <!-- Resume card — shown by JS when prior run detected -->
        <div id="resume-card" style="display:none" class="setup-section">
          <div class="rc-header">
            <span class="rc-icon" id="rc-icon">&#9989;</span>
            <span class="rc-title" id="rc-title">Prior run detected</span>
          </div>
          <div id="resume-info" class="rc-stages"></div>
          <div id="resume-toggle" class="resume-toggle">
            <button id="resume-btn-fresh" class="resume-btn active" onclick="setResumeMode(false)">Fresh run</button>
            <button id="resume-btn-resume" class="resume-btn" onclick="setResumeMode(true)">Resume</button>
          </div>
        </div>
        <input type="hidden" id="run-resume" value="0" />

        <!-- Fresh-run options -->
        <div id="fresh-options">
          <div class="setup-section">
            <label class="rf-label">Input — file path or URL</label>
            <div class="rf-input-row">
              <input id="run-input" class="rf-input" placeholder="./paper.pdf  or  https://arxiv.org/…" />
              <button id="browse-btn" onclick="toggleBrowser()">&#8943;</button>
            </div>
            <div id="file-browser">
              <div id="fb-crumb">/</div>
              <div id="fb-list"></div>
            </div>
          </div>

          <div class="setup-section">
            <label class="rf-label">Depth</label>
            <select id="run-depth" class="rf-select">
              <option value="standard" selected>Standard — balanced coverage</option>
              <option value="survey">Survey — broad overview</option>
              <option value="deep">Deep — exhaustive analysis</option>
            </select>
          </div>

          <div class="setup-section">
            <div class="toggle-row">
              <div>
                <div class="toggle-label">Auto-taxonomy</div>
                <div class="toggle-sub">Skip manual domain review</div>
              </div>
              <label class="toggle-wrap">
                <input type="checkbox" id="run-auto-tax" checked />
                <span class="toggle-track"></span>
              </label>
            </div>
            <div class="toggle-row">
              <div>
                <div class="toggle-label">Skip PDF render</div>
                <div class="toggle-sub">Use text extraction only</div>
              </div>
              <label class="toggle-wrap">
                <input type="checkbox" id="run-no-pdf" checked />
                <span class="toggle-track"></span>
              </label>
            </div>
          </div>

          <!-- Models & Keys collapsible -->
          <div class="setup-section">
            <div class="mk-section">
              <div class="mk-header" onclick="toggleMkPanel()">
                <span style="color:var(--muted2)">&#9881;</span> Models &amp; Keys
                <span class="mk-chevron" id="mk-chevron">&#9658;</span>
              </div>
              <div class="mk-body collapsed" id="mk-body">
                <div>
                  <div class="rf-label" style="margin-bottom:8px">API Keys</div>
                  <div style="display:flex;flex-direction:column;gap:7px">
                    <div class="key-row">
                      <span class="key-dot" id="dot-anthropic"></span>
                      <input class="rf-input" id="key-anthropic" type="password" placeholder="Anthropic sk-ant-…"
                             oninput="updateKeyDot('anthropic')" />
                      <button class="key-eye" onclick="toggleKeyVis('key-anthropic')">&#128065;</button>
                    </div>
                    <div class="key-row">
                      <span class="key-dot" id="dot-openai"></span>
                      <input class="rf-input" id="key-openai" type="password" placeholder="OpenAI sk-…"
                             oninput="updateKeyDot('openai')" />
                      <button class="key-eye" onclick="toggleKeyVis('key-openai')">&#128065;</button>
                    </div>
                    <div class="key-row">
                      <span class="key-dot" id="dot-gemini"></span>
                      <input class="rf-input" id="key-gemini" type="password" placeholder="Gemini AIza…"
                             oninput="updateKeyDot('gemini')" />
                      <button class="key-eye" onclick="toggleKeyVis('key-gemini')">&#128065;</button>
                    </div>
                  </div>
                </div>
                <div class="mk-divider"></div>
                <div>
                  <div class="rf-label" style="margin-bottom:8px">Primary model</div>
                  <div id="provider-sections">
                    <span style="color:var(--muted);font-size:11px;font-family:var(--font-mono)">loading…</span>
                  </div>
                  <input class="rf-input" id="custom-model-input" placeholder="or type any model ID…"
                         style="font-size:11px;padding:5px 9px;margin-top:6px"
                         oninput="onCustomModelInput(this.value)" />
                </div>
              </div>
            </div>
          </div>
        </div><!-- /#fresh-options -->

        <div class="setup-section">
          <button id="run-start-btn" onclick="startRun()">&#9654; Start Run</button>
        </div>
      </div><!-- /#run-setup -->

      <!-- mode: taxonomy review -->
      <div id="run-taxonomy" style="display:none">
        <div id="tax-body"></div>
        <div id="tax-actions">
          <div style="font-size:11px;color:var(--muted2);font-family:var(--font-mono);margin-bottom:4px;text-transform:uppercase;letter-spacing:.08em">Choose taxonomy strategy</div>
          <button class="tax-btn primary" onclick="submitTaxonomy('l')">&#9654; Use Lumper — broad domains</button>
          <button class="tax-btn" onclick="submitTaxonomy('s')">&#9654; Use Splitter — fine-grained</button>
          <button class="tax-btn" onclick="submitTaxonomy('m')">&#9881; Auto-merge both</button>
        </div>
      </div>

      <!-- mode: qa (chat interview) -->
      <div id="run-qa" style="display:none">
        <div id="chat-messages"></div>
        <div id="chat-input-area" style="display:none">
          <input id="chat-input" placeholder="type your answer…" />
          <button id="chat-send">&#8594;</button>
        </div>
        <div style="padding:8px 14px 10px;border-top:1px solid var(--border);display:flex;justify-content:flex-end">
          <button class="inline-stop-btn" onclick="stopRun()">&#9632; Stop run</button>
        </div>
      </div>

      <!-- mode: running -->
      <div id="run-active" style="display:none">
        <div class="run-active-spinner"></div>
        <div class="run-active-label">Pipeline running</div>
        <div id="run-active-info"></div>
        <button class="inline-stop-btn" onclick="stopRun()">&#9632; Stop run</button>
      </div>

    </div><!-- /#run-body -->
  </div><!-- /#run-pane -->
</div><!-- /#main -->

<script>
const $ = id => document.getElementById(id);

// ── Drawer ────────────────────────────────────────────────────────────────────
function openDrawer() {
  $('run-pane').classList.add('open');
  $('drawer-backdrop').classList.add('open');
}
function closeDrawer() {
  $('run-pane').classList.remove('open');
  $('drawer-backdrop').classList.remove('open');
}
function toggleDrawer() {
  const open = $('run-pane').classList.contains('open');
  if (open) closeDrawer(); else openDrawer();
}
// Open drawer automatically when a run starts or needs interaction
function ensureDrawerOpen() {
  if (!$('run-pane').classList.contains('open')) openDrawer();
}
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
let _pipelineRunning = false;
let _runId = '';
let _stateDir = '';

// ── Stage list ──────────────────────────────────────────────────────────────
function renderStageList(stages, pipelineRunning) {
  stageData = stages;
  const container = $('sl-stages');
  $('stop-btn').style.display = pipelineRunning ? 'inline-block' : 'none';
  // Keep badge honest: if process is alive but pane still shows 'idle', fix it
  if (pipelineRunning && $('run-state-badge').textContent === 'idle') {
    $('run-state-badge').textContent = 'running';
  } else if (!pipelineRunning && $('run-state-badge').textContent === 'running') {
    $('run-state-badge').textContent = 'idle';
  }

  container.innerHTML = STAGE_ORDER.map(sid => {
    const s = stages[sid] || {};
    const status = s.status || 'pending';
    const dotCls = {complete:'done', running:'running', error:'error', skipped:'skipped'}[status] || '';
    const active = selectedView === sid ? ' active' : '';
    const isRunning = status === 'running' ? ' stage-running' : '';
    const canClear = status === 'complete' || status === 'error' || status === 'skipped';
    const clearBtn = canClear
      ? `<span class="sl-clear" onclick="event.stopPropagation();clearStage('${sid}')" title="Clear stage">&#215;</span>`
      : `<span class="sl-clear" style="visibility:hidden">&#215;</span>`;
    const elapsed = s.elapsed_s != null ? `<span class="sl-elapsed">${s.elapsed_s}s</span>` : '';
    return `<div class="sl-item${active}${isRunning}" data-stage="${sid}" onclick="selectStage('${sid}')">
      <span class="sl-num">S${sid}</span>
      <span class="sl-main">
        <span class="sl-name">${STAGE_NAMES[sid]}</span>
        ${elapsed}
      </span>
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
      const wasRunning = _pipelineRunning;
      _pipelineRunning = s.pipeline_running;
      renderStageList(s.stages || {}, s.pipeline_running);
      domainData = s.domains || [];
      _runId = s.run_id || '';
      _stateDir = s.state_dir || '';
      $('run-label').textContent = s.run_id || '';
      $('top-status').textContent = s.summary || '';
      updateResumeCard(s.stages || {}, s.input_path || '', s.run_id || '');
      // Sync run pane mode with actual pipeline state
      if (s.pipeline_running && runPaneMode === 'setup') {
        setRunMode('running');
      } else if (!s.pipeline_running && runPaneMode === 'running') {
        // Pipeline finished — return to setup but keep resume selected if prior run exists
        setRunMode('setup');
        if (_resumeInputPath) setResumeMode(true);
      } else if (!s.pipeline_running && runPaneMode === 'taxonomy') {
        setRunMode('setup');
      }
      // Auto-switch to all-events when pipeline first starts
      if (s.pipeline_running && !wasRunning && selectedView !== 'all-events') {
        $('sl-all-events').click();
      }
      updateRunActiveInfo();
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
  await pollTaxonomy();
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

const STAGE_NAMES_SHORT = {
  '0':'Ingest','1':'Assess','2':'Extract','3':'Graph','4':'Audit',
  '5':'Research','6':'Align','7':'Synth','8':'Verify','9':'Prereq','10':'Assemble'
};

function updateResumeCard(stages, inputPath, runId) {
  const card = $('resume-card');
  const completedIds = Object.entries(stages)
    .filter(([,v]) => v.status === 'complete')
    .map(([k]) => k)
    .sort((a,b) => +a - +b);

  if (completedIds.length === 0) {
    card.style.display = 'none';
    $('run-resume').value = '0';
    $('fresh-options').style.display = 'block';
    $('run-start-btn').textContent = '▶ Start Run';
    _resumeInputPath = '';
    return;
  }

  _resumeInputPath = inputPath || '';
  card.style.display = 'block';

  // Health check: any error stages?
  const errorIds = Object.entries(stages)
    .filter(([,v]) => v.status === 'error')
    .map(([k]) => k);
  const healthy = errorIds.length === 0;

  const completedNames = completedIds.map(id => STAGE_NAMES_SHORT[id] || ('S'+id));
  card.classList.toggle('has-errors', !healthy);

  const rcIcon = $('rc-icon');
  const rcTitle = $('rc-title');
  if (rcIcon) rcIcon.textContent = healthy ? '✅' : '⚠️';
  if (rcTitle) rcTitle.textContent = healthy ? 'Prior run — resumable' : 'Prior run — errors present';

  let info = `${completedIds.length}/11 stages complete`;
  if (completedNames.length) info += `\n${completedNames.join(', ')}`;
  if (runId) info += `\nrun: ${runId}`;
  if (inputPath) info += `\n${inputPath}`;
  $('resume-info').textContent = info;
}

let _resumeInputPath = '';   // set by updateResumeCard from prior run's input_path

function setResumeMode(doResume) {
  $('run-resume').value = doResume ? '1' : '0';
  $('resume-btn-fresh').classList.toggle('active', !doResume);
  $('resume-btn-resume').classList.toggle('active', doResume);
  $('fresh-options').style.display = doResume ? 'none' : 'block';
  $('run-start-btn').textContent = doResume ? '▶ Resume Run' : '▶ Start Run';
  if (doResume && _resumeInputPath) {
    $('run-input').value = _resumeInputPath;
  }
}

function updateRunActiveInfo() {
  const el = $('run-active-info');
  if (!el) return;
  const parts = [];
  if (_stateDir) parts.push(_stateDir);
  if (_runId)    parts.push('run: ' + _runId);
  el.textContent = parts.join('\n');
}

// ── Stop / clear ─────────────────────────────────────────────────────────
async function stopRun() {
  const r = await fetch('/api/stop', {method: 'POST'});
  const d = await r.json().catch(() => ({}));
  lastStatusEtag = '';
  lastEventsEtag = '';   // force events refresh so run_stopped appears immediately
  $('stop-btn').style.display = 'none';
  currentQaId = null;
  qaHistory = [];
  // Don't call setRunMode here — the next poll will detect pipeline_running=false
  // and transition to 'setup' cleanly. Calling it here causes a flash to setup
  // before the process has fully died.
  // Switch to all-events so the run_stopped log line is visible
  if (selectedView !== 'all-events') $('sl-all-events').click();
  // Flash outcome in the events filter bar
  const bar = $('evt-filter-input');
  const prev = bar.placeholder;
  bar.placeholder = d.killed ? '■ run stopped' : '■ stop sent (no process found)';
  setTimeout(() => { bar.placeholder = prev; }, 3000);
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
  $('run-taxonomy').style.display = mode === 'taxonomy' ? 'flex' : 'none';
  $('run-qa').style.display = mode === 'qa' ? 'flex' : 'none';
  $('run-active').style.display = mode === 'running' ? 'flex' : 'none';
  // Update topbar badge
  const badge = $('run-state-badge');
  const labels = {setup:'idle', qa:'interview', taxonomy:'review', running:'running'};
  badge.textContent = labels[mode] || mode;
  // Make drawer wider during active modes
  const pane = $('run-pane');
  if (mode === 'setup') { pane.classList.remove('active'); }
  else { pane.classList.add('active'); }
  // Auto-open drawer when pipeline needs interaction
  if (mode !== 'setup') ensureDrawerOpen();
}

// ── Taxonomy review ───────────────────────────────────────────────────────
let taxReviewPending = false;

async function pollTaxonomy() {
  if (runPaneMode !== 'running' && runPaneMode !== 'taxonomy') return;
  try {
    const r = await fetch('/api/taxonomy');
    const d = await r.json();
    if (d.lumper && !taxReviewPending) {
      taxReviewPending = true;
      renderTaxonomyReview(d);
      setRunMode('taxonomy');
    } else if (!d.lumper && taxReviewPending) {
      taxReviewPending = false;
      setRunMode('running');
    }
  } catch {}
}

function renderTaxonomyReview(d) {
  const body = $('tax-body');
  body.innerHTML = '';
  for (const [key, label] of [['lumper','Lumper — broad domains'], ['splitter','Splitter — fine-grained']]) {
    const p = d[key] || {};
    const domains = p.domains || [];
    const el = document.createElement('div');
    el.className = 'tax-proposal';
    el.innerHTML = `
      <div class="tax-proposal-hdr">${label} · ${domains.length} domains</div>
      <div class="tax-domains">${domains.map(dm =>
        `<span class="tax-domain">${dm.label || dm.id}</span>`).join('')}</div>
      ${p.rationale ? `<div class="tax-rationale">${p.rationale}</div>` : ''}`;
    body.appendChild(el);
  }
  if (d.issues?.length) {
    const el = document.createElement('div');
    el.style.cssText = 'font-size:11px;color:var(--yellow);padding:4px 2px';
    el.textContent = '⚠ Issues: ' + d.issues.join('; ');
    body.appendChild(el);
  }
}

async function submitTaxonomy(choice) {
  $('tax-actions').querySelectorAll('.tax-btn').forEach(b => b.disabled = true);
  await fetch('/api/taxonomy/choice', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({choice}),
  });
  taxReviewPending = false;
  setRunMode('running');
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
  const isResume = $('run-resume').value === '1';
  const input = isResume ? _resumeInputPath : $('run-input').value.trim();
  if (!input) {
    if (isResume) {
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

    // Always start in running mode; pollQa/pollTaxonomy switch reactively
    setRunMode('running');

    // Show all-events view so user sees live log immediately
    $('sl-all-events').click();
  } catch(e) {
    $('run-start-btn').disabled = false;
    $('run-start-btn').textContent = isResume ? '▶ Resume Run' : '▶ Start Run';
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
  // Run in both 'running' (stage 1 may start) and 'qa' (actively interviewing) modes
  if (runPaneMode !== 'qa' && runPaneMode !== 'running') return;

  try {
    const r = await fetch('/api/qa');
    const d = await r.json();

    // If a question exists and we're not in qa mode yet, switch into it
    if ((d.question || (d.history && d.history.length > 0)) && runPaneMode === 'running') {
      setRunMode('qa');
    }

    // Rebuild history messages if history changed (covers resume case)
    const hist = d.history || [];
    if (hist.length !== qaHistory.length) {
      qaHistory = hist;
      rebuildChatHistory();
    }

    if (d.interview_complete) {
      // Remove thinking indicator, show completion
      removeThinking();
      if (runPaneMode === 'qa') {
        appendAgentBubble('Profile complete. Running pipeline…');
        setRunMode('running');
      }
      return;
    }

    if (runPaneMode !== 'qa') return;

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
  const area = $('chat-messages');
  // Re-render all history pairs from scratch (called only when history grows)
  area.innerHTML = '';
  for (const item of qaHistory) {
    // Agent question bubble
    const qEl = document.createElement('div');
    qEl.className = 'chat-msg agent';
    const qBub = document.createElement('div');
    qBub.className = 'chat-bubble agent';
    qBub.textContent = item.question;
    qEl.appendChild(qBub);
    area.appendChild(qEl);
    // User answer bubble
    const uEl = document.createElement('div');
    uEl.className = 'chat-msg user-msg';
    const uBub = document.createElement('div');
    uBub.className = 'chat-bubble user-bub';
    uBub.textContent = item.answer;
    uEl.appendChild(uBub);
    area.appendChild(uEl);
  }
  area.scrollTop = area.scrollHeight;
  // Reset currentQaId so the next live question renders fresh
  currentQaId = null;
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

    // Shared text box revealed when an "Other" option is toggled on
    const otherInput = document.createElement('input');
    otherInput.className = 'chat-other-input';
    otherInput.placeholder = 'Please specify…';
    otherInput.style.display = 'none';

    q.options.forEach(opt => {
      const isOther = /^other\b/i.test(opt.trim());
      const btn = document.createElement('button');
      btn.className = 'chat-opt';
      btn.textContent = opt;
      btn.dataset.isOther = isOther ? '1' : '';
      btn.onclick = () => {
        btn.classList.toggle('chosen');
        if (isOther) {
          const show = btn.classList.contains('chosen');
          otherInput.style.display = show ? 'block' : 'none';
          if (show) setTimeout(() => otherInput.focus(), 0);
        }
        submitBtn.style.display = opts.querySelector('.chat-opt.chosen') ? 'block' : 'none';
      };
      opts.appendChild(btn);
    });

    opts.appendChild(otherInput);

    const submitBtn = document.createElement('button');
    submitBtn.className = 'chat-opt-submit';
    submitBtn.textContent = 'Submit →';
    submitBtn.style.display = 'none';
    submitBtn.onclick = () => {
      const chosen = [...opts.querySelectorAll('.chat-opt.chosen')].map(b =>
        b.dataset.isOther && otherInput.value.trim() ? otherInput.value.trim() : b.textContent
      );
      if (!chosen.length) return;
      opts.querySelectorAll('.chat-opt').forEach(b => b.disabled = true);
      otherInput.disabled = true;
      submitBtn.style.display = 'none';
      submitAnswer(q.id, chosen.join(', '), msgEl);
    };
    opts.appendChild(submitBtn);
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


def _pipeline_is_running(state_dir: Path) -> bool:
    """True if the pipeline process (recorded in pipeline_pid) is still alive."""
    pid_file = state_dir / "pipeline_pid"
    if not pid_file.exists():
        return False
    try:
        pid = int(pid_file.read_text().strip())
        os.kill(pid, 0)   # signal 0 = probe only
        return True
    except (OSError, ValueError):
        return False


def _load_state(state_dir: Path) -> dict:
    result: dict = {"stages": {}, "domains": [], "run_id": "", "summary": "",
                    "pipeline_running": _pipeline_is_running(state_dir),
                    "state_dir": str(state_dir)}

    pipeline_path = state_dir / "pipeline.json"
    if pipeline_path.exists():
        try:
            data = json.loads(pipeline_path.read_text())
            result["stages"] = data.get("stages", {})
            result["run_id"] = data.get("run_id", "")
            result["input_path"] = data.get("input_path", "") or data.get("input", "")
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
    for fname in ("qa_queue.jsonl", "qa_answers.jsonl", "qa_complete",
                   "taxonomy_review.json", "taxonomy_choice.json"):
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

            elif self.path == "/api/taxonomy":
                review_path = state_dir / "taxonomy_review.json"
                if review_path.exists():
                    try:
                        _json_response(self, json.loads(review_path.read_text()))
                    except Exception:
                        _json_response(self, {})
                else:
                    _json_response(self, {})

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
                pid = None
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

            elif self.path == "/api/taxonomy/choice":
                choice = data.get("choice", "l")
                choice_path = state_dir / "taxonomy_choice.json"
                choice_path.write_text(json.dumps({"choice": choice}))
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
