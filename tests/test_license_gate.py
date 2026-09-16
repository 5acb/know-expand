"""Tests for license-aware input gating in Stage 0."""

import json
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest

from know_expand.config import Config
from know_expand.stages import s0_ingest


@pytest.fixture(autouse=True)
def mock_spacy():
    class DummyDoc:
        def __init__(self, *args, **kwargs):
            self.noun_chunks = []
        def __iter__(self):
            return iter([])
    mock_nlp = MagicMock(return_value=DummyDoc())
    with patch("spacy.load", return_value=mock_nlp) as m:
        yield m


def _make_cfg() -> Config:
    from know_expand.config import (
        BibliographyConfig,
        CentralityConfig,
        ConcurrencyConfig,
        RateLimitConfig,
        NlpModelsConfig,
    )
    return Config(
        bibliography=BibliographyConfig(),
        centrality=CentralityConfig(),
        rate_limits={"semantic_scholar": RateLimitConfig(max_rate=100, time_period=300)},
        concurrency=ConcurrencyConfig(),
        timeouts={"default": 30},
        adversarial_rounds={"survey": 1},
        boilerplate_stop_list=[],
        models={"critic": ["claude-3-haiku"], "classifier": ["claude-3-haiku"]},
        nlp_models=NlpModelsConfig(spacy="en_core_web_sm", tokenizer="gpt2"),
    )


def _make_state(state_dir: Path, input_file: Path, bypass: bool = False) -> dict:
    return {
        "run_id": "test_license_run",
        "state_dir": str(state_dir),
        "output_dir": str(state_dir / "output"),
        "input_path": str(input_file),
        "depth": "survey",
        "domain_ids": [],
        "bypass_license_gate": bypass,
    }


@pytest.mark.asyncio
async def test_license_gate_passes_permissive(tmp_path):
    """Permissive documents should ingest without issues."""
    cfg = _make_cfg()
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    
    input_file = tmp_path / "permissive.txt"
    input_file.write_text("This is a permissive document with CC-BY open license. Standard research paper.")
    
    state = _make_state(state_dir, input_file, bypass=False)
    
    # Run should complete successfully (no exceptions raised)
    await s0_ingest.run(state, cfg)
    assert (state_dir / "source.txt").exists()


@pytest.mark.asyncio
async def test_license_gate_blocks_restrictive(tmp_path):
    """Restrictive documents (e.g. non-commercial, CC-BY-NC-ND) should block ingestion."""
    cfg = _make_cfg()
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    
    input_file = tmp_path / "restrictive.txt"
    input_file.write_text("This work is licensed under a CC BY-NC-ND license. Non-commercial use only.")
    
    state = _make_state(state_dir, input_file, bypass=False)
    
    # Running ingestion should raise a ValueError
    with pytest.raises(ValueError, match="Restrictive license terms found in document"):
        await s0_ingest.run(state, cfg)


@pytest.mark.asyncio
async def test_license_gate_bypasses_restrictive_when_flagged(tmp_path):
    """Restrictive documents should ingest successfully if bypass_license_gate is True."""
    cfg = _make_cfg()
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    
    input_file = tmp_path / "restrictive_bypass.txt"
    input_file.write_text("This work is licensed under a CC BY-NC-ND license. Non-commercial use only.")
    
    state = _make_state(state_dir, input_file, bypass=True)
    
    # Should ingest without raising an exception because bypass=True
    await s0_ingest.run(state, cfg)
    assert (state_dir / "source.txt").exists()
