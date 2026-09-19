"""Live grounding tools for the S4 adversarial gap loop.

The Gap Finder and Defender roles in `s4_audit._run_gap_loop` get to issue
live tool calls before committing to a verdict, following the ReAct (Yao et
al., arXiv 2210.03629) and CRITIC (Gou et al., arXiv 2305.11738) pattern:
propose -> invoke a tool -> observe -> answer.

This module only wraps HTTP-fetch logic that already exists elsewhere
(`bibliography.py`'s Semantic Scholar search, `sources.py`'s arXiv/Wikipedia
fetchers) as OpenAI-style tool specs plus thin async dispatch functions. It
does not duplicate any fetch/parse logic and never raises — every function
here is best-effort and returns a human-readable string for the LLM to read
as a tool observation.
"""

import difflib
import logging

import httpx

from know_expand.bibliography import _ss_search
from know_expand.config import Config
from know_expand.sources import _fetch_arxiv, _fetch_wikipedia

_logger = logging.getLogger("know_expand.s4_tools")

COVERAGE_TOOL_NAME = "check_recent_coverage"
SYNONYM_TOOL_NAME = "check_graph_synonym"


def coverage_tool_spec() -> dict:
    """Tool spec for the Gap Finder: is this claimed gap already covered?"""
    return {
        "type": "function",
        "function": {
            "name": COVERAGE_TOOL_NAME,
            "description": (
                "Search Semantic Scholar (falling back to arXiv) for recent papers "
                "matching a query. Use this to check whether a candidate knowledge-graph "
                "gap already has real, findable coverage in the literature before "
                "reporting it as a gap."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The gap term or short phrase to search for.",
                    },
                },
                "required": ["query"],
            },
        },
    }


def synonym_tool_spec() -> dict:
    """Tool spec for the Defender: is this claimed gap already in the graph under another name?"""
    return {
        "type": "function",
        "function": {
            "name": SYNONYM_TOOL_NAME,
            "description": (
                "Check whether a claimed gap term is a plausible synonym, alias, or "
                "rebrand of an existing knowledge-graph term. Compares the term against "
                "the domain's existing graph terms by lexical similarity and looks it up "
                "on Wikipedia."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "term": {
                        "type": "string",
                        "description": "The claimed gap term to check.",
                    },
                },
                "required": ["term"],
            },
        },
    }


async def run_coverage_check(
    query: str,
    http: httpx.AsyncClient,
    cfg: Config,
    domain_label: str = "",
) -> str:
    """Semantic Scholar search, falling back to arXiv. Best-effort, never raises."""
    query = (query or "").strip()
    if not query:
        return "Empty query — nothing to search."
    try:
        papers = await _ss_search(query, http, cfg, limit=3)
    except Exception as exc:
        _logger.debug("s4_tools SS search failed for %r: %s", query, exc)
        papers = []
    if papers:
        lines = [f"Semantic Scholar results for {query!r}:"]
        for p in papers[:3]:
            lines.append(
                f"- {p.get('title', '')} ({p.get('year', '?')}, "
                f"{p.get('citationCount', 0)} citations)"
            )
        return "\n".join(lines)

    try:
        arxiv_results = await _fetch_arxiv(query, http, domain_context=domain_label, max_results=3)
    except Exception as exc:
        _logger.debug("s4_tools arXiv search failed for %r: %s", query, exc)
        arxiv_results = []
    if arxiv_results:
        lines = [f"arXiv results for {query!r}:"]
        for r in arxiv_results:
            lines.append(f"- {r.title} — {r.content[:150]}")
        return "\n".join(lines)

    return f"No Semantic Scholar or arXiv coverage found for {query!r}."


async def run_synonym_check(
    term: str,
    graph_terms: list[str],
    http: httpx.AsyncClient,
) -> str:
    """Lexical-similarity check against existing graph terms + Wikipedia lookup.

    Best-effort, never raises.
    """
    term = (term or "").strip()
    if not term:
        return "Empty term — nothing to check."

    scored = sorted(
        graph_terms,
        key=lambda t: difflib.SequenceMatcher(None, term.lower(), t.lower()).ratio(),
        reverse=True,
    )
    top = [
        t for t in scored[:3]
        if difflib.SequenceMatcher(None, term.lower(), t.lower()).ratio() > 0.3
    ]
    lines = []
    if top:
        lines.append(f"Graph terms lexically similar to {term!r}: {', '.join(top)}")
    else:
        lines.append(f"No lexically similar graph terms found for {term!r}.")

    try:
        wiki = await _fetch_wikipedia(term, http)
    except Exception as exc:
        _logger.debug("s4_tools wikipedia lookup failed for %r: %s", term, exc)
        wiki = None
    if wiki:
        lines.append(f"Wikipedia ({wiki.title}): {wiki.content[:300]}")
    else:
        lines.append(f"No Wikipedia match found for {term!r}.")

    return "\n".join(lines)
