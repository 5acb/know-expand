"""Stage 4.5 — Section Alignment Agent.

Uses langgraph.prebuilt.create_react_agent with a QuotaAwareRouter-backed
chat model to align each domain section against the "zero-to-building"
pedagogical checklist.

Checklist items the agent fixes:
  1. "What is X?" plain-English intro
  2. Symbol tables before every equation
  3. Worked examples after every major concept
  4. "Where to Go Next" section (open problems + start-here + 3 papers)
  5. [NEEDS_CITATION] markers resolved via Semantic Scholar search
"""

import json
import logging
import time
from pathlib import Path

import httpx
from langchain_core.tools import tool

from doc_expand.agents.base import make_router
from doc_expand.agents.lc_adapter import build_react_graph, make_lc_model
from doc_expand.bibliography import _ss_search, reset_ss_limiter
from doc_expand.config import Config
from doc_expand.state import PipelineState, emit, mark_stage_complete, stage_is_complete

_logger = logging.getLogger("doc_expand.s4_5")

_MAX_TOOL_CALLS_PER_DOMAIN = 12

_SYSTEM_PROMPT = """\
You are a pedagogical alignment agent. Your mission is to ensure that a domain \
section enables a reader to go from ZERO knowledge to being able to BUILD on the field.

You are working on the domain: {domain_label} (id: {domain_id})

CHECKLIST — verify each item and fix what's missing:
1. "What is {domain_label}?" — Does the section open with a plain-English intro \
   accessible to a newcomer? If not, write one and insert it at the start.
2. Symbol tables — Does every equation have a preceding Markdown table defining \
   every symbol? If not, add the missing tables immediately before the equation.
3. Worked examples — Does every major concept have a concrete numerical or code \
   example with specific values? If not, add one after the concept.
4. "Where to Go Next" — Does the section end with open problems, a start-here \
   resource, and 3 essential papers? If not, search for papers and add this section.
5. Citations — Are there [NEEDS_CITATION] markers that can be resolved via search? \
   Search for the claim, add the paper to bibliography, and update the text.

POSITION GUIDE for insert_content:
- "What is X?" intro missing → use position='after_intro' to insert after the first ## heading
- Symbol table before equation → use position='before:<equation heading or surrounding heading>'
- Worked example after concept → use position='after:<concept heading>'
- "Where to Go Next" section → use position='end' only if no suitable heading exists, otherwise 'after:<last concept heading>'
- Do NOT use position='start' for the "What is X?" intro; use 'after_intro' instead.

CONSTRAINTS:
- Only ADD content — never delete or rewrite existing text.
- Be surgical: add the minimum necessary to satisfy each checklist item.
- Budget: you have at most {max_calls} tool calls total.
- Call finish_domain when done (pass or all possible fixes applied).
- Start by calling read_section to see the current state.
"""


def _make_citation_id(title: str, year: int, first_author: str) -> str:
    import re
    slug = re.sub(r"[^a-z0-9]", "_", first_author.lower())[:15].strip("_")
    title_word = re.sub(r"[^a-z]", "", title.lower().split()[0]) if title else "paper"
    return f"{slug}_{year}_{title_word}"


def _make_tools(
    domain_id: str,
    sections_dir: Path,
    audit_dir: Path,
    http: httpx.AsyncClient,
    cfg: Config,
) -> list:
    """Create tool functions closed over per-domain context."""

    @tool
    async def read_section(section_domain_id: str) -> str:
        """Read the full current text of a domain section.
        Always call this first before inspecting a domain."""
        path = sections_dir / f"section_{section_domain_id}.md"
        if not path.exists():
            return f"Section file not found: {section_domain_id}"
        text = path.read_text()
        if len(text) > 8000:
            text = text[:8000] + f"\n\n... [truncated, {len(text)} chars total]"
        return text

    @tool
    async def search_papers(query: str, limit: int = 5) -> str:
        """Search Semantic Scholar for academic papers. Use this to find
        citations for claims marked [NEEDS_CITATION] or to find papers
        for the 'Where to Go Next' essential reading list."""
        limit = min(int(limit), 10)
        try:
            papers = await _ss_search(query, http, cfg, limit=limit)
            if not papers:
                return "No results found."
            lines = []
            for p in papers[:limit]:
                pid = p.get("paperId", "")
                title = p.get("title", "")
                year = p.get("year", "")
                authors = [a.get("name", "") for a in p.get("authors", [])[:3]]
                doi = p.get("externalIds", {}).get("DOI", "")
                lines.append(
                    f"paperId={pid} | year={year} | doi={doi}\n"
                    f"  title: {title}\n"
                    f"  authors: {', '.join(authors)}"
                )
            return "\n\n".join(lines)
        except Exception as exc:
            return f"Search failed: {exc}"

    @tool
    async def insert_content(content: str, position: str) -> str:
        """Insert Markdown content into this domain's section.
        position options:
          'end'              — append at document end (use only as last resort)
          'start'            — prepend before all content
          'after_intro'      — after the first section heading (## What is ...)
          'before:<heading>' — before the line containing <heading> (case-insensitive substring)
          'after:<heading>'  — after the section starting with <heading> (finds end of that section)
        """
        content = content.strip()
        path = sections_dir / f"section_{domain_id}.md"
        if not path.exists():
            return f"Section not found: {domain_id}"
        text = path.read_text()

        if position == "end":
            path.write_text(text.rstrip() + "\n\n" + content + "\n")
            return f"Appended {len(content)} chars at document end."

        elif position == "start":
            path.write_text(content + "\n\n" + text)
            return f"Prepended {len(content)} chars at document start."

        elif position == "after_intro":
            # Find the first ## heading after the opening, insert after it
            lines = text.split("\n")
            for i, line in enumerate(lines):
                if line.startswith("## ") and i > 0:
                    insert_at = i + 1
                    # Skip any immediate blank lines after the heading
                    while insert_at < len(lines) and lines[insert_at].strip() == "":
                        insert_at += 1
                    lines.insert(insert_at, "")
                    lines.insert(insert_at, content)
                    path.write_text("\n".join(lines))
                    return f"Inserted {len(content)} chars after intro heading."
            # Fallback: prepend
            path.write_text(content + "\n\n" + text)
            return f"No ## heading found; prepended instead."

        elif position.startswith("before:") or position.startswith("after:"):
            mode = "before" if position.startswith("before:") else "after"
            target = position[len(mode) + 1:]
            lines = text.split("\n")
            # Case-insensitive substring search across all lines
            match_idx = next(
                (i for i, line in enumerate(lines) if target.lower() in line.lower()),
                None
            )
            if match_idx is None:
                # Try to find the nearest heading as a fallback
                # Insert before the LAST top-level section as a best effort
                heading_indices = [i for i, l in enumerate(lines) if l.startswith("## ")]
                if heading_indices:
                    insert_at = heading_indices[-1]
                    lines.insert(insert_at, "")
                    lines.insert(insert_at, content)
                    path.write_text("\n".join(lines))
                    return f"Target '{target}' not found; inserted before last ## section as fallback."
                else:
                    path.write_text(text.rstrip() + "\n\n" + content + "\n")
                    return f"Target '{target}' not found and no ## headings; appended to end."
            if mode == "before":
                lines.insert(match_idx, "")
                lines.insert(match_idx, content)
            else:  # after: find end of that section
                # Insert at the blank line before the next ## heading, or at end
                next_heading = next(
                    (i for i in range(match_idx + 1, len(lines)) if lines[i].startswith("## ")),
                    len(lines)
                )
                insert_at = next_heading
                lines.insert(insert_at, "")
                lines.insert(insert_at, content)
            path.write_text("\n".join(lines))
            return f"Inserted {len(content)} chars {mode} '{target}'."

        else:
            return f"Unknown position '{position}'. Use: end, start, after_intro, before:<heading>, after:<heading>"

    @tool
    async def add_to_bibliography(
        paper_id: str,
        title: str,
        authors: list[str],
        year: int,
        doi: str = "",
        url: str = "",
    ) -> str:
        """Persist a paper from search_papers results into the domain bibliography
        so it can be cited as [@<id>]. Returns the citation key."""
        first_author = authors[0] if authors else "unknown"
        cit_id = _make_citation_id(title, year, first_author)

        author_objs = [{"family": a} for a in authors]
        entry = {
            "id": cit_id,
            "type": "article-journal",
            "title": title,
            "author": author_objs,
            "issued": {"date-parts": [[year]]},
            "DOI": doi,
            "URL": url,
            "paperId": paper_id,
        }

        bib_path = audit_dir / f"bibliography_{domain_id}.json"
        existing_bib: list[dict] = []
        if bib_path.exists():
            existing_bib = json.loads(bib_path.read_text())
        existing_ids = {e.get("id") for e in existing_bib} | {e.get("paperId") for e in existing_bib}
        if cit_id not in existing_ids and paper_id not in existing_ids:
            existing_bib.append(entry)
            bib_path.write_text(json.dumps(existing_bib, indent=2))
        return f"Citation key: {cit_id} — use [@{cit_id}] to cite this paper."

    @tool
    def finish_domain(
        checklist_what_is: bool = False,
        checklist_symbol_tables: bool = False,
        checklist_worked_examples: bool = False,
        checklist_where_to_go_next: bool = False,
        checklist_citations_resolved: bool = False,
        patches_applied: list[str] | None = None,
    ) -> str:
        """Signal that this domain section is now aligned.
        Call this when the section passes the checklist or when
        you have made all the improvements you can."""
        checklist = {
            "what_is_section": checklist_what_is,
            "symbol_tables": checklist_symbol_tables,
            "worked_examples": checklist_worked_examples,
            "where_to_go_next": checklist_where_to_go_next,
            "citations_resolved": checklist_citations_resolved,
        }
        emit({
            "event": "s4_5_domain_aligned",
            "domain_id": domain_id,
            "checklist": checklist,
            "patches": patches_applied or [],
        })
        return "ALIGNMENT_COMPLETE"

    return [read_section, search_papers, insert_content, add_to_bibliography, finish_domain]


# ---------------------------------------------------------------------------
# Per-domain agent loop
# ---------------------------------------------------------------------------

async def _align_domain(
    domain: dict,
    sections_dir: Path,
    audit_dir: Path,
    http: httpx.AsyncClient,
    cfg: Config,
    router,
) -> None:
    domain_id = domain["id"]
    domain_label = domain["label"]
    t0 = time.monotonic()

    sentinel = sections_dir / f"section_{domain_id}.aligned"
    if sentinel.exists():
        emit({"event": "s4_5_domain_skipped", "domain_id": domain_id, "reason": "already_aligned"})
        return

    emit({"event": "s4_5_domain_start", "domain_id": domain_id, "label": domain_label})

    system = _SYSTEM_PROMPT.format(
        domain_label=domain_label,
        domain_id=domain_id,
        max_calls=_MAX_TOOL_CALLS_PER_DOMAIN,
    )

    tools = _make_tools(domain_id, sections_dir, audit_dir, http, cfg)
    lc_model = make_lc_model(router)
    agent = build_react_graph(
        lc_model,
        tools=tools,
        system_prompt=system,
        recursion_limit=_MAX_TOOL_CALLS_PER_DOMAIN * 2 + 2,
    )

    try:
        result = await agent.ainvoke(
            {
                "messages": [
                    (
                        "user",
                        f"Please align the '{domain_label}' section now. "
                        "Start by reading it, then work through the checklist.",
                    )
                ]
            },
        )
        final_messages = result.get("messages", [])
        tool_calls_made = sum(
            1 for m in final_messages
            if hasattr(m, "tool_calls") and m.tool_calls
        )
        emit({
            "event": "s4_5_domain_complete",
            "domain_id": domain_id,
            "reason": "agent_done",
            "tool_calls": tool_calls_made,
            "elapsed_s": round(time.monotonic() - t0, 2),
        })
    except Exception as exc:
        emit({
            "event": "s4_5_domain_error",
            "domain_id": domain_id,
            "error": str(exc)[:200],
            "elapsed_s": round(time.monotonic() - t0, 2),
        })

    sentinel.touch()


# ---------------------------------------------------------------------------
# Stage entry point
# ---------------------------------------------------------------------------

async def run(state: PipelineState, cfg: Config) -> None:
    state_dir = Path(state["state_dir"])
    sections_dir = state_dir / "sections"
    audit_dir = state_dir / "audit"

    if stage_is_complete(state_dir, "4.5"):
        emit({"event": "stage_skipped", "stage": "4.5", "reason": "already_complete"})
        return

    emit({"event": "stage_start", "stage": "4.5"})
    reset_ss_limiter()

    taxonomy = json.loads((state_dir / "taxonomy.json").read_text())
    domains = taxonomy["domains"]

    if "agent" not in cfg.models:
        emit({"event": "s4_5_skipped", "reason": "no_agent_role_in_models"})
        mark_stage_complete(state_dir, "4.5")
        return

    router = make_router("agent", cfg)

    async with httpx.AsyncClient(timeout=cfg.timeouts.get("http_async_seconds", 30)) as http:
        for domain in domains:
            try:
                await _align_domain(domain, sections_dir, audit_dir, http, cfg, router)
            except Exception as exc:
                emit({
                    "event": "s4_5_domain_failed",
                    "domain_id": domain["id"],
                    "error": str(exc)[:200],
                })

    aligned = sum(1 for d in domains if (sections_dir / f"section_{d['id']}.aligned").exists())
    mark_stage_complete(state_dir, "4.5")
    emit({
        "event": "stage_complete",
        "stage": "4.5",
        "domains_aligned": aligned,
        "domains_total": len(domains),
    })
