"""Stage 6 — Verify: structural citation audit across all section files."""

import json
import logging
import re
from pathlib import Path

from doc_expand.agents.schemas import CitationAuditItem, CitationAuditResult
from doc_expand.config import Config
from doc_expand.state import (
    PipelineState,
    emit,
    mark_stage_complete,
    stage_is_complete,
)

_logger = logging.getLogger("doc_expand.s6")

# Pattern for [NEEDS_CITATION] markers
_NEEDS_CITATION_RE = re.compile(r"\[NEEDS_CITATION\]")
# Pattern for citation keys: [@key] or [key] where key looks like an identifier
# (contains letters, digits, underscores, hyphens; at least 3 chars)
_CITATION_KEY_RE = re.compile(r"\[@?([\w\-]{3,})\]")
# Strip fenced code blocks before scanning (avoids [@variable] false positives)
_FENCED_CODE_RE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`[^`\n]{1,80}`")

_SENTENCE_RE = re.compile(r"[^.!?\n]{0,120}(?:\[[@\w\-]{3,}\]|\[NEEDS_CITATION\])[^.!?\n]{0,120}")

# Pipeline artifact markers that should never appear as citation keys
_PIPELINE_MARKERS = frozenset({"NEEDS_CITATION", "INFERRED", "UNVALIDATED", "VERIFIED"})


def _extract_context(text: str, pos: int, window: int = 120) -> str:
    start = max(0, pos - window)
    end = min(len(text), pos + window)
    return text[start:end].replace("\n", " ").strip()


def _audit_section_file(
    section_path: Path,
    valid_citation_ids: set[str],
) -> list[CitationAuditItem]:
    items: list[CitationAuditItem] = []
    text = section_path.read_text()
    fname = section_path.name

    # Strip code blocks before scanning to avoid false positives on [@variable] patterns
    scan_text = _FENCED_CODE_RE.sub("", text)
    scan_text = _INLINE_CODE_RE.sub("", scan_text)

    # Find [NEEDS_CITATION] markers
    for match in _NEEDS_CITATION_RE.finditer(scan_text):
        context = _extract_context(text, match.start())
        items.append(CitationAuditItem(
            section_file=fname,
            marker="[NEEDS_CITATION]",
            context=context,
            status="needs_citation",
        ))

    # Find citation keys [@key] or [key]
    for match in _CITATION_KEY_RE.finditer(scan_text):
        key = match.group(1)
        if key in _PIPELINE_MARKERS:
            continue
        # Skip single lowercase words — likely markdown link syntax, not citations
        if key.islower() and "_" not in key and "-" not in key:
            continue
        context = _extract_context(text, match.start())
        if key in valid_citation_ids:
            status = "verified"
        else:
            status = "unknown_key"
        items.append(CitationAuditItem(
            section_file=fname,
            marker=f"[@{key}]",
            context=context,
            status=status,
        ))

    return items


def _build_needs_citation_md(result: CitationAuditResult) -> str:
    lines = [
        "# Citation Audit\n",
        f"**Total [NEEDS_CITATION]:** {result.total_needs_citation}",
        f"**Unknown citation keys:** {result.total_unknown_keys}",
        f"**Verified citations:** {result.total_verified}\n",
    ]

    needs_items = [i for i in result.items if i.status == "needs_citation"]
    if needs_items:
        lines.append("## Claims Needing Citations\n")
        for item in needs_items:
            lines.append(f"**File:** `{item.section_file}`")
            lines.append(f"> {item.context}\n")

    unknown_items = [i for i in result.items if i.status == "unknown_key"]
    if unknown_items:
        lines.append("## Unknown Citation Keys\n")
        for item in unknown_items:
            lines.append(f"**File:** `{item.section_file}`  **Key:** `{item.marker}`")
            lines.append(f"> {item.context}\n")

    return "\n".join(lines)


async def run(state: PipelineState, cfg: Config) -> None:
    state_dir = Path(state["state_dir"])
    audit_dir = state_dir / "audit"
    sections_dir = state_dir / "sections"

    audit_dir.mkdir(exist_ok=True)

    if stage_is_complete(state_dir, 6):
        emit({"event": "stage_skipped", "stage": 6, "reason": "already_complete"})
        return

    emit({"event": "stage_start", "stage": 6})
    emit({"event": "s6_start"})

    # Build valid citation ID set from all bibliography JSONs
    valid_citation_ids: set[str] = set()
    bibliography_index: dict[str, dict] = {}

    for bib_file in sorted(audit_dir.glob("bibliography_*.json")):
        entries = json.loads(bib_file.read_text())
        for entry in entries:
            cid = entry.get("id", "")
            if cid:
                valid_citation_ids.add(cid)
                bibliography_index[cid] = entry

    # Collect all section files to audit
    section_files: list[Path] = []
    if sections_dir.exists():
        section_files.extend(sorted(sections_dir.glob("section_*.md")))

    all_items: list[CitationAuditItem] = []
    for section_path in section_files:
        items = _audit_section_file(section_path, valid_citation_ids)
        all_items.extend(items)

    total_needs_citation = sum(1 for i in all_items if i.status == "needs_citation")
    total_unknown_keys = sum(1 for i in all_items if i.status == "unknown_key")
    total_verified = sum(1 for i in all_items if i.status == "verified")

    audit_result = CitationAuditResult(
        items=all_items,
        total_needs_citation=total_needs_citation,
        total_unknown_keys=total_unknown_keys,
        total_verified=total_verified,
    )

    needs_citation_path = audit_dir / "needs_citation.md"
    needs_citation_path.write_text(_build_needs_citation_md(audit_result))

    citation_index_path = audit_dir / "citation_index.json"
    citation_index_path.write_text(json.dumps(bibliography_index, indent=2))

    mark_stage_complete(state_dir, 6)
    emit({
        "event": "s6_complete",
        "stage": 6,
        "total_needs_citation": total_needs_citation,
        "total_unknown_keys": total_unknown_keys,
        "total_verified": total_verified,
        "artifact_needs_citation": str(needs_citation_path),
        "artifact_citation_index": str(citation_index_path),
    })
