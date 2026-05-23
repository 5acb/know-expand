"""Tests for know_expand.bibliography — two-bucket Semantic Scholar fetcher."""

import json
import re
from unittest.mock import patch

import pytest

from know_expand.agents.schemas import CitationRecord
from know_expand.bibliography import (
    _to_citation_record,
    fetch_anchors,
    fetch_bibliography,
)
from know_expand.config import BibliographyConfig, Config, CentralityConfig, ConcurrencyConfig, RateLimitConfig


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def cfg():
    return _make_cfg()


def _make_cfg(**bib_overrides) -> Config:
    bib = BibliographyConfig(**bib_overrides)
    return Config(
        bibliography=bib,
        centrality=CentralityConfig(),
        rate_limits={"semantic_scholar": RateLimitConfig(max_rate=100, time_period=300)},
        concurrency=ConcurrencyConfig(),
        timeouts={"default": 30},
        adversarial_rounds={"gap": 2},
        boilerplate_stop_list=[],
        models={"extractor": ["claude-3-haiku"]},
    )


def _ss_paper(
    title: str,
    year: int,
    citation_count: int,
    doi: str | None = None,
    authors: list[str] | None = None,
    abstract: str = "",
) -> dict:
    return {
        "title": title,
        "year": year,
        "citationCount": citation_count,
        "externalIds": {"DOI": doi} if doi else {},
        "authors": [{"name": n} for n in (authors or ["Jane Smith"])],
        "abstract": abstract,
    }


def _ss_response(papers: list[dict]) -> bytes:
    return json.dumps({"data": papers}).encode()


# ---------------------------------------------------------------------------
# No-op async context manager to replace _ss_limiter
# ---------------------------------------------------------------------------

class _NoOpLimiter:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        pass


# ---------------------------------------------------------------------------
# _to_citation_record
# ---------------------------------------------------------------------------

def test_ss_to_csl_valid():
    paper = _ss_paper(
        title="Efficient Memory Management for LLM Serving",
        year=2023,
        citation_count=412,
        doi="10.1145/123.456",
        authors=["Woosuk Kwon", "Zhuohan Li"],
        abstract="We describe PagedAttention.",
    )
    seen: set[str] = set()
    rec = _to_citation_record(paper, "foundational", seen)

    assert isinstance(rec, CitationRecord)
    assert rec.id == "li_2023_efficient"
    assert rec.bucket == "foundational"
    assert rec.citation_count == 412
    assert rec.DOI == "10.1145/123.456"
    assert len(rec.author) == 2
    assert rec.author[-1]["family"] == "Li"
    assert rec.issued == {"date-parts": [[2023]]}


def test_ss_to_csl_no_title():
    paper = _ss_paper(title="", year=2023, citation_count=5)
    seen: set[str] = set()
    rec = _to_citation_record(paper, "foundational", seen)
    assert rec.title == ""


# ---------------------------------------------------------------------------
# _deduplicate_by_doi — tested via fetch_bibliography / fetch_anchors
# (the dedup logic lives inline; we exercise it directly via fetch_anchors)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_deduplicate_by_doi(httpx_mock, cfg):
    """Two papers sharing a DOI: only the first (higher-cited) is returned."""
    papers = [
        _ss_paper("Paper Alpha", 2020, 100, doi="10.1/dup"),
        _ss_paper("Paper Beta", 2021, 80, doi="10.1/dup"),
        _ss_paper("Paper Gamma", 2019, 50, doi="10.1/unique"),
    ]
    httpx_mock.add_response(
        url=re.compile(r"https://api\.semanticscholar\.org/.*"),
        content=_ss_response(papers),
    )

    import httpx as _httpx
    with patch("know_expand.bibliography._ss_limiter", _NoOpLimiter()):
        async with _httpx.AsyncClient() as client:
            results = await fetch_anchors("test domain", client, cfg, n=3)

    dois = [r.DOI for r in results if r.DOI]
    assert dois.count("10.1/dup") == 1
    assert any(r.DOI == "10.1/unique" for r in results)


@pytest.mark.asyncio
async def test_deduplicate_doi_none_kept(httpx_mock, cfg):
    """Papers with DOI=None are never deduplicated against each other."""
    papers = [
        _ss_paper("No DOI First", 2020, 200, doi=None),
        _ss_paper("No DOI Second", 2021, 150, doi=None),
    ]
    httpx_mock.add_response(
        url=re.compile(r"https://api\.semanticscholar\.org/.*"),
        content=_ss_response(papers),
    )

    import httpx as _httpx
    with patch("know_expand.bibliography._ss_limiter", _NoOpLimiter()):
        async with _httpx.AsyncClient() as client:
            results = await fetch_anchors("no doi domain", client, cfg, n=2)

    assert len(results) == 2


# ---------------------------------------------------------------------------
# fetch_anchors
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fetch_anchors_returns_top_cited(httpx_mock, cfg):
    """fetch_anchors returns the top-n papers sorted by citationCount desc."""
    papers = [
        _ss_paper("Low Cited", 2018, 10),
        _ss_paper("Mid Cited", 2019, 500),
        _ss_paper("Top Cited", 2020, 9000),
        _ss_paper("Second Cited", 2021, 3000),
        _ss_paper("Almost Low", 2017, 50),
    ]
    httpx_mock.add_response(
        url=re.compile(r"https://api\.semanticscholar\.org/.*"),
        content=_ss_response(papers),
    )

    import httpx as _httpx
    with patch("know_expand.bibliography._ss_limiter", _NoOpLimiter()):
        async with _httpx.AsyncClient() as client:
            results = await fetch_anchors("attention mechanism", client, cfg, n=3)

    assert len(results) == 3
    counts = [r.citation_count for r in results]
    assert counts == sorted(counts, reverse=True)
    assert counts[0] == 9000


@pytest.mark.asyncio
async def test_frontier_papers_have_correct_bucket(httpx_mock):
    """Papers fetched into the frontier bucket carry bucket='frontier'."""
    foundational = [
        _ss_paper(f"Old Paper {i}", 2015, 1000 - i * 50, doi=f"10.1/old{i}")
        for i in range(8)
    ]
    frontier = [
        _ss_paper(f"New Paper {i}", 2024, 80 - i * 5, doi=f"10.1/new{i}")
        for i in range(8)
    ]

    httpx_mock.add_response(
        url=re.compile(r"https://api\.semanticscholar\.org/.*"),
        content=_ss_response(foundational),
    )
    httpx_mock.add_response(
        url=re.compile(r"https://api\.semanticscholar\.org/.*"),
        content=_ss_response(frontier),
    )

    import httpx as _httpx
    cfg = _make_cfg()
    with patch("know_expand.bibliography._ss_limiter", _NoOpLimiter()):
        async with _httpx.AsyncClient() as client:
            results = await fetch_bibliography("transformers", "survey", cfg, client)

    frontier_results = [r for r in results if r.bucket == "frontier"]
    assert len(frontier_results) > 0
    assert all(r.bucket == "frontier" for r in frontier_results)


# ---------------------------------------------------------------------------
# fetch_bibliography — bucket splits
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_bucket_split_survey(httpx_mock):
    """survey depth (total=10): 6 foundational + 4 frontier."""
    foundational = [
        _ss_paper(f"Foundational {i}", 2015 + i, 500 - i * 20, doi=f"10.1/f{i}")
        for i in range(8)
    ]
    frontier = [
        _ss_paper(f"Frontier {i}", 2024, 40 - i * 3, doi=f"10.1/r{i}")
        for i in range(8)
    ]

    httpx_mock.add_response(
        url=re.compile(r"https://api\.semanticscholar\.org/.*"),
        content=_ss_response(foundational),
    )
    httpx_mock.add_response(
        url=re.compile(r"https://api\.semanticscholar\.org/.*"),
        content=_ss_response(frontier),
    )

    import httpx as _httpx
    cfg = _make_cfg()
    assert cfg.bibliography.depth_totals["survey"] == 10
    assert cfg.bibliography.foundational_fraction == 0.65

    with patch("know_expand.bibliography._ss_limiter", _NoOpLimiter()):
        async with _httpx.AsyncClient() as client:
            results = await fetch_bibliography("neural networks", "survey", cfg, client)

    assert len(results) == 10
    n_found = sum(1 for r in results if r.bucket == "foundational")
    n_front = sum(1 for r in results if r.bucket == "frontier")
    assert n_found == 6
    assert n_front == 4


@pytest.mark.asyncio
async def test_bucket_split_standard(httpx_mock):
    """standard depth (total=30): 19 foundational + 11 frontier."""
    foundational = [
        _ss_paper(f"Foundational {i}", 2010 + i, 800 - i * 20, doi=f"10.1/sf{i}")
        for i in range(25)
    ]
    frontier = [
        _ss_paper(f"Frontier {i}", 2024, 100 - i * 5, doi=f"10.1/sr{i}")
        for i in range(15)
    ]

    httpx_mock.add_response(
        url=re.compile(r"https://api\.semanticscholar\.org/.*"),
        content=_ss_response(foundational),
    )
    httpx_mock.add_response(
        url=re.compile(r"https://api\.semanticscholar\.org/.*"),
        content=_ss_response(frontier),
    )

    import httpx as _httpx
    cfg = _make_cfg()
    assert cfg.bibliography.depth_totals["standard"] == 30

    with patch("know_expand.bibliography._ss_limiter", _NoOpLimiter()):
        async with _httpx.AsyncClient() as client:
            results = await fetch_bibliography("language models", "standard", cfg, client)

    assert len(results) == 30
    n_found = sum(1 for r in results if r.bucket == "foundational")
    n_front = sum(1 for r in results if r.bucket == "frontier")
    assert n_found == 19
    assert n_front == 11
