"""Stage 6.5 — Prerequisite Threading Agent.

Uses langgraph.prebuilt.create_react_agent to scan all domain sections for
forward references — concepts used before they are defined — and inserts
inline primers so a reader never hits an unexplained term.
"""

import json
import logging
import re
import time
from pathlib import Path

from langchain_core.tools import tool

from doc_expand.agents.base import make_router
from doc_expand.agents.lc_adapter import build_react_graph, make_lc_model
from doc_expand.config import Config
from doc_expand.state import PipelineState, emit, mark_stage_complete, stage_is_complete

_logger = logging.getLogger("doc_expand.s6_5")

_MAX_TOOL_CALLS = 30

_SYSTEM_PROMPT = """\
You are a prerequisite threading agent. Your mission: ensure that every section \
of this multi-domain learning document is self-contained — a reader must never \
encounter a technical term that hasn't been explained yet.

The document covers {domain_count} domains: {domain_labels}.

TASK:
1. Call list_sections to see the document structure.
2. For each section that seems technically dense, call read_section, then \
   identify terms used before definition within that section.
3. For each blocking forward reference, call insert_primer to add a brief \
   inline explanation.
4. When done (or budget is low), call finish_threading with a summary.

PRIMER RULES:
- Only add a primer if the term is genuinely opaque to someone with no background.
  Don't primer common English words or extremely general CS concepts like "array".
- Keep primers to 2-4 sentences: what the term IS, why it exists, one analogy.
- A primer enables the reader to continue — it is not a full definition.
- Never duplicate a primer for the same term in the same section.

BUDGET: you have at most {max_calls} total tool calls across all sections.
Prioritise the most technical sections and the most blocking forward references.
"""


def _make_tools(sections_dir: Path) -> list:
    """Create tool functions closed over sections_dir."""

    @tool
    def list_sections() -> str:
        """List all available section files and their opening headings.
        Call this first to understand the document structure."""
        files = sorted(sections_dir.glob("section_*.md"))
        if not files:
            return "No section files found."
        lines = []
        for f in files:
            sid = f.stem[len("section_"):]
            text = f.read_text()
            heading = next((ln.lstrip("#").strip() for ln in text.splitlines() if ln.startswith("#")), "(no heading)")
            lines.append(f"{sid}: {heading}")
        return "\n".join(lines)

    @tool
    def read_section(section_id: str) -> str:
        """Read the full text of a named section."""
        path = sections_dir / f"section_{section_id}.md"
        if not path.exists():
            return f"Section not found: {section_id}"
        text = path.read_text()
        if len(text) > 10000:
            text = text[:10000] + f"\n\n... [truncated, {len(text)} chars total]"
        return text

    @tool
    def insert_primer(section_id: str, term: str, primer_text: str) -> str:
        """Insert a short inline primer for a term, placed as a Markdown blockquote
        immediately before the paragraph that first uses the term.
        primer_text should be 2-4 plain-English sentences — the "> **Primer:**" prefix
        is added automatically. Never include it in primer_text."""
        path = sections_dir / f"section_{section_id}.md"
        if not path.exists():
            return f"Section not found: {section_id}"

        existing = path.read_text()
        marker = f"> **Primer:** *{term}*"
        if marker.lower() in existing.lower():
            return f"Primer for '{term}' already exists in section '{section_id}' — skipped."

        # Find the first occurrence of the term (case-insensitive)
        m = re.search(re.escape(term), existing, re.IGNORECASE)
        if m is None:
            return f"Term '{term}' not found in section '{section_id}'."

        # Find the start of the paragraph containing the first use
        para_break = existing.rfind("\n\n", 0, m.start())
        para_start = para_break + 2 if para_break != -1 else 0

        primer_block = f"{marker} — {primer_text.strip()}\n\n"
        new_text = existing[:para_start] + primer_block + existing[para_start:]
        path.write_text(new_text)

        emit({
            "event": "s6_5_primer_inserted",
            "section_id": section_id,
            "term": term,
        })
        return f"Primer for '{term}' inserted in section '{section_id}'."

    @tool
    def finish_threading(primers_inserted: int, sections_processed: list[str] | None = None) -> str:
        """Signal that prerequisite threading is complete.
        Call when all forward references have been addressed or budget is nearly exhausted."""
        emit({
            "event": "s6_5_finish",
            "primers_inserted": primers_inserted,
            "sections_processed": sections_processed or [],
        })
        return "THREADING_COMPLETE"

    return [list_sections, read_section, insert_primer, finish_threading]


# ---------------------------------------------------------------------------
# Stage entry point
# ---------------------------------------------------------------------------

async def run(state: PipelineState, cfg: Config) -> None:
    state_dir = Path(state["state_dir"])
    sections_dir = state_dir / "sections"

    if stage_is_complete(state_dir, "6.5"):
        emit({"event": "stage_skipped", "stage": "6.5", "reason": "already_complete"})
        return

    emit({"event": "stage_start", "stage": "6.5"})

    if "agent" not in cfg.models:
        emit({"event": "s6_5_skipped", "reason": "no_agent_role_in_models"})
        mark_stage_complete(state_dir, "6.5")
        return

    taxonomy = json.loads((state_dir / "taxonomy.json").read_text())
    domains = taxonomy["domains"]
    domain_count = len(domains)
    domain_labels = ", ".join(d["label"] for d in domains)

    router = make_router("agent", cfg)
    lc_model = make_lc_model(router)
    tools = _make_tools(sections_dir)

    system = _SYSTEM_PROMPT.format(
        domain_count=domain_count,
        domain_labels=domain_labels,
        max_calls=_MAX_TOOL_CALLS,
    )

    agent = build_react_graph(
        lc_model,
        tools=tools,
        system_prompt=system,
        recursion_limit=_MAX_TOOL_CALLS * 2 + 2,
    )

    t0 = time.monotonic()
    primers_inserted = 0
    try:
        result = await agent.ainvoke(
            {
                "messages": [
                    (
                        "user",
                        "Please thread prerequisites through the document now. "
                        "Start by listing sections, then identify and fix forward references.",
                    )
                ]
            },
        )
        # Count primer insertions from events (approximate via message scan)
        msgs = result.get("messages", [])
        primers_inserted = sum(
            1 for m in msgs
            if hasattr(m, "content") and "Primer for" in str(m.content) and "inserted" in str(m.content)
        )
        emit({
            "event": "s6_5_agent_complete",
            "reason": "agent_done",
            "elapsed_s": round(time.monotonic() - t0, 2),
        })
    except Exception as exc:
        emit({
            "event": "s6_5_agent_error",
            "error": str(exc)[:200],
            "elapsed_s": round(time.monotonic() - t0, 2),
        })

    mark_stage_complete(state_dir, "6.5")
    emit({
        "event": "stage_complete",
        "stage": "6.5",
        "primers_inserted": primers_inserted,
    })
