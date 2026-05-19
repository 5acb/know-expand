"""Stage 4 — Research: per-domain deep dives with top-down / bottom-up agents."""

import asyncio
import json
import logging
import time
from pathlib import Path

from doc_expand.agents.base import make_router
from doc_expand.agents.schemas import CritiqueResult, DomainSummary
from doc_expand.config import Config
from doc_expand.state import (
    PipelineState,
    emit,
    mark_stage_complete,
    sentinel_exists,
    stage_is_complete,
    write_sentinel,
)

_logger = logging.getLogger("doc_expand.s4")

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

_TOP_DOWN_PROMPT = """\
You are a technical research writer. Using the provided domain knowledge graph \
nodes, gap analysis findings, and bibliography, write a comprehensive technical \
overview of this domain.

Work TOP-DOWN: start with the field overview and high-level paradigms, then \
cover the key mechanisms, and finish with implementation details.

Domain: {domain_label} (id: {domain_id})

Knowledge graph nodes:
{graph_nodes}

Gap analysis findings for this domain:
{gap_analysis}

Bibliography (cite ONLY from this list using the citation id field):
{bibliography}

ANTI-HALLUCINATION RULES:
- Cite only from the provided bibliography JSON using [@citation_id] notation.
- If a claim needs a citation not in the list, write the claim and mark it \
[NEEDS_CITATION].
- Tag speculative or inferred claims with [INFERRED].
- Do not invent paper titles, authors, or results.

Write a DomainSummary with domain_id="{domain_id}" and \
domain_label="{domain_label}". Include at least 4 sections covering: \
overview, core mechanisms, key methods/algorithms, and open questions.
"""

_BOTTOM_UP_PROMPT = """\
You are a technical research writer. Using the provided domain knowledge graph \
nodes, gap analysis findings, and bibliography, write a comprehensive technical \
analysis of this domain.

Work BOTTOM-UP: start from concrete implementation details and worked examples, \
then build up to the theoretical foundations and first principles.

Domain: {domain_label} (id: {domain_id})

Knowledge graph nodes:
{graph_nodes}

Gap analysis findings for this domain:
{gap_analysis}

Bibliography (cite ONLY from this list using the citation id field):
{bibliography}

ANTI-HALLUCINATION RULES:
- Cite only from the provided bibliography JSON using [@citation_id] notation.
- If a claim needs a citation not in the list, write the claim and mark it \
[NEEDS_CITATION].
- Tag speculative or inferred claims with [INFERRED].
- Do not invent paper titles, authors, or results.

Write a DomainSummary with domain_id="{domain_id}" and \
domain_label="{domain_label}". Include at least 4 sections covering: \
concrete implementations, algorithms/pseudocode, theoretical underpinnings, \
and first-principles derivations.
"""

_CRITIC_PROMPT = """\
You are an adversarial research critic. Review the two drafts below and the \
provided bibliography. Identify flaws in both drafts.

Domain: {domain_label} (id: {domain_id})

Bibliography (only these citations are valid):
{bibliography_ids}

TOP-DOWN DRAFT:
{top_down_narrative}

BOTTOM-UP DRAFT:
{bottom_up_narrative}

Check for:
1. Unsupported claims not in the bibliography and not tagged [NEEDS_CITATION]
2. Missing implementation details that should be present
3. Citation keys that do not appear in the provided bibliography
4. [INFERRED] tags that appear to be citable from the bibliography
5. Logical gaps or contradictions between the two drafts

Return a CritiqueResult with domain_id="{domain_id}".
verdict must be "accept" if both drafts are high quality, "revise" otherwise.
"""

_REVISE_PROMPT = """\
You are a technical research writer revising your draft based on critic feedback.

Domain: {domain_label} (id: {domain_id})

Bibliography (cite ONLY from this list):
{bibliography}

ORIGINAL TOP-DOWN DRAFT:
{top_down_narrative}

ORIGINAL BOTTOM-UP DRAFT:
{bottom_up_narrative}

CRITIC FEEDBACK:
Issues: {issues}
Suggested additions: {suggested_additions}

Produce a revised, reconciled DomainSummary that:
- Combines the best elements of both drafts
- Addresses each issue raised by the critic
- Incorporates the suggested additions where supported by the bibliography
- Tags all unsupported claims [NEEDS_CITATION] or [INFERRED]
- Uses only citation IDs from the provided bibliography

Return a DomainSummary with domain_id="{domain_id}" and \
domain_label="{domain_label}".
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _domain_summary_to_markdown(summary: DomainSummary) -> str:
    lines = [f"# {summary.domain_label}\n", f"{summary.overview}\n"]
    for section in summary.sections:
        lines.append(f"## {section.heading}\n")
        lines.append(section.body)
        if section.citations:
            lines.append("\n**Citations:**")
            for c in section.citations:
                lines.append(f"- [@{c.citation_id}] {c.quote_or_claim}")
        lines.append("")
    if summary.key_open_questions:
        lines.append("## Open Questions\n")
        for q in summary.key_open_questions:
            lines.append(f"- {q}")
    return "\n".join(lines)


def _extract_gap_context(gap_md: str, domain_id: str, domain_label: str) -> str:
    """Extract gap findings relevant to this domain from gap_analysis.md."""
    lines = gap_md.splitlines()
    relevant: list[str] = []
    in_domain = False
    for line in lines:
        if f"`{domain_id}`" in line or domain_label in line:
            in_domain = True
        elif line.startswith("## ") and in_domain:
            break
        if in_domain:
            relevant.append(line)
    return "\n".join(relevant) if relevant else "(no gap findings for this domain)"


def _bib_summary(bibliography: list[dict]) -> str:
    """Compact representation of bibliography for LLM prompts."""
    entries = []
    for b in bibliography:
        authors = b.get("author", [])
        first_author = authors[0].get("family", "Unknown") if authors else "Unknown"
        year_parts = b.get("issued", {}).get("date-parts", [[0]])
        year = year_parts[0][0] if year_parts and year_parts[0] else 0
        title = b.get("title", "")[:100]
        bib_id = b.get("id", "")
        entries.append(f'  {{"id": "{bib_id}", "author": "{first_author}", "year": {year}, "title": "{title}"}}')
    return "[\n" + ",\n".join(entries) + "\n]"


# ---------------------------------------------------------------------------
# Core per-domain research
# ---------------------------------------------------------------------------

async def _research_domain(
    domain: dict,
    audit_dir: Path,
    sections_dir: Path,
    summaries_dir: Path,
    gap_md: str,
    graph_nodes: list[dict],
    depth: str,
    cfg: Config,
    router,
) -> DomainSummary:
    domain_id = domain["id"]
    domain_label = domain["label"]
    t0 = time.monotonic()

    sentinel = sections_dir / f"section_{domain_id}.md.done"
    if sentinel.exists():
        emit({
            "event": "s4_domain_start",
            "domain_id": domain_id,
            "skipped": True,
            "reason": "sentinel_exists",
        })
        summary_path = summaries_dir / f"summary_{domain_id}.json"
        if summary_path.exists():
            return DomainSummary.model_validate(json.loads(summary_path.read_text()))
        # Reconstruct minimal summary if only sentinel exists
        return DomainSummary(
            domain_id=domain_id,
            domain_label=domain_label,
            overview="(loaded from sentinel)",
            sections=[],
        )

    emit({"event": "s4_domain_start", "domain_id": domain_id, "label": domain_label})

    # Load bibliography
    bib_path = audit_dir / f"bibliography_{domain_id}.json"
    bibliography: list[dict] = []
    if bib_path.exists():
        bibliography = json.loads(bib_path.read_text())

    gap_context = _extract_gap_context(gap_md, domain_id, domain_label)
    bib_str = _bib_summary(bibliography)
    bib_ids = [b.get("id", "") for b in bibliography]

    domain_nodes = [n for n in graph_nodes if n.get("domain") == domain_id]
    nodes_str = json.dumps(
        [{"name": n.get("name"), "tier": n.get("tier"), "centrality": n.get("centrality")} for n in domain_nodes],
        indent=2,
    )

    # Parallel top-down + bottom-up
    top_down_msg = [{
        "role": "user",
        "content": _TOP_DOWN_PROMPT.format(
            domain_label=domain_label,
            domain_id=domain_id,
            graph_nodes=nodes_str,
            gap_analysis=gap_context,
            bibliography=bib_str,
        ),
    }]
    bottom_up_msg = [{
        "role": "user",
        "content": _BOTTOM_UP_PROMPT.format(
            domain_label=domain_label,
            domain_id=domain_id,
            graph_nodes=nodes_str,
            gap_analysis=gap_context,
            bibliography=bib_str,
        ),
    }]

    top_down_result, bottom_up_result = await asyncio.gather(
        router.call(top_down_msg, DomainSummary),
        router.call(bottom_up_msg, DomainSummary),
    )

    emit({"event": "s4_domain_top_down_done", "domain_id": domain_id})
    emit({"event": "s4_domain_bottom_up_done", "domain_id": domain_id})

    # Adversarial critic loop
    rounds = cfg.adversarial_rounds.get(depth, 1)
    current_top_down = top_down_result
    current_bottom_up = bottom_up_result

    def _summary_narrative(s: DomainSummary) -> str:
        parts = [s.overview]
        for sec in s.sections:
            parts.append(f"### {sec.heading}\n{sec.body}")
        return "\n\n".join(parts)

    final_summary: DomainSummary | None = None

    for rnd in range(1, rounds + 1):
        emit({"event": "s4_domain_critique_round", "domain_id": domain_id, "round": rnd, "total_rounds": rounds})

        critic_msg = [{
            "role": "user",
            "content": _CRITIC_PROMPT.format(
                domain_label=domain_label,
                domain_id=domain_id,
                bibliography_ids=json.dumps(bib_ids),
                top_down_narrative=_summary_narrative(current_top_down),
                bottom_up_narrative=_summary_narrative(current_bottom_up),
            ),
        }]
        critique: CritiqueResult = await router.call(critic_msg, CritiqueResult)

        # Write critique artifact
        critique_path = audit_dir / f"critique_s4_{domain_id}_round{rnd}.md"
        critique_lines = [
            f"# Critique — {domain_label} Round {rnd}\n",
            f"**Verdict:** {critique.verdict}\n",
            "## Issues\n",
        ]
        for issue in critique.issues:
            critique_lines.append(f"- {issue}")
        critique_lines.append("\n## Suggested Additions\n")
        for sugg in critique.suggested_additions:
            critique_lines.append(f"- {sugg}")
        critique_path.write_text("\n".join(critique_lines))

        if critique.verdict == "accept":
            if critique.revised_summary is not None:
                final_summary = critique.revised_summary
            break

        # Revise
        revise_msg = [{
            "role": "user",
            "content": _REVISE_PROMPT.format(
                domain_label=domain_label,
                domain_id=domain_id,
                bibliography=bib_str,
                top_down_narrative=_summary_narrative(current_top_down),
                bottom_up_narrative=_summary_narrative(current_bottom_up),
                issues=json.dumps(critique.issues),
                suggested_additions=json.dumps(critique.suggested_additions),
            ),
        }]
        revised: DomainSummary = await router.call(revise_msg, DomainSummary)
        # Feed revised as new top-down for next round; keep bottom-up stable
        current_top_down = revised
        final_summary = revised

    if final_summary is None:
        final_summary = current_top_down

    # Write outputs
    section_path = sections_dir / f"section_{domain_id}.md"
    section_path.write_text(_domain_summary_to_markdown(final_summary))

    summary_path = summaries_dir / f"summary_{domain_id}.json"
    summary_path.write_text(final_summary.model_dump_json(indent=2))

    # Write sentinel
    sentinel.touch()

    emit({
        "event": "s4_domain_complete",
        "domain_id": domain_id,
        "elapsed_s": round(time.monotonic() - t0, 2),
        "section_file": str(section_path),
    })
    return final_summary


# ---------------------------------------------------------------------------
# Stage entry point
# ---------------------------------------------------------------------------

async def run(state: PipelineState, cfg: Config) -> None:
    state_dir = Path(state["state_dir"])
    audit_dir = state_dir / "audit"
    sections_dir = state_dir / "sections"
    summaries_dir = state_dir / "summaries"

    sections_dir.mkdir(exist_ok=True)
    summaries_dir.mkdir(exist_ok=True)
    audit_dir.mkdir(exist_ok=True)

    if stage_is_complete(state_dir, 4):
        emit({"event": "stage_skipped", "stage": 4, "reason": "already_complete"})
        return

    emit({"event": "stage_start", "stage": 4})

    taxonomy = json.loads((state_dir / "taxonomy.json").read_text())
    domains = taxonomy["domains"]

    graph_data = json.loads((state_dir / "graph.json").read_text())
    nodes = graph_data.get("nodes", [])

    gap_md_path = audit_dir / "gap_analysis.md"
    gap_md = gap_md_path.read_text() if gap_md_path.exists() else ""

    depth = state["depth"]
    router = make_router("researcher", cfg)

    coros = [
        _research_domain(
            domain=domain,
            audit_dir=audit_dir,
            sections_dir=sections_dir,
            summaries_dir=summaries_dir,
            gap_md=gap_md,
            graph_nodes=nodes,
            depth=depth,
            cfg=cfg,
            router=router,
        )
        for domain in domains
    ]

    raw_results = await asyncio.gather(*coros, return_exceptions=True)

    failed = 0
    succeeded = 0
    for domain, result in zip(domains, raw_results):
        if isinstance(result, Exception):
            failed += 1
            emit({
                "event": "s4_domain_failed",
                "domain_id": domain["id"],
                "error": str(result)[:200],
            })
        else:
            succeeded += 1

    mark_stage_complete(state_dir, 4)
    emit({
        "event": "s4_complete",
        "stage": 4,
        "domains_succeeded": succeeded,
        "domains_failed": failed,
    })
