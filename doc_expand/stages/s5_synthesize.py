"""Stage 5 — Synthesize: cross-domain synthesis from domain summaries."""

import asyncio
import json
import logging
import re
import time
from pathlib import Path

from doc_expand.agents.base import make_router
from doc_expand.agents.schemas import SynthesisCritique, SynthesisDraft
from doc_expand.config import Config
from doc_expand.state import (
    PipelineState,
    emit,
    mark_stage_complete,
    stage_is_complete,
)

_logger = logging.getLogger("doc_expand.s5")

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

_STRUCTURAL_PROMPT = """\
You are writing the capstone chapter of a learning document. A reader has just \
finished studying {domain_count} domains. Your job is to answer ONE question: \
"Now that I understand all these domains, what can I BUILD that I couldn't \
build before?" Every insight must be a buildable project, not an observation.

Focus on STRUCTURAL connections: prerequisite chains that unlock capabilities, \
typed edges between domains that suggest combined implementations.

Knowledge graph edges (with types):
{graph_edges}

Domain summaries (overview only):
{domain_overviews}

Available bibliography (cite these IDs when you support a claim):
{bibliography_keys}

For each insight, describe:
1. The COMBINATION: which 2+ domains must be understood together
2. What becomes BUILDABLE that wasn't possible with any single domain
3. A concrete FIRST STEP: the first thing a reader could implement this week
4. At least one bibliography citation supporting the feasibility

Return a SynthesisDraft with at least {min_insights} insights. The narrative \
field should be a complete Markdown section titled "What You Can Now Build". \
The reading_roadmap should order domains so a newcomer gains the most \
capability the fastest. The boss_nodes should name 3-5 specific systems or \
techniques that emerge only from combining multiple domains.
"""

_SEMANTIC_PROMPT = """\
You are writing the capstone chapter of a learning document. A reader who has \
studied all {domain_count} domains needs to know: where is the unexplored \
territory at the intersection of these fields? What can only someone who \
understands ALL of them see?

Focus on SEMANTIC connections: shared unsolved problems, contradictions between \
domains that create research opportunities, emergent concepts that exist only \
at the boundary.

Domain summaries (full):
{domain_summaries}

Available bibliography (cite these IDs when you support a claim):
{bibliography_keys}

For each insight:
- Name the SPECIFIC GAP or tension at the domain boundary
- Explain why no single-domain expert could see it
- Describe the RESEARCH CONTRIBUTION that would close the gap
- Cite bibliography evidence where it exists

Return a SynthesisDraft with at least {min_insights} insights. The narrative \
should complement the structural analysis. The reading_roadmap should order \
domains by conceptual dependency for a first-time learner. The boss_nodes \
should name 3-5 emerging research directions at the intersections.
"""

_SYNTHESIS_CRITIC_PROMPT = """\
You are an adversarial critic reviewing a synthesis section. Identify weak \
connections, unsupported claims, and missing cross-domain insights.

STRUCTURAL DRAFT:
{structural_narrative}

SEMANTIC DRAFT:
{semantic_narrative}

Domain IDs in scope: {domain_ids}

Check for:
1. Connections that are trivial (just prerequisite edges, not genuine insights)
2. Connections not supported by the provided summaries or graph
3. Cross-domain connections that are clearly missing
4. Contradictions between the two drafts

Return a SynthesisCritique. verdict must be "accept" if both drafts are \
high quality, "revise" otherwise.
"""

_SYNTHESIS_REVISE_PROMPT = """\
You are a research synthesizer revising a cross-domain synthesis section \
based on critic feedback.

STRUCTURAL DRAFT:
{structural_narrative}

SEMANTIC DRAFT:
{semantic_narrative}

CRITIC FEEDBACK:
Trivial connections: {trivial_connections}
Unsupported connections: {unsupported_connections}
Missing cross-domain: {missing_cross_domain}

Produce a revised, unified SynthesisDraft that:
- Combines the best non-trivial insights from both drafts
- Removes or strengthens the trivial/unsupported connections
- Adds the missing cross-domain connections if evidence exists in the summaries
- Has a complete narrative field as a Markdown section

Return a SynthesisDraft.
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _summaries_to_overviews(summaries: list[dict]) -> str:
    lines = []
    for s in summaries:
        domain_id = s.get("domain_id", "")
        label = s.get("domain_label", "")
        overview = s.get("overview", "")
        lines.append(f"**{label}** (`{domain_id}`): {overview}")
    return "\n\n".join(lines)


def _edges_summary(edges: list[dict]) -> str:
    cross_domain_edges = []
    for e in edges:
        from_node = e.get("from", e.get("from_node", ""))
        to_node = e.get("to", e.get("to_node", ""))
        edge_type = e.get("type", "")
        cross_domain_edges.append(f"  {from_node} --[{edge_type}]--> {to_node}")
    # Limit to avoid overwhelming the LLM
    if len(cross_domain_edges) > 200:
        cross_domain_edges = cross_domain_edges[:200] + [f"  ... ({len(cross_domain_edges) - 200} more edges)"]
    return "\n".join(cross_domain_edges)


def _summaries_full(summaries: list[dict]) -> str:
    parts = []
    for s in summaries:
        domain_id = s.get("domain_id", "")
        label = s.get("domain_label", "")
        overview = s.get("overview", "")
        open_q = s.get("key_open_questions", [])
        sections_text = []
        for sec in s.get("sections", []):
            sections_text.append(f"  ### {sec.get('heading', '')}\n  {sec.get('body', '')[:500]}")
        parts.append(
            f"## {label} (`{domain_id}`)\n{overview}\n"
            + "\n".join(sections_text[:3])
            + (f"\nOpen questions: {'; '.join(open_q[:3])}" if open_q else "")
        )
    return "\n\n".join(parts)


def _synthesis_draft_to_markdown(draft: SynthesisDraft) -> str:
    lines = ["# Cross-Domain Synthesis\n"]
    lines.append(draft.narrative)

    # Only add structured sections if the narrative doesn't already contain them
    narrative_lower = draft.narrative.lower()
    has_roadmap = any(h in narrative_lower for h in (
        "## reading roadmap", "## roadmap", "reading roadmap\n",
    ))
    has_insights = any(h in narrative_lower for h in (
        "## insights", "## deep-dive", "## cross-domain insight",
    ))
    has_concepts = any(h in narrative_lower for h in (
        "## key synthesis", "## boss node", "## key concept",
    ))

    if not has_roadmap:
        seen: set[str] = set()
        unique_roadmap = []
        for step in draft.reading_roadmap:
            # Strip any leading "N. " or "N) " the LLM may have added to avoid double-numbering
            clean = re.sub(r'^\d+[\.\)]\s*', '', step.strip())
            key = clean.lower()
            if key not in seen:
                seen.add(key)
                unique_roadmap.append(clean)
        if unique_roadmap:
            lines.append("\n## Reading Roadmap\n")
            for i, step in enumerate(unique_roadmap, 1):
                lines.append(f"{i}. {step}")

    if not has_concepts and draft.boss_nodes:
        lines.append("\n## Key Synthesis Concepts\n")
        for node in draft.boss_nodes:
            lines.append(f"- **{node}**")

    if not has_insights:
        lines.append("\n## Insights\n")
        for insight in draft.insights:
            domains = ", ".join(insight.domains_involved)
            lines.append(f"### {insight.insight}")
            lines.append(f"*Domains: {domains}* | *Confidence: {insight.confidence}*")
            lines.append(f"*Evidence: {insight.evidence}*\n")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Stage entry point
# ---------------------------------------------------------------------------

async def run(state: PipelineState, cfg: Config) -> None:
    state_dir = Path(state["state_dir"])
    sections_dir = state_dir / "sections"
    summaries_dir = state_dir / "summaries"

    sections_dir.mkdir(exist_ok=True)

    if stage_is_complete(state_dir, 5):
        emit({"event": "stage_skipped", "stage": 5, "reason": "already_complete"})
        return

    emit({"event": "stage_start", "stage": 5})

    graph_data = json.loads((state_dir / "graph.json").read_text())
    edges = graph_data.get("edges", [])

    # Load all domain summaries
    summaries: list[dict] = []
    if summaries_dir.exists():
        for summary_file in sorted(summaries_dir.glob("summary_*.json")):
            summaries.append(json.loads(summary_file.read_text()))

    if not summaries:
        emit({
            "event": "s5_complete",
            "stage": 5,
            "summaries_loaded": 0,
            "skipped": True,
            "reason": "no_summaries",
        })
        synthesis_path = sections_dir / "section_synthesis.md"
        synthesis_path.write_text("# Cross-Domain Synthesis\n\n(No domain summaries available.)\n")
        mark_stage_complete(state_dir, 5)
        return

    domain_ids = [s.get("domain_id", "") for s in summaries]
    depth = state["depth"]
    router = make_router("researcher", cfg)

    # Load bibliography for citation grounding in synthesis
    bibliography_keys = ""
    bib_path = state_dir / "bibliography.json"
    if bib_path.exists():
        bib_entries = json.loads(bib_path.read_text())
        key_lines = [
            f"  [{e['id']}] {e.get('title', '')} ({(e.get('issued', {}).get('date-parts') or [['']])[0][0]})"
            for e in bib_entries[:60]  # cap to avoid token bloat
        ]
        bibliography_keys = "\n".join(key_lines)

    min_insights = max(len(summaries), 4)  # at least one per domain, minimum 4

    overviews_str = _summaries_to_overviews(summaries)
    edges_str = _edges_summary(edges)
    full_summaries_str = _summaries_full(summaries)

    emit({"event": "s5_structural_start", "domain_count": len(summaries)})
    emit({"event": "s5_semantic_start", "domain_count": len(summaries)})

    domain_count = len(summaries)

    structural_msg = [{
        "role": "user",
        "content": _STRUCTURAL_PROMPT.format(
            graph_edges=edges_str,
            domain_overviews=overviews_str,
            bibliography_keys=bibliography_keys,
            min_insights=min_insights,
            domain_count=domain_count,
        ),
    }]
    semantic_msg = [{
        "role": "user",
        "content": _SEMANTIC_PROMPT.format(
            domain_summaries=full_summaries_str,
            bibliography_keys=bibliography_keys,
            min_insights=min_insights,
            domain_count=domain_count,
        ),
    }]

    structural_draft, semantic_draft = await asyncio.gather(
        router.call(structural_msg, SynthesisDraft),
        router.call(semantic_msg, SynthesisDraft),
    )

    rounds = cfg.adversarial_rounds.get(depth, 1)
    current_structural = structural_draft
    current_semantic = semantic_draft
    final_draft: SynthesisDraft | None = None

    for rnd in range(1, rounds + 1):
        emit({"event": "s5_critique_round", "round": rnd, "total_rounds": rounds})

        critic_msg = [{
            "role": "user",
            "content": _SYNTHESIS_CRITIC_PROMPT.format(
                structural_narrative=current_structural.narrative,
                semantic_narrative=current_semantic.narrative,
                domain_ids=json.dumps(domain_ids),
            ),
        }]
        critique: SynthesisCritique = await router.call(critic_msg, SynthesisCritique)

        if critique.verdict == "accept":
            if critique.revised_narrative:
                # Build a combined draft using the revised narrative
                final_draft = SynthesisDraft(
                    insights=current_structural.insights + current_semantic.insights,
                    reading_roadmap=current_structural.reading_roadmap,
                    boss_nodes=list(dict.fromkeys(
                        current_structural.boss_nodes + current_semantic.boss_nodes
                    ))[:5],
                    narrative=critique.revised_narrative,
                )
            break

        revise_msg = [{
            "role": "user",
            "content": _SYNTHESIS_REVISE_PROMPT.format(
                structural_narrative=current_structural.narrative,
                semantic_narrative=current_semantic.narrative,
                trivial_connections=json.dumps(critique.trivial_connections),
                unsupported_connections=json.dumps(critique.unsupported_connections),
                missing_cross_domain=json.dumps(critique.missing_cross_domain),
            ),
        }]
        revised: SynthesisDraft = await router.call(revise_msg, SynthesisDraft)
        current_structural = revised
        final_draft = revised

    if final_draft is None:
        # Combine both drafts without revision
        combined_insights = current_structural.insights + current_semantic.insights
        combined_roadmap = current_structural.reading_roadmap or current_semantic.reading_roadmap
        combined_boss = list(dict.fromkeys(
            current_structural.boss_nodes + current_semantic.boss_nodes
        ))[:5]
        combined_narrative = (
            "## Structural Connections\n\n"
            + current_structural.narrative
            + "\n\n## Semantic Connections\n\n"
            + current_semantic.narrative
        )
        final_draft = SynthesisDraft(
            insights=combined_insights,
            reading_roadmap=combined_roadmap,
            boss_nodes=combined_boss,
            narrative=combined_narrative,
        )

    synthesis_path = sections_dir / "section_synthesis.md"
    synthesis_path.write_text(_synthesis_draft_to_markdown(final_draft))

    mark_stage_complete(state_dir, 5)
    emit({
        "event": "s5_complete",
        "stage": 5,
        "summaries_loaded": len(summaries),
        "insights_count": len(final_draft.insights),
        "artifact": str(synthesis_path),
    })
