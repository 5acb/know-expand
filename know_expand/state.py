"""
Logging and state management for the know-expand pipeline.

Log layout per run (runs/{run_id}/logs/):
  events.jsonl  — every emit() call as a timestamped JSON Line
  pipeline.log  — human-readable INFO+ with UTC timestamps
  debug.log     — everything including DEBUG
  errors.log    — WARNING+ only

stdout — real-time progress for the terminal operator (key events only)
stderr — nothing; all output is routed to files or stdout
"""

import contextvars
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import TextIO, TypedDict

# Context variables to track currently active pipeline execution context
active_stage: contextvars.ContextVar = contextvars.ContextVar("active_stage", default=None)
active_domain: contextvars.ContextVar = contextvars.ContextVar("active_domain", default=None)

# ---------------------------------------------------------------------------
# Run identity
# ---------------------------------------------------------------------------

_run_id: str = ""
_log_dir: Path | None = None
_event_fh: TextIO | None = None
_logger = logging.getLogger("know_expand")


def new_run_id() -> str:
    return str(uuid.uuid4())


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

class _UTCFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(timespec="milliseconds")
        return f"{ts} {record.levelname[0]} {record.name}: {record.getMessage()}"


def setup_logging(run_id: str, log_dir: Path) -> Path:
    """
    Wire up file handlers writing into log_dir (the full path, already including run_id).
    Must be called once at pipeline startup before any emit().
    Returns the log directory path.
    """
    global _run_id, _log_dir, _event_fh

    _run_id = run_id
    _log_dir = log_dir
    _log_dir.mkdir(parents=True, exist_ok=True)

    _event_fh = open(_log_dir / "events.jsonl", "a", buffering=1)

    fmt = _UTCFormatter()

    pipeline_handler = logging.FileHandler(_log_dir / "pipeline.log")
    pipeline_handler.setLevel(logging.INFO)
    pipeline_handler.setFormatter(fmt)

    debug_handler = logging.FileHandler(_log_dir / "debug.log")
    debug_handler.setLevel(logging.DEBUG)
    debug_handler.setFormatter(fmt)

    error_handler = logging.FileHandler(_log_dir / "errors.log")
    error_handler.setLevel(logging.WARNING)
    error_handler.setFormatter(fmt)

    root = logging.getLogger("know_expand")
    root.setLevel(logging.DEBUG)
    root.handlers.clear()
    root.addHandler(pipeline_handler)
    root.addHandler(debug_handler)
    root.addHandler(error_handler)
    root.propagate = False

    # Silence litellm/httpx noise from the root logger
    for noisy in ("litellm", "httpx", "httpcore", "openai"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _logger.info("run_id=%s log_dir=%s", run_id, _log_dir)
    return _log_dir


# ---------------------------------------------------------------------------
# PipelineState
# ---------------------------------------------------------------------------

class PipelineState(TypedDict):
    run_id: str
    state_dir: str       # absolute path; Path not JSON-serialisable in LangGraph
    output_dir: str
    input_path: str
    depth: str           # survey | standard | deep
    domain_ids: list[str]
    no_bibliography_fetch: bool
    bypass_license_gate: bool


# ---------------------------------------------------------------------------
# Sentinel helpers
# ---------------------------------------------------------------------------

def sentinel_path(base: Path) -> Path:
    return base.with_suffix(base.suffix + ".done")


def sentinel_exists(path: Path) -> bool:
    return sentinel_path(path).exists()


def write_sentinel(path: Path) -> None:
    sentinel_path(path).touch()


# ---------------------------------------------------------------------------
# pipeline.json helpers
# ---------------------------------------------------------------------------

def load_pipeline_json(state_dir: Path) -> dict:
    p = state_dir / "pipeline.json"
    if p.exists():
        return json.loads(p.read_text())
    return {}


def save_pipeline_json(state_dir: Path, data: dict) -> None:
    p = state_dir / "pipeline.json"
    atomic_write(p, json.dumps(data, indent=2))


def mark_stage_complete(state_dir: Path, stage: str | int) -> None:
    data = load_pipeline_json(state_dir)
    data.setdefault("stages", {})[str(stage)] = {
        "status": "complete",
        "completed_at": _utc(),
    }
    save_pipeline_json(state_dir, data)


def stage_is_complete(state_dir: Path, stage: str | int) -> bool:
    data = load_pipeline_json(state_dir)
    return data.get("stages", {}).get(str(stage), {}).get("status") == "complete"


# ---------------------------------------------------------------------------
# emit() — structured events → file + stdout progress
# ---------------------------------------------------------------------------

def calculate_cost(model: str, tok_in: int | None, tok_out: int | None) -> float:
    if not tok_in:
        tok_in = 0
    if not tok_out:
        tok_out = 0
    m = model.lower()
    if m.startswith("geminicli/") or m.startswith("llamacpp/"):
        return 0.0
    
    in_rate = 0.0
    out_rate = 0.0
    
    if "opus" in m:
        in_rate = 15.0 / 1_000_000
        out_rate = 75.0 / 1_000_000
    elif "sonnet" in m:
        in_rate = 3.0 / 1_000_000
        out_rate = 15.0 / 1_000_000
    elif "gpt-4o-mini" in m:
        in_rate = 0.15 / 1_000_000
        out_rate = 0.60 / 1_000_000
    elif "gpt-4o" in m:
        in_rate = 5.0 / 1_000_000
        out_rate = 15.0 / 1_000_000
    elif "gemini-2.5-flash" in m:
        in_rate = 0.30 / 1_000_000
        out_rate = 2.50 / 1_000_000
    elif "gemini-3.5-flash" in m:
        in_rate = 1.50 / 1_000_000
        out_rate = 9.00 / 1_000_000
    elif "mistral-small" in m:
        in_rate = 0.15 / 1_000_000
        out_rate = 0.60 / 1_000_000
    elif "llama-3.1-8b" in m:
        in_rate = 0.05 / 1_000_000
        out_rate = 0.08 / 1_000_000
    elif "llama-3.3-70b" in m or "llama-3.3-70b-versatile" in m:
        in_rate = 0.59 / 1_000_000
        out_rate = 0.79 / 1_000_000
    else:
        in_rate = 1.0 / 1_000_000
        out_rate = 3.0 / 1_000_000
        
    return (tok_in * in_rate) + (tok_out * out_rate)


def emit(event: dict) -> None:
    """
    Write a timestamped JSON event to events.jsonl, log it to the appropriate
    log file, and print a human-readable progress line to stdout if the event
    warrants operator attention.
    """
    ts = _utc()
    stamped = {"ts": ts, **event}

    name = event.get("event", "")
    if name == "llm_call_done":
        stage = active_stage.get()
        domain = active_domain.get()
        model = event.get("model", "")
        tok_in = event.get("tok_in")
        tok_out = event.get("tok_out")
        cost = calculate_cost(model, tok_in, tok_out)
        
        event["cost_usd"] = round(cost, 5)
        stamped["cost_usd"] = round(cost, 5)
        if stage is not None:
            stamped["stage"] = stage
        if domain is not None:
            stamped["domain"] = domain
            
        if _log_dir is not None:
            audit_dir = _log_dir.parent / "state" / "audit"
            audit_dir.mkdir(parents=True, exist_ok=True)
            with open(audit_dir / "model_usage.jsonl", "a") as f:
                f.write(json.dumps(stamped) + "\n")

    if name in ("model_quota_switch", "model_auth_skip", "model_geminicli_skip"):
        if _log_dir is not None:
            state_dir = _log_dir.parent / "state"
            if state_dir.exists():
                try:
                    data = load_pipeline_json(state_dir)
                    if data.get("model_consistency") != "mixed":
                        data["model_consistency"] = "mixed"
                        save_pipeline_json(state_dir, data)
                except Exception:
                    pass

    if _event_fh is not None:
        _event_fh.write(json.dumps(stamped) + "\n")

    _route_to_log(name, event)
    line = _progress_line(ts, name, event)
    if line:
        print(line, flush=True)


def _route_to_log(name: str, ev: dict) -> None:
    """Send event to the Python logger at the appropriate level."""
    error_events = {"domain_failed", "quota_exhausted", "ss_exhausted", "llm_call_error"}
    warn_events = {"ss_429_retry", "pipeline_paused"}
    debug_events = {
        "ss_rate_wait", "ss_request", "ss_response",
        "llm_call_start", "chunk_map_start", "chunk_map_cached",
        "source_refs_skipped", "model_auth_skip", "model_quota_switch",
    }

    if name in error_events:
        _logger.error("%s %s", name, _kv(ev))
    elif name in warn_events:
        _logger.warning("%s %s", name, _kv(ev))
    elif name in debug_events:
        _logger.debug("%s %s", name, _kv(ev))
    else:
        _logger.info("%s %s", name, _kv(ev))


def _progress_line(ts: str, name: str, ev: dict) -> str | None:
    """Return a stdout progress string for operator-visible events, or None."""
    match name:
        case "stage_start":
            return f"{ts}  ▶  stage {ev.get('stage')} starting"
        case "stage_complete":
            extra = _kv(ev, skip={"event", "stage", "artifact", "artifact_gap_analysis", "artifact_corrections"})
            return f"{ts}  ✓  stage {ev.get('stage')} complete  {extra}"
        case "stage_skipped":
            return f"{ts}  ⏭  stage {ev.get('stage')} skipped ({ev.get('reason')})"
        case "stage3_start":
            return (
                f"{ts}     {ev.get('domain_count')} domains  "
                f"{ev.get('node_count')} nodes  "
                f"{ev.get('conflict_count')} conflicts"
            )
        case "domain_start":
            return f"{ts}     domain {ev.get('domain_id')}: starting ({ev.get('term_count', '?')} terms)"
        case "domain_skipped":
            return f"{ts}     domain {ev.get('domain_id')}: skipped ({ev.get('reason')})"
        case "domain_fetch_start":
            return f"{ts}     domain {ev.get('domain_id')}: fetching bibliography (depth={ev.get('depth')})"
        case "domain_fetch_done":
            return f"{ts}     domain {ev.get('domain_id')}: fetch done ({ev.get('elapsed_s')}s)"
        case "anchors_fetched":
            return f"{ts}     domain {ev.get('domain_id')}: {ev.get('anchor_count')} anchors"
        case "bibliography_fetched":
            return f"{ts}     domain {ev.get('domain_id')}: {ev.get('entry_count')} bibliography entries"
        case "gap_finder_start":
            return f"{ts}     domain {ev.get('domain_id')}: gap finder running"
        case "gap_finder_complete":
            return f"{ts}     domain {ev.get('domain_id')}: {ev.get('gap_count')} gaps found"
        case "gap_defender_start":
            return f"{ts}     domain {ev.get('domain_id')}: gap defender running"
        case "gap_rebuttal_start":
            return f"{ts}     domain {ev.get('domain_id')}: rebuttal running"
        case "gap_rebuttal_complete":
            return (
                f"{ts}     domain {ev.get('domain_id')}: "
                f"real={ev.get('real_gaps')} not={ev.get('not_gaps')} ambig={ev.get('ambiguous')}"
            )
        case "domain_complete":
            return (
                f"{ts}     domain {ev.get('domain_id')}: done "
                f"({ev.get('elapsed_s')}s, {ev.get('real_gaps')} real gaps)"
            )
        case "domain_failed":
            return f"{ts}  ✗  domain {ev.get('domain_id')}: FAILED — {ev.get('error')}"
        case "ss_cooldown_wait":
            return (
                f"{ts}     SS cooldown: all tasks pausing {ev.get('wait_s')}s "
                f"(query={ev.get('query')!r})"
            )
        case "ss_429_retry":
            return (
                f"{ts}     SS 429 on {ev.get('query')!r} — "
                f"retry {ev.get('attempt')}/{ev.get('max_retries')} in {ev.get('sleep_s')}s "
                f"(global cooldown set)"
            )
        case "model_probe_done":
            avail = ev.get("available", [])
            unavail = ev.get("unavailable", [])
            parts = [f"available: {', '.join(avail)}"]
            if unavail:
                parts.append(f"skipping: {', '.join(unavail)}")
            return f"{ts}     probe  {' | '.join(parts)}"
        case "llm_call_done":
            tok_s = ev.get("tok_s")
            tok_out = ev.get("tok_out")
            throughput = f"  {tok_out}tok @ {tok_s}tok/s" if tok_s else ""
            return f"{ts}     LLM [{ev.get('role')}] {ev.get('schema')} done ({ev.get('elapsed_s')}s){throughput}"
        case "llm_call_error":
            return f"{ts}  ✗  LLM [{ev.get('role')}] {ev.get('schema')} error: {ev.get('error')}"
        case "model_auth_skip":
            return f"{ts}     auth fail on {ev.get('skipped_model')} → {ev.get('next_model')}"
        case "model_quota_switch":
            return f"{ts}     quota on {ev.get('exhausted_model')} → {ev.get('next_model')}"
        case "quota_exhausted":
            return f"{ts}  ✗  all models exhausted for role {ev.get('role')}"
        case "stage1_map_start":
            return f"{ts}     mapping {ev.get('chunk_count')} chunks"
        case "chunk_map_done":
            return f"{ts}     chunk {ev.get('chunk_id')}: {ev.get('term_count')} terms ({ev.get('elapsed_s')}s)"
        case "stage1_reduce_start":
            return f"{ts}     reducing {ev.get('raw_term_count')} raw terms"
        case "taxonomy_approved":
            return f"{ts}     taxonomy approved ({ev.get('mode')}, {ev.get('domain_count')} domains)"
        case "stage2_phase1_start":
            return f"{ts}     phase 1: ontology lock ({ev.get('core_term_count')} core terms)"
        case "stage2_phase2_start":
            return f"{ts}     phase 2: classification ({ev.get('domain_count')} domains)"
        case "pipeline_paused":
            return f"{ts}  ⏸  pipeline paused: {ev.get('reason')}  {ev.get('instructions', '')}"
        case "pipeline_status":
            return f"{ts}     status: {json.dumps(ev)}"
        case "s4_domain_start":
            if ev.get("skipped"):
                return f"{ts}     stage4 domain {ev.get('domain_id')}: skipped ({ev.get('reason')})"
            return f"{ts}     stage4 domain {ev.get('domain_id')}: starting research"
        case "s4_domain_top_down_done":
            return f"{ts}     stage4 domain {ev.get('domain_id')}: top-down draft done"
        case "s4_domain_bottom_up_done":
            return f"{ts}     stage4 domain {ev.get('domain_id')}: bottom-up draft done"
        case "s4_domain_critique_round":
            return (
                f"{ts}     stage4 domain {ev.get('domain_id')}: "
                f"critique round {ev.get('round')}/{ev.get('total_rounds')}"
            )
        case "s4_domain_complete":
            return (
                f"{ts}     stage4 domain {ev.get('domain_id')}: done "
                f"({ev.get('elapsed_s')}s)"
            )
        case "s4_domain_failed":
            return f"{ts}  ✗  stage4 domain {ev.get('domain_id')}: FAILED — {ev.get('error')}"
        case "s4_complete":
            return (
                f"{ts}  ✓  stage 4 complete  "
                f"domains_succeeded={ev.get('domains_succeeded')} "
                f"domains_failed={ev.get('domains_failed')}"
            )
        case "s5_structural_start":
            return f"{ts}     stage5 structural agent: {ev.get('domain_count')} domains"
        case "s5_semantic_start":
            return f"{ts}     stage5 semantic agent: {ev.get('domain_count')} domains"
        case "s5_critique_round":
            return f"{ts}     stage5 critique round {ev.get('round')}/{ev.get('total_rounds')}"
        case "s5_complete":
            if ev.get("skipped"):
                return f"{ts}  ⏭  stage 5 skipped ({ev.get('reason')})"
            return (
                f"{ts}  ✓  stage 5 complete  "
                f"summaries={ev.get('summaries_loaded')} "
                f"insights={ev.get('insights_count')}"
            )
        case "s6_start":
            return f"{ts}     stage6 citation audit starting"
        case "s6_complete":
            return (
                f"{ts}  ✓  stage 6 complete  "
                f"needs_citation={ev.get('total_needs_citation')} "
                f"unknown_keys={ev.get('total_unknown_keys')} "
                f"verified={ev.get('total_verified')}"
            )
        case "s7_start":
            return f"{ts}     stage7 assembly starting"
        case "s7_bibliography_merged":
            return f"{ts}     stage7 bibliography: {ev.get('entry_count')} entries merged"
        case "s7_markdown_written":
            return f"{ts}     stage7 markdown written ({ev.get('total_words')} words, {ev.get('sections')} sections)"
        case "s7_pdf_start":
            return f"{ts}     stage7 PDF: building with pandoc+xelatex"
        case "s7_pdf_complete":
            return f"{ts}  ✓  stage7 PDF: {ev.get('pages')} pages → {ev.get('path')}"
        case "s7_pdf_skipped":
            return f"{ts}     stage7 PDF: skipped ({ev.get('reason')})"
        case "s7_complete":
            return (
                f"{ts}  ✓  stage 7 complete  "
                f"words={ev.get('total_words')} "
                f"output={ev.get('output_md')}"
            )
        case "run_failed":
            return f"{ts}  ✗  run {ev.get('run_id')} FAILED: {ev.get('error')}"
        case "run_complete":
            return f"{ts}  ✓  run {ev.get('run_id')} COMPLETE"
        case _:
            return None


def _kv(ev: dict, skip: frozenset[str] = frozenset({"event"})) -> str:
    parts = [f"{k}={v!r}" for k, v in ev.items() if k not in skip and v is not None]
    return " ".join(parts[:8])


def emit_human(message: str) -> None:
    """Interactive/human-mode output; goes to stdout."""
    print(message, flush=True)


# ---------------------------------------------------------------------------
# Atomic file write
# ---------------------------------------------------------------------------

def atomic_write(path: Path, content: str) -> None:
    """Write content to path atomically via a tmp+rename guard (POSIX atomic)."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content)
    os.replace(tmp, path)
