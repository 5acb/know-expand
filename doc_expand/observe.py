"""Observability commands: `doc-expand tail` and `doc-expand serve`.

tail  — streams formatted event lines to stdout (pipe-friendly, greppable)
serve — tool dashboard at localhost: pipeline status, domain inspector, event log
"""

from __future__ import annotations

import http.server
import json
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
    if e in ("s4_5_domain_start", "domain_start"):
        return f"{ts}    domain {evt.get('domain_id')} starting"
    if e in ("s4_5_domain_complete", "domain_complete"):
        return f"{ts}    domain {evt.get('domain_id')} done  {evt.get('elapsed_s')}s"
    if e == "s4_5_domain_aligned":
        patches = evt.get("patches") or []
        return f"{ts}    aligned {evt.get('domain_id')}  patches={len(patches)}"
    if e == "s6_5_primer_inserted":
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
                if evt.get("event") == "stage_complete" and str(evt.get("stage")) == "7":
                    return
            offset = len(lines)
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass


# ---------------------------------------------------------------------------
# serve — tool dashboard
# ---------------------------------------------------------------------------

_STAGE_META = {
    "0": "Ingest", "0.5": "Assess", "1": "Extract", "2": "Graph",
    "3": "Audit", "4": "Research", "4.5": "Align", "5": "Synthesize",
    "6": "Verify", "6.5": "Prereq", "7": "Assemble",
}

_PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>doc-expand</title>
<style>
:root {
  --bg: #0f1117; --surface: #1a1d27; --border: #2a2d3a;
  --text: #c9cdd4; --muted: #555a6e; --accent: #4e9eff;
  --green: #3fb950; --yellow: #d29922; --red: #f85149;
  --font-mono: 'JetBrains Mono', 'Fira Code', monospace;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body { background: var(--bg); color: var(--text); font-family: system-ui, sans-serif;
       font-size: 14px; height: 100vh; display: flex; flex-direction: column; overflow: hidden; }

/* top bar */
#topbar { display: flex; align-items: center; gap: 16px; padding: 0 16px;
          height: 44px; background: var(--surface); border-bottom: 1px solid var(--border);
          flex-shrink: 0; }
#topbar h1 { font-size: 14px; font-weight: 600; color: var(--accent); letter-spacing: .04em; }
#topbar .run-id { font-family: var(--font-mono); font-size: 11px; color: var(--muted); }
#pulse { width: 8px; height: 8px; border-radius: 50%; background: var(--muted); flex-shrink: 0; }
#pulse.live { background: var(--green); animation: blink 1.4s infinite; }
@keyframes blink { 0%,100%{opacity:1} 50%{opacity:.3} }
#topbar .spacer { flex: 1; }
#topbar .status-text { font-size: 12px; color: var(--muted); }

/* tabs */
#tabs { display: flex; gap: 2px; padding: 0 12px;
        background: var(--surface); border-bottom: 1px solid var(--border); flex-shrink: 0; }
.tab { padding: 8px 16px; font-size: 13px; cursor: pointer; color: var(--muted);
       border-bottom: 2px solid transparent; user-select: none; }
.tab:hover { color: var(--text); }
.tab.active { color: var(--accent); border-bottom-color: var(--accent); }

/* main area */
#main { flex: 1; overflow: hidden; display: flex; }
.panel { display: none; flex: 1; overflow: auto; padding: 20px; }
.panel.active { display: flex; flex-direction: column; gap: 16px; }

/* pipeline panel */
.stage-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 10px; }
.stage-card { background: var(--surface); border: 1px solid var(--border); border-radius: 6px;
              padding: 12px 14px; display: flex; flex-direction: column; gap: 4px; }
.stage-card.done { border-color: var(--green); }
.stage-card.running { border-color: var(--accent); }
.stage-card.error { border-color: var(--red); }
.stage-name { font-weight: 600; font-size: 13px; }
.stage-status { font-size: 11px; color: var(--muted); font-family: var(--font-mono); }
.stage-meta { font-size: 11px; color: var(--muted); }

/* domain panel */
.domain-list { display: flex; flex-direction: column; gap: 8px; }
.domain-row { background: var(--surface); border: 1px solid var(--border); border-radius: 6px;
              padding: 12px 14px; cursor: pointer; transition: border-color .15s; }
.domain-row:hover { border-color: var(--accent); }
.domain-row.selected { border-color: var(--accent); }
.domain-header { display: flex; align-items: center; gap: 10px; margin-bottom: 4px; }
.domain-label { font-weight: 600; }
.domain-badge { font-size: 10px; font-family: var(--font-mono); padding: 2px 6px;
                border-radius: 3px; background: var(--border); color: var(--muted); }
.domain-badge.aligned { background: #1a3a1a; color: var(--green); }
.checklist { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 4px; }
.check { font-size: 10px; font-family: var(--font-mono); padding: 2px 6px;
         border-radius: 3px; background: var(--border); color: var(--muted); }
.check.pass { background: #1a3a1a; color: var(--green); }
.check.fail { background: #3a1a1a; color: var(--red); }

/* section preview */
#section-view { flex: 1; background: var(--surface); border: 1px solid var(--border);
                border-radius: 6px; overflow: auto; padding: 20px 28px; display: none; }
#section-view.visible { display: block; }
#section-view h1,h2,h3,h4 { font-family: system-ui; margin-top: 1.4rem; margin-bottom: .5rem; color: #e8eaed; }
#section-view p { color: var(--text); line-height: 1.7; margin-bottom: .8rem; }
#section-view pre { background: #11131b; border: 1px solid var(--border); border-radius: 4px;
                    padding: .8rem 1rem; overflow-x: auto; font-family: var(--font-mono); font-size: 12px; }
#section-view code { background: #11131b; border-radius: 3px; padding: .1em .35em;
                     font-family: var(--font-mono); font-size: 12px; }
#section-view pre code { background: none; padding: 0; }
#section-view blockquote { border-left: 3px solid var(--accent); padding-left: 1rem;
                           color: var(--muted); font-style: italic; margin: .8rem 0; }
#section-view table { border-collapse: collapse; width: 100%; margin: 1rem 0; font-size: 13px; }
#section-view th,td { border: 1px solid var(--border); padding: .4rem .7rem; }
#section-view th { background: var(--border); }

/* events panel */
#event-log { flex: 1; background: var(--surface); border: 1px solid var(--border);
             border-radius: 6px; overflow-y: auto; padding: 8px 0; font-family: var(--font-mono); font-size: 12px; }
.evt-row { padding: 3px 14px; display: flex; gap: 14px; border-bottom: 1px solid #1e2030; }
.evt-row:hover { background: #1e2030; }
.evt-ts { color: var(--muted); flex-shrink: 0; width: 135px; }
.evt-type { flex-shrink: 0; width: 180px; }
.evt-type.stage { color: var(--accent); }
.evt-type.llm { color: #b392f0; }
.evt-type.agent { color: #79c0ff; }
.evt-type.error { color: var(--red); }
.evt-type.ss { color: var(--yellow); }
.evt-body { color: var(--muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
#event-filter { padding: 8px 12px; background: var(--surface); border-bottom: 1px solid var(--border);
                display: flex; gap: 8px; align-items: center; }
#event-filter input { flex: 1; background: var(--bg); border: 1px solid var(--border);
                      border-radius: 4px; padding: 5px 10px; color: var(--text); font-family: var(--font-mono); font-size: 12px; }
#event-filter label { font-size: 12px; color: var(--muted); }

/* gaps panel */
.gap-card { background: var(--surface); border: 1px solid var(--border); border-radius: 6px; padding: 14px 16px; }
.gap-card h3 { font-size: 13px; margin-bottom: 8px; color: var(--text); }
.gap-item { padding: 8px 0; border-bottom: 1px solid var(--border); font-size: 13px; }
.gap-item:last-child { border-bottom: none; }
.gap-desc { color: var(--text); margin-bottom: 4px; }
.gap-evidence { font-size: 11px; color: var(--muted); font-family: var(--font-mono); }
.verdict-real { color: var(--red); font-weight: 600; }
.verdict-ambiguous { color: var(--yellow); }
.verdict-not { color: var(--muted); }

/* empty state */
.empty { color: var(--muted); font-size: 13px; padding: 32px; text-align: center; }
</style>
</head>
<body>
<div id="topbar">
  <div id="pulse"></div>
  <h1>doc-expand</h1>
  <span class="run-id" id="run-label">—</span>
  <span class="spacer"></span>
  <span class="status-text" id="top-status">loading…</span>
</div>
<div id="tabs">
  <div class="tab active" data-tab="pipeline">Pipeline</div>
  <div class="tab" data-tab="domains">Domains</div>
  <div class="tab" data-tab="events">Events</div>
  <div class="tab" data-tab="gaps">Gaps</div>
</div>
<div id="main">

  <!-- Pipeline tab -->
  <div class="panel active" id="tab-pipeline">
    <div class="stage-grid" id="stage-grid"></div>
  </div>

  <!-- Domains tab -->
  <div class="panel" id="tab-domains" style="flex-direction:row;gap:16px">
    <div style="width:320px;flex-shrink:0;overflow-y:auto">
      <div class="domain-list" id="domain-list"></div>
    </div>
    <div id="section-view"></div>
  </div>

  <!-- Events tab -->
  <div class="panel" id="tab-events" style="padding:0">
    <div id="event-filter">
      <label>filter:</label>
      <input id="evt-filter-input" placeholder="stage / llm / agent / ss / …" />
    </div>
    <div id="event-log"></div>
  </div>

  <!-- Gaps tab -->
  <div class="panel" id="tab-gaps">
    <div id="gaps-content"></div>
  </div>

</div>

<script>
const $ = id => document.getElementById(id);

// --- Tab switching ---
document.querySelectorAll('.tab').forEach(tab => {
  tab.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
    tab.classList.add('active');
    $('tab-' + tab.dataset.tab).classList.add('active');
  });
});

// --- Pipeline tab ---
const STAGE_ORDER = ['0','0.5','1','2','3','4','4.5','5','6','6.5','7'];
const STAGE_NAMES = {
  '0':'Ingest','0.5':'Assess','1':'Extract','2':'Graph','3':'Audit',
  '4':'Research','4.5':'Align','5':'Synthesize','6':'Verify','6.5':'Prereq','7':'Assemble'
};

function renderPipeline(data) {
  const stages = data.stages || {};
  const grid = $('stage-grid');
  grid.innerHTML = '';
  STAGE_ORDER.forEach(sid => {
    const s = stages[sid] || {};
    const status = s.status || 'pending';
    const card = document.createElement('div');
    card.className = 'stage-card ' + status;
    const icon = {done:'✓',running:'▶',skipped:'⏭',error:'✗',pending:'·'}[status] || '·';
    const color = {done:'var(--green)',running:'var(--accent)',skipped:'var(--muted)',error:'var(--red)',pending:'var(--muted)'}[status];
    let meta = '';
    if (s.completed_at) meta = s.completed_at.slice(11,19);
    card.innerHTML = `
      <div class="stage-name"><span style="color:${color}">${icon}</span>  ${STAGE_NAMES[sid] || sid}</div>
      <div class="stage-status">${status}</div>
      ${meta ? `<div class="stage-meta">${meta}</div>` : ''}
    `;
    grid.appendChild(card);
  });
  $('run-label').textContent = data.run_id || '';
}

// --- Domains tab ---
let selectedDomain = null;

function renderDomains(data) {
  const list = $('domain-list');
  list.innerHTML = '';
  (data.domains || []).forEach(d => {
    const row = document.createElement('div');
    row.className = 'domain-row' + (d.id === selectedDomain ? ' selected' : '');
    row.dataset.id = d.id;
    const aligned = d.aligned ? '<span class="domain-badge aligned">aligned</span>' : '';
    const cl = d.checklist || {};
    const checks = [
      ['what_is','What-is'],['symbols','Symbols'],['examples','Examples'],
      ['where_next','Where-next'],['citations','Citations']
    ].map(([k,label]) => {
      const pass = cl[k];
      const cls = pass === true ? 'pass' : pass === false ? 'fail' : '';
      return `<span class="check ${cls}">${label}</span>`;
    }).join('');
    row.innerHTML = `
      <div class="domain-header">
        <span class="domain-label">${d.label}</span>
        ${aligned}
        <span class="domain-badge">${d.node_count || 0} nodes</span>
        <span class="domain-badge">${d.word_count || 0} words</span>
      </div>
      <div class="checklist">${checks}</div>
    `;
    row.addEventListener('click', () => {
      document.querySelectorAll('.domain-row').forEach(r => r.classList.remove('selected'));
      row.classList.add('selected');
      selectedDomain = d.id;
      loadSection(d.id);
    });
    list.appendChild(row);
  });
}

async function loadSection(id) {
  const view = $('section-view');
  view.className = 'visible';
  view.innerHTML = '<p style="color:var(--muted);padding:1rem">Loading…</p>';
  const r = await fetch('/api/section/' + id);
  const d = await r.json();
  view.innerHTML = d.html || '<p class="empty">Section not yet available.</p>';
}

// --- Events tab ---
let allEvents = [];

function renderEvents(events) {
  const filter = $('evt-filter-input').value.trim().toLowerCase();
  const log = $('event-log');
  const atBottom = log.scrollTop + log.clientHeight >= log.scrollHeight - 20;

  const filtered = filter ? events.filter(e =>
    (e.event||'').includes(filter) || JSON.stringify(e).toLowerCase().includes(filter)
  ) : events;

  log.innerHTML = filtered.slice(-300).map(evt => {
    const e = evt.event || '';
    const ts = (evt.ts||'').slice(11,23);
    let cls = '';
    if (e.startsWith('stage')) cls = 'stage';
    else if (e.startsWith('llm')) cls = 'llm';
    else if (e.startsWith('agent')) cls = 'agent';
    else if (e.includes('error') || e.includes('fail')) cls = 'error';
    else if (e.startsWith('ss')) cls = 'ss';
    const body = Object.entries(evt)
      .filter(([k]) => !['event','ts'].includes(k))
      .map(([k,v]) => `${k}=${JSON.stringify(v)}`).join('  ');
    return `<div class="evt-row">
      <span class="evt-ts">${ts}</span>
      <span class="evt-type ${cls}">${e}</span>
      <span class="evt-body">${body}</span>
    </div>`;
  }).join('');

  if (atBottom) log.scrollTop = log.scrollHeight;
}

$('evt-filter-input').addEventListener('input', () => renderEvents(allEvents));

// --- Gaps tab ---
function renderGaps(data) {
  const el = $('gaps-content');
  if (!data.domains || data.domains.length === 0) {
    el.innerHTML = '<div class="empty">No gap analysis available yet.</div>';
    return;
  }
  el.innerHTML = data.domains.map(d => {
    const gaps = d.gaps || [];
    if (gaps.length === 0) return '';
    const items = gaps.map(g => {
      const v = g.verdict || '';
      const vc = v === 'real_gap' ? 'verdict-real' : v === 'ambiguous' ? 'verdict-ambiguous' : 'verdict-not';
      return `<div class="gap-item">
        <div class="gap-desc"><span class="${vc}">[${v}]</span>  ${g.gap_description||''}</div>
        ${g.evidence_anchor_ids?.length ? `<div class="gap-evidence">evidence: ${g.evidence_anchor_ids.join(', ')}</div>` : ''}
        ${g.defender_argument ? `<div class="gap-evidence">defender: ${g.defender_argument.slice(0,120)}</div>` : ''}
      </div>`;
    }).join('');
    return `<div class="gap-card"><h3>${d.label}</h3>${items}</div>`;
  }).join('');
}

// --- Polling ---
let lastEventEtag = '', lastStatusEtag = '';
let isLive = false;

async function poll() {
  try {
    // Status (pipeline + domains)
    const sr = await fetch('/api/status', {headers: lastStatusEtag ? {'If-None-Match': lastStatusEtag} : {}});
    if (sr.status !== 304) {
      lastStatusEtag = sr.headers.get('ETag') || '';
      const s = await sr.json();
      renderPipeline(s);
      renderDomains(s);
      $('top-status').textContent = s.summary || '';
      const alive = !s.stages?.['7']?.status;
      $('pulse').className = 'pulse' + (alive ? ' live' : '');
    }

    // Events
    const er = await fetch('/api/events', {headers: lastEventEtag ? {'If-None-Match': lastEventEtag} : {}});
    if (er.status !== 304) {
      lastEventEtag = er.headers.get('ETag') || '';
      const ed = await er.json();
      allEvents = ed.events || [];
      renderEvents(allEvents);
    }

    // Gaps (once)
    if (!$('gaps-content').dataset.loaded) {
      const gr = await fetch('/api/gaps');
      if (gr.ok) {
        renderGaps(await gr.json());
        $('gaps-content').dataset.loaded = '1';
      }
    }

  } catch(e) {
    $('top-status').textContent = 'disconnected';
  }
}

poll();
setInterval(poll, 2000);
</script>
</body>
</html>
"""


def _load_state(state_dir: Path) -> dict:
    """Load pipeline.json + taxonomy + alignment data into a unified status dict."""
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
    audit_dir = state_dir / "audit"

    # Load alignment checklist results from events
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
                    if evt.get("event") == "s4_5_domain_aligned":
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

    stage7_done = done_stages.get("7", {}).get("status") == "complete"
    total_words = sum(d["word_count"] for d in domains)
    result["domains"] = domains
    result["summary"] = f"{n_done}/11 stages · {total_words:,} words · {'complete' if stage7_done else 'running'}"
    return result


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


def _load_gaps(state_dir: Path) -> dict:
    audit_dir = state_dir / "audit"
    gap_file = audit_dir / "gap_analysis.md"
    # Parse gap JSON files per domain
    tax_path = state_dir / "taxonomy.json"
    if not tax_path.exists():
        return {"domains": []}
    try:
        tax = json.loads(tax_path.read_text())
    except Exception:
        return {"domains": []}

    domains_out = []
    for d in tax.get("domains", []):
        did = d["id"]
        gap_json = audit_dir / f"gap_{did}.json"
        gaps = []
        if gap_json.exists():
            try:
                gaps = json.loads(gap_json.read_text())
            except Exception:
                pass
        domains_out.append({"id": did, "label": d.get("label", did), "gaps": gaps})

    return {"domains": domains_out}


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


def cmd_serve(state_dir: Path, port: int = 7842) -> None:
    import hashlib

    def _etag(data: bytes) -> str:
        return '"' + hashlib.md5(data).hexdigest() + '"'

    def _json_response(handler, obj: object) -> None:
        payload = json.dumps(obj, default=str).encode()
        tag = _etag(payload)
        client_tag = handler.headers.get("If-None-Match", "")
        if client_tag == tag:
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
                events = _load_events(state_dir)
                _json_response(self, {"events": events})

            elif self.path == "/api/gaps":
                _json_response(self, _load_gaps(state_dir))

            elif self.path.startswith("/api/section/"):
                sid = self.path[len("/api/section/"):]
                sid = sid.split("?")[0].strip("/")
                path = state_dir / "sections" / f"section_{sid}.md"
                html = _render_section(path)
                _json_response(self, {"html": html})

            else:
                self.send_response(404)
                self.end_headers()

    server = http.server.HTTPServer(("127.0.0.1", port), Handler)
    print(f"http://localhost:{port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
