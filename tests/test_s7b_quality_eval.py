"""Tests for know_expand.stages.s7b_quality_eval (Quality Evaluator).

Mirrors the mocking style of test_s3_integration.py / test_s5_synthesize.py:
mocks `make_router`, asserts on call counts and emitted events, uses
tempfile-backed state dirs (pytest's `tmp_path`).
"""

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from know_expand.agents.schemas import QualityDimensionScore, QualityEvaluation
from know_expand.config import (
    BibliographyConfig,
    CentralityConfig,
    ConcurrencyConfig,
    Config,
    RateLimitConfig,
)
from know_expand.state import mark_stage_complete, stage_is_complete
from know_expand.stages import s7b_quality_eval


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_cfg(with_role: bool = True) -> Config:
    models = {"researcher": ["claude-3-haiku"]}
    if with_role:
        models["quality_evaluator"] = ["test-model"]
    return Config(
        bibliography=BibliographyConfig(),
        centrality=CentralityConfig(),
        rate_limits={"semantic_scholar": RateLimitConfig(max_rate=100, time_period=300)},
        concurrency=ConcurrencyConfig(),
        timeouts={"default": 30},
        adversarial_rounds={"survey": 1, "standard": 2, "deep": 3},
        boilerplate_stop_list=[],
        models=models,
    )


def _make_state(state_dir: Path, depth: str = "standard") -> dict:
    return {
        "run_id": "test_run",
        "state_dir": str(state_dir),
        "output_dir": str(state_dir / "output"),
        "input_path": "test.pdf",
        "depth": depth,
        "domain_ids": [],
    }


def _make_evaluation(score: float = 8.0, notes: str = "solid overall") -> QualityEvaluation:
    dim = lambda j: QualityDimensionScore(score=score, justification=j)
    return QualityEvaluation(
        citation_coverage=dim("covers claims"),
        citation_accuracy=dim("keys check out"),
        factual_accuracy=dim("looks correct"),
        synthesis_vs_enumeration=dim("synthesizes sources"),
        structural_organization=dim("flows well"),
        taxonomy_coherence=dim("matches graph"),
        readability_at_depth=dim("appropriate for depth"),
        terminology_consistency=dim("consistent terms"),
        comprehensiveness=dim("covers core nodes"),
        critical_analysis=dim("notes limitations"),
        frontier_novelty=dim("cites frontier work"),
        where_to_go_next_quality=dim("useful next steps"),
        overall_notes=notes,
    )


def _seed_state_dir(state_dir: Path) -> tuple[Path, Path, Path]:
    """Write taxonomy.json, graph.json, sections/, summaries/, audit/bibliography_*.json
    for two domains, plus a synthesis section. Returns (sections_dir, summaries_dir, audit_dir)."""
    taxonomy = {
        "domains": [
            {"id": "domain_a", "label": "Domain A"},
            {"id": "domain_b", "label": "Domain B"},
        ]
    }
    (state_dir / "taxonomy.json").write_text(json.dumps(taxonomy))

    graph = {
        "nodes": [
            {"id": "a1", "name": "Concept A1", "domain": "domain_a", "centrality": "core"},
            {"id": "a2", "name": "Concept A2", "domain": "domain_a", "centrality": "supporting"},
            {"id": "b1", "name": "Concept B1", "domain": "domain_b", "centrality": "core"},
        ],
        "edges": [{"from": "a1", "to": "b1", "type": "related"}],
        "domains": taxonomy["domains"],
    }
    (state_dir / "graph.json").write_text(json.dumps(graph))

    sections_dir = state_dir / "sections"
    sections_dir.mkdir()
    (sections_dir / "section_domain_a.md").write_text(
        "# Domain A\n\nSome content citing [@doe_2023_paper].\n"
    )
    (sections_dir / "section_domain_b.md").write_text("# Domain B\n\nSome content.\n")
    (sections_dir / "section_synthesis.md").write_text("# Cross-Domain Synthesis\n\nBridges A and B.\n")

    summaries_dir = state_dir / "summaries"
    summaries_dir.mkdir()
    (summaries_dir / "summary_domain_a.json").write_text(json.dumps({"domain_id": "domain_a"}))
    (summaries_dir / "summary_domain_b.json").write_text(json.dumps({"domain_id": "domain_b"}))
    (summaries_dir / "summary_synthesis.json").write_text(json.dumps({"domain_count": 2}))

    audit_dir = state_dir / "audit"
    audit_dir.mkdir()
    (audit_dir / "bibliography_domain_a.json").write_text(json.dumps([
        {"id": "doe_2023_paper", "bucket": "foundational"},
        {"id": "frontier_key", "bucket": "frontier"},
    ]))
    (audit_dir / "bibliography_domain_b.json").write_text(json.dumps([
        {"id": "smith_2022", "bucket": "foundational"},
    ]))

    return sections_dir, summaries_dir, audit_dir


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_scores_all_domains_and_synthesis(tmp_path):
    """One router.call per domain + one for synthesis; artifacts + rollup written."""
    cfg = _make_cfg()
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    _seed_state_dir(state_dir)
    state = _make_state(state_dir)

    evaluation = _make_evaluation()
    mock_router = AsyncMock()
    mock_router.call = AsyncMock(side_effect=[evaluation, evaluation, evaluation])

    emitted = []
    with (
        patch("know_expand.stages.s7b_quality_eval.make_router", return_value=mock_router),
        patch("know_expand.stages.s7b_quality_eval.emit", side_effect=emitted.append),
    ):
        await s7b_quality_eval.run(state, cfg)

    assert mock_router.call.call_count == 3

    audit_dir = state_dir / "audit"
    for domain_id in ("domain_a", "domain_b", "synthesis"):
        result_path = audit_dir / f"quality_eval_{domain_id}.json"
        assert result_path.exists(), f"missing {result_path}"
        record = json.loads(result_path.read_text())
        assert record["core_quality"] == pytest.approx(8.0)
        assert record["writing_quality"] == pytest.approx(8.0)
        assert record["content_depth"] == pytest.approx(8.0)
        assert record["weighted_total"] == pytest.approx(8.0)
        assert (audit_dir / f"quality_eval_{domain_id}.done").exists()

    rollup = (audit_dir / "quality_eval.md").read_text()
    assert "Domain A" in rollup
    assert "Domain B" in rollup
    assert "Cross-Domain Synthesis" in rollup
    assert "Weighted Total" in rollup

    pipeline = json.loads((state_dir / "pipeline.json").read_text())
    assert pipeline["stages"]["7b"]["status"] == "complete"

    scored_events = [e for e in emitted if e.get("event") == "quality_eval_scored"]
    assert len(scored_events) == 3
    for ev in scored_events:
        assert "weighted_total" in ev
        assert "justification" in ev


@pytest.mark.asyncio
async def test_synthesis_pass_sets_active_domain(tmp_path):
    """Regression: the synthesis-scope router.call must run under
    active_domain == "synthesis", not the last per-domain id left over from
    the per-domain loop — otherwise its cost/tokens get misattributed to
    that domain in model_usage.jsonl (see state.py's emit() for how
    llm_call_done events are tagged from the active_domain contextvar)."""
    from know_expand.state import active_domain

    cfg = _make_cfg()
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    _seed_state_dir(state_dir)
    state = _make_state(state_dir)

    evaluation = _make_evaluation()
    seen_domains = []

    async def fake_call(*args, **kwargs):
        seen_domains.append(active_domain.get())
        return evaluation

    mock_router = AsyncMock()
    mock_router.call = AsyncMock(side_effect=fake_call)

    with patch("know_expand.stages.s7b_quality_eval.make_router", return_value=mock_router):
        await s7b_quality_eval.run(state, cfg)

    assert seen_domains[-1] == "synthesis", (
        f"expected the last (synthesis) call to run under active_domain='synthesis', "
        f"got {seen_domains}"
    )


@pytest.mark.asyncio
async def test_skips_when_stage_already_complete(tmp_path):
    """Stage-level sentinel: a second full run makes no further router calls."""
    cfg = _make_cfg()
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    _seed_state_dir(state_dir)
    state = _make_state(state_dir)

    evaluation = _make_evaluation()
    mock_router = AsyncMock()
    mock_router.call = AsyncMock(side_effect=[evaluation, evaluation, evaluation])

    with patch("know_expand.stages.s7b_quality_eval.make_router", return_value=mock_router):
        await s7b_quality_eval.run(state, cfg)

    assert mock_router.call.call_count == 3
    assert stage_is_complete(state_dir, "7b")

    # Second run: stage already complete, should short-circuit immediately.
    with patch("know_expand.stages.s7b_quality_eval.make_router", return_value=mock_router):
        await s7b_quality_eval.run(state, cfg)

    assert mock_router.call.call_count == 3, "router should not be called again once stage is complete"


@pytest.mark.asyncio
async def test_per_domain_sentinel_skips_already_scored_domain(tmp_path):
    """If a domain's .done sentinel + artifact already exist, only the remaining
    domain + synthesis get scored (per-domain idempotency, not just stage-level)."""
    cfg = _make_cfg()
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    _seed_state_dir(state_dir)
    state = _make_state(state_dir)

    audit_dir = state_dir / "audit"
    pre_scored = {
        "domain_id": "domain_a",
        "domain_label": "Domain A",
        "core_quality": 9.0,
        "writing_quality": 9.0,
        "content_depth": 9.0,
        "weighted_total": 9.0,
        "dimension_scores": {},
        "justification": {},
        "overall_notes": "pre-scored",
    }
    (audit_dir / "quality_eval_domain_a.json").write_text(json.dumps(pre_scored))
    (audit_dir / "quality_eval_domain_a.done").touch()

    evaluation = _make_evaluation()
    mock_router = AsyncMock()
    # Only domain_b and synthesis should trigger a real LLM call.
    mock_router.call = AsyncMock(side_effect=[evaluation, evaluation])

    with patch("know_expand.stages.s7b_quality_eval.make_router", return_value=mock_router):
        await s7b_quality_eval.run(state, cfg)

    assert mock_router.call.call_count == 2
    rollup = (audit_dir / "quality_eval.md").read_text()
    assert "9.00" in rollup  # the pre-scored domain_a weighted total carries through


@pytest.mark.asyncio
async def test_non_blocking_on_llm_failure(tmp_path):
    """A router failure on one domain never raises and never stops the stage
    from completing (same philosophy as [NEEDS_CITATION] in S8)."""
    cfg = _make_cfg()
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    _seed_state_dir(state_dir)
    state = _make_state(state_dir)

    evaluation = _make_evaluation()
    mock_router = AsyncMock()
    mock_router.call = AsyncMock(side_effect=[RuntimeError("LLM exploded"), evaluation, evaluation])

    with patch("know_expand.stages.s7b_quality_eval.make_router", return_value=mock_router):
        await s7b_quality_eval.run(state, cfg)  # must not raise

    audit_dir = state_dir / "audit"
    # Failed domain: sentinel written (so it isn't retried forever) but no JSON result.
    assert (audit_dir / "quality_eval_domain_a.done").exists()
    assert not (audit_dir / "quality_eval_domain_a.json").exists()
    # Other domain + synthesis still scored.
    assert (audit_dir / "quality_eval_domain_b.json").exists()
    assert (audit_dir / "quality_eval_synthesis.json").exists()

    pipeline = json.loads((state_dir / "pipeline.json").read_text())
    assert pipeline["stages"]["7b"]["status"] == "complete"


@pytest.mark.asyncio
async def test_skips_when_no_quality_evaluator_role(tmp_path):
    """Without a `quality_evaluator` role in cfg.models, the stage skips
    cleanly (make_router would KeyError otherwise) and still marks complete."""
    cfg = _make_cfg(with_role=False)
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    _seed_state_dir(state_dir)
    state = _make_state(state_dir)

    with patch("know_expand.stages.s7b_quality_eval.make_router") as mock_make_router:
        await s7b_quality_eval.run(state, cfg)

    mock_make_router.assert_not_called()
    pipeline = json.loads((state_dir / "pipeline.json").read_text())
    assert pipeline["stages"]["7b"]["status"] == "complete"
    assert not (state_dir / "audit" / "quality_eval.md").exists()
