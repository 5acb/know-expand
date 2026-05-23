"""Stage 6 — Section Alignment Agent.

Uses a two-pass structured-output approach (no ReAct loops):

Pass 1: LLM reads section text and returns an AlignmentPlan (patches + search
        queries + checklist).
Pass 2: If search_queries non-empty, execute SS searches concurrently, then
        call again with results appended. Returns a refined AlignmentPlan.

Python applies patches deterministically via _apply_patch().

Checklist items the agent fixes:
  1. "What is X?" plain-English intro
  2. Symbol tables before every equation
  3. Worked examples after every major concept
  4. "Where to Go Next" section (open problems + start-here + 3 papers)
  5. [NEEDS_CITATION] markers resolved via Semantic Scholar search
"""

import asyncio
import json
import logging
import re
import time
from pathlib import Path

import httpx

from know_expand.agents.base import make_router
from know_expand.agents.schemas import AlignmentPlan
from know_expand.bibliography import _ss_search, reset_ss_limiter
from know_expand.config import Config
from know_expand.state import PipelineState, atomic_write, emit, mark_stage_complete, stage_is_complete

_logger = logging.getLogger("know_expand.s6_align")

_MAX_SEARCH_QUERIES = 3

_SYSTEM_PROMPT = """\
You are a pedagogical alignment agent for the domain: {domain_label}.
Here is the current section text:
---
{section_text}
---
CHECKLIST — identify what's missing and provide patches to fix it:
1. "What is {domain_label}?" — plain-English intro for a newcomer?
2. Symbol tables — does every equation have a preceding Markdown table defining symbols?
3. Worked examples — does every major concept have a concrete numerical/code example?
4. "Where to Go Next" — open problems, start-here resource, 3 essential papers?
5. Citations — any [NEEDS_CITATION] markers to resolve?

Return:
- patches: list of insertions (position + content) to add. Only ADD, never delete.
- search_queries: SS queries needed to find papers (for #4 and #5). Max {max_queries}.
- checklist: boolean pass/fail for each item AFTER your patches are applied.
  Keys: what_is_section, symbol_tables, worked_examples, where_to_go_next, citations_resolved.

Position options for each patch:
  "end"              — append at document end (use only as last resort)
  "start"            — prepend before all content
  "after_intro"      — after the first ## heading
  "before:<heading>" — before the line containing <heading> (case-insensitive substring)
  "after:<heading>"  — after the section starting with <heading>
"""

_SYSTEM_PROMPT_PASS2 = """\
You are a pedagogical alignment agent for the domain: {domain_label}.
Here is the current section text:
---
{section_text}
---
Below are Semantic Scholar search results for your queries:
---
{search_results}
---
Based on these results, return an updated AlignmentPlan. Include:
- patches: all insertions (including any from the first pass, plus new ones using paper info)
- search_queries: [] (empty — searches already done)
- checklist: boolean pass/fail for each item AFTER all patches are applied.
  Keys: what_is_section, symbol_tables, worked_examples, where_to_go_next, citations_resolved.

Only ADD content, never delete. Use citation keys in the form [@<author>_<year>_<word>].
"""


def _make_citation_id(title: str, year: int, first_author: str) -> str:
    slug = re.sub(r"[^a-z0-9]", "_", first_author.lower())[:15].strip("_")
    title_word = re.sub(r"[^a-z]", "", title.lower().split()[0]) if title else "paper"
    return f"{slug}_{year}_{title_word}"


def _normalize(text: str) -> str:
    """Strip markdown punctuation and collapse whitespace for fuzzy comparison."""
    stripped = re.sub(r"[*_`\[\]()#>]", "", text)
    stripped = re.sub(r"\s+", " ", stripped).strip().lower()
    return stripped


def _token_overlap(a: str, b: str) -> float:
    """Fraction of tokens in `a` that appear in `b` (recall-style)."""
    ta = set(_normalize(a).split())
    tb = set(_normalize(b).split())
    if not ta:
        return 0.0
    return len(ta & tb) / len(ta)


def _fuzzy_find_line(target: str, lines: list[str]) -> int | None:
    """
    Return index of the best matching line for `target`.
    Priority: (1) exact normalized substring, (2) token overlap >= 0.75.
    Returns None if no line meets either threshold.
    """
    norm_target = _normalize(target)
    best_idx: int | None = None
    best_score: float = 0.0
    for i, line in enumerate(lines):
        norm_line = _normalize(line)
        # Exact normalized substring match — always wins
        if norm_target and norm_target in norm_line:
            return i
        # Token overlap fallback
        score = _token_overlap(target, line)
        if score > best_score:
            best_score = score
            best_idx = i
    if best_score >= 0.75:
        return best_idx
    return None


def _apply_patch(path: Path, position: str, content: str) -> str:
    """Apply a single insertion patch to the section file at path.
    Returns a human-readable result string."""
    content = content.strip()
    if not path.exists():
        return f"Section file not found: {path}"
    text = path.read_text()

    if position == "end":
        path.write_text(text.rstrip() + "\n\n" + content + "\n")
        return f"Appended {len(content)} chars at document end."

    elif position == "start":
        path.write_text(content + "\n\n" + text)
        return f"Prepended {len(content)} chars at document start."

    elif position == "after_intro":
        lines = text.split("\n")
        for i, line in enumerate(lines):
            if line.startswith("## ") and i > 0:
                insert_at = i + 1
                while insert_at < len(lines) and lines[insert_at].strip() == "":
                    insert_at += 1
                lines.insert(insert_at, "")
                lines.insert(insert_at, content)
                path.write_text("\n".join(lines))
                return f"Inserted {len(content)} chars after intro heading."
        # Fallback: prepend
        path.write_text(content + "\n\n" + text)
        return "No ## heading found; prepended instead."

    elif position.startswith("before:") or position.startswith("after:"):
        mode = "before" if position.startswith("before:") else "after"
        target = position[len(mode) + 1:]
        lines = text.split("\n")
        match_idx = _fuzzy_find_line(target, lines)
        if match_idx is None:
            emit({
                "event": "patch_apply_failed",
                "path": str(path),
                "position": position,
                "reason": "anchor not found (fuzzy match < 0.75)",
            })
            return f"patch_apply_failed: anchor '{target}' not found — patch dropped."
        if mode == "before":
            lines.insert(match_idx, "")
            lines.insert(match_idx, content)
        else:  # after: find end of that section
            next_heading = next(
                (i for i in range(match_idx + 1, len(lines)) if lines[i].startswith("## ")),
                len(lines),
            )
            insert_at = next_heading
            lines.insert(insert_at, "")
            lines.insert(insert_at, content)
        path.write_text("\n".join(lines))
        return f"Inserted {len(content)} chars {mode} '{target}'."

    else:
        return f"Unknown position '{position}'. No patch applied."


def _format_paper(p: dict) -> str:
    pid = p.get("paperId", "")
    title = p.get("title", "")
    year = p.get("year", "")
    authors = [a.get("name", "") for a in p.get("authors", [])[:3]]
    doi = p.get("externalIds", {}).get("DOI", "")
    return (
        f"paperId={pid} | year={year} | doi={doi}\n"
        f"  title: {title}\n"
        f"  authors: {', '.join(authors)}"
    )


async def _run_searches(
    queries: list[str],
    http: httpx.AsyncClient,
    cfg: Config,
) -> str:
    queries = queries[:_MAX_SEARCH_QUERIES]
    tasks = [_ss_search(q, http, cfg, limit=5) for q in queries]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    blocks = []
    for q, r in zip(queries, results):
        blocks.append(f"Query: {q}")
        if isinstance(r, Exception):
            blocks.append(f"  Error: {r}")
        elif r:
            blocks.extend(f"  {_format_paper(p)}" for p in r[:5])
        else:
            blocks.append("  No results.")
    return "\n\n".join(blocks)


def _persist_bibliography(plan: AlignmentPlan, domain_id: str, audit_dir: Path) -> None:
    """Extract any paper references from patch content and note them.
    (Best-effort: actual citation ids are embedded by the LLM in content.)"""
    # No-op for now; bibliography persistence happens via existing s3 pipeline.
    pass


# ---------------------------------------------------------------------------
# Per-domain align (two-pass structured approach)
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

    section_path = sections_dir / f"section_{domain_id}.md"
    if not section_path.exists():
        emit({"event": "s6_align_domain_error", "domain_id": domain_id, "error": "section file missing"})
        sentinel.touch()
        return

    raw_text = section_path.read_text()
    section_text = raw_text[:8000]
    if len(raw_text) > 8000:
        section_text += f"\n\n... [truncated, {len(raw_text)} chars total]"

    # --- Pass 1 ---
    system1 = _SYSTEM_PROMPT.format(
        domain_label=domain_label,
        section_text=section_text,
        max_queries=_MAX_SEARCH_QUERIES,
    )
    messages: list[dict] = [{"role": "system", "content": system1}]

    try:
        plan: AlignmentPlan = await router.call(messages, AlignmentPlan)
    except Exception as exc:
        emit({
            "event": "s4_5_domain_error",
            "domain_id": domain_id,
            "error": str(exc)[:200],
            "elapsed_s": round(time.monotonic() - t0, 2),
        })
        sentinel.touch()
        return

    # --- Pass 2 (only if search needed) ---
    if plan.search_queries:
        try:
            search_results = await _run_searches(plan.search_queries, http, cfg)
        except Exception as exc:
            search_results = f"Search failed: {exc}"

        system2 = _SYSTEM_PROMPT_PASS2.format(
            domain_label=domain_label,
            section_text=section_text,
            search_results=search_results,
        )
        messages2: list[dict] = [{"role": "system", "content": system2}]
        try:
            plan = await router.call(messages2, AlignmentPlan)
        except Exception as exc:
            emit({
                "event": "s6_align_pass2_error",
                "domain_id": domain_id,
                "error": str(exc)[:200],
            })
            # Fall through with Pass 1 plan

    # --- Apply patches ---
    patches_applied = []
    for patch in plan.patches:
        result = _apply_patch(section_path, patch.position, patch.content)
        patches_applied.append({"position": patch.position, "checklist_item": patch.checklist_item, "result": result})

    emit({
        "event": "s4_5_domain_aligned",
        "domain_id": domain_id,
        "checklist": plan.checklist,
        "patches": [p["checklist_item"] for p in patches_applied],
    })
    emit({
        "event": "s4_5_domain_complete",
        "domain_id": domain_id,
        "reason": "structured_output_done",
        "patches_applied": len(patches_applied),
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

    if stage_is_complete(state_dir, 6):
        emit({"event": "stage_skipped", "stage": 6, "reason": "already_complete"})
        return

    emit({"event": "stage_start", "stage": 6})
    reset_ss_limiter()

    taxonomy = json.loads((state_dir / "taxonomy.json").read_text())
    domains = taxonomy["domains"]

    if "agent" not in cfg.models:
        emit({"event": "s6_align_skipped", "reason": "no_agent_role_in_models"})
        mark_stage_complete(state_dir, 6)
        return

    router = make_router("agent", cfg)

    async with httpx.AsyncClient(timeout=cfg.timeouts.get("http_async_seconds", 30)) as http:
        for domain in domains:
            try:
                await _align_domain(domain, sections_dir, audit_dir, http, cfg, router)
            except Exception as exc:
                emit({
                    "event": "s6_align_domain_failed",
                    "domain_id": domain["id"],
                    "error": str(exc)[:200],
                })

    aligned = sum(1 for d in domains if (sections_dir / f"section_{d['id']}.aligned").exists())
    mark_stage_complete(state_dir, 6)
    emit({
        "event": "stage_complete",
        "stage": 6,
        "domains_aligned": aligned,
        "domains_total": len(domains),
    })
