"""Stage 3 — Anchored Audit: per-domain bibliography and gap analysis."""

import asyncio
import json
import logging
import time
from pathlib import Path

import httpx

from doc_expand.agents.base import make_router
from doc_expand.agents.schemas import GapAnalysisResult, GapFinding
from doc_expand.bibliography import fetch_anchors, fetch_bibliography, reset_ss_limiter
from doc_expand.config import Config
from doc_expand.state import PipelineState, emit, mark_stage_complete, stage_is_complete

_logger = logging.getLogger("doc_expand.s3")

_GAP_FINDER_PROMPT = """\
You are a research gap analyst. Given the terms from a domain's knowledge graph and \
the titles and abstracts of anchor papers for that domain, identify what the anchor \
papers cover that the graph is missing.

Domain: {domain_label}

Graph terms: {graph_terms}

Anchor papers:
{anchor_summaries}

Look especially for gaps in these categories:
- Methodological gaps: approaches described in the papers but absent from the graph
- Citation graph gaps: the papers cite foundational work or datasets not present in the graph
- Snowballing gaps: if the anchor papers reference key sub-fields that the graph does not cover
- Evaluation gaps: benchmarks, metrics, or reproducibility practices mentioned in papers but missing

For each gap, provide a gap_description and list the evidence_anchor_ids (paper IDs \
from the anchor list) that reveal the gap. Return a GapAnalysisResult with \
domain_id="{domain_id}".
"""

_GAP_DEFENDER_PROMPT = """\
You are a knowledge graph defender. For each gap finding below, argue that the graph \
already covers it — either under a different term name, as an alias, or that it is \
out of scope for this domain.

Domain: {domain_label}
Domain ID: {domain_id}

Graph terms: {graph_terms}

Gap findings:
{gap_findings}

CRITICAL: You MUST provide a non-empty defender_argument for EVERY gap, even if \
you believe the gap is real. If you cannot argue it is already covered, write: \
"Conceded: this gap is genuine and not covered by any existing graph term." \
Never leave defender_argument blank or null.

Fill in defender_argument for each gap. Return a GapAnalysisResult with \
domain_id="{domain_id}".
"""

_GAP_FINDER_REBUTTAL_PROMPT = """\
You are the original gap analyst reviewing the defender's arguments. For each gap, \
set the verdict field:
- "real_gap" if the defender's argument is wrong or weak
- "ambiguous" if it is unclear
- "not_a_gap" if the defender is correct

Domain: {domain_label}
Domain ID: {domain_id}

Original gaps with defender arguments:
{gap_findings_with_defenses}

Return a GapAnalysisResult with domain_id="{domain_id}" and verdict set for every gap.
"""


def _anchor_summaries(anchors: list[dict]) -> str:
    lines = []
    for a in anchors:
        title = a.get("title", "")
        abstract = a.get("abstract", "")[:300]
        paper_id = a.get("id", "")
        lines.append(f"[{paper_id}] {title}\n  {abstract}")
    return "\n\n".join(lines)


async def _run_gap_loop(
    domain_id: str,
    domain_label: str,
    graph_terms: list[str],
    anchors: list[dict],
    router,
) -> GapAnalysisResult:
    terms_str = ", ".join(graph_terms)
    anchor_str = _anchor_summaries(anchors)

    emit({"event": "gap_finder_start", "domain_id": domain_id, "term_count": len(graph_terms), "anchor_count": len(anchors)})
    finder_msg = [{
        "role": "user",
        "content": _GAP_FINDER_PROMPT.format(
            domain_label=domain_label,
            domain_id=domain_id,
            graph_terms=terms_str,
            anchor_summaries=anchor_str,
        ),
    }]
    finder_result: GapAnalysisResult = await router.call(finder_msg, GapAnalysisResult)
    emit({
        "event": "gap_finder_complete",
        "domain_id": domain_id,
        "gap_count": len(finder_result.gaps),
    })

    if not finder_result.gaps:
        return finder_result

    gap_findings_str = json.dumps(
        [g.model_dump() for g in finder_result.gaps], indent=2
    )
    emit({"event": "gap_defender_start", "domain_id": domain_id, "gap_count": len(finder_result.gaps)})
    defender_msg = [{
        "role": "user",
        "content": _GAP_DEFENDER_PROMPT.format(
            domain_label=domain_label,
            domain_id=domain_id,
            graph_terms=terms_str,
            gap_findings=gap_findings_str,
        ),
    }]
    defender_result: GapAnalysisResult = await router.call(defender_msg, GapAnalysisResult)
    emit({
        "event": "gap_defender_complete",
        "domain_id": domain_id,
    })

    defended_gaps = defender_result.gaps if defender_result.gaps else finder_result.gaps
    defended_str = json.dumps(
        [g.model_dump() for g in defended_gaps], indent=2
    )
    emit({"event": "gap_rebuttal_start", "domain_id": domain_id})
    rebuttal_msg = [{
        "role": "user",
        "content": _GAP_FINDER_REBUTTAL_PROMPT.format(
            domain_label=domain_label,
            domain_id=domain_id,
            gap_findings_with_defenses=defended_str,
        ),
    }]
    rebuttal_result: GapAnalysisResult = await router.call(rebuttal_msg, GapAnalysisResult)
    emit({
        "event": "gap_rebuttal_complete",
        "domain_id": domain_id,
        "real_gaps": sum(1 for g in rebuttal_result.gaps if g.verdict == "real_gap"),
        "not_gaps": sum(1 for g in rebuttal_result.gaps if g.verdict == "not_a_gap"),
        "ambiguous": sum(1 for g in rebuttal_result.gaps if g.verdict == "ambiguous"),
    })
    return rebuttal_result


async def _process_domain(
    domain: dict,
    graph_terms: list[str],
    audit_dir: Path,
    depth: str,
    cfg: Config,
    router,
    http: httpx.AsyncClient,
) -> GapAnalysisResult:
    domain_id = domain["id"]
    domain_label = domain["label"]
    t0 = time.monotonic()

    bib_path = audit_dir / f"bibliography_{domain_id}.json"
    if bib_path.exists() and bib_path.stat().st_size > 0:
        emit({
            "event": "domain_skipped",
            "domain_id": domain_id,
            "reason": "bibliography_sentinel_exists",
        })
        anchors_path = audit_dir / f"anchors_{domain_id}.json"
        anchors = json.loads(anchors_path.read_text()) if anchors_path.exists() else []
        gap_result = await _run_gap_loop(
            domain_id, domain_label, graph_terms, anchors, router
        )
        return gap_result

    emit({
        "event": "domain_start",
        "domain_id": domain_id,
        "label": domain_label,
        "term_count": len(graph_terms),
    })

    emit({"event": "domain_fetch_start", "domain_id": domain_id, "depth": depth})
    anchors_list, bibliography = await asyncio.gather(
        fetch_anchors(domain_label, http, cfg),
        fetch_bibliography(domain_label, depth, cfg, http),
    )
    emit({
        "event": "domain_fetch_done",
        "domain_id": domain_id,
        "elapsed_s": round(time.monotonic() - t0, 2),
    })

    anchors_dicts = [a.model_dump() for a in anchors_list]
    anchors_path = audit_dir / f"anchors_{domain_id}.json"
    anchors_path.write_text(json.dumps(anchors_dicts, indent=2))
    emit({
        "event": "anchors_fetched",
        "domain_id": domain_id,
        "anchor_count": len(anchors_dicts),
        "artifact": str(anchors_path),
    })

    bib_path.write_text(json.dumps([b.model_dump() for b in bibliography], indent=2))
    emit({
        "event": "bibliography_fetched",
        "domain_id": domain_id,
        "entry_count": len(bibliography),
        "artifact": str(bib_path),
    })

    gap_result = await _run_gap_loop(
        domain_id, domain_label, graph_terms, anchors_dicts, router
    )
    emit({
        "event": "domain_complete",
        "domain_id": domain_id,
        "elapsed_s": round(time.monotonic() - t0, 2),
        "real_gaps": sum(1 for g in gap_result.gaps if g.verdict == "real_gap"),
    })
    return gap_result


def _build_gap_markdown(results: list[GapAnalysisResult], domains_by_id: dict) -> str:
    lines = ["# Gap Analysis — Real Gaps\n"]
    for result in results:
        label = domains_by_id.get(result.domain_id, result.domain_id)
        real_gaps = [g for g in result.gaps if g.verdict == "real_gap"]
        if not real_gaps:
            continue
        lines.append(f"## {label} (`{result.domain_id}`)\n")
        for g in real_gaps:
            lines.append(f"- **{g.gap_description}**")
            if g.evidence_anchor_ids:
                lines.append(f"  Evidence: {', '.join(g.evidence_anchor_ids)}")
            if g.finder_rebuttal:
                lines.append(f"  Rebuttal: {g.finder_rebuttal}")
            lines.append("")
    return "\n".join(lines)


def _build_corrections_markdown(
    results: list[GapAnalysisResult], domains_by_id: dict
) -> str:
    lines = ["# Gap Analysis — Corrections (Defender Won)\n"]
    for result in results:
        label = domains_by_id.get(result.domain_id, result.domain_id)
        not_gaps = [g for g in result.gaps if g.verdict == "not_a_gap"]
        if not not_gaps:
            continue
        lines.append(f"## {label} (`{result.domain_id}`)\n")
        for g in not_gaps:
            lines.append(f"- **{g.gap_description}**")
            if g.defender_argument:
                lines.append(f"  Defender: {g.defender_argument}")
            lines.append("")
    return "\n".join(lines)


async def run(state: PipelineState, cfg: Config) -> None:
    state_dir = Path(state["state_dir"])
    audit_dir = state_dir / "audit"
    audit_dir.mkdir(exist_ok=True)

    if stage_is_complete(state_dir, 3):
        emit({"event": "stage_skipped", "stage": 3, "reason": "already_complete"})
        return

    emit({"event": "stage_start", "stage": 3})
    reset_ss_limiter()

    input_path = state["input_path"]
    if not input_path.lower().endswith(".pdf"):
        emit({
            "event": "source_refs_skipped",
            "reason": "not_pdf",
        })

    taxonomy = json.loads((state_dir / "taxonomy.json").read_text())
    domains = taxonomy["domains"]

    graph_data = json.loads((state_dir / "graph.json").read_text())
    nodes = graph_data.get("nodes", [])

    conflicts_path = state_dir / "audit" / "classification_conflicts.json"
    conflicts: list[dict] = []
    if conflicts_path.exists():
        conflicts = json.loads(conflicts_path.read_text())

    conflict_term_names = {c.get("term_name", "") for c in conflicts}

    domains_by_id = {d["id"]: d["label"] for d in domains}
    depth = state["depth"]
    router = make_router("critic", cfg)

    emit({
        "event": "stage3_start",
        "domain_count": len(domains),
        "node_count": len(nodes),
        "conflict_count": len(conflicts),
    })

    gap_results: list[GapAnalysisResult] = []
    async with httpx.AsyncClient(timeout=cfg.timeouts.get("http_async_seconds", 30)) as http:
        # Process domains serially: concurrent SS fetches from multiple domains
        # trigger 429 storms because all coroutines share the global rate limiter
        # and pile up retries in lock-step.  One domain at a time keeps the SS
        # request queue shallow and avoids the retry thundering-herd.
        for domain in domains:
            domain_id = domain["id"]
            graph_terms = [
                n["name"] for n in nodes
                if n.get("domain") == domain_id
            ]
            gap_candidates = [
                t for t in conflict_term_names
                if any(n["name"] == t and n.get("domain") == domain_id for n in nodes)
            ]
            all_terms = list(dict.fromkeys(graph_terms + gap_candidates))
            try:
                result = await _process_domain(
                    domain, all_terms, audit_dir, depth, cfg, router, http
                )
                gap_results.append(result)
            except Exception as exc:
                emit({
                    "event": "domain_failed",
                    "domain_id": domain_id,
                    "error": str(exc)[:200],
                })

    gap_md = _build_gap_markdown(gap_results, domains_by_id)
    corrections_md = _build_corrections_markdown(gap_results, domains_by_id)

    gap_md_path = audit_dir / "gap_analysis.md"
    corrections_md_path = audit_dir / "corrections.md"
    gap_md_path.write_text(gap_md)
    corrections_md_path.write_text(corrections_md)

    total_real = sum(
        sum(1 for g in r.gaps if g.verdict == "real_gap") for r in gap_results
    )
    total_not = sum(
        sum(1 for g in r.gaps if g.verdict == "not_a_gap") for r in gap_results
    )
    total_ambiguous = sum(
        sum(1 for g in r.gaps if g.verdict == "ambiguous") for r in gap_results
    )

    mark_stage_complete(state_dir, 3)
    emit({
        "event": "stage_complete",
        "stage": 3,
        "real_gaps": total_real,
        "not_gaps": total_not,
        "ambiguous": total_ambiguous,
        "artifact_gap_analysis": str(gap_md_path),
        "artifact_corrections": str(corrections_md_path),
    })
