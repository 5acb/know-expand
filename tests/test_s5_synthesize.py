"""Tests for doc_expand.stages.s5_synthesize."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from doc_expand.agents.schemas import (
    SynthesisCritique,
    SynthesisDraft,
    SynthesisInsight,
)
from doc_expand.config import (
    BibliographyConfig,
    CentralityConfig,
    ConcurrencyConfig,
    Config,
    RateLimitConfig,
)
from doc_expand.stages import s5_synthesize


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
        adversarial_rounds={"survey": 1, "standard": 2, "deep": 3},
        boilerplate_stop_list=[],
        models={"researcher": ["claude-3-haiku"]},
    )


def _make_state(state_dir: Path, depth: str = "survey") -> dict:
    return {
        "run_id": "test_run",
        "state_dir": str(state_dir),
        "output_dir": str(state_dir / "output"),
        "input_path": "test.pdf",
        "depth": depth,
        "domain_ids": [],
    }


def _make_summary_dict(domain_id: str, label: str) -> dict:
    return {
        "domain_id": domain_id,
        "domain_label": label,
        "overview": f"Overview of {label}",
        "sections": [
            {"heading": "Core", "body": "Core content.", "citations": [], "confidence": "high"}
        ],
        "key_open_questions": ["Open Q1"],
        "citation_ids_used": [],
    }


def _make_draft() -> SynthesisDraft:
    return SynthesisDraft(
        insights=[
            SynthesisInsight(
                insight="Domain A and B share optimization techniques",
                domains_involved=["domain_a", "domain_b"],
                evidence="edge: domain_a_node --[related]--> domain_b_node",
                confidence="high",
            )
        ],
        reading_roadmap=["domain_a", "domain_b"],
        boss_nodes=["unified_optimization"],
        narrative="# Cross-Domain Synthesis\n\nBoth domains share optimization roots.",
    )


def _make_critique_accept() -> SynthesisCritique:
    return SynthesisCritique(
        trivial_connections=[],
        unsupported_connections=[],
        missing_cross_domain=[],
        verdict="accept",
    )


def _write_graph(state_dir: Path) -> None:
    graph = {
        "nodes": [
            {"id": "a_node", "name": "concept a", "domain": "domain_a"},
            {"id": "b_node", "name": "concept b", "domain": "domain_b"},
        ],
        "edges": [
            {"from": "a_node", "to": "b_node", "type": "related"},
        ],
    }
    (state_dir / "graph.json").write_text(json.dumps(graph))

    taxonomy = {
        "domains": [
            {"id": "domain_a", "label": "Domain A"},
            {"id": "domain_b", "label": "Domain B"},
        ]
    }
    (state_dir / "taxonomy.json").write_text(json.dumps(taxonomy))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_s5_writes_synthesis_section(tmp_path):
    """section_synthesis.md is written with cross-domain content."""
    cfg = _make_cfg()
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    _write_graph(state_dir)

    summaries_dir = state_dir / "summaries"
    summaries_dir.mkdir()
    sections_dir = state_dir / "sections"
    sections_dir.mkdir()

    (summaries_dir / "summary_domain_a.json").write_text(
        json.dumps(_make_summary_dict("domain_a", "Domain A"))
    )
    (summaries_dir / "summary_domain_b.json").write_text(
        json.dumps(_make_summary_dict("domain_b", "Domain B"))
    )

    state = _make_state(state_dir)
    draft = _make_draft()
    critique = _make_critique_accept()

    mock_router = AsyncMock()
    mock_router.call = AsyncMock(side_effect=[draft, draft, critique])

    with patch("doc_expand.stages.s5_synthesize.make_router", return_value=mock_router):
        await s5_synthesize.run(state, cfg)

    synthesis_path = sections_dir / "section_synthesis.md"
    assert synthesis_path.exists()
    content = synthesis_path.read_text()
    assert "Synthesis" in content


@pytest.mark.asyncio
async def test_s5_empty_summaries_dir(tmp_path):
    """With no summary files, synthesis writes a placeholder and completes."""
    cfg = _make_cfg()
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    _write_graph(state_dir)
    sections_dir = state_dir / "sections"
    sections_dir.mkdir()
    # No summaries dir
    state = _make_state(state_dir)

    mock_router = AsyncMock()
    mock_router.call = AsyncMock()

    with patch("doc_expand.stages.s5_synthesize.make_router", return_value=mock_router):
        await s5_synthesize.run(state, cfg)

    synthesis_path = sections_dir / "section_synthesis.md"
    assert synthesis_path.exists()
    content = synthesis_path.read_text()
    assert "No domain summaries available" in content
    # Router should not have been called
    mock_router.call.assert_not_called()


@pytest.mark.asyncio
async def test_s5_idempotent_when_complete(tmp_path):
    """Stage 5 is skipped if already marked complete."""
    from doc_expand.state import mark_stage_complete

    cfg = _make_cfg()
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    _write_graph(state_dir)
    (state_dir / "pipeline.json").write_text("{}")
    mark_stage_complete(state_dir, 5)
    state = _make_state(state_dir)

    mock_router = AsyncMock()
    mock_router.call = AsyncMock()

    with patch("doc_expand.stages.s5_synthesize.make_router", return_value=mock_router):
        await s5_synthesize.run(state, cfg)

    mock_router.call.assert_not_called()


@pytest.mark.asyncio
async def test_s5_critique_revise_loop(tmp_path):
    """With standard depth (2 rounds), a 'revise' verdict triggers revision."""
    cfg = _make_cfg()
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    _write_graph(state_dir)

    summaries_dir = state_dir / "summaries"
    summaries_dir.mkdir()
    sections_dir = state_dir / "sections"
    sections_dir.mkdir()

    (summaries_dir / "summary_domain_a.json").write_text(
        json.dumps(_make_summary_dict("domain_a", "Domain A"))
    )

    state = _make_state(state_dir, depth="standard")
    draft = _make_draft()
    critique_revise = SynthesisCritique(
        trivial_connections=["A -> B is just prerequisite"],
        unsupported_connections=[],
        missing_cross_domain=["C and D connection missing"],
        verdict="revise",
    )
    critique_accept = _make_critique_accept()

    call_results = [
        draft,            # structural
        draft,            # semantic
        critique_revise,  # round 1 → revise
        draft,            # revised draft
        critique_accept,  # round 2 → accept
    ]

    mock_router = AsyncMock()
    mock_router.call = AsyncMock(side_effect=call_results)

    with patch("doc_expand.stages.s5_synthesize.make_router", return_value=mock_router):
        await s5_synthesize.run(state, cfg)

    assert mock_router.call.call_count == 5
    synthesis_path = sections_dir / "section_synthesis.md"
    assert synthesis_path.exists()
