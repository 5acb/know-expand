"""Stage 7 — Assemble: stitch sections into final document and optionally PDF."""

import json
import logging
import re
import shutil
import subprocess
from pathlib import Path

from know_expand.agents.schemas import AssemblyManifest
from know_expand.config import Config
from know_expand.state import (
    PipelineState,
    atomic_write,
    emit,
    mark_stage_complete,
    stage_is_complete,
)

_logger = logging.getLogger("know_expand.s10")


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

def _sanitize_bib_entry(entry: dict) -> dict:
    """
    Remove null values and ensure required CSL-JSON fields are present.
    pandoc citeproc rejects entries that have null for string/number fields.
    """
    cleaned = {k: v for k, v in entry.items() if v is not None}
    # Ensure mandatory fields
    cleaned.setdefault("id", "unknown")
    cleaned.setdefault("title", "Untitled")
    cleaned.setdefault("type", "article-journal")
    cleaned.setdefault("issued", {"date-parts": [[0]]})
    # author must be a list of dicts; sanitize nested nulls
    if "author" in cleaned and isinstance(cleaned["author"], list):
        cleaned["author"] = [
            {ak: av for ak, av in a.items() if av is not None}
            for a in cleaned["author"]
            if isinstance(a, dict)
        ]
    return cleaned


def _merge_bibliographies(audit_dir: Path) -> list[dict]:
    """
    Load all bibliography_{domain_id}.json files, deduplicate by DOI then title.
    Returns a flat list of unique, sanitized CitationRecord dicts.
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
            merged.append(_sanitize_bib_entry(entry))

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


# ---------------------------------------------------------------------------
# Bare LaTeX math fixer
# ---------------------------------------------------------------------------

# LaTeX commands that must be inside $...$ to survive xelatex.
# We only wrap ones that are clearly mathematical (not \textbf, \emph, etc.).
_MATH_CMDS = (
    r"bar|hat|tilde|vec|dot|ddot|breve|check|acute|grave|widehat|widetilde"
    r"|mathbb|mathbf|mathcal|mathit|mathsf|mathtt|mathfrak|boldsymbol"
    r"|frac|dfrac|tfrac|sfrac|sqrt|binom|tbinom|dbinom"
    r"|sum|prod|int|oint|iint|iiint|partial|nabla|infty"
    r"|alpha|beta|gamma|delta|epsilon|varepsilon|zeta|eta|theta|vartheta"
    r"|iota|kappa|lambda|mu|nu|xi|pi|varpi|rho|varrho|sigma|varsigma"
    r"|tau|upsilon|phi|varphi|chi|psi|omega"
    r"|Alpha|Beta|Gamma|Delta|Epsilon|Zeta|Eta|Theta|Iota|Kappa|Lambda"
    r"|Mu|Nu|Xi|Pi|Rho|Sigma|Tau|Upsilon|Phi|Chi|Psi|Omega"
    r"|pm|mp|times|div|cdot|circ|bullet|oplus|otimes|odot"
    r"|leq|geq|neq|approx|equiv|sim|simeq|propto|ll|gg|prec|succ"
    r"|in|notin|subset|subseteq|supset|supseteq|cup|cap|setminus|emptyset"
    r"|forall|exists|nexists|neg|wedge|vee|langle|rangle"
    r"|ldots|cdots|vdots|ddots|to|leftarrow|rightarrow|Rightarrow|Leftarrow"
    r"|iff|implies|gets|mapsto|longrightarrow|leftrightarrow"
    r"|lim|sup|inf|max|min|arg|det|dim|exp|ker|log|ln|sin|cos|tan"
    r"|arcsin|arccos|arctan|sinh|cosh|tanh|cot|sec|csc"
    r"|text|mathrm|operatorname"
)
# Matches a bare math command name followed by a non-letter (word-boundary guard).
# The (?![A-Za-z]) lookahead prevents \sec matching inside \section, \text inside \textbf, etc.
_CMD_NAME_RE = re.compile(
    r"\\(?:" + _MATH_CMDS + r")(?![A-Za-z])"
)


def _wrap_bare_math_in_segment(seg: str) -> str:
    """Scan *seg* for bare math commands and wrap each (plus all its brace/script args)
    in $...$. Uses a brace-depth walker so arbitrary nesting is handled correctly."""
    result: list[str] = []
    pos = 0
    n = len(seg)

    def _consume_brace(p: int) -> int:
        """Advance p past one balanced {…} group starting at seg[p]=='{'."""
        depth = 0
        while p < n:
            if seg[p] == '{':
                depth += 1
            elif seg[p] == '}':
                depth -= 1
                if depth == 0:
                    return p + 1
            p += 1
        return p  # unclosed brace — return where we stopped

    while pos < n:
        m = _CMD_NAME_RE.search(seg, pos)
        if m is None:
            result.append(seg[pos:])
            break
        result.append(seg[pos:m.start()])
        p = m.end()
        # Consume any immediately-following {brace} groups and ^ _ scripts
        while p < n:
            if seg[p] == '{':
                p = _consume_brace(p)
            elif seg[p] in ('^', '_') and p + 1 < n:
                if seg[p + 1] == '{':
                    p = _consume_brace(p + 1)  # skip ^ or _, then consume brace
                elif seg[p + 1].isalnum():
                    p += 2  # e.g. ^2 or _n
                else:
                    break
            else:
                break
        result.append(f"${seg[m.start():p]}$")
        pos = p
    return "".join(result)


def _fix_bare_math(text: str) -> str:
    """
    Wrap bare LaTeX math commands in $...$ when outside existing math/code blocks.
    Operates line-by-line; skips fenced code blocks and inline code spans.
    """
    result_lines: list[str] = []
    in_fence = False
    fence_marker = ""

    for line in text.splitlines():
        # Track fenced code blocks (``` or ~~~)
        stripped = line.lstrip()
        if not in_fence:
            if stripped.startswith("```") or stripped.startswith("~~~"):
                in_fence = True
                fence_marker = stripped[:3]
                result_lines.append(line)
                continue
        else:
            if stripped.startswith(fence_marker):
                in_fence = False
            result_lines.append(line)
            continue

        # Process line outside code fence: split around inline code (`...`)
        # and existing math ($...$ / $$...$$), only fix plain-text segments.
        segments = re.split(r'(`[^`]*`|\$\$.*?\$\$|\$[^$\n]+?\$)', line)
        fixed_segments: list[str] = []
        for i, seg in enumerate(segments):
            if i % 2 == 1:
                # Odd segments are the delimiters themselves (code/math) — leave alone
                fixed_segments.append(seg)
            else:
                # Even segments are plain text — wrap bare math commands
                fixed_segments.append(_wrap_bare_math_in_segment(seg))
        result_lines.append("".join(fixed_segments))

    return "\n".join(result_lines)


def _fix_mixed_display_math(text: str) -> str:
    """Remove $...$ markers incorrectly embedded inside \\[...\\] display math blocks,
    and remove \\[...\\] markers incorrectly embedded inside $$...$$ display math blocks.

    The LLM occasionally generates \\[expr = $\\frac{...}$\\] which is invalid
    LaTeX — display math cannot nest inline math delimiters.
    """
    # Fix \\[...$...\\] — remove lone $ (not $$) inside \\[...\\]
    def _strip_inline_dollars(m: re.Match) -> str:
        inner = re.sub(r'(?<!\$)\$(?!\$)', '', m.group(1))
        return r'\[' + inner + r'\]'

    text = re.sub(r'\\\[(.*?)\\\]', _strip_inline_dollars, text, flags=re.DOTALL)

    # Fix $$...\\[...\\]...$$ — remove \\[ \\] inside $$...$$
    def _strip_display_in_dmath(m: re.Match) -> str:
        inner = m.group(1)
        inner = inner.replace(r'\[', '').replace(r'\]', '')
        return '$$' + inner + '$$'

    text = re.sub(r'\$\$(.*?)\$\$', _strip_display_in_dmath, text, flags=re.DOTALL)
    return text


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
    """Strip internal pipeline markers and fix bare math before writing final output."""
    text = _UNVALIDATED_RE.sub(" ", text)
    text = _NEEDS_CITATION_RE.sub(" ", text)
    text = _CITATION_NEEDED_RE.sub(" ", text)
    text = _INFERRED_MARKER_RE.sub(" ", text)
    text = _INFERRED_COMMENT_RE.sub("", text)
    text = _CITE_SYNTAX_RE.sub(lambda m: _convert_cite_syntax(m.group(1)), text)
    text = _ORPHAN_SPACE_PUNCT_RE.sub(r'\1', text)
    text = _fix_mixed_display_math(text)
    text = _fix_bare_math(text)
    return text


def _word_count(text: str) -> int:
    return len(text.split())


# ---------------------------------------------------------------------------
# Exact primer deduplication (safe: only removes byte-identical blocks)
# ---------------------------------------------------------------------------

def _dedup_exact_primers(text: str) -> str:
    """Remove exact-duplicate primer blockquotes only. Never touches other content."""
    blocks = text.split("\n\n")
    kept: list[str] = []
    seen_primers: set[str] = set()
    removed = 0

    for block in blocks:
        if block.strip().startswith("> **Primer:**"):
            key = block.strip()
            if key in seen_primers:
                removed += 1
                continue
            seen_primers.add(key)
        kept.append(block)

    if removed:
        _logger.info("s10: removed %d exact-duplicate primer block(s)", removed)

    return "\n\n".join(kept)


async def run(state: PipelineState, cfg: Config, no_pdf: bool = False) -> None:
    state_dir = Path(state["state_dir"])
    output_dir = Path(state["output_dir"])
    audit_dir = state_dir / "audit"
    sections_dir = state_dir / "sections"

    output_dir.mkdir(parents=True, exist_ok=True)

    if stage_is_complete(state_dir, 10):
        emit({"event": "stage_skipped", "stage": 10, "reason": "already_complete"})
        return

    emit({"event": "stage_start", "stage": 10})
    emit({"event": "s10_start"})

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

    # Add synthesis section, optionally prepending a Reading Roadmap from S5 metadata
    synthesis_path = sections_dir / "section_synthesis.md"
    if synthesis_path.exists():
        synthesis_text = _clean_section_text(synthesis_path.read_text())

        # Inject structured Reading Roadmap from summary_synthesis.json if available
        summary_synthesis_path = state_dir / "summaries" / "summary_synthesis.json"
        if summary_synthesis_path.exists():
            try:
                syn_meta = json.loads(summary_synthesis_path.read_text())
                roadmap: list[str] = syn_meta.get("reading_roadmap", [])
                domain_count: int = syn_meta.get("domain_count", len(ordered_domain_ids))
                if roadmap:
                    # Deduplicate and strip any leading "N. " numbering the LLM may have added
                    seen_keys: set[str] = set()
                    unique_steps: list[str] = []
                    for step in roadmap:
                        import re as _re
                        clean = _re.sub(r'^\d+[\.\)]\s*', '', step.strip())
                        key = clean.lower()
                        if key not in seen_keys:
                            seen_keys.add(key)
                            unique_steps.append(clean)

                    roadmap_lines = [
                        f"## Reading Roadmap\n",
                        f"This document covers {domain_count} domain"
                        + ("s" if domain_count != 1 else "")
                        + ". For a reader working through this material for the"
                        " first time, the recommended sequence is:\n",
                    ]
                    for i, step in enumerate(unique_steps, 1):
                        # Steps may be plain domain names or "Domain — reason" strings
                        if " — " in step or " - " in step:
                            roadmap_lines.append(f"{i}. **{step}**")
                        else:
                            roadmap_lines.append(f"{i}. **{step}**")
                    roadmap_section = "\n".join(roadmap_lines)

                    # Prepend roadmap before synthesis body (after the first # heading if present)
                    syn_lines = synthesis_text.splitlines(keepends=True)
                    insert_at = 0
                    for idx, line in enumerate(syn_lines):
                        if line.startswith("# "):
                            insert_at = idx + 1
                            # Skip blank lines immediately after the heading
                            while insert_at < len(syn_lines) and syn_lines[insert_at].strip() == "":
                                insert_at += 1
                            break
                    syn_lines.insert(insert_at, roadmap_section + "\n\n")
                    synthesis_text = "".join(syn_lines)
            except Exception as _exc:
                _logger.warning("s7: could not load summary_synthesis.json: %s", _exc)

        doc_parts.append(synthesis_text)
        section_files_used.append(synthesis_path.name)

    # Add bibliography section
    doc_parts.append(_bibliography_to_markdown(bibliography))

    full_text = "\n\n---\n\n".join(doc_parts)
    full_text = _dedup_exact_primers(full_text)
    total_words = _word_count(full_text)

    output_md = output_dir / "expanded.md"
    atomic_write(output_md, full_text)

    emit({
        "event": "s7_markdown_written",
        "path": str(output_md),
        "total_words": total_words,
        "sections": len(section_files_used),
    })

    # Write bibliography in CSL-JSON format for pandoc citeproc
    output_bib = output_dir / "bibliography.json"
    output_bib.write_text(json.dumps(bibliography, indent=2))

    # Write a pandoc-ready markdown copy with YAML frontmatter.
    # Embedding bibliography in frontmatter avoids the pandoc 3.x bug where
    # --bibliography with a JSON array triggers a YAML parse exception.
    csl_path = Path("style") / "chicago-author-date.csl"
    frontmatter_lines = [
        "---",
        f'title: "{doc_title.replace(chr(34), chr(39))}"',
        f'bibliography: "{output_bib}"',
        "link-citations: true",
        "header-includes:",
        "  - \\usepackage{amsmath}",
        "  - \\usepackage{amssymb}",
        "  - \\usepackage{unicode-math}",
        "geometry: margin=1in",
        "fontsize: 11pt",
    ]
    if csl_path.exists():
        frontmatter_lines.append(f'csl: "{csl_path.resolve()}"')
    frontmatter_lines.append("---\n")
    pandoc_md = output_dir / "expanded_pandoc.md"
    # Strip the leading "# Title" from full_text since frontmatter carries the title
    body = re.sub(r"^#[^\n]*\n", "", full_text, count=1)
    pandoc_md.write_text("\n".join(frontmatter_lines) + body)

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
                str(pandoc_md),
                "--pdf-engine=xelatex",
                "--citeproc",           # process [@key] citations via frontmatter bibliography
                "-o", str(pdf_path),
            ]

            try:
                result = subprocess.run(
                    pandoc_cmd,
                    capture_output=True,
                    text=True,
                    timeout=300,
                )
                if result.returncode != 0:
                    emit({
                        "event": "s7_pdf_error",
                        "reason": "pandoc_error",
                        "returncode": result.returncode,
                        "stderr": result.stderr[:1000],
                    })
                    _logger.error("s10: pandoc failed (rc=%d): %s", result.returncode, result.stderr[:500])
                else:
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
                            "event": "s7_pdf_error",
                            "reason": "pdf_not_created",
                            "stderr": result.stderr[:500],
                        })
            except subprocess.TimeoutExpired:
                emit({"event": "s7_pdf_error", "reason": "pandoc_timeout"})
            except Exception as exc:
                emit({"event": "s7_pdf_error", "reason": "pandoc_exception", "error": str(exc)[:200]})

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

    mark_stage_complete(state_dir, 10)
    emit({
        "event": "s10_complete",
        "stage": 10,
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
