"""Two-bucket bibliography fetcher (Semantic Scholar only)."""

import asyncio
import datetime
import logging
import os
import re

import httpx
from aiolimiter import AsyncLimiter

from know_expand.agents.schemas import CitationRecord
from know_expand.config import Config
from know_expand.state import emit

_FIELD_BASELINES: dict[tuple[str, int], float] = {
    # Field, Year -> approximate avg citation count for a paper in that field/year
    # These are order-of-magnitude estimates; good enough for ranking
    ("computer science", 2020): 15.0,
    ("computer science", 2021): 12.0,
    ("computer science", 2022): 8.0,
    ("computer science", 2023): 4.0,
    ("computer science", 2024): 1.5,
    ("medicine", 2020): 20.0,
    ("medicine", 2021): 16.0,
    ("medicine", 2022): 10.0,
    ("medicine", 2023): 5.0,
    ("biology", 2020): 12.0,
    ("biology", 2021): 9.0,
    ("biology", 2022): 6.0,
    ("biology", 2023): 3.0,
    ("mathematics", 2020): 5.0,
    ("mathematics", 2021): 4.0,
    ("mathematics", 2022): 2.5,
    ("mathematics", 2023): 1.5,
    ("physics", 2020): 10.0,
    ("physics", 2021): 8.0,
    ("physics", 2022): 5.0,
    ("physics", 2023): 2.5,
}


def _mncs_score(citation_count: int, field: str, year: int, field_baselines: dict) -> float:
    """
    Mean Normalized Citation Score: citation_count / field_year_average.
    Eliminates recency bias and field-size bias.
    field_baselines: {(field, year): avg_citation_count}
    Falls back to raw count if no baseline available.
    """
    key = (field.lower(), year)
    baseline = field_baselines.get(key)
    if baseline and baseline > 0:
        return citation_count / baseline
    # Fallback: apply a recency bonus to papers from last 24 months
    # (approximates normalization without baselines)
    current_year = datetime.datetime.now().year
    age = max(1, current_year - year)
    # Score = citations * recency_factor where newer papers get more weight
    recency_factor = 1.0 + max(0, (3 - age) * 0.3)  # 1.9x for this year, 1.6x for last, 1.3x for 2y ago
    return citation_count * recency_factor

_SS_API_KEY: str = os.environ.get("SS_API_KEY", "")

_logger = logging.getLogger("know_expand.bibliography")
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
_SS_PAPER_BASE = "https://api.semanticscholar.org/graph/v1/paper"
_SS_FIELDS = "title,year,citationCount,externalIds,abstract,authors,fieldsOfStudy,venue,publicationVenue"


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


def _paper_venues(paper: dict) -> list[str]:
    """Venue strings SS may attach to a paper: flat `venue` and `publicationVenue.name`."""
    out: list[str] = []
    if v := paper.get("venue"):
        out.append(str(v))
    pv = paper.get("publicationVenue") or {}
    if isinstance(pv, dict) and (name := pv.get("name")):
        out.append(str(name))
    return out


def _is_junk(paper: dict, venue_res: list[re.Pattern], doi_prefixes: list[str]) -> bool:
    """True if the paper's venue matches a junk regex or its DOI carries a junk prefix."""
    for venue in _paper_venues(paper):
        if any(r.search(venue) for r in venue_res):
            return True
    doi = ((paper.get("externalIds") or {}).get("DOI") or "").lower()
    return bool(doi) and any(doi.startswith(pfx.lower()) for pfx in doi_prefixes)


def _drop_junk(papers: list[dict], cfg: Config, *, query: str, bucket: str) -> list[dict]:
    """Remove self-upload / preprint-mill results (Zenodo, SSRN, Research Square, TechRxiv).

    Controlled by `bibliography.junk_venue_filter`; patterns come from
    `bibliography.junk_venues` (regex on the S2 venue field, case-insensitive) and
    `bibliography.junk_doi_prefixes`. Emits `ss_junk_filtered` with the dropped count
    whenever the filter runs on a non-empty list, so the dashboard can confirm it's active.
    """
    bib = cfg.bibliography
    if not bib.junk_venue_filter or not papers:
        return papers
    venue_res = [re.compile(pat, re.IGNORECASE) for pat in bib.junk_venues]
    kept: list[dict] = []
    dropped: list[dict] = []
    for p in papers:
        (dropped if _is_junk(p, venue_res, bib.junk_doi_prefixes) else kept).append(p)
    emit({
        "event": "ss_junk_filtered",
        "query": query[:60],
        "bucket": bucket,
        "dropped": len(dropped),
        "kept": len(kept),
        "dropped_titles": [(p.get("title") or "")[:80] for p in dropped[:5]],
    })
    return kept


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


async def _ss_paper_edges(
    paper_id: str,
    edge: str,
    http: httpx.AsyncClient,
    cfg: Config,
    limit: int = 8,
) -> list[dict]:
    """Fetch citing or cited papers for an SS paperId (best-effort, rate-limited)."""
    url = f"{_SS_PAPER_BASE}/{paper_id}/{edge}"
    params = {"fields": _SS_FIELDS, "limit": limit}
    headers = {"x-api-key": _SS_API_KEY} if _SS_API_KEY else {}
    limiter = _get_ss_limiter(cfg)
    async with limiter:
        try:
            resp = await http.get(url, params=params, headers=headers)
            if resp.status_code == 404:
                return []
            resp.raise_for_status()
            data = resp.json().get("data", [])
            key = "citingPaper" if edge == "citations" else "citedPaper"
            return [item[key] for item in data if item.get(key)]
        except Exception as exc:
            _logger.warning("SS %s fetch failed for %r: %s", edge, paper_id, exc)
            return []


async def fetch_anchor_neighbors(
    anchor_ss_ids: list[str],
    existing_bib: list[CitationRecord],
    http: httpx.AsyncClient,
    cfg: Config,
    top_n: int = 3,
    per_paper: int = 8,
) -> list[CitationRecord]:
    """Fetch papers citing the top-N anchor papers (frontier neighborhood expansion).

    Catches frontier work that domain-label queries miss — the Connected Papers /
    ResearchRabbit pattern applied to the bibliography stage.
    Returns CitationRecord list with bucket='frontier', deduped against existing_bib.
    """
    ids = [sid for sid in anchor_ss_ids if sid][:top_n]
    if not ids:
        return []

    seen_dois: set[str] = {r.DOI for r in existing_bib if r.DOI}
    seen_local: set[str] = {r.id for r in existing_bib}
    results: list[CitationRecord] = []

    current_year = datetime.datetime.now().year
    cutoff_year = current_year - 3

    for paper_id in ids:
        citing = await _ss_paper_edges(paper_id, "citations", http, cfg, limit=per_paper)
        citing = _drop_junk(citing, cfg, query=f"citations:{paper_id}", bucket="frontier")
        for paper in citing:
            year = paper.get("year") or 0
            if year < cutoff_year:
                continue
            doi = ((paper.get("externalIds") or {}).get("DOI") or "").lower()
            if doi and doi in seen_dois:
                continue
            if doi:
                seen_dois.add(doi)
            rec = _to_citation_record(paper, "frontier", seen_local)
            results.append(rec)

    emit({
        "event": "anchor_neighbors_fetched",
        "anchor_count": len(ids),
        "neighbor_count": len(results),
    })
    return results


async def fetch_anchors(
    domain_label: str,
    http: httpx.AsyncClient,
    cfg: Config,
    n: int | None = None,
) -> tuple[list[CitationRecord], list[str]]:
    """Return (anchor_records, ss_paper_ids) for the top-N cited papers in the domain."""
    n = n if n is not None else cfg.bibliography.ss_anchors_n
    papers = await _ss_search(domain_label, http, cfg, limit=n * 4)
    papers = _drop_junk(papers, cfg, query=domain_label, bucket="anchor")
    papers.sort(key=lambda p: p.get("citationCount") or 0, reverse=True)
    seen: set[str] = set()
    seen_dois: set[str] = set()
    results: list[CitationRecord] = []
    ss_ids: list[str] = []
    for p in papers:
        doi = ((p.get("externalIds") or {}).get("DOI") or "").lower()
        if doi and doi in seen_dois:
            continue
        if doi:
            seen_dois.add(doi)
        results.append(_to_citation_record(p, "anchor", seen))
        if pid := p.get("paperId"):
            ss_ids.append(pid)
        if len(results) >= n:
            break
    return results, ss_ids


def _clean_ref_query(raw: str, max_chars: int = 160) -> str:
    """Strip leading [N] / (N) numbering and trim to a SS-friendly query length."""
    text = re.sub(r"^\s*[\[\(]\d+[\]\)]\s*", "", raw).strip()
    return text[:max_chars]


async def fetch_source_refs(
    raw_refs: list[str],
    http: httpx.AsyncClient,
    cfg: Config,
    limit: int = 30,
) -> list[CitationRecord]:
    """Look up the source document's own reference list in Semantic Scholar.

    Returns one CitationRecord per successfully matched ref, bucket="source_ref".
    Capped at *limit* refs to stay within SS rate limits.
    """
    refs_to_fetch = raw_refs[:limit]
    seen: set[str] = set()
    seen_dois: set[str] = set()
    results: list[CitationRecord] = []

    for raw in refs_to_fetch:
        query = _clean_ref_query(raw)
        if not query:
            continue
        try:
            papers = await _ss_search(query, http, cfg, limit=1)
        except Exception as exc:
            _logger.warning("source_ref SS lookup failed for %r: %s", query[:60], exc)
            continue
        if not papers:
            continue
        p = papers[0]
        doi = ((p.get("externalIds") or {}).get("DOI") or "").lower()
        if doi and doi in seen_dois:
            continue
        if doi:
            seen_dois.add(doi)
        results.append(_to_citation_record(p, "source_ref", seen))

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
    cutoff_year = datetime.datetime.now().year - max(1, cfg.bibliography.frontier_months // 12)
    current_year = datetime.datetime.now().year

    # Sequential — both share the global rate limiter, so parallel offers no benefit
    # and doubles queue pressure on the SS API.
    foundational_raw = await _ss_search(domain_label, http, cfg, limit=n_history * 4)
    frontier_raw = await _ss_search(
        domain_label, http, cfg, limit=n_frontier * 4,
        year_filter=f"{cutoff_year}-{current_year}",
    )

    # Junk-venue filter runs before ranking so self-uploads with inflated citation
    # counts never compete for a slot. The 4x over-fetch absorbs the loss; the
    # 65/35 split arithmetic is untouched.
    foundational_raw = _drop_junk(foundational_raw, cfg, query=domain_label, bucket="foundational")
    frontier_raw = _drop_junk(frontier_raw, cfg, query=domain_label, bucket="frontier")

    # Foundational: sort by raw citation count (time-tested impact, no recency penalty)
    foundational_raw.sort(key=lambda p: p.get("citationCount") or 0, reverse=True)
    # Frontier: sort by MNCS to correct recency and field-size bias
    frontier_raw.sort(
        key=lambda p: _mncs_score(
            p.get("citationCount") or 0,
            (p.get("fieldsOfStudy") or [""])[0] if p.get("fieldsOfStudy") else "",
            p.get("year") or current_year,
            _FIELD_BASELINES,
        ),
        reverse=True,
    )

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


# ---------------------------------------------------------------------------
# fetch_paper_abstract — S8 tool-grounding for missing cached abstracts
# ---------------------------------------------------------------------------
# Additive: appended at end of file to avoid overlapping edits elsewhere in
# this module. See CLAUDE.md's S8 note for the motivating gap (cached
# CitationRecord.abstract == "" for some older/less-indexed SS entries).

async def fetch_paper_abstract(
    http: httpx.AsyncClient,
    cfg: Config,
    doi: str | None = None,
    title: str | None = None,
) -> str:
    """Best-effort live abstract lookup for a single already-known paper.

    Used by S8 when a cached `CitationRecord.abstract` is empty. Tries a
    direct DOI lookup first via the SS single-paper endpoint (exact match,
    no fuzzy-matching risk), then falls back to a title search (reusing
    `_ss_search`) when no DOI is available or the DOI lookup comes back
    empty.

    Shares the same rate limiter, API key, and field list as the rest of
    this module. Never raises — any failure (network, 404, missing
    abstract) returns "" so callers can fall through to their existing
    "no abstract available" handling unchanged.
    """
    headers = {"x-api-key": _SS_API_KEY} if _SS_API_KEY else {}
    limiter = _get_ss_limiter(cfg)

    if doi:
        url = f"{_SS_PAPER_BASE}/DOI:{doi}"
        try:
            async with limiter:
                resp = await http.get(url, params={"fields": _SS_FIELDS}, headers=headers)
            if resp.status_code == 200:
                abstract = resp.json().get("abstract") or ""
                if abstract:
                    return abstract
        except Exception as exc:
            _logger.warning("SS live abstract fetch by DOI failed for %r: %s", doi, exc)

    if title:
        try:
            papers = await _ss_search(title, http, cfg, limit=1)
        except Exception as exc:
            _logger.warning("SS live abstract fetch by title failed for %r: %s", title[:60], exc)
            return ""
        if papers:
            return papers[0].get("abstract") or ""

    return ""


