"""Stage 7b — Quality Evaluator: terminal agent-as-judge scoring pass.

Adapted from the Quality Evaluator agent in "Agentic AutoSurvey: Let LLMs
Survey LLMs" (arXiv 2509.18661): a 12-dimension rubric across 3 weighted
categories, scored 0-10 with textual justification per dimension, via a
multi-stage internal reasoning process (read-through -> per-dimension
scoring -> citation-count check -> synthesis-pattern check ->
critical-analysis check).

Unlike that paper's use as a survey-quality gate, this stage is purely
observational, exactly like [NEEDS_CITATION] in S8:
  - It runs after S7 Synthesize and before S8 Verify.
  - It NEVER raises on a low score and NEVER blocks the pipeline.
  - It does not assume rigid pass/fail thresholds; the prompt instructs the
    judge to adapt expectations to the calibrated UserProfile depth
    (survey vs. standard vs. deep), not score a survey section as a failed
    deep one.

Rubric (adapted from a literature-survey rubric to this pipeline's actual
per-domain research narrative + cross-domain synthesis):
  Core Quality (60%): citation_coverage, citation_accuracy, factual_accuracy,
    synthesis_vs_enumeration, structural_organization, taxonomy_coherence
  Writing Quality (20%): readability_at_depth, terminology_consistency
  Content Depth (20%): comprehensiveness, critical_analysis,
    frontier_novelty, where_to_go_next_quality

Scores one domain at a time (state/sections/section_{domain}.md +
state/summaries/summary_{domain}.json against state/audit/bibliography_{
domain}.json and its state/graph.json nodes), plus one cross-domain pass
over state/sections/section_synthesis.md.

Idempotent: one `.done` sentinel per domain (and one for synthesis) under
state/audit/, mirroring the S5/S6 `section_{domain_id}.done` /
`.aligned` sentinel convention.
"""

import json
import logging
import re
import time
from pathlib import Path

from know_expand.agents.base import make_router
from know_expand.agents.schemas import QualityEvaluation
from know_expand.config import Config
from know_expand.state import (
    PipelineState,
    atomic_write,
    emit,
    mark_stage_complete,
    stage_is_complete,
)

_logger = logging.getLogger("know_expand.s7b_quality_eval")

STAGE_ID = "7b"

# ---------------------------------------------------------------------------
# Rubric structure
# ---------------------------------------------------------------------------

_CORE_FIELDS = [
    "citation_coverage",
    "citation_accuracy",
    "factual_accuracy",
    "synthesis_vs_enumeration",
    "structural_organization",
    "taxonomy_coherence",
]
_WRITING_FIELDS = ["readability_at_depth", "terminology_consistency"]
_CONTENT_FIELDS = [
    "comprehensiveness",
    "critical_analysis",
    "frontier_novelty",
    "where_to_go_next_quality",
]
_ALL_FIELDS = _CORE_FIELDS + _WRITING_FIELDS + _CONTENT_FIELDS

CATEGORY_WEIGHTS = {"core_quality": 0.6, "writing_quality": 0.2, "content_depth": 0.2}

# ---------------------------------------------------------------------------
# Deterministic citation scan (self-contained — not shared with s8_verify.py,
# which is being edited in parallel; a small independent regex is cheaper
# than cross-module coupling here).
# ---------------------------------------------------------------------------

_CITATION_KEY_RE = re.compile(r"\[@?([\w\-]{3,})\]")
_NEEDS_CITATION_RE = re.compile(r"\[NEEDS_CITATION\]")
_FENCED_CODE_RE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`[^`\n]{1,80}`")
_PIPELINE_MARKERS = frozenset({"NEEDS_CITATION", "INFERRED", "UNVALIDATED", "VERIFIED"})


def _citation_stats(text: str, valid_ids: set[str]) -> dict:
    """Deterministic citation-count check (paper's step 3), computed in
    Python so the judge scores against real numbers, not a prose impression."""
    scan = _FENCED_CODE_RE.sub("", text)
    scan = _INLINE_CODE_RE.sub("", scan)

    needs_citation = len(_NEEDS_CITATION_RE.findall(scan))
    keys: list[str] = []
    for match in _CITATION_KEY_RE.finditer(scan):
        key = match.group(1)
        if key in _PIPELINE_MARKERS:
            continue
        if key.islower() and "_" not in key and "-" not in key:
            continue
        keys.append(key)

    valid_keys = [k for k in keys if k in valid_ids]
    return {
        "needs_citation_markers": needs_citation,
        "total_citation_keys_found": len(keys),
        "valid_citation_keys": len(valid_keys),
        "unknown_citation_keys": len(keys) - len(valid_keys),
        "unique_bibliography_entries_cited": len(set(valid_keys)),
        "bibliography_pool_size": len(valid_ids),
    }


def _bibliography_bucket_counts(entries: list[dict]) -> dict:
    counts = {"foundational": 0, "frontier": 0, "anchor": 0, "other": 0}
    for e in entries:
        bucket = e.get("bucket", "other")
        counts[bucket] = counts.get(bucket, 0) + 1
    counts["total"] = len(entries)
    return counts


def _domain_graph_context(graph_data: dict, domain_id: str) -> dict:
    nodes = [n for n in graph_data.get("nodes", []) if n.get("domain") == domain_id]
    node_ids = {n.get("id") for n in nodes}
    core_terms = [n.get("name", "") for n in nodes if n.get("centrality") == "core"]
    supporting_terms = [n.get("name", "") for n in nodes if n.get("centrality") == "supporting"]
    edges = []
    for e in graph_data.get("edges", []):
        f = e.get("from", e.get("from_node", ""))
        t = e.get("to", e.get("to_node", ""))
        if f in node_ids or t in node_ids:
            edges.append(f"{f} --[{e.get('type', '')}]--> {t}")
    return {
        "core_terms": core_terms,
        "supporting_terms": supporting_terms,
        "cross_domain_edges": edges[:60],
    }


def _synthesis_graph_context(graph_data: dict, domain_ids: list[str]) -> dict:
    domains = graph_data.get("domains", [])
    domain_labels = [d.get("label", d.get("id", "")) for d in domains] or domain_ids
    edges = []
    for e in graph_data.get("edges", []):
        f = e.get("from", e.get("from_node", ""))
        t = e.get("to", e.get("to_node", ""))
        edges.append(f"{f} --[{e.get('type', '')}]--> {t}")
    return {
        "domains": domain_labels,
        "cross_domain_edges": edges[:100],
    }


def _load_user_profile(state_dir: Path, fallback_depth: str) -> dict:
    path = state_dir / "user_profile.json"
    if path.exists():
        try:
            data = json.loads(path.read_text())
            return {
                "effective_depth": data.get("effective_depth", fallback_depth),
                "math_mode": data.get("math_mode", "equations_explained"),
                "familiarity_level": data.get("familiarity_level", "aware"),
                "learning_goal": data.get("learning_goal", "explain"),
            }
        except Exception:
            pass
    return {
        "effective_depth": fallback_depth,
        "math_mode": "unknown",
        "familiarity_level": "unknown",
        "learning_goal": "unknown",
    }


def _load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except Exception:
        return default


def _load_bibliography(state_dir: Path, audit_dir: Path, domain_id: str | None) -> list[dict]:
    """Per-domain bibliography (`audit/bibliography_{domain}.json`), or the
    merged top-level `bibliography.json` for the synthesis pass, falling
    back to a union of every per-domain file if that's missing."""
    if domain_id is not None:
        return _load_json(audit_dir / f"bibliography_{domain_id}.json", [])

    merged = state_dir / "bibliography.json"
    if merged.exists():
        return _load_json(merged, [])

    entries: list[dict] = []
    seen_ids: set[str] = set()
    for bib_file in sorted(audit_dir.glob("bibliography_*.json")):
        for entry in _load_json(bib_file, []):
            cid = entry.get("id")
            if cid and cid in seen_ids:
                continue
            if cid:
                seen_ids.add(cid)
            entries.append(entry)
    return entries


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

_QUALITY_EVAL_PROMPT = """\
You are the Quality Evaluator — a terminal, agent-as-judge scoring pass over \
a finished piece of research writing. You do NOT rewrite, patch, or gate \
anything; your only output is a scored rubric with textual justification. A \
low score here never blocks the pipeline — it is a report, exactly like \
[NEEDS_CITATION] markers, which are logged but never fail the build.

Work through these internal steps before scoring (report only the final \
scores and justifications, not a transcript of the steps):
1. READ-THROUGH: read the full text end to end for overall shape and argument.
2. PER-DIMENSION SCORING: score each of the 12 dimensions below, 0-10.
3. CITATION-COUNT CHECK: weigh the deterministic citation counts below \
   against the bibliography pool size — trust these numbers over any \
   impression the prose's confident tone gives you.
4. SYNTHESIS-PATTERN CHECK: distinguish genuine cross-source synthesis \
   (comparing, reconciling, building claims on top of sources) from mere \
   enumeration (listing facts source by source with no connective reasoning).
5. CRITICAL-ANALYSIS CHECK: does the text surface limitations, open \
   problems, or tensions, or is it uniformly affirmative?

ADAPT your scoring expectations to context — do not apply rigid pass/fail \
thresholds. A "survey"-depth section is not a failed "deep" section; fewer \
frontier citations is not automatically weak novelty if the reader profile \
calls for a gentler introduction. Judge each dimension relative to what this \
text is trying to be for the given reader.

SCOPE: {scope_label}
READER PROFILE: depth={effective_depth}, math_mode={math_mode}, \
familiarity={familiarity_level}, learning_goal={learning_goal}

DETERMINISTIC CITATION STATS (computed by Python — see check #3):
{citation_stats_json}

BIBLIOGRAPHY POOL — the ONLY pool the writer was allowed to cite from \
(bucket breakdown): {bibliography_buckets_json}

TAXONOMY / GRAPH CONTEXT:
{graph_context_json}

TEXT TO EVALUATE:
---
{section_text}
---

STRUCTURED SUMMARY (cross-check against claimed citations and open \
questions):
{summary_json}

Score all 12 dimensions:

Core Quality (60% of weighted total):
- citation_coverage: are load-bearing claims backed by a citation from the pool?
- citation_accuracy: do cited keys plausibly support the claims made near them?
- factual_accuracy: are technical claims correct as far as you can tell?
- synthesis_vs_enumeration: genuine synthesis (check #4) vs. a list of facts?
- structural_organization: logical flow, sensible heading hierarchy?
- taxonomy_coherence: does the content match this scope's place in the \
  taxonomy above (core/supporting terms, cross-domain edges, or overall \
  domain hierarchy)?

Writing Quality (20% of weighted total):
- readability_at_depth: appropriate for the reader profile's depth/math_mode?
- terminology_consistency: consistent term usage, matching graph node names?

Content Depth (20% of weighted total):
- comprehensiveness: coverage of the relevant core AND supporting graph nodes \
  (or, for a cross-domain scope, of the domains themselves)?
- critical_analysis: per check #5 above — limitations/tensions surfaced?
- frontier_novelty: engagement with the frontier bibliography bucket, not \
  just foundational sources?
- where_to_go_next_quality: is there a genuinely useful "where to go next" \
  (open problems, start-here resource, further reading, or reading roadmap) \
  for this reader?

Return a QualityEvaluation with all 12 fields populated (score + \
justification each) plus overall_notes summarizing the read-through and the \
three checks.
"""


def _build_prompt(
    scope_label: str,
    section_text: str,
    summary: dict,
    profile: dict,
    citation_stats: dict,
    bibliography_buckets: dict,
    graph_context: dict,
) -> list[dict]:
    truncated = section_text[:48000]
    if len(section_text) > 48000:
        truncated += f"\n\n... [truncated, {len(section_text)} chars total]"
    content = _QUALITY_EVAL_PROMPT.format(
        scope_label=scope_label,
        effective_depth=profile["effective_depth"],
        math_mode=profile["math_mode"],
        familiarity_level=profile["familiarity_level"],
        learning_goal=profile["learning_goal"],
        citation_stats_json=json.dumps(citation_stats),
        bibliography_buckets_json=json.dumps(bibliography_buckets),
        graph_context_json=json.dumps(graph_context),
        section_text=truncated,
        summary_json=json.dumps(summary)[:4000],
    )
    return [{"role": "user", "content": content}]


# ---------------------------------------------------------------------------
# Scoring helpers
# ---------------------------------------------------------------------------

def _category_avg(evaluation: QualityEvaluation, fields: list[str]) -> float:
    if not fields:
        return 0.0
    return sum(getattr(evaluation, f).score for f in fields) / len(fields)


def _score_record(domain_id: str, domain_label: str, evaluation: QualityEvaluation) -> dict:
    core = round(_category_avg(evaluation, _CORE_FIELDS), 3)
    writing = round(_category_avg(evaluation, _WRITING_FIELDS), 3)
    content = round(_category_avg(evaluation, _CONTENT_FIELDS), 3)
    weighted_total = round(
        core * CATEGORY_WEIGHTS["core_quality"]
        + writing * CATEGORY_WEIGHTS["writing_quality"]
        + content * CATEGORY_WEIGHTS["content_depth"],
        3,
    )
    justification = {f: getattr(evaluation, f).justification for f in _ALL_FIELDS}
    dimension_scores = {f: getattr(evaluation, f).score for f in _ALL_FIELDS}
    return {
        "domain_id": domain_id,
        "domain_label": domain_label,
        "core_quality": core,
        "writing_quality": writing,
        "content_depth": content,
        "weighted_total": weighted_total,
        "dimension_scores": dimension_scores,
        "justification": justification,
        "overall_notes": evaluation.overall_notes,
    }


# ---------------------------------------------------------------------------
# Per-domain / synthesis evaluation
# ---------------------------------------------------------------------------

async def _evaluate_one(
    domain_id: str,
    domain_label: str,
    scope_label: str,
    section_path: Path,
    summary: dict,
    valid_ids: set[str],
    bibliography_buckets: dict,
    graph_context: dict,
    profile: dict,
    audit_dir: Path,
    router,
) -> dict | None:
    t0 = time.monotonic()
    sentinel = audit_dir / f"quality_eval_{domain_id}.done"
    result_path = audit_dir / f"quality_eval_{domain_id}.json"

    if sentinel.exists():
        existing = _load_json(result_path, None)
        if existing is not None:
            emit({"event": "quality_eval_skipped", "domain_id": domain_id, "reason": "already_scored"})
            return existing
        # Sentinel exists but artifact is missing/corrupt — fall through and re-score.

    if not section_path.exists():
        emit({
            "event": "quality_eval_error",
            "domain_id": domain_id,
            "error": "section file missing",
        })
        sentinel.touch()
        return None

    section_text = section_path.read_text()
    citation_stats = _citation_stats(section_text, valid_ids)

    messages = _build_prompt(
        scope_label=scope_label,
        section_text=section_text,
        summary=summary,
        profile=profile,
        citation_stats=citation_stats,
        bibliography_buckets=bibliography_buckets,
        graph_context=graph_context,
    )

    try:
        evaluation: QualityEvaluation = await router.call(messages, QualityEvaluation)
    except Exception as exc:
        emit({
            "event": "quality_eval_error",
            "domain_id": domain_id,
            "error": str(exc)[:200],
            "elapsed_s": round(time.monotonic() - t0, 2),
        })
        sentinel.touch()
        return None

    record = _score_record(domain_id, domain_label, evaluation)
    record["citation_stats"] = citation_stats
    record["bibliography_buckets"] = bibliography_buckets

    atomic_write(result_path, json.dumps(record, indent=2))
    sentinel.touch()

    emit({
        "event": "quality_eval_scored",
        "domain_id": domain_id,
        "core_quality": record["core_quality"],
        "writing_quality": record["writing_quality"],
        "content_depth": record["content_depth"],
        "weighted_total": record["weighted_total"],
        "justification": record["justification"],
        "elapsed_s": round(time.monotonic() - t0, 2),
    })
    return record


# ---------------------------------------------------------------------------
# Rollup markdown
# ---------------------------------------------------------------------------

def _build_rollup_md(records: list[dict]) -> str:
    lines = [
        "# Quality Evaluation\n",
        "Terminal agent-as-judge scoring pass (adapted from the Quality "
        "Evaluator in \"Agentic AutoSurvey\", arXiv 2509.18661). Scores are "
        "reported only — they never gate or trigger revision.\n",
        "| Scope | Core (60%) | Writing (20%) | Depth (20%) | Weighted Total |",
        "|---|---|---|---|---|",
    ]
    for r in records:
        lines.append(
            f"| {r['domain_label']} (`{r['domain_id']}`) | {r['core_quality']:.2f} "
            f"| {r['writing_quality']:.2f} | {r['content_depth']:.2f} "
            f"| **{r['weighted_total']:.2f}** |"
        )

    lines.append("")
    for r in records:
        lines.append(f"## {r['domain_label']} (`{r['domain_id']}`)\n")
        lines.append(r.get("overall_notes", "") + "\n")
        for field in _ALL_FIELDS:
            score = r["dimension_scores"].get(field)
            just = r["justification"].get(field, "")
            if score is None:
                continue
            lines.append(f"- **{field}** ({score:.1f}/10): {just}")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Stage entry point
# ---------------------------------------------------------------------------

async def run(state: PipelineState, cfg: Config) -> None:
    state_dir = Path(state["state_dir"])
    sections_dir = state_dir / "sections"
    summaries_dir = state_dir / "summaries"
    audit_dir = state_dir / "audit"
    audit_dir.mkdir(exist_ok=True)

    if stage_is_complete(state_dir, STAGE_ID):
        emit({"event": "stage_skipped", "stage": STAGE_ID, "reason": "already_complete"})
        return

    emit({"event": "stage_start", "stage": STAGE_ID})

    if "quality_evaluator" not in cfg.models:
        emit({"event": "quality_eval_skipped", "reason": "no_quality_evaluator_role_in_models"})
        mark_stage_complete(state_dir, STAGE_ID)
        emit({"event": "stage_complete", "stage": STAGE_ID, "domains_scored": 0, "skipped": True})
        return

    taxonomy = _load_json(state_dir / "taxonomy.json", None)
    if taxonomy is None:
        emit({"event": "quality_eval_skipped", "reason": "no_taxonomy"})
        mark_stage_complete(state_dir, STAGE_ID)
        emit({"event": "stage_complete", "stage": STAGE_ID, "domains_scored": 0, "skipped": True})
        return

    domains = taxonomy.get("domains", [])
    graph_data = _load_json(state_dir / "graph.json", {"nodes": [], "edges": [], "domains": []})
    fallback_depth = state.get("depth", "standard")
    profile = _load_user_profile(state_dir, fallback_depth)

    router = make_router("quality_evaluator", cfg)

    records: list[dict] = []

    # Everything below is best-effort and non-blocking: an exception scoring
    # one domain (or the synthesis pass) is caught locally; a failure in the
    # loop/rollup machinery itself is caught here so the stage always reaches
    # mark_stage_complete().
    try:
        for domain in domains:
            domain_id = domain.get("id", "")
            domain_label = domain.get("label", domain_id)
            try:
                from know_expand.state import active_domain
                active_domain.set(domain_id)

                section_path = sections_dir / f"section_{domain_id}.md"
                summary = _load_json(summaries_dir / f"summary_{domain_id}.json", {})
                bib_entries = _load_bibliography(state_dir, audit_dir, domain_id)
                valid_ids = {e.get("id") for e in bib_entries if e.get("id")}

                record = await _evaluate_one(
                    domain_id=domain_id,
                    domain_label=domain_label,
                    scope_label=f"Per-domain research narrative for '{domain_label}'",
                    section_path=section_path,
                    summary=summary,
                    valid_ids=valid_ids,
                    bibliography_buckets=_bibliography_bucket_counts(bib_entries),
                    graph_context=_domain_graph_context(graph_data, domain_id),
                    profile=profile,
                    audit_dir=audit_dir,
                    router=router,
                )
                if record is not None:
                    records.append(record)
            except Exception as exc:
                emit({
                    "event": "quality_eval_domain_failed",
                    "domain_id": domain_id,
                    "error": str(exc)[:200],
                })

        # Cross-domain synthesis pass.
        try:
            from know_expand.state import active_domain
            active_domain.set("synthesis")

            domain_ids = [d.get("id", "") for d in domains]
            synthesis_path = sections_dir / "section_synthesis.md"
            synthesis_summary = _load_json(summaries_dir / "summary_synthesis.json", {})
            bib_entries = _load_bibliography(state_dir, audit_dir, None)
            valid_ids = {e.get("id") for e in bib_entries if e.get("id")}

            record = await _evaluate_one(
                domain_id="synthesis",
                domain_label="Cross-Domain Synthesis",
                scope_label="Cross-domain synthesis chapter spanning all domains",
                section_path=synthesis_path,
                summary=synthesis_summary,
                valid_ids=valid_ids,
                bibliography_buckets=_bibliography_bucket_counts(bib_entries),
                graph_context=_synthesis_graph_context(graph_data, domain_ids),
                profile=profile,
                audit_dir=audit_dir,
                router=router,
            )
            if record is not None:
                records.append(record)
        except Exception as exc:
            emit({"event": "quality_eval_synthesis_failed", "error": str(exc)[:200]})

        if records:
            rollup_path = audit_dir / "quality_eval.md"
            atomic_write(rollup_path, _build_rollup_md(records))
    except Exception as exc:
        # Belt-and-braces: this stage must never prevent S8 from running.
        _logger.warning("s7b_quality_eval: unexpected error, continuing: %s", exc)
        emit({"event": "quality_eval_stage_error", "error": str(exc)[:200]})

    mark_stage_complete(state_dir, STAGE_ID)
    emit({
        "event": "stage_complete",
        "stage": STAGE_ID,
        "domains_scored": len(records),
        "artifact": str(audit_dir / "quality_eval.md"),
    })
