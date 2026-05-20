"""Multi-source knowledge fetcher for doc-expand.

Fetches Wikipedia, PyPI, and arXiv content for terms extracted from the pipeline.
All fetches are best-effort — exceptions are caught and logged, never propagated.
No API keys required.
"""

import asyncio
import logging
import re
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Literal

import httpx
from pydantic import BaseModel

_logger = logging.getLogger("doc_expand.sources")

_ARXIV_NS = "http://www.w3.org/2005/Atom"

# Wikipedia requires a descriptive User-Agent; without it they return 403.
_USER_AGENT = "doc-expand/0.1 (research document expander; open-source) python-httpx"
_WIKI_HEADERS = {"User-Agent": _USER_AGENT, "Accept": "application/json"}


class FetchedSource(BaseModel):
    term: str
    source_type: Literal["wikipedia", "pypi", "arxiv"]
    title: str
    url: str
    content: str   # truncated excerpt, max ~1500 chars
    fetched_at: str  # ISO timestamp


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
    query: str, http: httpx.AsyncClient, max_results: int = 3
) -> "list[FetchedSource]":
    """Fetch arXiv abstracts for a query.

    Uses `ti+abs` field search which returns more relevant results than bare `all:`.
    Multi-word queries are space-joined (Lucene AND within field).
    """
    # For multi-word queries search title OR abstract; single words use all fields.
    # Pass plain strings — httpx params= handles URL encoding automatically.
    words = query.strip().split()
    if len(words) > 1:
        arxiv_query = f"ti:{query} OR abs:{query}"
    else:
        arxiv_query = f"all:{query}"
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


# ---------------------------------------------------------------------------
# Dispatch by term type
# ---------------------------------------------------------------------------

async def fetch_sources_for_term(
    term: str,
    term_type: str,
    http: httpx.AsyncClient,
) -> "list[FetchedSource]":
    """Fetch knowledge sources for a single term based on its type.

    term_type:
      "tool_library" -> PyPI first, then Wikipedia
      "concept"      -> Wikipedia, then arXiv (2 papers)
      "academic"     -> Wikipedia only (SS handles bibliography)
    """
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
        arxiv_results = await _fetch_arxiv(term, http, max_results=2)
        sources.extend(arxiv_results)

    else:  # "academic" or fallback
        wiki = await _fetch_wikipedia(term, http)
        if wiki:
            sources.append(wiki)

    return sources


async def fetch_sources_for_terms(
    terms: list[tuple[str, str]],
    http: httpx.AsyncClient,
    concurrency: int = 4,
) -> "dict[str, list[FetchedSource]]":
    """Fetch knowledge sources for multiple terms concurrently.

    Args:
        terms: List of (term_name, term_type) pairs.
        http: Shared async HTTP client.
        concurrency: Maximum parallel requests.

    Returns:
        Dict mapping term_name -> list of FetchedSource. Terms with no results
        are omitted. Exceptions are swallowed silently.
    """
    sem = asyncio.Semaphore(concurrency)
    results: dict[str, list[FetchedSource]] = {}

    async def _fetch_one(term: str, term_type: str) -> None:
        async with sem:
            try:
                fetched = await fetch_sources_for_term(term, term_type, http)
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
