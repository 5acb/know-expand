"""Multi-source knowledge fetcher for know-expand.

Fetches Wikipedia, PyPI, and arXiv content for terms extracted from the pipeline.
All fetches are best-effort — exceptions are caught and logged, never propagated.
No API keys required.
"""

import asyncio
import logging
import re
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from typing import Literal

import httpx
from aiolimiter import AsyncLimiter
from pydantic import BaseModel

from know_expand.agents.base import _parse_retry_after
from know_expand.state import emit

_logger = logging.getLogger("know_expand.sources")

_ARXIV_NS = "http://www.w3.org/2005/Atom"

# Wikipedia requires a descriptive User-Agent; without it they return 403.
_USER_AGENT = "know-expand/0.1 (research document expander; open-source) python-httpx"
_WIKI_HEADERS = {"User-Agent": _USER_AGENT, "Accept": "application/json"}


class FetchedSource(BaseModel):
    term: str
    source_type: Literal["wikipedia", "pypi", "arxiv", "openalex"]
    title: str
    url: str
    content: str   # truncated excerpt, max ~1500 chars
    fetched_at: str  # ISO timestamp


# OpenAlex allows 10 req/s for polite pool (with email in User-Agent), but
# separately enforces a small **daily USD budget** per caller ($0 free budget
# observed 2026-09) that a 429 with an "Insufficient budget" body signals —
# distinct from an ordinary rate-limit 429 that clears in seconds. Once hit,
# no request will succeed again until the budget resets (~UTC midnight), so
# we stop calling OpenAlex for the rest of the run instead of retrying.
# Lazy-initialised so the module can be imported without a Config in scope.
_OPENALEX_LIMITER: AsyncLimiter | None = None
_OPENALEX_BUDGET_EXHAUSTED = False


def _get_openalex_limiter() -> AsyncLimiter:
    global _OPENALEX_LIMITER
    if _OPENALEX_LIMITER is None:
        _OPENALEX_LIMITER = AsyncLimiter(10, 1)
    return _OPENALEX_LIMITER


def _parse_openalex_budget_error(
    status_code: int,
    body_text: str,
    retry_after_header: str | None,
    threshold_s: float,
) -> tuple[bool, float | None]:
    """Distinguish a terminal daily-budget exhaustion from an ordinary transient 429.

    Returns (is_budget_exhausted, retry_after_seconds). A budget exhaustion is
    either an explicit "Insufficient budget" phrase in the response body, or a
    429 with a Retry-After longer than `threshold_s` (an ordinary rate-limit
    429 clears in seconds; a multi-hour Retry-After means "come back tomorrow").

    Retry-After may be either delta-seconds or an HTTP-date (RFC 7231) — uses
    the shared `_parse_retry_after()` (agents/base.py) so both forms parse
    correctly instead of only recognizing the numeric form.
    """
    retry_after_s: float | None = None
    if retry_after_header:
        retry_after_s = float(_parse_retry_after(retry_after_header))

    if status_code != 429:
        return False, retry_after_s

    if "insufficient budget" in body_text.lower():
        return True, retry_after_s
    if retry_after_s is not None and retry_after_s > threshold_s:
        return True, retry_after_s
    return False, retry_after_s


_OPENALEX_HEADERS = {
    "User-Agent": "know-expand/1.0 (mailto:research@example.com)",
    "Accept": "application/json",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _strip_markup(text: str) -> str:
    """Very light markup stripping — removes reStructuredText/Markdown/HTML noise."""
    # Remove RST/Sphinx directives
    text = re.sub(r"\.\.\s+\w[^:\n]*::[^\n]*\n", "", text)
    # Remove HTML tags
    text = re.sub(r"<[^>]+>", "", text)
    # Collapse multiple blank lines
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _is_searchable_term(term: str) -> bool:
    """Return False for terms that are structurally unsuitable as external search queries.

    Uses only document-agnostic structural rules — no hardcoded domain vocabulary,
    so the same filter works for any input document.
    """
    t = term.strip()
    tl = t.lower()

    # Too short or too long
    if len(t) < 3 or len(t.split()) > 4:
        return False

    # Phrases starting with determiners/articles — sentence fragments, not concepts
    if re.match(r'^(a |an |the |every |any |all |some |each |this |that |these |those )', tl):
        return False

    # "N <noun>" patterns — metrics/counts, not searchable concepts
    # e.g. "300 terms", "24 months", "8 domains"
    if re.match(r'^\d+\s+\w+', tl):
        return False

    # Possessive forms — refer to something else, not a standalone concept
    # e.g. "stage 5's", "model's output"
    if re.search(r"'s\b", tl):
        return False

    # snake_case or camelCase — almost certainly a variable/identifier artifact
    if re.search(r'[a-z]_[a-z]', t) or re.search(r'[a-z][A-Z]', t):
        return False

    # Verb phrases — action descriptions extracted as terms
    # e.g. "running docling", "mapping chunks", "writing sections"
    if re.match(r'^(using|running|writing|reading|loading|saving|mapping|fetching|'
                r'building|parsing|generating|computing|extracting|merging|'
                r'resolving|validating|processing|producing|inserting)\b', tl):
        return False

    return True


# ---------------------------------------------------------------------------
# Per-source fetchers
# ---------------------------------------------------------------------------

async def _fetch_wikipedia(term: str, http: httpx.AsyncClient) -> "FetchedSource | None":
    """Search Wikipedia and return a plain-text extract for the best match."""
    try:
        search_url = (
            "https://en.wikipedia.org/w/api.php"
            "?action=opensearch"
            f"&search={urllib.parse.quote(term)}"
            "&limit=3&format=json"
        )
        resp = await http.get(search_url, headers=_WIKI_HEADERS, timeout=10.0)
        resp.raise_for_status()
        data = resp.json()

        # OpenSearch returns [query, titles, descriptions, urls]
        titles: list[str] = data[1] if len(data) > 1 else []
        if not titles:
            return None

        # Try each candidate title; reject disambiguation mismatches
        term_norm = re.sub(r"[^a-z0-9 ]", "", term.lower())
        for title in titles:
            encoded_title = urllib.parse.quote(title.replace(" ", "_"))
            summary_url = (
                f"https://en.wikipedia.org/api/rest_v1/page/summary/{encoded_title}"
            )
            try:
                sresp = await http.get(summary_url, headers=_WIKI_HEADERS, timeout=10.0)
                if sresp.status_code == 404:
                    continue
                sresp.raise_for_status()
                sdata = sresp.json()
                extract: str = sdata.get("extract", "")
                if len(extract) < 80:
                    continue  # disambiguation page or stub

                # Relevance gate: reject clear disambiguation mismatches like
                # "docling" → "Docking and berthing of spacecraft".
                # Three-way accept: term in combined text, OR title starts with
                # term prefix (e.g. "asyncio" → "Asynchronous I/O"), OR any
                # significant word (≥5 chars) from the term appears in the title.
                term_lower = term.replace("-", " ").replace("_", " ").lower()
                combined_lower = (sdata.get("title", "") + " " + extract[:600]).lower()
                title_lower_words = re.sub(r"[^a-z0-9 ]", " ", title.lower()).split()
                sig_words = [w for w in term_lower.split() if len(w) >= 5]
                title_first = title_lower_words[0] if title_lower_words else ""
                accepted = (
                    term_lower in combined_lower                            # exact match
                    or term_lower.replace(" ", "") in combined_lower.replace(" ", "")  # merged form
                    or title_first.startswith(term_lower.split()[0][:4])   # prefix: asyncio→asynchronous
                    or (sig_words and any(w in combined_lower for w in sig_words))
                )
                if not accepted:
                    _logger.debug("wikipedia relevance rejected %r → %r", term, title)
                    continue

                page_url = sdata.get("content_urls", {}).get("desktop", {}).get("page", summary_url)
                return FetchedSource(
                    term=term,
                    source_type="wikipedia",
                    title=sdata.get("title", title),
                    url=page_url,
                    content=extract[:1500],
                    fetched_at=_now_iso(),
                )
            except Exception as exc:
                _logger.debug("wikipedia summary fetch failed for %r: %s", title, exc)
                continue
    except Exception as exc:
        _logger.debug("wikipedia search failed for %r: %s", term, exc)
    return None


async def _fetch_pypi(term: str, http: httpx.AsyncClient) -> "FetchedSource | None":
    """Fetch PyPI package metadata for a term."""
    package = term.lower().replace(" ", "-")
    url = f"https://pypi.org/pypi/{urllib.parse.quote(package)}/json"
    try:
        resp = await http.get(url, timeout=10.0)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        data = resp.json()
        info = data.get("info", {})
        name: str = info.get("name", package)
        summary: str = info.get("summary", "")
        description: str = info.get("description", "")
        # Strip markup and truncate description
        description_clean = _strip_markup(description)
        content_parts = []
        if summary:
            content_parts.append(f"{name}: {summary}")
        if description_clean:
            content_parts.append(description_clean[:1200])
        content = "\n\n".join(content_parts)[:1500]
        # Reject stub/backport packages that say they contain no real code
        content_lc = content.lower()
        if any(phrase in content_lc for phrase in (
            "do not install", "deprecated backport", "no longer needed",
            "contains no code", "obsolete backport",
        )):
            _logger.debug("pypi stub rejected for %r", term)
            return None
        if not content:
            return None
        return FetchedSource(
            term=term,
            source_type="pypi",
            title=name,
            url=f"https://pypi.org/project/{urllib.parse.quote(package)}/",
            content=content,
            fetched_at=_now_iso(),
        )
    except Exception as exc:
        _logger.debug("pypi fetch failed for %r: %s", term, exc)
        return None


async def _fetch_arxiv(
    query: str, http: httpx.AsyncClient, domain_context: str = "", max_results: int = 3
) -> "list[FetchedSource]":
    """Fetch arXiv abstracts for a query.

    Uses `ti+abs` field search which returns more relevant results than bare `all:`.
    Multi-word queries are space-joined (Lucene AND within field).

    Short terms (≤2 words) are enriched with domain_context to avoid off-topic results
    (e.g. "domain" alone → astronomy; "domain NLP" → relevant ML papers).
    """
    # For multi-word queries search title OR abstract; single words use all fields.
    # Pass plain strings — httpx params= handles URL encoding automatically.
    words = query.strip().split()

    # Enrich short/generic queries with domain context to improve relevance
    effective_query = query
    if domain_context and len(words) <= 2:
        effective_query = f"{query} {domain_context}"

    if len(effective_query.strip().split()) > 1:
        arxiv_query = f"ti:{effective_query} OR abs:{effective_query}"
    else:
        arxiv_query = f"all:{effective_query}"
    results: list[FetchedSource] = []
    try:
        resp = await http.get(
            "https://export.arxiv.org/api/query",
            params={"search_query": arxiv_query, "max_results": max_results, "sortBy": "relevance"},
            timeout=20.0,
        )
        resp.raise_for_status()
        root = ET.fromstring(resp.text)

        for entry in root.findall(f"{{{_ARXIV_NS}}}entry"):
            title_el = entry.find(f"{{{_ARXIV_NS}}}title")
            summary_el = entry.find(f"{{{_ARXIV_NS}}}summary")
            link_el = entry.find(f'{{{_ARXIV_NS}}}link[@type="text/html"]')
            if link_el is None:
                link_el = entry.find(f"{{{_ARXIV_NS}}}id")

            title = title_el.text.strip() if title_el is not None and title_el.text else ""
            abstract = summary_el.text.strip() if summary_el is not None and summary_el.text else ""
            link = link_el.get("href", "") if link_el is not None else ""
            # arxiv id element is plain text, not href
            if not link and link_el is not None and link_el.text:
                link = link_el.text.strip()

            if not title and not abstract:
                continue

            results.append(FetchedSource(
                term=query,
                source_type="arxiv",
                title=title,
                url=link,
                content=abstract[:800],
                fetched_at=_now_iso(),
            ))
    except Exception as exc:
        _logger.debug("arxiv fetch failed for %r: %s", query, exc)
    return results


def _reconstruct_abstract(inv_idx: dict) -> str:
    """Reconstruct plaintext from an OpenAlex abstract_inverted_index."""
    if not inv_idx:
        return ""
    words = sorted(
        (pos, word)
        for word, positions in inv_idx.items()
        for pos in positions
    )
    return " ".join(w for _, w in words)


async def _fetch_openalex_works(
    term: str, http: httpx.AsyncClient, budget_retry_after_threshold_s: float = 600.0
) -> "list[FetchedSource]":
    """Fetch top cited works from OpenAlex for a term, returning up to 2 results.

    Once a daily-budget exhaustion is detected (see _parse_openalex_budget_error),
    OpenAlex is skipped for the rest of the process — no more requests are sent
    until the process restarts.
    """
    global _OPENALEX_BUDGET_EXHAUSTED
    results: list[FetchedSource] = []
    if _OPENALEX_BUDGET_EXHAUSTED:
        return results
    try:
        limiter = _get_openalex_limiter()
        async with limiter:
            resp = await http.get(
                "https://api.openalex.org/works",
                params={
                    "search": term,
                    "filter": "has_abstract:true",
                    "sort": "cited_by_count:desc",
                    "per-page": 3,
                },
                headers=_OPENALEX_HEADERS,
                timeout=15.0,
            )
            if resp.status_code == 429:
                is_budget_exhausted, retry_after_s = _parse_openalex_budget_error(
                    resp.status_code,
                    resp.text,
                    resp.headers.get("retry-after"),
                    budget_retry_after_threshold_s,
                )
                if is_budget_exhausted:
                    _OPENALEX_BUDGET_EXHAUSTED = True
                    resets_at = (
                        (datetime.now(timezone.utc) + timedelta(seconds=retry_after_s)).isoformat()
                        if retry_after_s is not None
                        else None
                    )
                    emit({
                        "event": "openalex_budget_exhausted",
                        "term": term,
                        "retry_after_s": retry_after_s,
                        "resets_at": resets_at,
                    })
                    return results
            resp.raise_for_status()
            data = resp.json()

        for work in data.get("results", []):
            title = work.get("title") or ""
            inv_idx = work.get("abstract_inverted_index") or {}
            abstract_text = _reconstruct_abstract(inv_idx)
            if not abstract_text:
                continue
            doi = work.get("doi") or ""
            results.append(FetchedSource(
                term=term,
                source_type="openalex",
                title=title,
                url=doi,
                content=abstract_text[:600],
                fetched_at=_now_iso(),
            ))
            if len(results) >= 2:
                break
    except Exception as exc:
        _logger.debug("openalex fetch failed for %r: %s", term, exc)
    return results


# ---------------------------------------------------------------------------
# Dispatch by term type
# ---------------------------------------------------------------------------

async def fetch_sources_for_term(
    term: str,
    term_type: str,
    http: httpx.AsyncClient,
    domain_label: str = "",
    openalex_budget_retry_after_threshold_s: float = 600.0,
) -> "list[FetchedSource]":
    """Fetch knowledge sources for a single term based on its type.

    term_type:
      "tool_library" -> PyPI first, then Wikipedia
      "concept"      -> Wikipedia, then OpenAlex fallback, then arXiv (2 papers)
      "academic"     -> Wikipedia, then OpenAlex fallback, then arXiv (2 papers)
                        if OpenAlex also came up empty (e.g. daily budget exhausted)

    domain_label: passed to arXiv fetcher to enrich short/generic queries.
    """
    if not _is_searchable_term(term):
        _logger.debug("skipping unsearchable term %r (type=%s)", term, term_type)
        return []

    sources: list[FetchedSource] = []

    if term_type == "tool_library":
        pypi = await _fetch_pypi(term, http)
        if pypi:
            sources.append(pypi)
        wiki = await _fetch_wikipedia(term, http)
        if wiki:
            sources.append(wiki)

    elif term_type == "concept":
        wiki = await _fetch_wikipedia(term, http)
        if wiki:
            sources.append(wiki)
        else:
            oa_results = await _fetch_openalex_works(
                term, http, openalex_budget_retry_after_threshold_s
            )
            sources.extend(oa_results)
        arxiv_results = await _fetch_arxiv(term, http, domain_context=domain_label, max_results=2)
        sources.extend(arxiv_results)

    else:  # "academic" or fallback
        wiki = await _fetch_wikipedia(term, http)
        if wiki:
            sources.append(wiki)
        else:
            oa_results = await _fetch_openalex_works(
                term, http, openalex_budget_retry_after_threshold_s
            )
            sources.extend(oa_results)
            if not oa_results:
                # OpenAlex empty (no results, or daily budget exhausted) — fall
                # back to arXiv rather than leaving the term with no sources at all.
                arxiv_results = await _fetch_arxiv(
                    term, http, domain_context=domain_label, max_results=2
                )
                sources.extend(arxiv_results)

    return sources


async def fetch_sources_for_terms(
    terms: list[tuple[str, str]],
    http: httpx.AsyncClient,
    concurrency: int = 4,
    domain_label: str = "",
    openalex_budget_retry_after_threshold_s: float = 600.0,
) -> "dict[str, list[FetchedSource]]":
    """Fetch knowledge sources for multiple terms concurrently.

    Args:
        terms: List of (term_name, term_type) pairs.
        http: Shared async HTTP client.
        concurrency: Maximum parallel requests.
        domain_label: Domain context string (e.g. "NLP", "computer vision") used to
            enrich short/generic arXiv queries for concept-type terms.
        openalex_budget_retry_after_threshold_s: passed through to the OpenAlex
            fetcher's budget-exhaustion detection (config.yaml
            timeouts.openalex_budget_retry_after_threshold_s).

    Returns:
        Dict mapping term_name -> list of FetchedSource. Terms with no results
        are omitted. Exceptions are swallowed silently.
    """
    sem = asyncio.Semaphore(concurrency)
    results: dict[str, list[FetchedSource]] = {}

    async def _fetch_one(term: str, term_type: str) -> None:
        async with sem:
            try:
                fetched = await fetch_sources_for_term(
                    term,
                    term_type,
                    http,
                    domain_label=domain_label,
                    openalex_budget_retry_after_threshold_s=openalex_budget_retry_after_threshold_s,
                )
                if fetched:
                    results[term] = fetched
            except Exception as exc:
                _logger.debug("fetch_sources_for_term failed for %r: %s", term, exc)

    await asyncio.gather(*[_fetch_one(t, tt) for t, tt in terms], return_exceptions=True)
    return results


# ---------------------------------------------------------------------------
# Formatting helper (used by both s4 and s5)
# ---------------------------------------------------------------------------

def format_sources(
    term_sources: dict,
    max_terms: int = 25,
    max_sources_per_term: int = 2,
    max_chars_per_source: int = 600,
) -> str:
    """Format fetched sources into a prompt-ready string.

    Caps output at roughly 30k chars total budget.
    """
    lines: list[str] = []
    term_count = 0
    for term, sources in term_sources.items():
        if term_count >= max_terms:
            break
        shown = 0
        for src in sources:
            if shown >= max_sources_per_term:
                break
            # src may be a dict (loaded from JSON) or FetchedSource
            if isinstance(src, dict):
                source_type = src.get("source_type", "")
                title = src.get("title", "")
                url = src.get("url", "")
                content = src.get("content", "")
            else:
                source_type = src.source_type
                title = src.title
                url = src.url
                content = src.content
            lines.append(f"### [{source_type}] {title}")
            lines.append(f"Source: {url}")
            lines.append(content[:max_chars_per_source])
            lines.append("")
            shown += 1
        if shown > 0:
            term_count += 1
    return "\n".join(lines)
