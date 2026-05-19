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

_CONCEPT_PROTOCOL = """\
CONCEPT INTRODUCTION PROTOCOL — apply to EVERY non-trivial concept:
1. INTUITION: One plain-English paragraph using a concrete everyday analogy. \
   No jargon allowed here.
2. SYMBOL TABLE (required before any equation): A Markdown table:
   | Symbol | Type | Meaning |
   |--------|------|---------|
   Every symbol in the equation must have a row. No exceptions.
3. FORMAL DEFINITION: The equation or precise definition.
4. WORKED EXAMPLE: Substitute specific numbers or a minimal code snippet. \
   Walk through the computation step by step, line by line.
5. WHY IT MATTERS: One sentence on the practical payoff.
"""

_CLOSING_SECTION = """\
The FINAL section must be titled "Where to Go Next" with this structure:
- **Open problems**: 2-3 specific, unresolved questions at the research \
  frontier of {domain_label}.
- **Start here**: One codebase, dataset, or benchmark a reader can clone and \
  run today to begin contributing.
- **Essential reading**: Exactly 3 papers from the bibliography that would \
  most accelerate a newcomer's understanding of this domain. Explain in one \
  sentence why each paper matters.
"""

_TOP_DOWN_PROMPT = """\
You are writing a self-contained learning chapter. The reader's goal is to \
go from zero knowledge of {domain_label} to being able to build on and advance \
the field — using only this chapter. They must not need to consult any other \
source.

Work TOP-DOWN: establish the mental model first, then fill in the mechanisms, \
then show the state of the art.

Domain: {domain_label} (id: {domain_id})
Reader profile: {reader_profile}

Knowledge graph nodes:
{graph_nodes}

Gap analysis findings for this domain:
{gap_analysis}

Bibliography (cite ONLY from this list using the citation id field):
{bibliography}

STRUCTURE (follow this section order exactly):
1. "What is {domain_label}?" — 3–5 paragraphs. One-sentence definition. \
   One concrete real-world example. Why it exists and what problem it solves. \
   What a practitioner can do AFTER mastering it that they could not do before.
2. "Prerequisites and Notation" — Any math or CS concepts this domain \
   builds on that a practitioner might not know. Derive them briefly from \
   scratch. Do NOT assume the reader knows them; do NOT link out.
3. "Core Concepts" — The 5–10 load-bearing ideas. Apply the CONCEPT \
   INTRODUCTION PROTOCOL to each.
4. "Key Methods and Algorithms" — The main algorithms with pseudocode or \
   code. Derive from first principles where possible.
5. "State of the Art" — Current best approaches and their trade-offs, grounded \
   in bibliography citations.
6. "Where to Go Next" — see closing section spec below.

{concept_protocol}
{closing_section}

ANTI-HALLUCINATION RULES:
- Cite only from the bibliography using [@citation_id] notation.
- Mark unciteable claims [NEEDS_CITATION].
- Tag speculative claims [INFERRED].
- Do not invent paper titles, authors, or results.

Write a DomainSummary with domain_id="{domain_id}" and \
domain_label="{domain_label}".
"""

_BOTTOM_UP_PROMPT = """\
You are writing a self-contained learning chapter. The reader's goal is to \
go from zero knowledge of {domain_label} to being able to build on and advance \
the field — using only this chapter.

Work BOTTOM-UP: start with the minimal runnable thing, then build upward to \
theory and frontier.

Domain: {domain_label} (id: {domain_id})
Reader profile: {reader_profile}

Knowledge graph nodes:
{graph_nodes}

Gap analysis findings for this domain:
{gap_analysis}

Bibliography (cite ONLY from this list using the citation id field):
{bibliography}

STRUCTURE (follow this section order exactly):
1. "Minimal Working Example" — The simplest possible demonstration of \
   {domain_label} doing one useful thing. Complete, runnable code or concrete \
   step-by-step walkthrough. No prerequisites, no "first install X". \
   A reader who runs this should say "I understand what this is."
2. "Building the Intuition" — Explain WHY the minimal example works. \
   Use analogies. Work backwards from the example to the underlying idea.
3. "Foundations from Scratch" — Derive the theoretical underpinnings of \
   what was just demonstrated. Apply the CONCEPT INTRODUCTION PROTOCOL \
   to every mathematical concept. Never assume prior knowledge.
4. "Worked Examples with Increasing Complexity" — Three examples: trivial \
   → practical → research-grade. Show the full progression explicitly.
5. "Implementation Guide" — How to build a real system. What to watch out for. \
   Common failure modes and how to diagnose them.
6. "Where to Go Next" — see closing section spec below.

{concept_protocol}
{closing_section}

ANTI-HALLUCINATION RULES:
- Cite only from the bibliography using [@citation_id] notation.
- Mark unciteable claims [NEEDS_CITATION].
- Tag speculative claims [INFERRED].
- Do not invent paper titles, authors, or results.

Write a DomainSummary with domain_id="{domain_id}" and \
domain_label="{domain_label}".
"""

_CRITIC_PROMPT = """\
You are an adversarial research critic. The goal of these documents is to take \
a reader from zero knowledge to being able to build on and advance {domain_label}. \
Identify every way they fall short of that goal.

Domain: {domain_label} (id: {domain_id})

Bibliography (only these citations are valid):
{bibliography_ids}

TOP-DOWN DRAFT:
{top_down_narrative}

BOTTOM-UP DRAFT:
{bottom_up_narrative}

Check EVERY item below. For each issue found, quote the exact sentence and \
explain what is wrong:
1. Citations: unsupported claims not tagged [NEEDS_CITATION]; invalid keys
2. Pedagogy — symbol tables: any equation lacking a preceding symbol table
3. Pedagogy — worked examples: any abstract concept without a concrete \
   numerical or code example with specific values
4. Pedagogy — intuition: formal definitions without a plain-English intuition \
   paragraph preceding them
5. Pedagogy — undefined terms: jargon used before it is defined
6. Pedagogy — assumed knowledge: concepts that require prerequisites the \
   reader was not given
7. Pedagogy — missing "What is {domain_label}?" accessible intro section
8. Missing "Where to Go Next" section with open problems, start-here \
   codebase/dataset, and 3 essential papers
9. Logical gaps or contradictions between the two drafts

Return a CritiqueResult with domain_id="{domain_id}".
verdict must be "accept" ONLY if all 9 checks pass. Otherwise "revise".
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

def _reader_profile_strings(profile: dict) -> tuple[str, str]:
    """Return (profile_summary, calibration_note) for prompt injection."""
    level = profile.get("familiarity_level", "practitioner")
    field = profile.get("background_field", "unknown")
    goal = profile.get("learning_goal", "apply")
    math = profile.get("math_comfort", "engage")

    summary = f"familiarity={level}, background={field}, goal={goal}, math={math}"

    notes = {
        "novice": "Write for someone encountering this field for the first time. "
                  "Use analogies liberally. Every term must be defined. Avoid acronyms "
                  "without expansion. Prefer plain language over technical precision.",
        "aware": "The reader has heard of this field but has not worked in it. "
                 "Define specialist terms, skip basic general computing concepts. "
                 "Analogies are welcome but don't over-explain fundamentals.",
        "practitioner": "The reader uses these tools day-to-day. Define specialist "
                        "academic terms but skip obvious practitioner knowledge. "
                        "Prefer concrete examples and code over abstract descriptions.",
        "expert": "The reader is a domain expert. Focus on nuance, open problems, "
                  "and non-obvious connections. Minimal hand-holding on fundamentals.",
    }
    calibration = notes.get(level, notes["practitioner"])

    goal_notes = {
        "explain": " Prioritise clear explanations over exhaustive coverage.",
        "apply": " Emphasise implementation guidance and worked examples.",
        "critique": " Include limitations, failure modes, and counterarguments.",
        "research": " Highlight open questions, reproducibility gaps, and future directions.",
    }
    calibration += goal_notes.get(goal, "")
    return summary, calibration


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
    user_profile: dict | None = None,
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

    reader_profile_str, reader_profile_note = _reader_profile_strings(user_profile or {})

    closing_section = _CLOSING_SECTION.format(domain_label=domain_label)

    # Parallel top-down + bottom-up
    top_down_msg = [{
        "role": "user",
        "content": _TOP_DOWN_PROMPT.format(
            domain_label=domain_label,
            domain_id=domain_id,
            graph_nodes=nodes_str,
            gap_analysis=gap_context,
            bibliography=bib_str,
            reader_profile=reader_profile_str,
            reader_profile_note=reader_profile_note,
            concept_protocol=_CONCEPT_PROTOCOL,
            closing_section=closing_section,
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
            reader_profile=reader_profile_str,
            reader_profile_note=reader_profile_note,
            concept_protocol=_CONCEPT_PROTOCOL,
            closing_section=closing_section,
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

    profile_path = state_dir / "user_profile.json"
    user_profile: dict = {}
    if profile_path.exists():
        user_profile = json.loads(profile_path.read_text())

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
            user_profile=user_profile,
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
