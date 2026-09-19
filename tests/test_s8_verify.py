"""Tests for the S8 additions on top of know_expand.stages.s8_verify's existing
structural citation audit (that original coverage lives in tests/test_s6_verify.py,
despite the filename mismatch — it predates this file).

Covers:
  - live Semantic Scholar abstract fetch (know_expand.bibliography.fetch_paper_abstract)
    is used only when the cached bibliography abstract is empty
  - the original "No abstract available for this citation." fallback still fires
    when the live fetch also comes back empty
  - the claim-verification call site is wired to ensemble_verify() with the
    verifier_proposer_a / verifier_proposer_b / verifier_adjudicator roles,
    not a single classifier_router.call()
"""

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from know_expand.agents.base import EnsembleVerdict
from know_expand.config import (
    BibliographyConfig,
    CentralityConfig,
    ConcurrencyConfig,
    Config,
    RateLimitConfig,
)
from know_expand.stages import s8_verify
from know_expand.stages.s8_verify import ClaimVerification, SentenceVerification


# ---------------------------------------------------------------------------
# Fixtures / helpers
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
        models={
            "verifier_proposer_a": ["model-a"],
            "verifier_proposer_b": ["model-b"],
            "verifier_adjudicator": ["model-c"],
        },
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


def _setup_state(tmp_path: Path, bib_entry: dict, sentence: str) -> dict:
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    audit_dir = state_dir / "audit"
    audit_dir.mkdir()
    sections_dir = state_dir / "sections"
    sections_dir.mkdir()
    (audit_dir / "bibliography_domain_a.json").write_text(json.dumps([bib_entry]))
    (sections_dir / "section_domain_a.md").write_text(f"# Domain A\n\n{sentence}\n")
    return _make_state(state_dir)


_AGREED_VERDICT = EnsembleVerdict(
    result=SentenceVerification(claims=[
        ClaimVerification(claim="x", relation="supports", reason="ok")
    ]),
    agreed=True,
    proposer_roles=["verifier_proposer_a", "verifier_proposer_b"],
    proposer_results=[],
)


def _report(state: dict) -> dict:
    return json.loads((Path(state["output_dir"]) / "verification_report.json").read_text())


# ---------------------------------------------------------------------------
# Live abstract fetch (tool-grounding)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_live_fetch_used_only_when_cached_abstract_empty(tmp_path):
    """fetch_paper_abstract is called when the cached abstract is empty."""
    cfg = _make_cfg()
    bib_entry = {
        "id": "smith_2023_title",
        "title": "Some Paper",
        "author": [{"family": "Smith"}],
        "issued": {"date-parts": [[2023]]},
        "bucket": "foundational",
        "abstract": "",
        "DOI": "10.1/xyz",
    }
    state = _setup_state(tmp_path, bib_entry, "The algorithm converges quickly [@smith_2023_title].")

    fetch_mock = AsyncMock(return_value="Live-fetched abstract text.")

    with patch("know_expand.stages.s8_verify.fetch_paper_abstract", fetch_mock), \
         patch("know_expand.stages.s8_verify.ensemble_verify", new=AsyncMock(return_value=_AGREED_VERDICT)), \
         patch("know_expand.stages.s8_verify.resolve_bibliography_links", return_value={}):
        await s8_verify.run(state, cfg)

    fetch_mock.assert_awaited_once()
    _, kwargs = fetch_mock.call_args
    assert kwargs["doi"] == "10.1/xyz"
    assert kwargs["title"] == "Some Paper"

    claim = _report(state)["claims"][0]
    assert claim["decomposed_claims"][0]["relation"] == "supports"


@pytest.mark.asyncio
async def test_live_fetch_not_called_when_cached_abstract_present(tmp_path):
    """A non-empty cached abstract is used directly — no live network call."""
    cfg = _make_cfg()
    bib_entry = {
        "id": "smith_2023_title",
        "title": "Some Paper",
        "author": [{"family": "Smith"}],
        "issued": {"date-parts": [[2023]]},
        "bucket": "foundational",
        "abstract": "Already cached abstract.",
        "DOI": "10.1/xyz",
    }
    state = _setup_state(tmp_path, bib_entry, "The algorithm converges quickly [@smith_2023_title].")

    fetch_mock = AsyncMock(return_value="Should never be used.")

    with patch("know_expand.stages.s8_verify.fetch_paper_abstract", fetch_mock), \
         patch("know_expand.stages.s8_verify.ensemble_verify", new=AsyncMock(return_value=_AGREED_VERDICT)), \
         patch("know_expand.stages.s8_verify.resolve_bibliography_links", return_value={}):
        await s8_verify.run(state, cfg)

    fetch_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_abstract_fallback_when_live_fetch_also_fails(tmp_path):
    """The original 'No abstract available' fallback still fires when the cached
    abstract is empty AND the live fetch also comes back empty — and the LLM
    ensemble is never invoked in that case (nothing to verify against)."""
    cfg = _make_cfg()
    bib_entry = {
        "id": "smith_2023_title",
        "title": "Some Paper",
        "author": [{"family": "Smith"}],
        "issued": {"date-parts": [[2023]]},
        "bucket": "foundational",
        "abstract": "",
        "DOI": None,
    }
    state = _setup_state(tmp_path, bib_entry, "The algorithm converges quickly [@smith_2023_title].")

    fetch_mock = AsyncMock(return_value="")  # live fetch also fails
    ensemble_mock = AsyncMock(return_value=_AGREED_VERDICT)

    with patch("know_expand.stages.s8_verify.fetch_paper_abstract", fetch_mock), \
         patch("know_expand.stages.s8_verify.ensemble_verify", ensemble_mock), \
         patch("know_expand.stages.s8_verify.resolve_bibliography_links", return_value={}):
        await s8_verify.run(state, cfg)

    fetch_mock.assert_awaited_once()
    ensemble_mock.assert_not_awaited()

    claim = _report(state)["claims"][0]
    assert claim["decomposed_claims"][0]["reason"] == "No abstract available for this citation."
    assert claim["decomposed_claims"][0]["relation"] == "neutral"


# ---------------------------------------------------------------------------
# ensemble_verify wiring
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ensemble_verify_wired_with_correct_roles(tmp_path):
    """The claim-verification call site uses the three verifier_* models.yaml
    roles via ensemble_verify(), not a single classifier_router.call()."""
    cfg = _make_cfg()
    bib_entry = {
        "id": "smith_2023_title",
        "title": "Some Paper",
        "author": [{"family": "Smith"}],
        "issued": {"date-parts": [[2023]]},
        "bucket": "foundational",
        "abstract": "This paper presents a fast converging algorithm.",
    }
    state = _setup_state(tmp_path, bib_entry, "The algorithm converges quickly [@smith_2023_title].")

    ensemble_mock = AsyncMock(return_value=_AGREED_VERDICT)

    with patch("know_expand.stages.s8_verify.ensemble_verify", ensemble_mock), \
         patch("know_expand.stages.s8_verify.resolve_bibliography_links", return_value={}):
        await s8_verify.run(state, cfg)

    ensemble_mock.assert_awaited_once()
    _, kwargs = ensemble_mock.call_args
    assert kwargs["proposer_roles"] == ["verifier_proposer_a", "verifier_proposer_b"]
    assert kwargs["adjudicator_role"] == "verifier_adjudicator"
    assert kwargs["schema"] is SentenceVerification
    prompt = kwargs["messages"][0]["content"]
    assert "converges quickly" in prompt
    assert "fast converging algorithm" in prompt


@pytest.mark.asyncio
async def test_ensemble_verify_error_falls_back_to_neutral(tmp_path):
    """If ensemble_verify raises (e.g. all models exhausted), the existing
    non-blocking fallback still records a neutral claim instead of failing
    the stage — verification results are logged, never a build failure."""
    cfg = _make_cfg()
    bib_entry = {
        "id": "smith_2023_title",
        "title": "Some Paper",
        "author": [{"family": "Smith"}],
        "issued": {"date-parts": [[2023]]},
        "bucket": "foundational",
        "abstract": "This paper presents a fast converging algorithm.",
    }
    state = _setup_state(tmp_path, bib_entry, "The algorithm converges quickly [@smith_2023_title].")

    ensemble_mock = AsyncMock(side_effect=RuntimeError("All models exhausted for role: verifier_proposer_a"))

    with patch("know_expand.stages.s8_verify.ensemble_verify", ensemble_mock), \
         patch("know_expand.stages.s8_verify.resolve_bibliography_links", return_value={}):
        await s8_verify.run(state, cfg)

    claim = _report(state)["claims"][0]
    assert claim["decomposed_claims"][0]["relation"] == "neutral"
    assert "Verification failed due to error" in claim["decomposed_claims"][0]["reason"]
