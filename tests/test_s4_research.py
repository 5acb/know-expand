"""Tests for know_expand.stages.s4_research."""

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from know_expand.agents.schemas import CritiqueResult, DomainSummary, ResearchSection
from know_expand.config import (
    BibliographyConfig,
    CentralityConfig,
    ConcurrencyConfig,
    Config,
    RateLimitConfig,
)
from know_expand.stages import s4_research


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_cfg(depth: str = "survey") -> Config:
    return Config(
        bibliography=BibliographyConfig(),
        centrality=CentralityConfig(),
        rate_limits={"semantic_scholar": RateLimitConfig(max_rate=100, time_period=300)},
        concurrency=ConcurrencyConfig(),
        timeouts={"default": 30},
        adversarial_rounds={"survey": 1, "standard": 2, "deep": 3},
        boilerplate_stop_list=[],
        models={"researcher": ["claude-3-haiku"], "critic": ["claude-3-haiku"]},
    )


def _make_domain_summary(domain_id: str, domain_label: str) -> DomainSummary:
    return DomainSummary(
        domain_id=domain_id,
        domain_label=domain_label,
        overview=f"Overview of {domain_label}",
        sections=[
            ResearchSection(
                heading="Core Concepts",
                body="The core concepts are [@smith_2023_title].",
                citations=[],
                confidence="high",
            )
        ],
        key_open_questions=["What is left to solve?"],
        citation_ids_used=["smith_2023_title"],
    )


def _make_critique_accept(domain_id: str) -> CritiqueResult:
    return CritiqueResult(
        domain_id=domain_id,
        issues=[],
        suggested_additions=[],
        verdict="accept",
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


def _write_graph_and_taxonomy(state_dir: Path, domain_ids: list[str]) -> None:
    domains = [
        {"id": did, "label": did.replace("_", " ").title()}
        for did in domain_ids
    ]
    taxonomy = {"domains": domains}
    (state_dir / "taxonomy.json").write_text(json.dumps(taxonomy))

    graph = {
        "nodes": [
            {"id": f"{did}_node1", "name": f"{did} concept", "domain": did, "tier": "journeyman", "centrality": "core"}
            for did in domain_ids
        ],
        "edges": [],
    }
    (state_dir / "graph.json").write_text(json.dumps(graph))

    audit_dir = state_dir / "audit"
    audit_dir.mkdir(exist_ok=True)
    (audit_dir / "gap_analysis.md").write_text("# Gap Analysis\n\n(no gaps)")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_s4_writes_section_and_summary(tmp_path):
    """Section markdown and summary JSON are written for each domain."""
    cfg = _make_cfg()
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    _write_graph_and_taxonomy(state_dir, ["ml_basics"])
    state = _make_state(state_dir)

    summary = _make_domain_summary("ml_basics", "Ml Basics")
    critique = _make_critique_accept("ml_basics")

    mock_router = AsyncMock()
    mock_router.call = AsyncMock(side_effect=[summary, summary, critique])

    with patch("know_expand.stages.s4_research.make_router", return_value=mock_router):
        await s4_research.run(state, cfg)

    sections_dir = state_dir / "sections"
    summaries_dir = state_dir / "summaries"

    assert (sections_dir / "section_ml_basics.md").exists()
    assert (summaries_dir / "summary_ml_basics.json").exists()

    md_text = (sections_dir / "section_ml_basics.md").read_text()
    assert "Ml Basics" in md_text

    summary_data = json.loads((summaries_dir / "summary_ml_basics.json").read_text())
    assert summary_data["domain_id"] == "ml_basics"


@pytest.mark.asyncio
async def test_s4_skips_domain_with_sentinel(tmp_path):
    """Domain with existing sentinel is not reprocessed."""
    cfg = _make_cfg()
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    _write_graph_and_taxonomy(state_dir, ["ml_basics"])
    state = _make_state(state_dir)

    sections_dir = state_dir / "sections"
    sections_dir.mkdir()
    summaries_dir = state_dir / "summaries"
    summaries_dir.mkdir()

    # Write sentinel and summary for this domain
    sentinel = sections_dir / "section_ml_basics.md.done"
    sentinel.touch()
    summary = _make_domain_summary("ml_basics", "Ml Basics")
    (summaries_dir / "summary_ml_basics.json").write_text(summary.model_dump_json())

    mock_router = AsyncMock()
    mock_router.call = AsyncMock()

    with patch("know_expand.stages.s4_research.make_router", return_value=mock_router):
        await s4_research.run(state, cfg)

    # Router should not have been called for the skipped domain
    mock_router.call.assert_not_called()


@pytest.mark.asyncio
async def test_s4_domain_failure_doesnt_kill_others(tmp_path):
    """A single domain failure does not prevent other domains from completing."""
    cfg = _make_cfg()
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    _write_graph_and_taxonomy(state_dir, ["domain_a", "domain_b"])
    state = _make_state(state_dir)

    summary_b = _make_domain_summary("domain_b", "Domain B")
    critique_b = _make_critique_accept("domain_b")

    # Patch _research_domain directly: domain_a raises, domain_b succeeds normally
    original_research_domain = s4_research._research_domain

    async def fake_research_domain(domain, **kwargs):
        if domain["id"] == "domain_a":
            raise RuntimeError("simulated domain_a failure")
        # domain_b: call router to simulate real behavior
        router = kwargs["router"]
        # top-down + bottom-up (parallel), then critique
        td, bu = await asyncio.gather(
            router.call([], DomainSummary),
            router.call([], DomainSummary),
        )
        critique = await router.call([], CritiqueResult)
        sections_dir = kwargs["sections_dir"]
        summaries_dir = kwargs["summaries_dir"]
        sections_dir.mkdir(exist_ok=True)
        summaries_dir.mkdir(exist_ok=True)
        (sections_dir / f"section_{domain['id']}.md").write_text("# Domain B\n")
        (summaries_dir / f"summary_{domain['id']}.json").write_text(summary_b.model_dump_json())
        (sections_dir / f"section_{domain['id']}.md.done").touch()
        return summary_b

    mock_router = AsyncMock()
    mock_router.call = AsyncMock(side_effect=[summary_b, summary_b, critique_b])

    with patch("know_expand.stages.s4_research._research_domain", side_effect=fake_research_domain):
        with patch("know_expand.stages.s4_research.make_router", return_value=mock_router):
            # Should not raise even though domain_a fails
            await s4_research.run(state, cfg)

    # domain_b should have produced output
    sections_dir = state_dir / "sections"
    assert (sections_dir / "section_domain_b.md").exists()


@pytest.mark.asyncio
async def test_s4_idempotent_when_complete(tmp_path):
    """Stage 4 is skipped entirely if already marked complete."""
    from know_expand.state import mark_stage_complete

    cfg = _make_cfg()
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    _write_graph_and_taxonomy(state_dir, ["ml_basics"])
    state = _make_state(state_dir)
    (state_dir / "pipeline.json").write_text("{}")
    mark_stage_complete(state_dir, 4)

    mock_router = AsyncMock()
    mock_router.call = AsyncMock()

    with patch("know_expand.stages.s4_research.make_router", return_value=mock_router):
        await s4_research.run(state, cfg)

    mock_router.call.assert_not_called()


@pytest.mark.asyncio
async def test_s4_adversarial_rounds_revise(tmp_path):
    """With standard depth (2 rounds), critique triggers a revision call."""
    cfg = _make_cfg(depth="standard")
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    _write_graph_and_taxonomy(state_dir, ["rl"])
    state = _make_state(state_dir, depth="standard")

    summary = _make_domain_summary("rl", "Rl")
    critique_revise = CritiqueResult(
        domain_id="rl",
        issues=["Missing implementation details"],
        suggested_additions=["Add Q-learning example"],
        verdict="revise",
    )
    critique_accept = _make_critique_accept("rl")

    call_results = [
        summary,      # top-down
        summary,      # bottom-up
        critique_revise,  # round 1 critique → revise
        summary,      # revision
        critique_accept,  # round 2 critique → accept
    ]

    mock_router = AsyncMock()
    mock_router.call = AsyncMock(side_effect=call_results)

    with patch("know_expand.stages.s4_research.make_router", return_value=mock_router):
        await s4_research.run(state, cfg)

    # Should have made 5 calls (top-down, bottom-up, critique×2, revise×1)
    assert mock_router.call.call_count == 5
