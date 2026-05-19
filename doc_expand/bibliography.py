"""Two-bucket bibliography fetcher (Semantic Scholar only)."""

import asyncio
import re
from datetime import datetime

import httpx
from aiolimiter import AsyncLimiter

from doc_expand.agents.schemas import CitationRecord
from doc_expand.config import Config

_ss_limiter: AsyncLimiter | None = None


def _get_ss_limiter(cfg: Config) -> AsyncLimiter:
    global _ss_limiter
    if _ss_limiter is None:
        rl = cfg.rate_limits["semantic_scholar"]
        _ss_limiter = AsyncLimiter(rl.max_rate, rl.time_period)
    return _ss_limiter

_SS_BASE = "https://api.semanticscholar.org/graph/v1/paper/search"
_SS_FIELDS = "title,year,citationCount,externalIds,abstract,authors"


def _parse_author(name: str) -> dict:
    parts = name.rsplit(" ", 1)
    if len(parts) == 2:
        return {"given": parts[0], "family": parts[1]}
    return {"family": name, "given": ""}


def _make_id(paper: dict, seen: set[str]) -> str:
    authors = paper.get("authors") or []
    last = authors[-1]["name"].rsplit(" ", 1)[-1] if authors else "unknown"
    year = paper.get("year") or 0
    title = paper.get("title") or ""
    m = re.search(r"[A-Za-z0-9]+", title)
    first_word = m.group() if m else "x"
    base = re.sub(r"[^a-z0-9_]", "", f"{last}_{year}_{first_word}".lower())
    candidate = base
    n = 2
    while candidate in seen:
        candidate = f"{base}_{n}"
        n += 1
    seen.add(candidate)
    return candidate


def _to_citation_record(paper: dict, bucket: str, seen: set[str]) -> CitationRecord:
    year = paper.get("year") or 0
    doi = (paper.get("externalIds") or {}).get("DOI")
    if doi:
        doi = doi.lower()
    return CitationRecord(
        id=_make_id(paper, seen),
        title=paper.get("title") or "",
        author=[_parse_author(a["name"]) for a in (paper.get("authors") or [])],
        issued={"date-parts": [[year]]},
        DOI=doi,
        abstract=paper.get("abstract") or "",
        bucket=bucket,
        citation_count=paper.get("citationCount") or 0,
    )


async def _ss_search(
    query: str,
    http: httpx.AsyncClient,
    cfg: Config,
    limit: int,
    year_filter: str | None = None,
) -> list[dict]:
    params: dict = {"query": query, "fields": _SS_FIELDS, "limit": min(limit, 100)}
    if year_filter:
        params["year"] = year_filter
    async with _get_ss_limiter(cfg):
        resp = await http.get(_SS_BASE, params=params)
    resp.raise_for_status()
    return resp.json().get("data") or []


async def fetch_anchors(
    domain_label: str,
    http: httpx.AsyncClient,
    cfg: Config,
    n: int = 3,
) -> list[CitationRecord]:
    papers = await _ss_search(domain_label, http, cfg, limit=n * 4)
    papers.sort(key=lambda p: p.get("citationCount") or 0, reverse=True)
    seen: set[str] = set()
    seen_dois: set[str] = set()
    results = []
    for p in papers:
        doi = ((p.get("externalIds") or {}).get("DOI") or "").lower()
        if doi and doi in seen_dois:
            continue
        if doi:
            seen_dois.add(doi)
        results.append(_to_citation_record(p, "anchor", seen))
        if len(results) >= n:
            break
    return results


async def fetch_bibliography(
    domain_label: str,
    depth: str,
    cfg: Config,
    http: httpx.AsyncClient,
) -> list[CitationRecord]:
    n_total = cfg.bibliography.depth_totals[depth]
    n_history = int(n_total * cfg.bibliography.foundational_fraction)
    n_frontier = n_total - n_history

    # frontier_months may not be a clean multiple of 12; floor to at least 1 year back
    cutoff_year = datetime.now().year - max(1, cfg.bibliography.frontier_months // 12)
    current_year = datetime.now().year

    foundational_raw, frontier_raw = await asyncio.gather(
        _ss_search(domain_label, http, cfg, limit=n_history * 4),
        _ss_search(domain_label, http, cfg, limit=n_frontier * 4,
                   year_filter=f"{cutoff_year}-{current_year}"),
    )

    foundational_raw.sort(key=lambda p: p.get("citationCount") or 0, reverse=True)
    frontier_raw.sort(key=lambda p: p.get("citationCount") or 0, reverse=True)

    seen: set[str] = set()
    seen_dois: set[str] = set()
    results: list[CitationRecord] = []

    def _consume(papers: list[dict], bucket: str, limit: int) -> None:
        count = 0
        for p in papers:
            if count >= limit:
                break
            doi = ((p.get("externalIds") or {}).get("DOI") or "").lower()
            if doi and doi in seen_dois:
                continue
            if doi:
                seen_dois.add(doi)
            results.append(_to_citation_record(p, bucket, seen))
            count += 1

    _consume(foundational_raw, "foundational", n_history)
    _consume(frontier_raw, "frontier", n_frontier)
    return results


