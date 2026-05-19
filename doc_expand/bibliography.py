"""Two-bucket bibliography fetcher (Semantic Scholar only)."""

import asyncio
import logging
import os
import re
from datetime import datetime

import httpx
from aiolimiter import AsyncLimiter

from doc_expand.agents.schemas import CitationRecord
from doc_expand.config import Config
from doc_expand.state import emit

_SS_API_KEY: str = os.environ.get("SS_API_KEY", "")

_logger = logging.getLogger("doc_expand.bibliography")
_ss_limiter: AsyncLimiter | None = None
_ss_interval: float = 20.0  # cached for emit
_ss_cooldown_until: float = 0.0  # monotonic clock; all tasks pause until this time


def reset_ss_limiter() -> None:
    """Reset rate limiter and circuit breaker state at the start of each run."""
    global _ss_limiter, _ss_cooldown_until
    _ss_limiter = None
    _ss_cooldown_until = 0.0


def _get_ss_limiter(cfg: Config) -> AsyncLimiter:
    global _ss_limiter, _ss_interval
    if _ss_limiter is None:
        rl = cfg.rate_limits["semantic_scholar"]
        # Use 1-token bucket to serialize requests and prevent burst traffic.
        # AsyncLimiter(N, T) starts with N tokens, so concurrent calls all
        # get tokens immediately. Instead, use 1 token per (T/N) seconds.
        _ss_interval = rl.time_period / max(rl.max_rate, 1)
        _ss_limiter = AsyncLimiter(1, _ss_interval)
        _logger.debug(
            "SS rate limiter created: 1 request per %.1fs (max_rate=%s, time_period=%s)",
            _ss_interval, rl.max_rate, rl.time_period,
        )
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
    global _ss_cooldown_until

    params: dict = {"query": query, "fields": _SS_FIELDS, "limit": min(limit, 100)}
    if year_filter:
        params["year"] = year_filter
    headers = {"x-api-key": _SS_API_KEY} if _SS_API_KEY else {}
    delay = cfg.bibliography.ss_retry_initial_delay
    limiter = _get_ss_limiter(cfg)

    for attempt in range(cfg.bibliography.ss_max_retries):
        # Global circuit breaker: if any prior request got 429, all tasks wait here.
        now = asyncio.get_event_loop().time()
        remaining = _ss_cooldown_until - now
        if remaining > 0:
            emit({
                "event": "ss_cooldown_wait",
                "query": query[:60],
                "wait_s": round(remaining, 1),
                "attempt": attempt,
            })
            await asyncio.sleep(remaining)

        emit({
            "event": "ss_rate_wait",
            "query": query[:60],
            "attempt": attempt,
            "interval_s": _ss_interval,
        })
        async with limiter:
            emit({
                "event": "ss_request",
                "query": query[:60],
                "limit": min(limit, 100),
                "year_filter": year_filter,
                "attempt": attempt,
            })
            resp = await http.get(_SS_BASE, params=params, headers=headers)

        emit({
            "event": "ss_response",
            "query": query[:60],
            "status": resp.status_code,
            "result_count": len(resp.json().get("data") or []) if resp.status_code == 200 else 0,
            "attempt": attempt,
        })

        if resp.status_code == 429:
            # Set global cooldown: every queued task will pause until this expires.
            _ss_cooldown_until = asyncio.get_event_loop().time() + delay
            emit({
                "event": "ss_429_retry",
                "query": query[:60],
                "sleep_s": delay,
                "attempt": attempt,
                "max_retries": cfg.bibliography.ss_max_retries,
                "cooldown_until": _ss_cooldown_until,
            })
            delay = min(delay * 2, cfg.bibliography.ss_max_backoff)
            continue

        resp.raise_for_status()
        return resp.json().get("data") or []

    emit({"event": "ss_exhausted", "query": query[:60], "attempts": cfg.bibliography.ss_max_retries})
    resp.raise_for_status()
    return []


async def fetch_anchors(
    domain_label: str,
    http: httpx.AsyncClient,
    cfg: Config,
    n: int | None = None,
) -> list[CitationRecord]:
    n = n if n is not None else cfg.bibliography.ss_anchors_n
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

    # Sequential — both share the global rate limiter, so parallel offers no benefit
    # and doubles queue pressure on the SS API.
    foundational_raw = await _ss_search(domain_label, http, cfg, limit=n_history * 4)
    frontier_raw = await _ss_search(
        domain_label, http, cfg, limit=n_frontier * 4,
        year_filter=f"{cutoff_year}-{current_year}",
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


