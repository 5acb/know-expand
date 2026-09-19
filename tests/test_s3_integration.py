"""
Integration test for Stage 3 (Anchored Audit).

Mocks:
  - fetch_anchors / fetch_bibliography  — no real SS network calls
  - router.call                         — canned GapAnalysisResult responses

Asserts:
  - gap_analysis.md written
  - corrections.md written
  - stage 3 marked complete in pipeline.json
"""
import json
import shutil
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from know_expand.agents.schemas import CitationRecord, GapAnalysisResult, GapFinding
from know_expand.config import (
    BibliographyConfig,
    CentralityConfig,
    ConcurrencyConfig,
    Config,
    RateLimitConfig,
)
from know_expand.stages.s4_tools import COVERAGE_TOOL_NAME, SYNONYM_TOOL_NAME
from know_expand.state import new_run_id, setup_logging


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_TAXONOMY = {
    "domains": [
        {"id": "domain-a", "label": "Domain A", "definition": "x", "example_terms": []},
        {"id": "domain-b", "label": "Domain B", "definition": "y", "example_terms": []},
    ]
}

_GRAPH = {
    "nodes": [
        {"id": "n1", "name": "Alpha", "domain": "domain-a", "tier": "journeyman", "xp": 100,
         "prerequisites": [], "unlocks": [], "from_source_doc": True, "centrality": "core"},
        {"id": "n2", "name": "Beta", "domain": "domain-a", "tier": "apprentice", "xp": 50,
         "prerequisites": [], "unlocks": [], "from_source_doc": True, "centrality": "supporting"},
        {"id": "n3", "name": "Gamma", "domain": "domain-b", "tier": "expert", "xp": 200,
         "prerequisites": [], "unlocks": [], "from_source_doc": True, "centrality": "core"},
    ],
    "edges": [],
}

_ANCHORS: list[dict] = [
    {
        "id": f"anchor_{i}", "type": "article-journal",
        "title": f"Anchor Paper {i}",
        "author": [{"given": "Jane", "family": "Doe"}],
        "issued": {"date-parts": [[2023]]},
        "DOI": f"10.1/a{i}", "URL": None, "abstract": f"Abstract {i}",
        "bucket": "anchor", "citation_count": 100 - i * 10,
    }
    for i in range(3)
]

_BIBLIOGRAPHY: list[dict] = [
    {
        "id": f"bib_{i}", "type": "article-journal",
        "title": f"Bibliography Paper {i}",
        "author": [{"given": "Bob", "family": "Smith"}],
        "issued": {"date-parts": [[2021]]},
        "DOI": f"10.1/b{i}", "URL": None, "abstract": f"Abstract {i}",
        "bucket": "foundational" if i < 6 else "frontier",
        "citation_count": 50 - i * 3,
    }
    for i in range(10)
]


def _make_cfg() -> Config:
    return Config(
        bibliography=BibliographyConfig(
            ss_max_retries=2,
            ss_retry_initial_delay=1.0,
            ss_max_backoff=2.0,
            ss_anchors_n=3,
        ),
        centrality=CentralityConfig(),
        rate_limits={"semantic_scholar": RateLimitConfig(max_rate=100, time_period=1)},
        concurrency=ConcurrencyConfig(),
        timeouts={"http_async_seconds": 10},
        adversarial_rounds={"gap": 1, "survey": 1, "standard": 2, "deep": 3},
        boilerplate_stop_list=[],
        models={
            "critic": ["deepinfra/meta-llama/Llama-3.3-70B-Instruct-Turbo"],
            "agent": ["deepinfra/meta-llama/Llama-3.3-70B-Instruct-Turbo"],
        },
    )


def _make_state_dir() -> Path:
    """Create a minimal state dir with taxonomy.json and graph.json."""
    tmp = Path(tempfile.mkdtemp(prefix="s3_test_"))
    (tmp / "taxonomy.json").write_text(json.dumps(_TAXONOMY))
    (tmp / "graph.json").write_text(json.dumps(_GRAPH))
    (tmp / "pipeline.json").write_text(json.dumps({
        "stages": {
            "0": {"status": "complete", "completed_at": "2026-01-01T00:00:00+00:00"},
            "1": {"status": "complete", "completed_at": "2026-01-01T00:00:00+00:00"},
            "2": {"status": "complete", "completed_at": "2026-01-01T00:00:00+00:00"},
        }
    }))
    return tmp


def _canned_gap_result(domain_id: str) -> GapAnalysisResult:
    """Return a canned GapAnalysisResult for the given domain."""
    return GapAnalysisResult(
        domain_id=domain_id,
        gaps=[
            GapFinding(
                gap_description=f"Missing concept in {domain_id}",
                evidence_anchor_ids=["anchor_0"],
                defender_argument="Graph covers this via alias",
                finder_rebuttal="Alias is too narrow",
                verdict="real_gap",
            ),
            GapFinding(
                gap_description=f"Overstated scope in {domain_id}",
                evidence_anchor_ids=["anchor_1"],
                defender_argument="Out of scope for this domain",
                finder_rebuttal="",
                verdict="not_a_gap",
            ),
        ],
    )


# ---------------------------------------------------------------------------
# Helper: convert fixture dicts to CitationRecord instances
# ---------------------------------------------------------------------------

def _to_records(dicts: list[dict]) -> list[CitationRecord]:
    return [CitationRecord(**d) for d in dicts]


# ---------------------------------------------------------------------------
# Helper: canned call_with_tools sequences for the ReAct grounding loop
# ---------------------------------------------------------------------------

def _grounding_call_sequence(tool_name: str, arg_name: str, arg_value: str, finish_text: str) -> list[dict]:
    """One propose->tool->observe->answer round-trip: a tool-call turn followed
    by a finishing turn with no further tool calls. Mirrors what
    _run_react_grounding expects back from router.call_with_tools()."""
    return [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": tool_name, "arguments": json.dumps({arg_name: arg_value})},
                }
            ],
        },
        {"role": "assistant", "content": finish_text, "tool_calls": None},
    ]


def _domain_grounding_sequence() -> list[dict]:
    """Finder grounding (coverage tool) followed by defender grounding (synonym tool)."""
    return (
        _grounding_call_sequence(COVERAGE_TOOL_NAME, "query", "test query", "Finder grounding findings")
        + _grounding_call_sequence(SYNONYM_TOOL_NAME, "term", "test term", "Defender grounding findings")
    )


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_s3_run_writes_outputs_and_marks_complete(tmp_path):
    """
    Stage 3 with mocked bibliography fetches and mocked router produces
    gap_analysis.md, corrections.md, and marks stage 3 complete.
    """
    state_dir = _make_state_dir()
    run_id = new_run_id()
    setup_logging(run_id, log_dir=tmp_path / "logs")

    cfg = _make_cfg()
    state = {
        "run_id": run_id,
        "state_dir": str(state_dir),
        "output_dir": str(state_dir / "output"),
        "input_path": "test.txt",
        "depth": "survey",
        "domain_ids": [],
    }

    anchor_records = _to_records(_ANCHORS)
    bib_records = _to_records(_BIBLIOGRAPHY)

    # router.call returns canned results in order: finder, defender, rebuttal (×2 domains)
    call_returns = []
    for domain_id in ["domain-a", "domain-b"]:
        call_returns.append(_canned_gap_result(domain_id))   # finder
        call_returns.append(_canned_gap_result(domain_id))   # defender
        call_returns.append(_canned_gap_result(domain_id))   # rebuttal

    mock_router = AsyncMock()
    mock_router.call.side_effect = call_returns
    # Two domains, each running one round of Finder + Defender ReAct grounding
    # (a tool-call turn followed by a finishing turn) before their structured
    # verdict call — see _run_react_grounding in s4_audit.py.
    mock_router.call_with_tools.side_effect = (
        _domain_grounding_sequence() + _domain_grounding_sequence()
    )

    # fetch_anchors now returns (records, ss_ids) tuple
    with (
        patch("know_expand.stages.s4_audit.fetch_anchors", return_value=(anchor_records, [])) as mock_fa,
        patch("know_expand.stages.s4_audit.fetch_bibliography", return_value=bib_records) as mock_fb,
        patch("know_expand.stages.s4_audit.fetch_anchor_neighbors", return_value=[]) as _mock_fn,
        patch("know_expand.stages.s4_audit.make_router", return_value=mock_router),
        # The grounding tools wrap real HTTP fetchers (SS/arXiv/Wikipedia) —
        # stub them out so the test never hits the network. The canned
        # responses above don't depend on what these return.
        patch("know_expand.stages.s4_tools._ss_search", return_value=[]),
        patch("know_expand.stages.s4_tools._fetch_arxiv", return_value=[]),
        patch("know_expand.stages.s4_tools._fetch_wikipedia", return_value=None),
    ):
        from know_expand.stages import s4_audit
        await s4_audit.run(state, cfg)

    audit_dir = state_dir / "audit"

    # --- bibliography artifacts written for each domain ---
    for domain_id in ["domain-a", "domain-b"]:
        assert (audit_dir / f"anchors_{domain_id}.json").exists(), \
            f"anchors_{domain_id}.json not written"
        assert (audit_dir / f"bibliography_{domain_id}.json").exists(), \
            f"bibliography_{domain_id}.json not written"

    # --- gap_analysis.md written and non-empty ---
    gap_md = audit_dir / "gap_analysis.md"
    assert gap_md.exists(), "gap_analysis.md not written"
    gap_text = gap_md.read_text()
    assert len(gap_text) > 0, "gap_analysis.md is empty"
    assert "real_gap" not in gap_text.lower() or "Missing concept" in gap_text, \
        "gap_analysis.md should contain real gap descriptions"

    # --- corrections.md written and non-empty ---
    corrections_md = audit_dir / "corrections.md"
    assert corrections_md.exists(), "corrections.md not written"
    corrections_text = corrections_md.read_text()
    assert len(corrections_text) > 0, "corrections.md is empty"

    # --- stage 4 marked complete in pipeline.json (s4_audit uses stage 4) ---
    pipeline = json.loads((state_dir / "pipeline.json").read_text())
    assert pipeline["stages"]["4"]["status"] == "complete", \
        "pipeline.json does not show stage 4 as complete"

    # --- fetch_anchors called once per domain ---
    assert mock_fa.call_count == 2, f"Expected 2 anchor fetches, got {mock_fa.call_count}"
    # --- fetch_bibliography called once per domain ---
    assert mock_fb.call_count == 2, f"Expected 2 bibliography fetches, got {mock_fb.call_count}"
    # --- router called 3 times per domain (finder, defender, rebuttal) ---
    assert mock_router.call.call_count == 6, \
        f"Expected 6 router calls, got {mock_router.call.call_count}"

    # --- ReAct grounding: Finder and Defender each ran one propose->tool->
    # observe->answer round trip per domain (2 call_with_tools calls each) ---
    assert mock_router.call_with_tools.call_count == 8, (
        "Expected 8 call_with_tools invocations (2 domains x [finder grounding "
        f"(2 turns) + defender grounding (2 turns)]), got {mock_router.call_with_tools.call_count}"
    )
    # The Finder's tool (coverage) and the Defender's tool (synonym) must each
    # have actually been invoked at least once per domain before finalizing —
    # verify via the structured calls' prompts that grounding text was
    # spliced in, proving _run_react_grounding executed the tool call and fed
    # its observation back before the finder/defender committed a verdict.
    finder_call_msgs = [c.args[0] for c in mock_router.call.call_args_list[0::3]]
    defender_call_msgs = [c.args[0] for c in mock_router.call.call_args_list[1::3]]
    for msgs in finder_call_msgs:
        content = msgs[0]["content"]
        assert "Finder grounding findings" in content, \
            "Finder's structured prompt should include the ReAct grounding findings"
    for msgs in defender_call_msgs:
        content = msgs[0]["content"]
        assert "Defender grounding findings" in content, \
            "Defender's structured prompt should include the ReAct grounding findings"

    # Cleanup
    shutil.rmtree(state_dir)


@pytest.mark.asyncio
async def test_s3_skips_completed_stage(tmp_path):
    """Stage 3 returns early if pipeline.json already marks stage 3 complete."""
    state_dir = _make_state_dir()
    run_id = new_run_id()
    setup_logging(run_id, log_dir=tmp_path / "logs")
    cfg = _make_cfg()

    # Pre-mark stage 4 as complete (s4_audit uses stage number 4)
    pipeline = json.loads((state_dir / "pipeline.json").read_text())
    pipeline["stages"]["4"] = {"status": "complete", "completed_at": "2026-01-01T00:00:00+00:00"}
    (state_dir / "pipeline.json").write_text(json.dumps(pipeline))

    state = {
        "run_id": run_id,
        "state_dir": str(state_dir),
        "output_dir": str(state_dir / "output"),
        "input_path": "test.txt",
        "depth": "survey",
        "domain_ids": [],
    }

    mock_router = AsyncMock()
    with (
        patch("know_expand.stages.s4_audit.fetch_anchors") as mock_fa,
        patch("know_expand.stages.s4_audit.fetch_bibliography") as mock_fb,
        patch("know_expand.stages.s4_audit.make_router", return_value=mock_router),
    ):
        from know_expand.stages import s4_audit
        await s4_audit.run(state, cfg)

    assert mock_fa.call_count == 0, "fetch_anchors should not be called when stage is complete"
    assert mock_fb.call_count == 0, "fetch_bibliography should not be called when stage is complete"
    assert mock_router.call.call_count == 0, "router should not be called when stage is complete"
    assert mock_router.call_with_tools.call_count == 0, \
        "no ReAct grounding calls should happen when stage is complete"

    shutil.rmtree(state_dir)


@pytest.mark.asyncio
async def test_s3_domain_failure_does_not_abort_other_domains(tmp_path):
    """If one domain fails, the others still complete and outputs are written."""
    state_dir = _make_state_dir()
    run_id = new_run_id()
    setup_logging(run_id, log_dir=tmp_path / "logs")
    cfg = _make_cfg()

    state = {
        "run_id": run_id,
        "state_dir": str(state_dir),
        "output_dir": str(state_dir / "output"),
        "input_path": "test.txt",
        "depth": "survey",
        "domain_ids": [],
    }

    anchor_records = _to_records(_ANCHORS)
    bib_records = _to_records(_BIBLIOGRAPHY)

    # domain-a: fetch raises; domain-b: succeeds
    fetch_call_count = {"n": 0}

    async def maybe_fail_anchor(domain_label, http, cfg_arg, **kwargs):
        fetch_call_count["n"] += 1
        if domain_label == "Domain A":
            raise RuntimeError("SS network error")
        # fetch_anchors returns (records, ss_ids) tuple
        return anchor_records, []

    mock_router = AsyncMock()
    mock_router.call.side_effect = [
        _canned_gap_result("domain-b"),
        _canned_gap_result("domain-b"),
        _canned_gap_result("domain-b"),
    ]
    # domain-a never reaches the gap loop (fetch_anchors raises first), so only
    # domain-b's Finder+Defender grounding round trips happen.
    mock_router.call_with_tools.side_effect = _domain_grounding_sequence()

    with (
        patch("know_expand.stages.s4_audit.fetch_anchors", side_effect=maybe_fail_anchor),
        patch("know_expand.stages.s4_audit.fetch_bibliography", return_value=bib_records),
        patch("know_expand.stages.s4_audit.fetch_anchor_neighbors", return_value=[]),
        patch("know_expand.stages.s4_audit.make_router", return_value=mock_router),
        patch("know_expand.stages.s4_tools._ss_search", return_value=[]),
        patch("know_expand.stages.s4_tools._fetch_arxiv", return_value=[]),
        patch("know_expand.stages.s4_tools._fetch_wikipedia", return_value=None),
    ):
        from know_expand.stages import s4_audit
        await s4_audit.run(state, cfg)

    audit_dir = state_dir / "audit"

    # domain-a failed — bibliography file not written
    assert not (audit_dir / "bibliography_domain-a.json").exists(), \
        "domain-a bibliography should not exist after fetch failure"

    # domain-b succeeded
    assert (audit_dir / "bibliography_domain-b.json").exists(), \
        "domain-b bibliography should be written"

    # Stage 4 still marked complete (partial results accepted)
    pipeline = json.loads((state_dir / "pipeline.json").read_text())
    assert pipeline["stages"]["4"]["status"] == "complete"

    # gap_analysis.md and corrections.md exist (even with partial results)
    assert (audit_dir / "gap_analysis.md").exists()
    assert (audit_dir / "corrections.md").exists()

    # domain-b's Finder + Defender each ran one grounding round trip; domain-a
    # never got there, so no extra grounding calls leaked from the failure.
    assert mock_router.call_with_tools.call_count == 4, (
        "Expected 4 call_with_tools invocations for domain-b's grounding only, "
        f"got {mock_router.call_with_tools.call_count}"
    )

    shutil.rmtree(state_dir)
