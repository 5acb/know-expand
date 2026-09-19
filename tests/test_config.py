"""Tests for config.yaml and models.yaml loading via know_expand.config."""

from pathlib import Path

import pytest

from know_expand.config import (
    BibliographyConfig,
    CentralityConfig,
    Config,
    ConcurrencyConfig,
    RateLimitConfig,
    ResearchContextConfig,
    load_config,
)

_REPO_ROOT = Path(__file__).parent.parent


# ---------------------------------------------------------------------------
# Load live config files
# ---------------------------------------------------------------------------

def test_config_yaml_loads():
    """config.yaml parses without error and produces a valid Config object."""
    cfg = load_config(
        config_path=_REPO_ROOT / "config.yaml",
        models_path=_REPO_ROOT / "models.yaml",
    )
    assert isinstance(cfg, Config)


def test_bibliography_fractions():
    cfg = load_config(_REPO_ROOT / "config.yaml", _REPO_ROOT / "models.yaml")
    assert cfg.bibliography.foundational_fraction == pytest.approx(0.65)
    assert cfg.bibliography.depth_totals["survey"] == 10
    assert cfg.bibliography.depth_totals["standard"] == 30
    assert cfg.bibliography.depth_totals["deep"] == 50


def test_timeouts_dict_has_expected_keys():
    cfg = load_config(_REPO_ROOT / "config.yaml", _REPO_ROOT / "models.yaml")
    # Keys used in the codebase via cfg.timeouts.get(...)
    assert "http_async_seconds" in cfg.timeouts
    assert "openalex_seconds" in cfg.timeouts
    assert "http_fetch_seconds" in cfg.timeouts


def test_concurrency_is_dataclass_not_dict():
    """cfg.concurrency must be a dataclass with .default, not a raw dict.
    A raw dict would cause cfg.concurrency.default to raise AttributeError."""
    cfg = load_config(_REPO_ROOT / "config.yaml", _REPO_ROOT / "models.yaml")
    assert isinstance(cfg.concurrency.default, int)
    assert cfg.concurrency.default > 0


def test_models_yaml_has_required_roles():
    cfg = load_config(_REPO_ROOT / "config.yaml", _REPO_ROOT / "models.yaml")
    required_roles = {"researcher", "extractor", "classifier", "critic", "synthesizer", "assembler"}
    for role in required_roles:
        assert role in cfg.models, f"models.yaml missing role: {role}"
        assert len(cfg.models[role]) > 0, f"No models for role: {role}"


def test_adversarial_rounds_has_all_depths():
    cfg = load_config(_REPO_ROOT / "config.yaml", _REPO_ROOT / "models.yaml")
    assert "survey" in cfg.adversarial_rounds
    assert "standard" in cfg.adversarial_rounds
    assert "deep" in cfg.adversarial_rounds


def test_research_context_has_all_depths():
    cfg = load_config(_REPO_ROOT / "config.yaml", _REPO_ROOT / "models.yaml")
    rc = cfg.research_context
    for depth in ("survey", "standard", "deep"):
        assert depth in rc.sources_max_terms, f"sources_max_terms missing depth: {depth}"
        assert depth in rc.sources_max_per_term
        assert depth in rc.sources_max_chars


def test_rate_limits_semantic_scholar_present():
    cfg = load_config(_REPO_ROOT / "config.yaml", _REPO_ROOT / "models.yaml")
    assert "semantic_scholar" in cfg.rate_limits
    rl = cfg.rate_limits["semantic_scholar"]
    assert rl.max_rate > 0
    assert rl.time_period > 0


# ---------------------------------------------------------------------------
# Config dataclass construction (unit — no file I/O)
# ---------------------------------------------------------------------------

def _make_minimal_cfg() -> Config:
    return Config(
        bibliography=BibliographyConfig(),
        centrality=CentralityConfig(),
        rate_limits={"semantic_scholar": RateLimitConfig(max_rate=1, time_period=1)},
        concurrency=ConcurrencyConfig(default=4),
        timeouts={"http_async_seconds": 30},
        adversarial_rounds={"survey": 1, "standard": 2, "deep": 3},
        boilerplate_stop_list=["introduction", "conclusion"],
        models={"researcher": ["deepinfra/meta-llama/Llama-3.3-70B-Instruct-Turbo"]},
    )


def test_minimal_cfg_constructs():
    cfg = _make_minimal_cfg()
    assert cfg.concurrency.default == 4
    assert cfg.timeouts.get("http_async_seconds") == 30


def test_bibliography_default_fraction():
    bib = BibliographyConfig()
    assert bib.foundational_fraction == pytest.approx(0.65)


def test_centrality_defaults_match_config_yaml():
    """CentralityConfig defaults must match config.yaml values.
    If config.yaml changes, this test reminds us to update the dataclass too."""
    cfg = load_config(_REPO_ROOT / "config.yaml", _REPO_ROOT / "models.yaml")
    assert cfg.centrality.structural_weight == pytest.approx(2.0)
    assert cfg.centrality.alias_cosine_threshold == pytest.approx(0.92)
