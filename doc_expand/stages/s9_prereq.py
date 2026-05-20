"""Stage 9 — Prerequisite Threading Agent.

Uses a single structured-output call per section (no ReAct loops).
The LLM reads the section text and returns a PrimerPlan listing primers to
insert. Python applies the primers deterministically via _apply_patch() from
s6_align, using "before:<phrase>" position matching.
"""

import json
import logging
import re
import time
from pathlib import Path

from doc_expand.agents.base import make_router
from doc_expand.agents.schemas import PrimerPlan
from doc_expand.config import Config
from doc_expand.state import PipelineState, emit, mark_stage_complete, stage_is_complete
from doc_expand.stages.s6_align import _apply_patch

_logger = logging.getLogger("doc_expand.s9_prereq")

_SYSTEM_PROMPT = """\
You are a prerequisite threading agent reviewing one section of a multi-domain document.
Domain sections in this document: {domain_labels}
User profile: background={background_field}, unknown_concepts={unknown_concepts}

Section text ({section_id}):
---
{section_text}
---

Identify terms used before they are defined that would block a reader with this background.
For each, provide a primer patch:
- term: the exact term
- position: "before:<short phrase from the paragraph where term first appears>"
- primer_text: "**Primer:** [2-4 sentences: what it is, why it exists, one analogy]"

Rules:
- Only primer genuinely opaque technical terms. Skip common words, obvious concepts.
- Do not primer terms from unknown_concepts that are already explained in the section.
- Max 5 primers per section.
- section_id must match the section id provided above.
"""


def _load_user_profile(state_dir: Path) -> dict:
    """Load user_profile.json; return empty dict on any error."""
    path = state_dir / "user_profile.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def _insert_primer_by_position(section_path: Path, term: str, position: str, primer_text: str) -> str:
    """Insert a primer blockquote using the position string from PrimerPatch.

    If position is "before:<phrase>", delegates to _apply_patch.
    Otherwise falls back to term-based paragraph insertion.
    """
    if not section_path.exists():
        return f"Section file not found: {section_path}"

    existing = section_path.read_text()

    # Deduplicate: don't insert a primer for the same term twice
    marker = f"> **Primer:**"
    term_marker = f"*{term}*"
    if marker.lower() in existing.lower() and term_marker.lower() in existing.lower():
        return f"Primer for '{term}' already exists — skipped."

    # Format the primer block (blockquote style matching old insert_primer tool)
    primer_block = f"> **Primer:** *{term}* — {primer_text.strip()}\n\n"

    if position.startswith("before:"):
        # Use _apply_patch with the before:<phrase> position
        # Write primer_block content (strip trailing \n\n for _apply_patch which adds spacing)
        content_for_patch = primer_block.rstrip("\n")
        result = _apply_patch(section_path, position, content_for_patch)
        return result

    # Fallback: find first occurrence of the term in the text
    m = re.search(re.escape(term), existing, re.IGNORECASE)
    if m is None:
        return f"Term '{term}' not found in section."

    para_break = existing.rfind("\n\n", 0, m.start())
    para_start = para_break + 2 if para_break != -1 else 0
    new_text = existing[:para_start] + primer_block + existing[para_start:]
    section_path.write_text(new_text)
    return f"Primer for '{term}' inserted (term-search fallback)."


# ---------------------------------------------------------------------------
# Per-section structured call
# ---------------------------------------------------------------------------

async def _thread_section(
    section_id: str,
    sections_dir: Path,
    domain_labels: str,
    background_field: str,
    unknown_concepts: list[str],
    router,
) -> int:
    """Process one section. Returns number of primers inserted."""
    section_path = sections_dir / f"section_{section_id}.md"
    if not section_path.exists():
        emit({"event": "s9_prereq_section_missing", "section_id": section_id})
        return 0

    raw_text = section_path.read_text()
    section_text = raw_text[:8000]
    if len(raw_text) > 8000:
        section_text += f"\n\n... [truncated, {len(raw_text)} chars total]"

    system = _SYSTEM_PROMPT.format(
        domain_labels=domain_labels,
        background_field=background_field or "general",
        unknown_concepts=", ".join(unknown_concepts) if unknown_concepts else "none listed",
        section_id=section_id,
        section_text=section_text,
    )
    messages: list[dict] = [{"role": "system", "content": system}]

    t0 = time.monotonic()
    try:
        plan: PrimerPlan = await router.call(messages, PrimerPlan)
    except Exception as exc:
        emit({
            "event": "s9_prereq_section_error",
            "section_id": section_id,
            "error": str(exc)[:200],
            "elapsed_s": round(time.monotonic() - t0, 2),
        })
        return 0

    primers_inserted = 0
    for patch in plan.primers:
        result = _insert_primer_by_position(
            section_path, patch.term, patch.position, patch.primer_text
        )
        if "already exists" not in result and "not found" not in result.lower():
            primers_inserted += 1
        emit({
            "event": "s6_5_primer_inserted",
            "section_id": section_id,
            "term": patch.term,
            "result": result,
        })

    emit({
        "event": "s9_prereq_section_done",
        "section_id": section_id,
        "primers_inserted": primers_inserted,
        "elapsed_s": round(time.monotonic() - t0, 2),
    })
    return primers_inserted


# ---------------------------------------------------------------------------
# Stage entry point
# ---------------------------------------------------------------------------

async def run(state: PipelineState, cfg: Config) -> None:
    state_dir = Path(state["state_dir"])
    sections_dir = state_dir / "sections"

    if stage_is_complete(state_dir, 9):
        emit({"event": "stage_skipped", "stage": 9, "reason": "already_complete"})
        return

    emit({"event": "stage_start", "stage": 9})

    if "agent" not in cfg.models:
        emit({"event": "s9_prereq_skipped", "reason": "no_agent_role_in_models"})
        mark_stage_complete(state_dir, 9)
        return

    taxonomy = json.loads((state_dir / "taxonomy.json").read_text())
    domains = taxonomy["domains"]
    domain_labels = ", ".join(d["label"] for d in domains)

    profile = _load_user_profile(state_dir)
    background_field = profile.get("background_field", "")
    unknown_concepts: list[str] = profile.get("unknown_concepts", [])

    router = make_router("agent", cfg)

    t0 = time.monotonic()
    total_primers = 0
    sections_processed = []

    section_files = sorted(sections_dir.glob("section_*.md"))
    for sf in section_files:
        section_id = sf.stem[len("section_"):]
        try:
            n = await _thread_section(
                section_id=section_id,
                sections_dir=sections_dir,
                domain_labels=domain_labels,
                background_field=background_field,
                unknown_concepts=unknown_concepts,
                router=router,
            )
            total_primers += n
            sections_processed.append(section_id)
        except Exception as exc:
            emit({
                "event": "s9_prereq_section_failed",
                "section_id": section_id,
                "error": str(exc)[:200],
            })

    emit({
        "event": "s6_5_finish",
        "primers_inserted": total_primers,
        "sections_processed": sections_processed,
    })

    mark_stage_complete(state_dir, 9)
    emit({
        "event": "stage_complete",
        "stage": 9,
        "primers_inserted": total_primers,
        "elapsed_s": round(time.monotonic() - t0, 2),
    })
