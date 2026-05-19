"""Stage 7 — Assemble: stitch sections into final document and optionally PDF."""

import json
import logging
import re
import shutil
import subprocess
from pathlib import Path

from doc_expand.agents.schemas import AssemblyManifest
from doc_expand.config import Config
from doc_expand.state import (
    PipelineState,
    emit,
    mark_stage_complete,
    stage_is_complete,
)

_logger = logging.getLogger("doc_expand.s7")


# ---------------------------------------------------------------------------
# Topological sort helpers
# ---------------------------------------------------------------------------

def _topo_sort_domains(domains: list[dict], edges: list[dict]) -> list[str]:
    """
    Sort domain IDs such that domains with more prerequisite edges pointing AT
    them (i.e. they depend on others) come later.

    Simple approach: count how many prerequisite-type edges end at each domain,
    then sort ascending by that count (domains with zero prerequisites first).
    """
    domain_ids = [d["id"] for d in domains]
    prerequisite_in_count: dict[str, int] = {did: 0 for did in domain_ids}

    # Build a node-to-domain mapping
    # We don't have it directly, so we use graph nodes
    # The edges list here contains from_node/to_node as node IDs, not domain IDs
    # We'll fall back to stable domain order from taxonomy if no node-domain map
    # is available (edges may not map cleanly to domains without the node list)
    # So: return stable original order augmented by any simple heuristics.
    # For now: use the original taxonomy order (already a logical ordering).
    return domain_ids


def _topo_sort_domains_with_nodes(
    domains: list[dict],
    nodes: list[dict],
    edges: list[dict],
) -> list[str]:
    """
    Topological sort using node-level prerequisite edges.
    Domains with more prerequisite-type edges pointing at their nodes come later.
    """
    domain_ids = [d["id"] for d in domains]
    node_to_domain = {n["id"]: n.get("domain", "") for n in nodes}

    prerequisite_in_count: dict[str, int] = {did: 0 for did in domain_ids}

    for edge in edges:
        if edge.get("type") != "prerequisite":
            continue
        to_node = edge.get("to", edge.get("to_node", ""))
        to_domain = node_to_domain.get(to_node, "")
        if to_domain in prerequisite_in_count:
            prerequisite_in_count[to_domain] += 1

    return sorted(domain_ids, key=lambda did: prerequisite_in_count.get(did, 0))


# ---------------------------------------------------------------------------
# Bibliography merge
# ---------------------------------------------------------------------------

def _merge_bibliographies(audit_dir: Path) -> list[dict]:
    """
    Load all bibliography_{domain_id}.json files, deduplicate by DOI then title.
    Returns a flat list of unique CitationRecord dicts.
    """
    seen_dois: set[str] = set()
    seen_titles: set[str] = set()
    merged: list[dict] = []

    for bib_file in sorted(audit_dir.glob("bibliography_*.json")):
        entries = json.loads(bib_file.read_text())
        for entry in entries:
            doi = entry.get("DOI") or entry.get("doi")
            title = (entry.get("title") or "").strip().lower()

            if doi:
                if doi in seen_dois:
                    continue
                seen_dois.add(doi)
            elif title:
                if title in seen_titles:
                    continue

            if title:
                seen_titles.add(title)
            merged.append(entry)

    return merged


# ---------------------------------------------------------------------------
# Document assembly
# ---------------------------------------------------------------------------

def _load_title(state_dir: Path, input_path: str | None = None) -> str:
    """Try to extract document title from source_meta.json or taxonomy.json."""
    meta_path = state_dir / "source_meta.json"
    if meta_path.exists():
        meta = json.loads(meta_path.read_text())
        title = meta.get("title", "")
        if title and not _is_slug_title(title):
            return title

    tax_path = state_dir / "taxonomy.json"
    if tax_path.exists():
        tax = json.loads(tax_path.read_text())
        title = tax.get("title", "")
        if title and not _is_slug_title(title):
            return title

    if input_path:
        stem = Path(input_path).stem
        return stem.replace("_", " ").replace("-", " ").title()

    return "Expanded Knowledge Document"


def _bibliography_to_markdown(bibliography: list[dict]) -> str:
    lines = ["## Bibliography\n"]
    for entry in bibliography:
        bib_id = entry.get("id", "")
        title = entry.get("title", "Untitled")
        authors = entry.get("author", [])
        year_parts = entry.get("issued", {}).get("date-parts", [[0]])
        year = year_parts[0][0] if year_parts and year_parts[0] else ""
        doi = entry.get("DOI") or entry.get("doi")
        url = entry.get("URL")

        author_str = ""
        if authors:
            if len(authors) == 1:
                author_str = authors[0].get("family", "")
            elif len(authors) == 2:
                author_str = f"{authors[0].get('family', '')} & {authors[1].get('family', '')}"
            else:
                author_str = f"{authors[0].get('family', '')} et al."

        ref_parts = []
        if author_str:
            ref_parts.append(author_str)
        if year:
            ref_parts.append(f"({year})")
        ref_parts.append(f"*{title}*")
        if doi:
            ref_parts.append(f"DOI: {doi}")
        elif url:
            ref_parts.append(f"URL: {url}")

        lines.append(f"- [{bib_id}] {' '.join(ref_parts)}")

    return "\n".join(lines)


_UNVALIDATED_RE = re.compile(r"\s*\[UNVALIDATED\]\s*", re.IGNORECASE)
_NEEDS_CITATION_RE = re.compile(r"\s*\[NEEDS_CITATION\]\s*", re.IGNORECASE)
_CITATION_NEEDED_RE = re.compile(r"\s*\[citation needed\]\s*", re.IGNORECASE)
_INFERRED_MARKER_RE = re.compile(r"\s*\[INFERRED\]\s*", re.IGNORECASE)
_INFERRED_COMMENT_RE = re.compile(r"[ \t]*#[ \t]*INFERRED:[^\n]*", re.IGNORECASE)
_CITE_SYNTAX_RE = re.compile(r'\[cite:\s*([^\]]+)\]')
_ORPHAN_SPACE_PUNCT_RE = re.compile(r' +([.,;:!?])')


def _convert_cite_syntax(keys_str: str) -> str:
    keys = [k.strip() for k in keys_str.split(',')]
    return '[@' + '; @'.join(keys) + ']'


def _is_slug_title(title: str) -> bool:
    return bool(re.match(r'^[A-Z0-9_\-]+$', title))


def _clean_section_text(text: str) -> str:
    """Strip internal pipeline markers before writing final output."""
    text = _UNVALIDATED_RE.sub(" ", text)
    text = _NEEDS_CITATION_RE.sub(" ", text)
    text = _CITATION_NEEDED_RE.sub(" ", text)
    text = _INFERRED_MARKER_RE.sub(" ", text)
    text = _INFERRED_COMMENT_RE.sub("", text)
    text = _CITE_SYNTAX_RE.sub(lambda m: _convert_cite_syntax(m.group(1)), text)
    text = _ORPHAN_SPACE_PUNCT_RE.sub(r'\1', text)
    return text


def _word_count(text: str) -> int:
    return len(text.split())


async def run(state: PipelineState, cfg: Config, no_pdf: bool = False) -> None:
    state_dir = Path(state["state_dir"])
    output_dir = Path(state["output_dir"])
    audit_dir = state_dir / "audit"
    sections_dir = state_dir / "sections"

    output_dir.mkdir(parents=True, exist_ok=True)

    if stage_is_complete(state_dir, 7):
        emit({"event": "stage_skipped", "stage": 7, "reason": "already_complete"})
        return

    emit({"event": "stage_start", "stage": 7})
    emit({"event": "s7_start"})

    # Load graph for topological sort
    graph_data = json.loads((state_dir / "graph.json").read_text())
    nodes = graph_data.get("nodes", [])
    edges = graph_data.get("edges", [])

    taxonomy = json.loads((state_dir / "taxonomy.json").read_text())
    domains = taxonomy["domains"]

    ordered_domain_ids = _topo_sort_domains_with_nodes(domains, nodes, edges)

    # Merge bibliography
    bibliography = _merge_bibliographies(audit_dir)
    bib_path = state_dir / "bibliography.json"
    bib_path.write_text(json.dumps(bibliography, indent=2))

    emit({
        "event": "s7_bibliography_merged",
        "entry_count": len(bibliography),
        "artifact": str(bib_path),
    })

    # Assemble sections in order
    doc_title = _load_title(state_dir, state.get("input_path"))
    doc_parts: list[str] = [f"# {doc_title}\n"]

    section_files_used: list[str] = []

    for domain_id in ordered_domain_ids:
        section_path = sections_dir / f"section_{domain_id}.md"
        if section_path.exists():
            doc_parts.append(_clean_section_text(section_path.read_text()))
            section_files_used.append(section_path.name)
        else:
            emit({
                "event": "s7_section_missing",
                "domain_id": domain_id,
                "expected_path": str(section_path),
            })

    # Add synthesis section
    synthesis_path = sections_dir / "section_synthesis.md"
    if synthesis_path.exists():
        doc_parts.append(_clean_section_text(synthesis_path.read_text()))
        section_files_used.append(synthesis_path.name)

    # Add bibliography section
    doc_parts.append(_bibliography_to_markdown(bibliography))

    full_text = "\n\n---\n\n".join(doc_parts)
    total_words = _word_count(full_text)

    output_md = output_dir / "expanded.md"
    output_md.write_text(full_text)

    emit({
        "event": "s7_markdown_written",
        "path": str(output_md),
        "total_words": total_words,
        "sections": len(section_files_used),
    })

    # Build bibliography.json in CSL-JSON format for pandoc
    output_bib = output_dir / "bibliography.json"
    output_bib.write_text(json.dumps(bibliography, indent=2))

    # PDF generation
    output_pdf: str | None = None
    pdf_path = output_dir / "expanded.pdf"

    if no_pdf:
        emit({"event": "s7_pdf_skipped", "reason": "no_pdf_flag"})
    else:
        pandoc_available = shutil.which("pandoc") is not None
        xelatex_available = shutil.which("xelatex") is not None

        if not pandoc_available or not xelatex_available:
            missing = []
            if not pandoc_available:
                missing.append("pandoc")
            if not xelatex_available:
                missing.append("xelatex")
            emit({
                "event": "s7_pdf_skipped",
                "reason": "missing_tools",
                "missing": missing,
            })
        else:
            emit({"event": "s7_pdf_start", "output": str(pdf_path)})
            pandoc_cmd = [
                "pandoc",
                str(output_md),
                "--pdf-engine=xelatex",
                f"--bibliography={output_bib}",
                "-o", str(pdf_path),
            ]

            # Add CSL if available
            csl_path = Path("style") / "chicago-author-date.csl"
            if csl_path.exists():
                pandoc_cmd.extend(["--csl", str(csl_path)])

            try:
                result = subprocess.run(
                    pandoc_cmd,
                    capture_output=True,
                    text=True,
                    timeout=300,
                )
                if result.returncode != 0:
                    emit({
                        "event": "s7_pdf_skipped",
                        "reason": "pandoc_error",
                        "returncode": result.returncode,
                        "stderr": result.stderr[:500],
                    })
                else:
                    # Verify PDF exists and has pages
                    if pdf_path.exists() and pdf_path.stat().st_size > 0:
                        pages = _count_pdf_pages(pdf_path)
                        output_pdf = str(pdf_path)
                        emit({
                            "event": "s7_pdf_complete",
                            "path": str(pdf_path),
                            "pages": pages,
                        })
                    else:
                        emit({
                            "event": "s7_pdf_skipped",
                            "reason": "pdf_not_created",
                        })
            except subprocess.TimeoutExpired:
                emit({"event": "s7_pdf_skipped", "reason": "pandoc_timeout"})
            except Exception as exc:
                emit({"event": "s7_pdf_skipped", "reason": "pandoc_exception", "error": str(exc)[:200]})

    manifest = AssemblyManifest(
        domain_order=ordered_domain_ids,
        section_files=section_files_used,
        bibliography_count=len(bibliography),
        total_words=total_words,
        output_md=str(output_md),
        output_pdf=output_pdf,
    )

    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(manifest.model_dump_json(indent=2))

    mark_stage_complete(state_dir, 7)
    emit({
        "event": "s7_complete",
        "stage": 7,
        "output_md": str(output_md),
        "output_pdf": output_pdf,
        "total_words": total_words,
        "bibliography_count": len(bibliography),
    })


def _count_pdf_pages(pdf_path: Path) -> int | None:
    """Return page count of PDF using pypdf, or None if unavailable."""
    try:
        from pypdf import PdfReader
        reader = PdfReader(str(pdf_path))
        return len(reader.pages)
    except ImportError:
        return None
    except Exception:
        return None
