"""Tests for know_expand.bibliography — two-bucket Semantic Scholar fetcher."""

import json
import re
from unittest.mock import patch

import pytest

from know_expand.agents.schemas import CitationRecord
from know_expand.bibliography import (
    _drop_junk,
    _to_citation_record,
    fetch_anchors,
    fetch_bibliography,
    fetch_paper_abstract,
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
    venue: str = "",
) -> dict:
    return {
        "title": title,
        "year": year,
        "citationCount": citation_count,
        "externalIds": {"DOI": doi} if doi else {},
        "authors": [{"name": n} for n in (authors or ["Jane Smith"])],
        "abstract": abstract,
        "venue": venue,
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
            results, _ss_ids = await fetch_anchors("test domain", client, cfg, n=3)

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
            results, _ss_ids = await fetch_anchors("no doi domain", client, cfg, n=2)

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
            results, _ss_ids = await fetch_anchors("attention mechanism", client, cfg, n=3)

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


# ---------------------------------------------------------------------------
# Junk-venue filter (bibliography.junk_venues / junk_doi_prefixes)
# ---------------------------------------------------------------------------

def _junk_and_real_papers() -> tuple[list[dict], list[dict]]:
    """Mixed SS result list modelled on the 2026-09-17 run: self-uploads with
    inflated citation counts alongside genuine peer-reviewed frontier work."""
    junk = [
        _ss_paper("TA-14 Admissibility Before Execution Doctrine", 2026, 35,
                  doi="10.5281/zenodo.1234567", venue="Zenodo"),
        _ss_paper("SΔϕ-42 Alignment as Transition Governance", 2026, 22,
                  doi="10.2139/ssrn.4999999", venue="SSRN Electronic Journal"),
        _ss_paper("HNBP-CORE Cross-Platform Tamper-Evident Witness Logs", 2026, 18,
                  doi="10.21203/rs.3.rs-777777/v1", venue="Research Square"),
        _ss_paper("Agentic Sovereignty Ledger", 2026, 12,
                  doi="10.36227/techrxiv.9999", venue="TechRxiv"),
        # DOI-only signal: venue blank, DOI prefix identifies the platform
        _ss_paper("Untitled Zenodo Upload", 2026, 10,
                  doi="10.5281/ZENODO.7654321", venue=""),
        # publicationVenue.name only (flat `venue` empty)
        {**_ss_paper("Nested Venue Upload", 2026, 11, doi="10.9999/x1", venue=""),
         "publicationVenue": {"name": "Research  Square"}},
    ]
    real = [
        _ss_paper("Agent Passports for Accountable Autonomy", 2025, 9,
                  doi="10.1145/3700000.3700001", venue="ACM CCS"),
        _ss_paper("Provenance-Bound Tool Use in LLM Agents", 2025, 7,
                  doi="10.48550/arxiv.2505.01234", venue="arXiv.org"),
        _ss_paper("Runtime Policy Enforcement for Autonomous Agents", 2024, 14,
                  doi="10.1109/sp.2024.00001", venue="IEEE Symposium on Security and Privacy"),
    ]
    return junk, real


def test_drop_junk_filters_and_emits(cfg):
    junk, real = _junk_and_real_papers()
    papers = junk[:3] + real[:1] + junk[3:] + real[1:]

    with patch("know_expand.bibliography.emit") as emit:
        kept = _drop_junk(papers, cfg, query="agentic security governance", bucket="frontier")

    assert [p["title"] for p in kept] == [p["title"] for p in real]
    emit.assert_called_once()
    ev = emit.call_args.args[0]
    assert ev["event"] == "ss_junk_filtered"
    assert ev["bucket"] == "frontier"
    assert ev["query"] == "agentic security governance"
    assert ev["dropped"] == len(junk)
    assert ev["kept"] == len(real)
    assert len(ev["dropped_titles"]) == 5  # capped at five examples
    assert ev["dropped_titles"][0].startswith("TA-14")


def test_drop_junk_keeps_arxiv():
    """arXiv is frontier CS's home; it must never match the junk patterns."""
    cfg = _make_cfg()
    paper = _ss_paper("An arXiv Paper", 2025, 3, doi="10.48550/arxiv.2501.00001", venue="arXiv.org")
    with patch("know_expand.bibliography.emit"):
        assert _drop_junk([paper], cfg, query="q", bucket="frontier") == [paper]


def test_drop_junk_disabled():
    cfg = _make_cfg(junk_venue_filter=False)
    junk, real = _junk_and_real_papers()
    with patch("know_expand.bibliography.emit") as emit:
        kept = _drop_junk(junk + real, cfg, query="q", bucket="frontier")
    assert kept == junk + real
    emit.assert_not_called()


def test_drop_junk_custom_patterns():
    cfg = _make_cfg(junk_venues=[r"^preprints\.org$"], junk_doi_prefixes=[])
    a = _ss_paper("Preprint", 2025, 1, doi="10.1/a", venue="preprints.org")
    b = _ss_paper("Zenodo now allowed", 2025, 1, doi="10.5281/zenodo.1", venue="Zenodo")
    with patch("know_expand.bibliography.emit"):
        kept = _drop_junk([a, b], cfg, query="q", bucket="anchor")
    assert kept == [b]


@pytest.mark.asyncio
async def test_frontier_bucket_excludes_junk_venues(httpx_mock):
    """End-to-end: junk self-uploads outrank real frontier papers on MNCS but
    never reach the frontier bucket; the emitted event counts them."""
    foundational = [
        _ss_paper(f"Old Paper {i}", 2015, 1000 - i * 50, doi=f"10.1/old{i}", venue="NeurIPS")
        for i in range(8)
    ]
    junk, real = _junk_and_real_papers()
    frontier = junk + real

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
    with patch("know_expand.bibliography._ss_limiter", _NoOpLimiter()), \
         patch("know_expand.bibliography.emit") as emit:
        async with _httpx.AsyncClient() as client:
            results = await fetch_bibliography("agentic security", "survey", cfg, client)

    frontier_titles = {r.title for r in results if r.bucket == "frontier"}
    assert frontier_titles == {p["title"] for p in real}
    assert not (frontier_titles & {p["title"] for p in junk})
    # 6 foundational + 3 frontier: the short frontier bucket is NOT backfilled
    assert sum(1 for r in results if r.bucket == "foundational") == 6

    junk_events = [c.args[0] for c in emit.call_args_list if c.args[0]["event"] == "ss_junk_filtered"]
    by_bucket = {e["bucket"]: e for e in junk_events}
    assert by_bucket["frontier"]["dropped"] == len(junk)
    assert by_bucket["frontier"]["kept"] == len(real)
    assert by_bucket["foundational"]["dropped"] == 0


# ---------------------------------------------------------------------------
# fetch_paper_abstract — live abstract lookup for S8 tool-grounding
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fetch_paper_abstract_by_doi_success(httpx_mock, cfg):
    """A direct DOI lookup that returns an abstract is used without a title search."""
    httpx_mock.add_response(
        url=re.compile(r"https://api\.semanticscholar\.org/graph/v1/paper/DOI:.*"),
        json={"abstract": "Direct DOI abstract."},
    )

    import httpx as _httpx
    with patch("know_expand.bibliography._ss_limiter", _NoOpLimiter()):
        async with _httpx.AsyncClient() as client:
            result = await fetch_paper_abstract(client, cfg, doi="10.1/xyz", title="Some Paper")

    assert result == "Direct DOI abstract."
    # Only the DOI endpoint should have been hit — the title-search fallback
    # is skipped once the DOI lookup already returns a usable abstract.
    requests = httpx_mock.get_requests()
    assert len(requests) == 1
    assert "DOI:10.1/xyz" in str(requests[0].url)


@pytest.mark.asyncio
async def test_fetch_paper_abstract_falls_back_to_title_search(httpx_mock, cfg):
    """A 404/empty DOI lookup falls back to a title search via _ss_search."""
    httpx_mock.add_response(
        url=re.compile(r"https://api\.semanticscholar\.org/graph/v1/paper/DOI:.*"),
        status_code=404,
    )
    httpx_mock.add_response(
        url=re.compile(r"https://api\.semanticscholar\.org/graph/v1/paper/search.*"),
        content=_ss_response([_ss_paper("Some Paper", 2023, 10, abstract="Found via title search.")]),
    )

    import httpx as _httpx
    with patch("know_expand.bibliography._ss_limiter", _NoOpLimiter()):
        async with _httpx.AsyncClient() as client:
            result = await fetch_paper_abstract(client, cfg, doi="10.1/missing", title="Some Paper")

    assert result == "Found via title search."


@pytest.mark.asyncio
async def test_fetch_paper_abstract_no_doi_uses_title_search(httpx_mock, cfg):
    """With no DOI at all, goes straight to the title search."""
    httpx_mock.add_response(
        url=re.compile(r"https://api\.semanticscholar\.org/graph/v1/paper/search.*"),
        content=_ss_response([_ss_paper("Some Paper", 2023, 10, abstract="Title-only search result.")]),
    )

    import httpx as _httpx
    with patch("know_expand.bibliography._ss_limiter", _NoOpLimiter()):
        async with _httpx.AsyncClient() as client:
            result = await fetch_paper_abstract(client, cfg, doi=None, title="Some Paper")

    assert result == "Title-only search result."


@pytest.mark.asyncio
async def test_fetch_paper_abstract_all_sources_fail_returns_empty(httpx_mock, cfg):
    """Both the DOI lookup and the title-search fallback failing returns ""
    (never raises) so callers keep their existing 'no abstract' path."""
    httpx_mock.add_response(
        url=re.compile(r"https://api\.semanticscholar\.org/graph/v1/paper/DOI:.*"),
        status_code=404,
    )
    httpx_mock.add_response(
        url=re.compile(r"https://api\.semanticscholar\.org/graph/v1/paper/search.*"),
        content=_ss_response([]),
    )

    import httpx as _httpx
    with patch("know_expand.bibliography._ss_limiter", _NoOpLimiter()):
        async with _httpx.AsyncClient() as client:
            result = await fetch_paper_abstract(client, cfg, doi="10.1/missing", title="Nothing Found")

    assert result == ""


@pytest.mark.asyncio
async def test_fetch_paper_abstract_no_doi_no_title_returns_empty(cfg):
    """No identifying info at all — returns "" without making any request."""
    import httpx as _httpx
    with patch("know_expand.bibliography._ss_limiter", _NoOpLimiter()):
        async with _httpx.AsyncClient() as client:
            result = await fetch_paper_abstract(client, cfg, doi=None, title=None)

    assert result == ""
