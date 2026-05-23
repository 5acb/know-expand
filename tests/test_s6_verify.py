"""Tests for know_expand.stages.s8_verify."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from know_expand.config import (
    BibliographyConfig,
    CentralityConfig,
    ConcurrencyConfig,
    Config,
    RateLimitConfig,
)
from know_expand.stages import s8_verify


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
        models={"critic": ["claude-3-haiku"], "classifier": ["claude-3-haiku"]},
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


def _write_bibliography(audit_dir: Path, domain_id: str, entries: list[dict]) -> None:
    (audit_dir / f"bibliography_{domain_id}.json").write_text(json.dumps(entries))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_s6_detects_needs_citation(tmp_path):
    """[NEEDS_CITATION] markers are reported in needs_citation.md."""
    cfg = _make_cfg()
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    audit_dir = state_dir / "audit"
    audit_dir.mkdir()
    sections_dir = state_dir / "sections"
    sections_dir.mkdir()

    _write_bibliography(audit_dir, "domain_a", [
        {"id": "smith_2023_title", "title": "Some Paper", "author": [{"family": "Smith"}],
         "issued": {"date-parts": [[2023]]}, "bucket": "foundational"}
    ])

    section_text = (
        "# Domain A\n\n"
        "The algorithm converges quickly [NEEDS_CITATION].\n"
        "See [@smith_2023_title] for details.\n"
    )
    (sections_dir / "section_domain_a.md").write_text(section_text)

    state = _make_state(state_dir)
    await s8_verify.run(state, cfg)

    needs_md = (audit_dir / "needs_citation.md").read_text()
    assert "[NEEDS_CITATION]" in needs_md
    assert "section_domain_a.md" in needs_md


@pytest.mark.asyncio
async def test_s6_verifies_known_citation(tmp_path):
    """Known citation keys are reported as verified in the audit."""
    cfg = _make_cfg()
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    audit_dir = state_dir / "audit"
    audit_dir.mkdir()
    sections_dir = state_dir / "sections"
    sections_dir.mkdir()

    _write_bibliography(audit_dir, "domain_a", [
        {"id": "smith_2023_title", "title": "Some Paper", "author": [{"family": "Smith"}],
         "issued": {"date-parts": [[2023]]}, "bucket": "foundational"}
    ])

    (sections_dir / "section_domain_a.md").write_text(
        "# Domain A\n\nSee [@smith_2023_title] for details.\n"
    )

    state = _make_state(state_dir)
    await s8_verify.run(state, cfg)

    citation_index = json.loads((audit_dir / "citation_index.json").read_text())
    assert "smith_2023_title" in citation_index


@pytest.mark.asyncio
async def test_s6_flags_unknown_key(tmp_path):
    """Citation keys not in bibliography are reported as unknown_key."""
    cfg = _make_cfg()
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    audit_dir = state_dir / "audit"
    audit_dir.mkdir()
    sections_dir = state_dir / "sections"
    sections_dir.mkdir()

    _write_bibliography(audit_dir, "domain_a", [
        {"id": "smith_2023_title", "title": "Some Paper", "author": [{"family": "Smith"}],
         "issued": {"date-parts": [[2023]]}, "bucket": "foundational"}
    ])

    (sections_dir / "section_domain_a.md").write_text(
        "# Domain A\n\nSee [@ghost_2020_paper] which is not in bibliography.\n"
    )

    state = _make_state(state_dir)
    await s8_verify.run(state, cfg)

    needs_md = (audit_dir / "needs_citation.md").read_text()
    assert "ghost_2020_paper" in needs_md


@pytest.mark.asyncio
async def test_s6_citation_index_contains_all_valid_ids(tmp_path):
    """citation_index.json contains all bibliography entries."""
    cfg = _make_cfg()
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    audit_dir = state_dir / "audit"
    audit_dir.mkdir()
    sections_dir = state_dir / "sections"
    sections_dir.mkdir()

    _write_bibliography(audit_dir, "domain_a", [
        {"id": "alpha_2021", "title": "Alpha", "author": [], "issued": {"date-parts": [[2021]]}, "bucket": "foundational"},
        {"id": "beta_2022", "title": "Beta", "author": [], "issued": {"date-parts": [[2022]]}, "bucket": "frontier"},
    ])

    (sections_dir / "section_domain_a.md").write_text("# Domain A\n\nContent.\n")

    state = _make_state(state_dir)
    await s8_verify.run(state, cfg)

    index = json.loads((audit_dir / "citation_index.json").read_text())
    assert "alpha_2021" in index
    assert "beta_2022" in index


@pytest.mark.asyncio
async def test_s6_idempotent_when_complete(tmp_path):
    """Stage 6 is skipped if already marked complete."""
    from know_expand.state import mark_stage_complete

    cfg = _make_cfg()
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "pipeline.json").write_text("{}")
    mark_stage_complete(state_dir, 6)
    state = _make_state(state_dir)

    # Should not raise even though audit_dir and sections_dir don't exist
    await s8_verify.run(state, cfg)
