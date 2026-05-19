"""Tests for doc_expand.stages.s7_assemble."""

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from doc_expand.config import (
    BibliographyConfig,
    CentralityConfig,
    ConcurrencyConfig,
    Config,
    RateLimitConfig,
)
from doc_expand.stages import s7_assemble


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_cfg() -> Config:
    return Config(
        bibliography=BibliographyConfig(),
        centrality=CentralityConfig(),
        rate_limits={"semantic_scholar": RateLimitConfig(max_rate=100, time_period=300)},
        concurrency=ConcurrencyConfig(),
        timeouts={"default": 30},
        adversarial_rounds={"survey": 1},
        boilerplate_stop_list=[],
        models={"researcher": ["claude-3-haiku"]},
    )


def _make_state(state_dir: Path) -> dict:
    return {
        "run_id": "test_run",
        "state_dir": str(state_dir),
        "output_dir": str(state_dir / "output"),
        "input_path": "test.pdf",
        "depth": "survey",
        "domain_ids": [],
    }


def _write_state_files(
    state_dir: Path,
    domain_ids: list[str],
    bib_entries: dict[str, list[dict]] | None = None,
) -> None:
    """Set up taxonomy.json, graph.json, sections, and bibliographies."""
    domains = [{"id": did, "label": did.replace("_", " ").title()} for did in domain_ids]
    (state_dir / "taxonomy.json").write_text(json.dumps({"domains": domains}))

    nodes = [
        {"id": f"{did}_n1", "name": f"{did} concept", "domain": did, "tier": "journeyman"}
        for did in domain_ids
    ]
    (state_dir / "graph.json").write_text(json.dumps({"nodes": nodes, "edges": []}))

    sections_dir = state_dir / "sections"
    sections_dir.mkdir(exist_ok=True)
    for did in domain_ids:
        (sections_dir / f"section_{did}.md").write_text(f"# {did}\n\nContent for {did}.\n")

    # Synthesis
    (sections_dir / "section_synthesis.md").write_text("# Synthesis\n\nCross-domain insights.\n")

    audit_dir = state_dir / "audit"
    audit_dir.mkdir(exist_ok=True)
    if bib_entries:
        for did, entries in bib_entries.items():
            (audit_dir / f"bibliography_{did}.json").write_text(json.dumps(entries))
    else:
        for did in domain_ids:
            (audit_dir / f"bibliography_{did}.json").write_text("[]")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_s7_creates_expanded_md(tmp_path):
    """expanded.md is written with domain sections in order."""
    cfg = _make_cfg()
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    _write_state_files(state_dir, ["domain_a", "domain_b"])
    state = _make_state(state_dir)

    await s7_assemble.run(state, cfg, no_pdf=True)

    output_dir = state_dir / "output"
    assert (output_dir / "expanded.md").exists()

    md_text = (output_dir / "expanded.md").read_text()
    assert "domain_a" in md_text
    assert "domain_b" in md_text
    assert "Synthesis" in md_text
    assert "Bibliography" in md_text


@pytest.mark.asyncio
async def test_s7_bibliography_dedup_by_doi(tmp_path):
    """Bibliography deduplication removes entries with duplicate DOIs."""
    cfg = _make_cfg()
    state_dir = tmp_path / "state"
    state_dir.mkdir()

    dup_doi = "10.1/duplicate"
    bib_a = [
        {"id": "paper_a1", "title": "Paper A1", "DOI": dup_doi, "author": [],
         "issued": {"date-parts": [[2021]]}, "bucket": "foundational"},
        {"id": "paper_a2", "title": "Paper A2", "DOI": "10.1/unique_a", "author": [],
         "issued": {"date-parts": [[2020]]}, "bucket": "foundational"},
    ]
    bib_b = [
        {"id": "paper_b1", "title": "Paper B1 (dup doi)", "DOI": dup_doi, "author": [],
         "issued": {"date-parts": [[2021]]}, "bucket": "frontier"},
        {"id": "paper_b2", "title": "Paper B2", "DOI": "10.1/unique_b", "author": [],
         "issued": {"date-parts": [[2022]]}, "bucket": "frontier"},
    ]
    _write_state_files(state_dir, ["domain_a", "domain_b"], {"domain_a": bib_a, "domain_b": bib_b})
    state = _make_state(state_dir)

    await s7_assemble.run(state, cfg, no_pdf=True)

    bib_path = state_dir / "bibliography.json"
    merged = json.loads(bib_path.read_text())
    dois = [e.get("DOI") for e in merged if e.get("DOI") == dup_doi]
    assert len(dois) == 1, f"Expected 1 entry with dup DOI, got {len(dois)}"
    assert len(merged) == 3  # paper_a1, paper_a2, paper_b2 (paper_b1 deduped)


@pytest.mark.asyncio
async def test_s7_bibliography_dedup_by_title(tmp_path):
    """Bibliography deduplication removes entries with same title (no DOI)."""
    cfg = _make_cfg()
    state_dir = tmp_path / "state"
    state_dir.mkdir()

    bib_a = [
        {"id": "paper_x", "title": "Shared Title Paper", "DOI": None, "author": [],
         "issued": {"date-parts": [[2021]]}, "bucket": "foundational"},
    ]
    bib_b = [
        {"id": "paper_y", "title": "Shared Title Paper", "DOI": None, "author": [],
         "issued": {"date-parts": [[2021]]}, "bucket": "frontier"},
    ]
    _write_state_files(state_dir, ["domain_a", "domain_b"], {"domain_a": bib_a, "domain_b": bib_b})
    state = _make_state(state_dir)

    await s7_assemble.run(state, cfg, no_pdf=True)

    bib_path = state_dir / "bibliography.json"
    merged = json.loads(bib_path.read_text())
    titles = [e.get("title", "").lower() for e in merged]
    assert titles.count("shared title paper") == 1


@pytest.mark.asyncio
async def test_s7_pdf_skip_when_pandoc_unavailable(tmp_path):
    """PDF generation is skipped gracefully when pandoc is not available."""
    cfg = _make_cfg()
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    _write_state_files(state_dir, ["domain_a"])
    state = _make_state(state_dir)

    with patch("doc_expand.stages.s7_assemble.shutil.which", return_value=None):
        await s7_assemble.run(state, cfg, no_pdf=False)

    output_dir = state_dir / "output"
    assert (output_dir / "expanded.md").exists()
    # PDF should not exist
    assert not (output_dir / "expanded.pdf").exists()


@pytest.mark.asyncio
async def test_s7_pdf_skip_with_no_pdf_flag(tmp_path):
    """PDF generation is skipped when no_pdf=True."""
    cfg = _make_cfg()
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    _write_state_files(state_dir, ["domain_a"])
    state = _make_state(state_dir)

    await s7_assemble.run(state, cfg, no_pdf=True)

    output_dir = state_dir / "output"
    assert (output_dir / "expanded.md").exists()
    assert not (output_dir / "expanded.pdf").exists()


@pytest.mark.asyncio
async def test_s7_domain_order_uses_prerequisite_count(tmp_path):
    """Domains with more prerequisites pointing at them come later."""
    cfg = _make_cfg()
    state_dir = tmp_path / "state"
    state_dir.mkdir()

    domains = [{"id": "basics", "label": "Basics"}, {"id": "advanced", "label": "Advanced"}]
    (state_dir / "taxonomy.json").write_text(json.dumps({"domains": domains}))

    nodes = [
        {"id": "basics_n1", "name": "basics concept", "domain": "basics"},
        {"id": "advanced_n1", "name": "advanced concept", "domain": "advanced"},
    ]
    # advanced has a prerequisite edge pointing at it from basics
    edges = [
        {"from": "basics_n1", "to": "advanced_n1", "type": "prerequisite"},
    ]
    (state_dir / "graph.json").write_text(json.dumps({"nodes": nodes, "edges": edges}))

    sections_dir = state_dir / "sections"
    sections_dir.mkdir()
    (sections_dir / "section_basics.md").write_text("# Basics\n\n")
    (sections_dir / "section_advanced.md").write_text("# Advanced\n\n")

    audit_dir = state_dir / "audit"
    audit_dir.mkdir()
    (audit_dir / "bibliography_basics.json").write_text("[]")
    (audit_dir / "bibliography_advanced.json").write_text("[]")

    state = _make_state(state_dir)
    await s7_assemble.run(state, cfg, no_pdf=True)

    md_text = (state_dir / "output" / "expanded.md").read_text()
    basics_pos = md_text.find("# Basics")
    advanced_pos = md_text.find("# Advanced")
    assert basics_pos < advanced_pos, "Basics should appear before Advanced"


@pytest.mark.asyncio
async def test_s7_idempotent_when_complete(tmp_path):
    """Stage 7 is skipped if already marked complete."""
    from doc_expand.state import mark_stage_complete

    cfg = _make_cfg()
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "pipeline.json").write_text("{}")
    mark_stage_complete(state_dir, 7)
    state = _make_state(state_dir)

    # Should not raise even though files are missing
    await s7_assemble.run(state, cfg, no_pdf=True)
