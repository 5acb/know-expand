"""Stage 4 — Research: per-domain deep dives with three-persona multi-expert panel."""

import asyncio
import json
import logging
import re
import time
from pathlib import Path

import httpx

from doc_expand.agents.base import make_router
from doc_expand.agents.schemas import (
    CitationRecord,
    CritiqueResult,
    DomainSummary,
    PersonaOutput,
    ReconcilerOutput,
)
from doc_expand.bibliography import _ss_search, _get_ss_limiter, _to_citation_record, _make_id
from doc_expand.config import Config
from doc_expand.state import (
    PipelineState,
    atomic_write,
    emit,
    mark_stage_complete,
    stage_is_complete,
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

_MATH_PROTOCOL = """\
MATHEMATICS PROTOCOL — apply to EVERY non-trivial concept that involves an equation:
1. INTUITION: One plain-English paragraph using a concrete everyday analogy. No jargon.
2. SYMBOL TABLE: A Markdown table with columns | Symbol | Type | Meaning |
   Every symbol appearing in the equation must have a row. No exceptions.
3. FORMAL DEFINITION: The equation in LaTeX (use $...$ for inline, $$...$$ for display).
4. WORKED EXAMPLE: Substitute specific real numbers and walk through the computation
   step by step. No variable-only examples.
5. WHY IT MATTERS: One sentence on the practical payoff.
"""

_ANTI_HALLUCINATION = """\
ANTI-HALLUCINATION RULES:
- Cite only from the bibliography using [@citation_id] notation.
- Mark unciteable claims [NEEDS_CITATION].
- Tag speculative claims [INFERRED].
- Do not invent paper titles, authors, or results.
- For tool/library descriptions, prefer the fetched knowledge sources over parametric memory.
- You may reference fetched source URLs inline as (source: URL) for non-academic claims.
"""

_PERSONA_DRIFT_GUARD = """\
ROLE DISCIPLINE — you are ONLY a {persona_name}. Stay strictly within this role:
- Do NOT write content outside your designated scope (see your role description above).
- Do NOT correct grammar, style, or prose in other sections.
- Do NOT produce a synthesis or summary — that is the Reconciler's job.
- If you find yourself writing about something outside your scope, stop and return \
  to your role.
"""

# ---------------------------------------------------------------------------
# Shared context block — same for all three personas in a given domain.
# Marked with cache_control so Anthropic's prompt cache activates on resume /
# retry runs and for the second+third persona within a single-domain retry.
# ---------------------------------------------------------------------------

_SHARED_CONTEXT_BLOCK = """\
Domain: {domain_label} (id: {domain_id})
Reader profile: {reader_profile}
{reader_profile_note}

Knowledge graph nodes:
{graph_nodes}

Gap analysis findings for this domain:
{gap_analysis}

Bibliography (cite ONLY from this list using the citation id field):
{bibliography}

Fetched Knowledge Sources (retrieved documentation — use for technical accuracy on tools and concepts):
{knowledge_sources}

{math_protocol}
{concept_protocol}
{anti_hallucination}
"""

# ---------------------------------------------------------------------------
# Persona task blocks — persona-specific role + structure + output instruction.
# These are the second content block in each persona message.
# ---------------------------------------------------------------------------

_THEORETICIAN_TASK = """\
You are a THEORETICIAN writing one persona's contribution to a self-contained \
learning chapter on {domain_label}. The reader's goal is to go from zero \
knowledge to being able to build on and advance the field.

Your role: provide mathematical foundations, formal definitions, historical \
context, and first-principles derivations. Work TOP-DOWN: establish the mental \
model and axiomatic basis first, then derive mechanisms from theory.

STRUCTURE (follow this section order exactly):
1. "What is {domain_label}?" — 3–5 paragraphs. One-sentence definition. \
   One concrete real-world example. Why it exists and what problem it solves.
2. "Prerequisites and Notation" — Any math or CS concepts this domain \
   builds on. Derive them briefly from scratch. Do NOT assume prior knowledge; \
   do NOT link out.
3. "Mathematical Foundations" — The core formalisms. Apply the MATHEMATICS \
   PROTOCOL to every equation and non-trivial concept.
4. "Historical Development" — Key milestones that shaped the theory. \
   Cite from bibliography.
5. "Theoretical Limits and Guarantees" — What the theory proves is possible \
   or impossible. Apply MATHEMATICS PROTOCOL to any bound or theorem.

{closing_section_note}

{drift_guard}

Write a PersonaOutput with persona="theoretician" and domain_id="{domain_id}". \
Your sections list must cover the 5 sections above. \
key_claims should list 3–7 central theoretical claims you make. \
citations_used should list every citation id you reference.
"""

_ENGINEER_TASK = """\
You are an ENGINEER writing one persona's contribution to a self-contained \
learning chapter on {domain_label}. The reader's goal is to go from zero \
knowledge to being able to build on and advance the field.

Your role: cover algorithms, architectures, implementation patterns, and system \
design. Work FROM MECHANISM TO THEORY: show how things are built, then explain why.

STRUCTURE (follow this section order exactly):
1. "Key Algorithms" — The main algorithms with pseudocode. Derive from first \
   principles where possible. Apply MATHEMATICS PROTOCOL to any non-trivial \
   expression in the algorithm.
2. "System Architecture" — Canonical architectures. Diagrams in ASCII or \
   Markdown tables. Explain every component's purpose.
3. "Implementation Guide" — How to build a real system. What to watch out for. \
   Concrete code sketches where they clarify understanding.
4. "Engineering Trade-offs" — Latency vs. throughput, memory vs. accuracy, etc. \
   Be specific: quote numbers from bibliography where available.
5. "Common Failure Modes" — What breaks, why, and how to diagnose it. \
   Include concrete debugging checklists.

{drift_guard}

Write a PersonaOutput with persona="engineer" and domain_id="{domain_id}". \
Your sections list must cover the 5 sections above. \
key_claims should list 3–7 central engineering claims you make. \
citations_used should list every citation id you reference.
"""

_PRACTITIONER_TASK = """\
You are a PRACTITIONER writing one persona's contribution to a self-contained \
learning chapter on {domain_label}. The reader's goal is to go from zero \
knowledge to being able to build on and advance the field.

Your role: cover real-world trade-offs, failure modes, benchmarks, gotchas, \
and where to go next. Work FROM USAGE TO MECHANISM: start with what a \
practitioner does, then explain why it works.

STRUCTURE (follow this section order exactly):
1. "Minimal Working Example" — The simplest possible demonstration of \
   {domain_label} doing one useful thing. Complete, runnable code or concrete \
   step-by-step walkthrough. A reader who runs this should say "I understand \
   what this is."
2. "Real-World Trade-offs" — When to use this approach vs. alternatives. \
   Concrete decision criteria. Cite benchmarks from bibliography.
3. "Benchmarks and Empirical Results" — State-of-the-art numbers, datasets \
   used, reproducibility notes. Apply MATHEMATICS PROTOCOL to any metric definition.
4. "Gotchas and Production Pitfalls" — What surprises practitioners who \
   deploy this in the real world. Be specific and actionable.
5. "Where to Go Next" — \
   - **Open problems**: 2-3 specific, unresolved questions at the research \
     frontier of {domain_label}. \
   - **Start here**: One codebase, dataset, or benchmark a reader can clone \
     and run today to begin contributing. \
   - **Essential reading**: Exactly 3 papers from the bibliography that would \
     most accelerate a newcomer's understanding of this domain. Explain in one \
     sentence why each paper matters.

{drift_guard}

Write a PersonaOutput with persona="practitioner" and domain_id="{domain_id}". \
Your sections list must cover the 5 sections above. \
key_claims should list 3–7 central practitioner insights you make. \
citations_used should list every citation id you reference.
"""

# ---------------------------------------------------------------------------
# Reconciler prompt
# ---------------------------------------------------------------------------

_RECONCILER_PROMPT = """\
You are a RECONCILER synthesizing three expert perspectives into a single \
definitive learning chapter on {domain_label}.

You have received outputs from three persona agents:
- THEORETICIAN: mathematical foundations, formal definitions, historical context
- ENGINEER: algorithms, architectures, implementation patterns
- PRACTITIONER: real-world trade-offs, benchmarks, failure modes, where to go next

Your task: merge their best content into one coherent, non-redundant chapter \
that takes a reader from zero knowledge to being able to build on and advance \
{domain_label} — using only this chapter.

Domain: {domain_label} (id: {domain_id})
Reader profile: {reader_profile}

Bibliography (cite ONLY from this list using the citation id field):
{bibliography}

Fetched Knowledge Sources (retrieved documentation — use for technical accuracy on tools and concepts):
{knowledge_sources}

--- THEORETICIAN OUTPUT ---
{theoretician_sections}

--- ENGINEER OUTPUT ---
{engineer_sections}

--- PRACTITIONER OUTPUT ---
{practitioner_sections}

RECONCILIATION RULES:
1. Deduplicate: when two personas cover the same concept, keep the deeper \
   treatment and discard the shallower one.
2. Smooth transitions: the chapter must read as one continuous narrative, \
   not as three stitched sections.
3. Section order must be:
   a. "What is {domain_label}?" (from Theoretician)
   b. "Prerequisites and Notation" (from Theoretician)
   c. "Mathematical Foundations" (from Theoretician, annotated with Engineer \
      context where relevant)
   d. "Key Algorithms and Architecture" (from Engineer)
   e. "Implementation Guide and Failure Modes" (from Engineer + Practitioner)
   f. "Benchmarks and Trade-offs" (from Practitioner)
   g. "Historical Development" (from Theoretician)
   h. "Theoretical Limits and Guarantees" (from Theoretician)
   i. "Where to Go Next" (from Practitioner — must include open problems, \
      start-here resource, and 3 essential papers)
4. The narrative field must be complete Markdown — headers, tables, code \
   blocks, LaTeX equations. No placeholders.

{math_protocol}
{concept_protocol}

MATHEMATICS PROTOCOL reminder: every equation in the merged chapter must have \
its INTUITION → SYMBOL TABLE → FORMAL DEFINITION → WORKED EXAMPLE → WHY IT \
MATTERS structure intact. Do not strip symbol tables or worked examples during \
merging.

{anti_hallucination}

Return a ReconcilerOutput with:
- narrative: the complete merged Markdown chapter
- summary: a DomainSummary with domain_id="{domain_id}" and \
  domain_label="{domain_label}" populated from the merged content
"""

# ---------------------------------------------------------------------------
# Critic and revise prompts
# ---------------------------------------------------------------------------

_CRITIC_PROMPT = """\
You are an adversarial research critic. The goal of this document is to take \
a reader from zero knowledge to being able to build on and advance {domain_label}. \
Identify every way it falls short of that goal.

Domain: {domain_label} (id: {domain_id})

Bibliography (only these citation IDs are valid):
{bibliography_ids}

Gap analysis (real gaps that should be addressed):
{gap_analysis}

RECONCILED CHAPTER DRAFT:
{narrative}

Check EVERY item below. For each issue found, quote the exact sentence and \
explain what is wrong:
1. Citations: unsupported claims not tagged [NEEDS_CITATION]; invalid citation keys
2. Pedagogy — symbol tables: any equation lacking a preceding symbol table
3. Pedagogy — worked examples: any abstract concept without a concrete \
   numerical or code example with specific values (not variable-only)
4. Pedagogy — intuition: formal definitions without a plain-English intuition \
   paragraph preceding them
5. Pedagogy — undefined terms: jargon used before it is defined
6. Pedagogy — assumed knowledge: concepts that require prerequisites the \
   reader was not given
7. Pedagogy — missing "What is {domain_label}?" accessible intro section
8. Missing or incomplete "Where to Go Next" section (must have open problems, \
   start-here codebase/dataset, and exactly 3 essential papers with explanations)
9. Gap analysis coverage: real gaps identified in gap_analysis that are not \
   addressed in the chapter
10. Logical gaps, contradictions, or unsupported leaps

Return a CritiqueResult with domain_id="{domain_id}".
verdict must be "accept" ONLY if all 10 checks pass. Otherwise "revise".
"""

_REVISE_PROMPT = """\
You are a technical research writer revising a reconciled chapter based on \
critic feedback.

Domain: {domain_label} (id: {domain_id})

Bibliography (cite ONLY from this list):
{bibliography}

CURRENT NARRATIVE:
{narrative}

CRITIC FEEDBACK:
Issues: {issues}
Suggested additions: {suggested_additions}

Produce a revised ReconcilerOutput that:
- Addresses each issue raised by the critic
- Incorporates the suggested additions where supported by the bibliography
- Tags all unsupported claims [NEEDS_CITATION] or [INFERRED]
- Uses only citation IDs from the provided bibliography
- Keeps all symbol tables and worked examples intact
- Retains the "Where to Go Next" section structure

Return a ReconcilerOutput with domain_id="{domain_id}" and \
domain_label="{domain_label}".
"""


# ---------------------------------------------------------------------------
# Bibliography expansion helpers (A-3)
# ---------------------------------------------------------------------------

_NEEDS_CITATION_RE = re.compile(r"\[NEEDS_CITATION\]")
# Capture the sentence containing [NEEDS_CITATION] — up to ~300 chars around it
_SENTENCE_CONTEXT_RE = re.compile(
    r"(?:^|(?<=[.!?])\s+)([^.!?\n]{0,200}\[NEEDS_CITATION\][^.!?\n]{0,200})",
    re.MULTILINE,
)


def _title_relevance(query: str, title: str) -> float:
    """Word-overlap relevance between query and paper title (0.0–1.0)."""
    stopwords = {"the", "a", "an", "of", "in", "for", "and", "to", "is"}
    q_words = set(query.lower().split()) - stopwords
    t_words = set(title.lower().split())
    return len(q_words & t_words) / max(len(q_words), 1)


async def _expand_bibliography(
    domain_id: str,
    personas: list[PersonaOutput],
    bibliography: list[dict],
    bib_path: Path,
    cfg: Config,
) -> tuple[list[dict], list[PersonaOutput]]:
    """Scan persona outputs for citation gaps and [NEEDS_CITATION] markers.

    For each missing citation ID and each [NEEDS_CITATION] marker:
    - Search Semantic Scholar
    - If a confident match is found, add it to the bibliography
    - Replace [NEEDS_CITATION] markers in persona section bodies

    Returns (updated_bibliography, updated_personas).
    Never raises — all SS errors are swallowed and logged.
    """
    existing_ids: set[str] = {b.get("id", "") for b in bibliography}
    seen_ids: set[str] = set(existing_ids)
    new_papers: list[dict] = []

    # Collect all citation IDs referenced by personas that are NOT in the bibliography
    all_cited_ids: set[str] = set()
    for p in personas:
        all_cited_ids.update(p.citations_used)
        for section in p.sections:
            for c in section.citations:
                all_cited_ids.add(c.citation_id)

    missing_ids = all_cited_ids - existing_ids

    # Collect [NEEDS_CITATION] contexts from all persona section bodies
    needs_citation_contexts: list[tuple[int, int, str]] = []  # (persona_idx, section_idx, sentence)
    for pi, persona in enumerate(personas):
        for si, section in enumerate(persona.sections):
            for m in _SENTENCE_CONTEXT_RE.finditer(section.body):
                needs_citation_contexts.append((pi, si, m.group(1).strip()))

    if not missing_ids and not needs_citation_contexts:
        return bibliography, personas

    emit({
        "event": "s4_bibliography_expansion_start",
        "domain_id": domain_id,
        "missing_citation_ids": len(missing_ids),
        "needs_citation_markers": len(needs_citation_contexts),
    })

    _get_ss_limiter(cfg)

    async def _search_one(query: str, http: httpx.AsyncClient) -> list[dict]:
        try:
            return await _ss_search(query, http, cfg, limit=5)
        except Exception as exc:
            _logger.warning("s4 bib expansion: SS search failed for %r: %s", query[:60], exc)
            return []

    try:
        async with httpx.AsyncClient(timeout=15.0) as http:
            # Search for missing citation IDs
            missing_id_tasks = {mid: asyncio.create_task(_search_one(mid, http)) for mid in missing_ids}
            # Search for [NEEDS_CITATION] contexts
            nc_tasks = [
                asyncio.create_task(_search_one(ctx, http))
                for (_, _, ctx) in needs_citation_contexts
            ]
            await asyncio.gather(
                *missing_id_tasks.values(), *nc_tasks, return_exceptions=True
            )
    except Exception as exc:
        _logger.warning("s4 bibliography expansion: SS unavailable, skipping (%s)", exc)
        emit({"event": "s4_bibliography_expansion_skipped", "domain_id": domain_id, "reason": str(exc)[:120]})
        return bibliography, personas

    # Process missing-ID results
    for mid, task in missing_id_tasks.items():
        if task.exception():
            continue
        papers = task.result()
        for paper in papers:
            title = paper.get("title") or ""
            if _title_relevance(mid, title) >= 0.4:
                rec = _to_citation_record(paper, "frontier", seen_ids)
                new_papers.append(rec.model_dump())
                break  # take the first confident match per missing ID

    # Process [NEEDS_CITATION] results — build replacement map per (persona, section)
    replacement_map: dict[tuple[int, int], list[tuple[str, str]]] = {}
    nc_resolved = 0

    for idx, ((pi, si, sentence), task) in enumerate(zip(needs_citation_contexts, nc_tasks)):
        if task.exception():
            continue
        papers = task.result()
        best_paper = None
        best_score = 0.0
        for paper in papers:
            title = paper.get("title") or ""
            score = _title_relevance(sentence, title)
            if score > best_score:
                best_score = score
                best_paper = paper
        if best_paper and best_score >= 0.4:
            rec = _to_citation_record(best_paper, "frontier", seen_ids)
            new_papers.append(rec.model_dump())
            new_sentence = sentence.replace("[NEEDS_CITATION]", f"[@{rec.id}]", 1)
            replacement_map.setdefault((pi, si), []).append((sentence, new_sentence))
            nc_resolved += 1

    # Apply body replacements to persona sections
    updated_personas: list[PersonaOutput] = []
    for pi, persona in enumerate(personas):
        updated_sections = []
        for si, section in enumerate(persona.sections):
            body = section.body
            for (old_s, new_s) in replacement_map.get((pi, si), []):
                body = body.replace(old_s, new_s, 1)
            updated_sections.append(section.model_copy(update={"body": body}))
        updated_personas.append(persona.model_copy(update={"sections": updated_sections}))

    updated_bibliography = bibliography + new_papers
    emit({
        "event": "s4_bibliography_expanded",
        "domain_id": domain_id,
        "new_papers": len(new_papers),
        "needs_citation_resolved": nc_resolved,
    })

    if new_papers and bib_path.exists():
        try:
            bib_path.write_text(json.dumps(updated_bibliography, indent=2))
        except Exception as exc:
            _logger.warning("s4 bib expansion: could not write updated bib for %s: %s", domain_id, exc)

    return updated_bibliography, updated_personas


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _narrative_to_markdown(output: ReconcilerOutput) -> str:
    """Return the full Markdown content of a reconciler output."""
    return output.narrative


def _compact_narrative(narrative: str, domain_label: str, max_chars: int = 8000) -> str:
    """Trim narrative to max_chars for critic context, preserving structure.

    The critic only needs the intro and the most recently revised content; the
    reviser always receives the full text so it can edit any part of the chapter.
    """
    if len(narrative) <= max_chars:
        return narrative
    # Keep first 3000 chars (intro + what-is section) and last 5000 chars (most recent content)
    head = narrative[:3000]
    tail = narrative[-5000:]
    truncated = len(narrative) - max_chars
    return head + f"\n\n[... {truncated} chars truncated ...]\n\n" + tail


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


def _persona_sections_to_text(persona_output: PersonaOutput) -> str:
    """Render persona output sections as readable Markdown for the reconciler prompt."""
    lines = [f"**Key claims:** {', '.join(persona_output.key_claims)}\n"]
    for section in persona_output.sections:
        lines.append(f"### {section.heading}\n")
        lines.append(section.body)
        if section.citations:
            for c in section.citations:
                lines.append(f"[@{c.citation_id}] {c.quote_or_claim}")
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Core per-domain research
# ---------------------------------------------------------------------------

def _reader_profile_strings(profile: dict) -> tuple[str, str]:
    """Return (profile_summary, calibration_note) for prompt injection."""
    level = profile.get("familiarity_level", "practitioner")
    field = profile.get("background_field", "unknown")
    goal = profile.get("learning_goal", "apply")
    math = profile.get("math_comfort", "engage")
    unknown_concepts: list[str] = profile.get("unknown_concepts", [])
    known_concepts: list[str] = profile.get("known_concepts", [])

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

    # Thread specific calibration misconceptions — the reader got these wrong or
    # said they didn't know them; they need first-principles treatment regardless
    # of how central they are to this domain.
    if unknown_concepts:
        concepts_str = ", ".join(f'"{c}"' for c in unknown_concepts[:8])
        calibration += (
            f" PRIORITY: The reader specifically does not understand {concepts_str}. "
            "Trace each of these from absolute first principles before using them."
        )
    if known_concepts:
        known_str = ", ".join(f'"{c}"' for c in known_concepts[:8])
        calibration += f" The reader already understands {known_str} — do not re-explain these."

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
    critic_router,
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

    # Load fetched knowledge sources (written by s4_audit)
    from doc_expand.sources import format_sources  # noqa: PLC0415
    sources_cache = audit_dir / "sources" / f"sources_{domain_id}.json"
    term_sources: dict = {}
    if sources_cache.exists():
        try:
            term_sources = json.loads(sources_cache.read_text())
        except Exception as exc:
            _logger.warning("s5 sources load failed for %r: %s", domain_id, exc)

    # Depth-aware caps — keeps persona prompts under ~20k chars at all depths.
    # Defaults: survey≈1.5k / standard≈3.2k / deep≈15k chars of knowledge sources.
    ctx = cfg.research_context
    knowledge_sources_str = format_sources(
        term_sources,
        max_terms=ctx.sources_max_terms.get(depth, 8),
        max_sources_per_term=ctx.sources_max_per_term.get(depth, 1),
        max_chars_per_source=ctx.sources_max_chars.get(depth, 400),
    )

    gap_context = _extract_gap_context(gap_md, domain_id, domain_label)
    bib_str = _bib_summary(bibliography)
    bib_ids = [b.get("id", "") for b in bibliography]

    domain_nodes = [n for n in graph_nodes if n.get("domain") == domain_id]
    # Top-N by tier+centrality: core first, then supporting, then incidental.
    _tier_rank = {"core": 0, "supporting": 1, "incidental": 2}
    domain_nodes_sorted = sorted(
        domain_nodes,
        key=lambda n: (_tier_rank.get(n.get("centrality", "incidental"), 2), n.get("name", "")),
    )[:ctx.nodes_top_n]
    nodes_str = json.dumps(
        [{"name": n.get("name"), "tier": n.get("tier"), "centrality": n.get("centrality")}
         for n in domain_nodes_sorted],
        indent=2,
    )

    reader_profile_str, reader_profile_note = _reader_profile_strings(user_profile or {})

    closing_section_note = (
        "NOTE: The Practitioner agent will write the 'Where to Go Next' section. "
        "You do NOT need to include it."
    )

    # Shared context block — identical across all three personas for this domain.
    # cache_control tells Anthropic to cache this prefix; the 90% cache-read discount
    # activates on resume/retry runs and when parallel personas complete and the
    # reconciler reuses the same prefix. Requires ≥4096 tokens (deep depth reliably
    # exceeds this; verify via cache_read_input_tokens in llm_call_done events).
    _shared_block = {
        "type": "text",
        "text": _SHARED_CONTEXT_BLOCK.format(
            domain_label=domain_label,
            domain_id=domain_id,
            graph_nodes=nodes_str,
            gap_analysis=gap_context,
            bibliography=bib_str,
            knowledge_sources=knowledge_sources_str,
            reader_profile=reader_profile_str,
            reader_profile_note=reader_profile_note,
            math_protocol=_MATH_PROTOCOL,
            concept_protocol=_CONCEPT_PROTOCOL,
            anti_hallucination=_ANTI_HALLUCINATION,
        ),
        "cache_control": {"type": "ephemeral"},
    }

    def _persona_msg(task_text: str) -> list[dict]:
        return [{"role": "user", "content": [_shared_block, {"type": "text", "text": task_text}]}]

    theoretician_msg = _persona_msg(_THEORETICIAN_TASK.format(
        domain_label=domain_label,
        domain_id=domain_id,
        closing_section_note=closing_section_note,
        drift_guard=_PERSONA_DRIFT_GUARD.format(persona_name="THEORETICIAN"),
    ))
    engineer_msg = _persona_msg(_ENGINEER_TASK.format(
        domain_label=domain_label,
        domain_id=domain_id,
        drift_guard=_PERSONA_DRIFT_GUARD.format(persona_name="ENGINEER"),
    ))
    practitioner_msg = _persona_msg(_PRACTITIONER_TASK.format(
        domain_label=domain_label,
        domain_id=domain_id,
        drift_guard=_PERSONA_DRIFT_GUARD.format(persona_name="PRACTITIONER"),
    ))

    # Run all three personas in parallel
    theoretician_result, engineer_result, practitioner_result = await asyncio.gather(
        router.call(theoretician_msg, PersonaOutput),
        router.call(engineer_msg, PersonaOutput),
        router.call(practitioner_msg, PersonaOutput),
    )

    emit({"event": "s4_personas_done", "domain_id": domain_id,
          "theoretician_sections": len(theoretician_result.sections),
          "engineer_sections": len(engineer_result.sections),
          "practitioner_sections": len(practitioner_result.sections)})

    # Bibliography expansion (A-3): fill citation gaps before reconciler sees the text
    _expanded_bib, _expanded_personas = await _expand_bibliography(
        domain_id=domain_id,
        personas=[theoretician_result, engineer_result, practitioner_result],
        bibliography=bibliography,
        bib_path=bib_path,
        cfg=cfg,
    )
    bibliography = _expanded_bib
    theoretician_result, engineer_result, practitioner_result = _expanded_personas
    # Refresh derived bibliography strings after expansion
    bib_str = _bib_summary(bibliography)
    bib_ids = [b.get("id", "") for b in bibliography]

    # Reconciler: merge all three persona outputs
    reconciler_msg = [{"role": "user", "content": _RECONCILER_PROMPT.format(
        domain_label=domain_label,
        domain_id=domain_id,
        reader_profile=reader_profile_str,
        bibliography=bib_str,
        knowledge_sources=knowledge_sources_str,
        theoretician_sections=_persona_sections_to_text(theoretician_result),
        engineer_sections=_persona_sections_to_text(engineer_result),
        practitioner_sections=_persona_sections_to_text(practitioner_result),
        math_protocol=_MATH_PROTOCOL,
        concept_protocol=_CONCEPT_PROTOCOL,
        anti_hallucination=_ANTI_HALLUCINATION,
    )}]
    reconciled: ReconcilerOutput = await router.call(reconciler_msg, ReconcilerOutput)
    emit({"event": "s4_reconciler_done", "domain_id": domain_id})

    # Adversarial critic loop with convergence termination
    rounds = cfg.adversarial_rounds.get(depth, 1)
    current_reconciled = reconciled
    final_summary: DomainSummary | None = None
    prev_issues: set[str] | None = None
    termination_reason = "max_rounds"

    for rnd in range(1, rounds + 1):
        emit({
            "event": "s4_domain_critique_round",
            "domain_id": domain_id,
            "round": rnd,
            "total_rounds": rounds,
        })

        critic_msg = [{"role": "user", "content": _CRITIC_PROMPT.format(
            domain_label=domain_label,
            domain_id=domain_id,
            bibliography_ids=json.dumps(bib_ids),
            gap_analysis=gap_context,
            narrative=_compact_narrative(current_reconciled.narrative, domain_label),
        )}]
        critique: CritiqueResult = await critic_router.call(critic_msg, CritiqueResult)

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

        current_issues = set(critique.issues)

        # (a) Critic accepts
        if critique.verdict == "accept":
            termination_reason = "critic_accepted"
            if critique.revised_summary is not None:
                final_summary = critique.revised_summary
            break

        # (b) Gap list is empty
        if not current_issues:
            termination_reason = "no_issues"
            break

        # (c) Critic has stalled — same issues rephrased, no new ground covered.
        # Exact set equality rarely fires (model rephrases); use token-level Jaccard
        # so "same structural concerns, different wording" is caught at >0.85 similarity.
        if prev_issues is not None:
            all_prev = set(" ".join(prev_issues).lower().split())
            all_cur = set(" ".join(current_issues).lower().split())
            union = all_prev | all_cur
            jaccard = len(all_prev & all_cur) / len(union) if union else 1.0
            if jaccard > 0.85:
                termination_reason = "critic_stalled"
                emit({
                    "event": "s4_critic_stalled",
                    "domain_id": domain_id,
                    "round": rnd,
                    "issue_count": len(current_issues),
                    "jaccard": round(jaccard, 3),
                })
                break

        prev_issues = current_issues

        # Revise the reconciled output
        revise_msg = [{"role": "user", "content": _REVISE_PROMPT.format(
            domain_label=domain_label,
            domain_id=domain_id,
            bibliography=bib_str,
            narrative=current_reconciled.narrative,
            issues=json.dumps(critique.issues),
            suggested_additions=json.dumps(critique.suggested_additions),
        )}]
        revised: ReconcilerOutput = await router.call(revise_msg, ReconcilerOutput)
        current_reconciled = revised
        final_summary = revised.summary

    emit({
        "event": "s4_critic_loop_done",
        "domain_id": domain_id,
        "termination_reason": termination_reason,
        "rounds_run": rnd,
    })

    if final_summary is None:
        final_summary = current_reconciled.summary

    # Write outputs (atomic to prevent partial writes on crash)
    section_path = sections_dir / f"section_{domain_id}.md"
    atomic_write(section_path, current_reconciled.narrative)

    summary_path = summaries_dir / f"summary_{domain_id}.json"
    atomic_write(summary_path, final_summary.model_dump_json(indent=2))

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

    if stage_is_complete(state_dir, 5):
        emit({"event": "stage_skipped", "stage": 5, "reason": "already_complete"})
        return

    emit({"event": "stage_start", "stage": 5})

    taxonomy = json.loads((state_dir / "taxonomy.json").read_text())
    domains = taxonomy["domains"]

    graph_data = json.loads((state_dir / "graph.json").read_text())
    nodes = graph_data.get("nodes", [])

    gap_md_path = audit_dir / "gap_analysis.md"
    gap_md = gap_md_path.read_text() if gap_md_path.exists() else ""

    depth = state["depth"]
    router = make_router("researcher", cfg)
    critic_router = make_router("researcher", cfg, thinking_budget=cfg.critic_thinking.budget_tokens)

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
            critic_router=critic_router,
            user_profile=user_profile,
        )
        for domain in domains
    ]

    raw_results = await asyncio.gather(*coros, return_exceptions=True)

    failed = 0
    succeeded = 0
    failed_ids: list[str] = []
    for domain, result in zip(domains, raw_results):
        if isinstance(result, Exception):
            failed += 1
            failed_ids.append(domain["id"])
            emit({
                "event": "s4_domain_failed",
                "domain_id": domain["id"],
                "error": str(result)[:200],
            })
        else:
            succeeded += 1

    if failed_ids:
        _logger.warning("s5: %d domain(s) failed to produce content: %s", len(failed_ids), ", ".join(failed_ids))

    mark_stage_complete(state_dir, 5)
    emit({
        "event": "s5_research_complete",
        "stage": 5,
        "domains_succeeded": succeeded,
        "domains_failed": failed,
        "domains_failed_ids": failed_ids,
    })
