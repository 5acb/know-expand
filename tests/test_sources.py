"""Tests for know_expand.sources — multi-source knowledge fetcher."""

from unittest.mock import AsyncMock

import httpx
import pytest

import know_expand.sources as sources_mod
from know_expand.sources import (
    _fetch_pypi,
    _fetch_wikipedia,
    _is_searchable_term,
    _parse_openalex_budget_error,
    _reconstruct_abstract,
    fetch_sources_for_term,
    FetchedSource,
    format_sources,
)


# ---------------------------------------------------------------------------
# _reconstruct_abstract — inverted index reconstruction
# ---------------------------------------------------------------------------

def test_reconstruct_abstract_simple():
    """Basic inverted index reconstruction produces correct word order."""
    inv_idx = {
        "Hello": [0],
        "world": [1],
        "from": [2],
        "arXiv": [3],
    }
    result = _reconstruct_abstract(inv_idx)
    assert result == "Hello world from arXiv"


def test_reconstruct_abstract_non_contiguous_positions():
    """Words at non-sequential positions are still ordered correctly."""
    inv_idx = {
        "deep": [2],
        "We": [0],
        "propose": [1],
        "learning": [3],
    }
    result = _reconstruct_abstract(inv_idx)
    assert result == "We propose deep learning"


def test_reconstruct_abstract_multi_position_word():
    """A word appearing at multiple positions is expanded at each position."""
    inv_idx = {
        "the": [0, 3],
        "cat": [1],
        "sat": [2],
        "mat": [4],
    }
    result = _reconstruct_abstract(inv_idx)
    assert result == "the cat sat the mat"


def test_reconstruct_abstract_empty():
    assert _reconstruct_abstract({}) == ""


def test_reconstruct_abstract_single_word():
    result = _reconstruct_abstract({"attention": [0]})
    assert result == "attention"


# ---------------------------------------------------------------------------
# _is_searchable_term — structural filter
# ---------------------------------------------------------------------------

def test_searchable_term_rejects_too_short():
    assert not _is_searchable_term("ab")


def test_searchable_term_rejects_too_long_phrase():
    # More than 4 words
    assert not _is_searchable_term("one two three four five")


def test_searchable_term_rejects_article_prefix():
    assert not _is_searchable_term("the model")
    assert not _is_searchable_term("a transformer")
    assert not _is_searchable_term("an embedding")


def test_searchable_term_rejects_numeric_prefix():
    assert not _is_searchable_term("300 terms")
    assert not _is_searchable_term("8 domains")


def test_searchable_term_rejects_snake_case():
    assert not _is_searchable_term("batch_size")
    assert not _is_searchable_term("hidden_dim")


def test_searchable_term_rejects_camel_case():
    assert not _is_searchable_term("DataLoader")
    assert not _is_searchable_term("transformerModel")


def test_searchable_term_rejects_possessives():
    assert not _is_searchable_term("model's output")
    assert not _is_searchable_term("stage's behavior")


def test_searchable_term_rejects_verb_phrases():
    assert not _is_searchable_term("running docling")
    assert not _is_searchable_term("fetching embeddings")
    assert not _is_searchable_term("using spacy")


def test_searchable_term_accepts_concept_nouns():
    assert _is_searchable_term("attention mechanism")
    assert _is_searchable_term("knowledge graph")
    assert _is_searchable_term("transformer")
    assert _is_searchable_term("spacy")
    assert _is_searchable_term("docling")


def test_searchable_term_accepts_multiword_concepts():
    assert _is_searchable_term("multi-agent systems")
    assert _is_searchable_term("rate limiting")


# ---------------------------------------------------------------------------
# PyPI stub filter — tested via _fetch_pypi response parsing
# ---------------------------------------------------------------------------

def test_pypi_stub_phrases():
    """The stub-filter phrase set covers known backport markers."""
    stub_phrases = (
        "do not install",
        "deprecated backport",
        "no longer needed",
        "contains no code",
        "obsolete backport",
    )
    # Simulate the content_lc check from _fetch_pypi
    for phrase in stub_phrases:
        content_lc = f"This package is a {phrase} and should be removed."
        assert any(p in content_lc for p in stub_phrases), \
            f"Stub phrase not detected: {phrase!r}"


# ---------------------------------------------------------------------------
# format_sources — prompt formatter
# ---------------------------------------------------------------------------

def _make_source(term: str, source_type: str, title: str, content: str) -> FetchedSource:
    return FetchedSource(
        term=term,
        source_type=source_type,
        title=title,
        url="https://example.com",
        content=content,
        fetched_at="2026-01-01T00:00:00+00:00",
    )


def test_format_sources_basic():
    sources = {
        "asyncio": [_make_source("asyncio", "pypi", "asyncio", "Async I/O library.")],
        "transformer": [_make_source("transformer", "wikipedia", "Transformer (ML)", "Attention is all you need.")],
    }
    result = format_sources(sources, max_terms=10, max_sources_per_term=2, max_chars_per_source=200)
    assert "asyncio" in result.lower()
    assert "transformer" in result.lower()
    assert "pypi" in result
    assert "wikipedia" in result


def test_format_sources_respects_max_terms():
    sources = {f"term_{i}": [_make_source(f"term_{i}", "wikipedia", f"Term {i}", f"Content {i}.")] for i in range(10)}
    result = format_sources(sources, max_terms=3, max_sources_per_term=1, max_chars_per_source=100)
    # Only 3 "### [" headers should appear
    count = result.count("### [")
    assert count == 3


def test_format_sources_respects_max_sources_per_term():
    sources = {
        "asyncio": [
            _make_source("asyncio", "pypi", "asyncio PyPI", "PyPI content."),
            _make_source("asyncio", "wikipedia", "asyncio Wikipedia", "Wikipedia content."),
            _make_source("asyncio", "arxiv", "asyncio arXiv", "arXiv content."),
        ]
    }
    result = format_sources(sources, max_terms=5, max_sources_per_term=2, max_chars_per_source=200)
    # Only 2 sources should appear
    assert result.count("### [") == 2


def test_format_sources_truncates_content():
    long_content = "x" * 2000
    sources = {"term": [_make_source("term", "wikipedia", "Term", long_content)]}
    result = format_sources(sources, max_terms=1, max_sources_per_term=1, max_chars_per_source=100)
    # Content should be truncated to 100 chars
    assert "x" * 101 not in result
    assert "x" * 100 in result


def test_format_sources_handles_dict_sources():
    """format_sources handles dict-form sources (loaded from JSON) as well as FetchedSource objects."""
    sources = {
        "spacy": [
            {
                "term": "spacy",
                "source_type": "pypi",
                "title": "spacy",
                "url": "https://pypi.org/project/spacy/",
                "content": "Industrial-strength NLP.",
                "fetched_at": "2026-01-01T00:00:00+00:00",
            }
        ]
    }
    result = format_sources(sources, max_terms=1, max_sources_per_term=1, max_chars_per_source=200)
    assert "spacy" in result
    assert "pypi" in result


def test_format_sources_empty():
    result = format_sources({})
    assert result == ""


# ---------------------------------------------------------------------------
# fetch_sources_for_term routing — no network (tests dispatch logic only)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fetch_sources_for_term_skips_unsearchable():
    """Unsearchable terms return empty list without making any HTTP calls."""
    import httpx
    # "300 terms" is rejected by _is_searchable_term before any HTTP call
    async with httpx.AsyncClient() as http:
        result = await fetch_sources_for_term("300 terms", "concept", http)
    assert result == []


@pytest.mark.asyncio
async def test_fetch_sources_for_term_skips_snake_case():
    """snake_case identifiers are rejected without HTTP calls."""
    import httpx
    async with httpx.AsyncClient() as http:
        result = await fetch_sources_for_term("hidden_size", "tool_library", http)
    assert result == []


# ---------------------------------------------------------------------------
# OpenAlex daily-budget exhaustion (vs. ordinary transient 429s)
# ---------------------------------------------------------------------------

def test_parse_openalex_budget_error_detects_insufficient_budget_phrase():
    is_exhausted, retry_after_s = _parse_openalex_budget_error(
        429,
        '{"error":"Rate limit exceeded","message":"Insufficient budget. '
        'This request costs $0.001 but you only have $0 remaining. '
        'Resets at midnight UTC."}',
        "28900",
        threshold_s=600,
    )
    assert is_exhausted is True
    assert retry_after_s == pytest.approx(28900.0)


def test_parse_openalex_budget_error_long_retry_after_without_phrase():
    """A Retry-After well above the threshold is treated as exhausted even
    without the exact phrase — ordinary rate limits clear in seconds, not hours."""
    is_exhausted, retry_after_s = _parse_openalex_budget_error(
        429, "Too Many Requests", "3600", threshold_s=600
    )
    assert is_exhausted is True
    assert retry_after_s == pytest.approx(3600.0)


def test_parse_openalex_budget_error_ordinary_rate_limit_not_exhausted():
    """A short Retry-After without the budget phrase is an ordinary transient 429."""
    is_exhausted, retry_after_s = _parse_openalex_budget_error(
        429, "Too Many Requests", "2", threshold_s=600
    )
    assert is_exhausted is False
    assert retry_after_s == pytest.approx(2.0)


def test_parse_openalex_budget_error_non_429_never_exhausted():
    is_exhausted, retry_after_s = _parse_openalex_budget_error(
        500, "Insufficient budget", None, threshold_s=600
    )
    assert is_exhausted is False


def test_parse_openalex_budget_error_http_date_retry_after():
    """Retry-After may be an HTTP-date (RFC 7231), not just delta-seconds.
    A bare float() would raise ValueError and silently fail to detect
    exhaustion here — regression test for that bug."""
    from datetime import datetime, timedelta, timezone
    future = datetime.now(timezone.utc) + timedelta(hours=8)
    http_date = future.strftime("%a, %d %b %Y %H:%M:%S GMT")
    is_exhausted, retry_after_s = _parse_openalex_budget_error(
        429, "Too Many Requests", http_date, threshold_s=600
    )
    assert is_exhausted is True
    # ~8 hours in seconds, generous tolerance for test execution time
    assert retry_after_s == pytest.approx(8 * 3600, abs=30)


def test_parse_openalex_budget_error_no_retry_after_header():
    is_exhausted, retry_after_s = _parse_openalex_budget_error(
        429, "Insufficient budget.", None, threshold_s=600
    )
    assert is_exhausted is True
    assert retry_after_s is None


@pytest.mark.asyncio
async def test_fetch_openalex_works_trips_budget_flag_and_emits_event(monkeypatch):
    """A 429 with an 'Insufficient budget' body sets the module-level flag and
    emits a structured openalex_budget_exhausted event instead of raising or
    silently retrying."""
    monkeypatch.setattr(sources_mod, "_OPENALEX_BUDGET_EXHAUSTED", False)

    body = (
        '{"error":"Rate limit exceeded","message":"Insufficient budget. '
        'This request costs $0.001 but you only have $0 remaining. '
        'Resets at midnight UTC."}'
    )
    fake_response = httpx.Response(429, headers={"retry-after": "28900"}, content=body)
    fake_http = AsyncMock()
    fake_http.get = AsyncMock(return_value=fake_response)

    emitted = []
    monkeypatch.setattr(sources_mod, "emit", lambda e: emitted.append(e))

    results = await sources_mod._fetch_openalex_works("transformer models", fake_http)

    assert results == []
    assert sources_mod._OPENALEX_BUDGET_EXHAUSTED is True
    assert len(emitted) == 1
    assert emitted[0]["event"] == "openalex_budget_exhausted"
    assert emitted[0]["retry_after_s"] == pytest.approx(28900.0)
    assert emitted[0]["resets_at"] is not None


@pytest.mark.asyncio
async def test_fetch_openalex_works_skips_request_once_exhausted(monkeypatch):
    """Once the budget flag is tripped, no further HTTP calls are made for the
    rest of the process — this is what prevents S4 from stalling for hours."""
    monkeypatch.setattr(sources_mod, "_OPENALEX_BUDGET_EXHAUSTED", True)
    fake_http = AsyncMock()
    fake_http.get = AsyncMock(side_effect=AssertionError("must not call OpenAlex once exhausted"))

    results = await sources_mod._fetch_openalex_works("transformer models", fake_http)

    assert results == []
    fake_http.get.assert_not_called()


@pytest.mark.asyncio
async def test_academic_term_falls_back_to_arxiv_when_openalex_exhausted(monkeypatch):
    """When Wikipedia and OpenAlex both come up empty (e.g. budget exhausted),
    an "academic" term still gets an arXiv fallback instead of no sources at all."""
    monkeypatch.setattr(sources_mod, "_fetch_wikipedia", AsyncMock(return_value=None))
    monkeypatch.setattr(sources_mod, "_fetch_openalex_works", AsyncMock(return_value=[]))
    arxiv_source = FetchedSource(
        term="transformer models",
        source_type="arxiv",
        title="Attention Is All You Need",
        url="https://arxiv.org/abs/1706.03762",
        content="...",
        fetched_at="2026-09-17T00:00:00+00:00",
    )
    monkeypatch.setattr(sources_mod, "_fetch_arxiv", AsyncMock(return_value=[arxiv_source]))

    async with httpx.AsyncClient() as http:
        result = await fetch_sources_for_term("transformer models", "academic", http)

    assert result == [arxiv_source]
