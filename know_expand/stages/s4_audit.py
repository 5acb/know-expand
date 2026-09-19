"""Stage 3 — Anchored Audit: per-domain bibliography and gap analysis."""

import asyncio
import json
import logging
import time
from pathlib import Path

import httpx

from know_expand.agents.base import make_router
from know_expand.agents.schemas import GapAnalysisResult
from know_expand.bibliography import fetch_anchor_neighbors, fetch_anchors, fetch_bibliography, reset_ss_limiter
from know_expand.config import Config
from know_expand.stages.s4_tools import (
    COVERAGE_TOOL_NAME,
    SYNONYM_TOOL_NAME,
    coverage_tool_spec,
    run_coverage_check,
    run_synonym_check,
    synonym_tool_spec,
)
from know_expand.state import PipelineState, emit, mark_stage_complete, stage_is_complete

_logger = logging.getLogger("know_expand.s4")

# Max propose->tool->observe turns in the bespoke ReAct grounding loop before
# giving up and falling back to whatever text the model has produced so far.
_MAX_GROUNDING_TURNS = 4

_GAP_FINDER_PROMPT = """\
You are a research gap analyst. Given the terms from a domain's knowledge graph and \
the titles and abstracts of anchor papers for that domain, identify what the anchor \
papers cover that the graph is missing.

Domain: {domain_label}

Graph terms: {graph_terms}

Anchor papers:
{anchor_summaries}

Look especially for gaps in these categories:
- Methodological gaps: approaches described in the papers but absent from the graph
- Citation graph gaps: the papers cite foundational work or datasets not present in the graph
- Snowballing gaps: if the anchor papers reference key sub-fields that the graph does not cover
- Evaluation gaps: benchmarks, metrics, or reproducibility practices mentioned in papers but missing

For each gap, provide a gap_description and list the evidence_anchor_ids (paper IDs \
from the anchor list) that reveal the gap. Return a GapAnalysisResult with \
domain_id="{domain_id}".
"""

_GAP_DEFENDER_PROMPT = """\
You are a knowledge graph defender. For each gap finding below, argue that the graph \
already covers it — either under a different term name, as an alias, or that it is \
out of scope for this domain.

Domain: {domain_label}
Domain ID: {domain_id}

Graph terms: {graph_terms}

Gap findings:
{gap_findings}

CRITICAL: You MUST provide a non-empty defender_argument for EVERY gap, even if \
you believe the gap is real. If you cannot argue it is already covered, write: \
"Conceded: this gap is genuine and not covered by any existing graph term." \
Never leave defender_argument blank or null.

Fill in defender_argument for each gap. Return a GapAnalysisResult with \
domain_id="{domain_id}".
"""

_FINDER_REACT_SYSTEM = """\
You are a research gap analyst investigating a knowledge domain before finalizing a gap \
report. You have a tool, `check_recent_coverage`, that searches Semantic Scholar (and \
arXiv as a fallback) for a query. Use it to verify that each candidate gap you are \
considering reflects real, findable research literature — not a hallucinated, overly \
narrow, or already-obsolete topic — before deciding to include it.

Call the tool for at least one candidate gap term; call it again for every additional \
candidate you are unsure about. When you are done investigating, respond with a concise \
plain-text summary (not JSON): for each term you checked, state what you searched for, \
what the tool returned, and whether you still consider it a genuine gap.
"""

_DEFENDER_REACT_SYSTEM = """\
You are a knowledge-graph defender investigating whether gaps identified by the Gap \
Finder are already covered by the existing graph terms. You have a tool, \
`check_graph_synonym`, that checks a claimed gap term against the domain's existing \
graph terms for lexical similarity and looks the term up on Wikipedia.

Use the tool for each gap before asserting the graph already covers it under a \
different name or as an alias. If the tool finds no plausible synonym or rebrand, \
concede the gap honestly rather than inventing a defense. When you are done \
investigating, respond with a concise plain-text summary (not JSON): for each gap, \
state what you checked and what the tool returned.
"""

_GAP_FINDER_REBUTTAL_PROMPT = """\
You are the original gap analyst reviewing the defender's arguments. For each gap, \
set the verdict field:
- "real_gap" if the defender's argument is wrong or weak
- "ambiguous" if it is unclear
- "not_a_gap" if the defender is correct

Domain: {domain_label}
Domain ID: {domain_id}

Original gaps with defender arguments:
{gap_findings_with_defenses}

Return a GapAnalysisResult with domain_id="{domain_id}" and verdict set for every gap.
"""


def _anchor_summaries(anchors: list[dict]) -> str:
    lines = []
    for a in anchors:
        title = a.get("title", "")
        abstract = a.get("abstract", "")[:300]
        paper_id = a.get("id", "")
        lines.append(f"[{paper_id}] {title}\n  {abstract}")
    return "\n\n".join(lines)


async def _run_react_grounding(
    agent_router,
    messages: list[dict],
    tool_spec: dict,
    tool_name: str,
    tool_fn,
    domain_id: str,
    role_label: str,
    round_no: int,
    cfg: Config,
    max_turns: int = _MAX_GROUNDING_TURNS,
) -> tuple[str, int]:
    """Bespoke ReAct loop: propose -> invoke `tool_fn` -> observe -> answer.

    Built directly on `QuotaAwareRouter.call_with_tools()` (a single call) rather
    than a LangGraph StateGraph/ToolNode, to keep this stage's control flow in
    plain, easily-testable Python. Loops until the model stops requesting the
    tool or `max_turns` is exhausted, dispatching every requested tool call to
    `tool_fn` and feeding the observation back as a `role: tool` message.

    Returns (final_text, tool_call_count). This is entirely best-effort: any
    exception (including "all models exhausted for agent role") is swallowed
    and reported as ("", tool_call_count so far) so the caller can fall back
    to the original ungrounded prompt. Nothing here can fail the S4 build.
    """
    call_timeout = cfg.timeouts.get("s4_gap_grounding_seconds", 90)
    working_messages = list(messages)
    tool_call_count = 0
    final_text = ""
    try:
        for _turn in range(max_turns):
            msg = await asyncio.wait_for(
                agent_router.call_with_tools(working_messages, [tool_spec]),
                timeout=call_timeout,
            )
            working_messages.append(msg)
            tool_calls = msg.get("tool_calls")
            if not tool_calls:
                final_text = msg.get("content", "") or ""
                break
            for tc in tool_calls:
                fn = tc.get("function", {}) or {}
                if fn.get("name") != tool_name:
                    observation = f"Unknown tool: {fn.get('name')!r}"
                else:
                    try:
                        args = json.loads(fn.get("arguments") or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    try:
                        observation = await tool_fn(**args)
                    except Exception as exc:
                        observation = f"Tool error: {exc}"
                tool_call_count += 1
                working_messages.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id", ""),
                    "content": observation,
                })
                emit({
                    "event": f"gap_{role_label}_tool_call",
                    "domain_id": domain_id,
                    "round": round_no,
                    "tool": tool_name,
                })
        else:
            # Exhausted max_turns while the model kept requesting tools.
            # working_messages[-1] is always a {"role": "tool", ...}
            # observation at this point (the model never returned a
            # response with no tool_calls), never model-authored text —
            # using it as final_text would splice a raw tool-observation
            # string into the next verdict prompt as if it were the
            # model's own investigation summary. Leave final_text empty
            # instead; the caller treats "" the same as any other
            # inconclusive grounding pass.
            final_text = ""
            emit({
                "event": f"gap_{role_label}_grounding_max_turns",
                "domain_id": domain_id,
                "round": round_no,
                "tool_call_count": tool_call_count,
            })
    except Exception as exc:
        emit({
            "event": f"gap_{role_label}_grounding_failed",
            "domain_id": domain_id,
            "round": round_no,
            "error": str(exc)[:200],
        })
        return "", tool_call_count
    return final_text, tool_call_count


async def _run_gap_loop(
    domain_id: str,
    domain_label: str,
    graph_terms: list[str],
    anchors: list[dict],
    router,
    rounds: int = 3,
    agent_router=None,
    http: httpx.AsyncClient | None = None,
    cfg: Config | None = None,
    no_bibliography_fetch: bool = False,
) -> GapAnalysisResult:
    """Adversarial Gap Finder -> Defender -> Rebuttal loop.

    When `agent_router`/`http`/`cfg` are available and `no_bibliography_fetch`
    is False, the Finder and Defender each run a live-tool ReAct grounding
    pass (see `_run_react_grounding`) before their structured verdict call:
    the Finder checks candidate gaps against Semantic Scholar/arXiv, the
    Defender checks claimed rebrands/synonyms against the graph terms and
    Wikipedia. The grounding findings are spliced into the same structured
    prompt used before, so the final `router.call(..., GapAnalysisResult)`
    call is unchanged in shape — just better-informed. In air-gapped mode
    (`no_bibliography_fetch=True`) or if grounding fails/produces nothing,
    this falls back to the original ungrounded prompt so the stage never
    blocks on a live lookup.
    """
    terms_str = ", ".join(graph_terms)
    anchor_str = _anchor_summaries(anchors)
    can_ground = agent_router is not None and http is not None and cfg is not None and not no_bibliography_fetch

    prev_gap_ids: set[str] | None = None
    termination_reason = "max_rounds"
    rebuttal_result: GapAnalysisResult | None = None
    current_gap_ids: set[str] = set()
    rnd = 0

    async def _finder_tool_fn(query: str = "") -> str:
        return await run_coverage_check(query, http, cfg, domain_label=domain_label)

    async def _defender_tool_fn(term: str = "") -> str:
        return await run_synonym_check(term, graph_terms, http)

    for rnd in range(1, rounds + 1):
        emit({"event": "gap_finder_start", "domain_id": domain_id, "round": rnd, "term_count": len(graph_terms), "anchor_count": len(anchors)})

        finder_prompt = _GAP_FINDER_PROMPT.format(
            domain_label=domain_label,
            domain_id=domain_id,
            graph_terms=terms_str,
            anchor_summaries=anchor_str,
        )
        finder_tool_calls = 0
        if can_ground:
            emit({"event": "gap_finder_grounding_start", "domain_id": domain_id, "round": rnd})
            grounding_text, finder_tool_calls = await _run_react_grounding(
                agent_router,
                [
                    {"role": "system", "content": _FINDER_REACT_SYSTEM},
                    {"role": "user", "content": finder_prompt},
                ],
                coverage_tool_spec(),
                COVERAGE_TOOL_NAME,
                _finder_tool_fn,
                domain_id, "finder", rnd, cfg,
            )
            emit({
                "event": "gap_finder_grounding_done",
                "domain_id": domain_id,
                "round": rnd,
                "tool_calls": finder_tool_calls,
                "grounded": bool(grounding_text),
            })
            if grounding_text:
                finder_prompt = (
                    f"{finder_prompt}\n\n"
                    "You already investigated the candidates below using live literature "
                    "search tools (Semantic Scholar / arXiv). Use these findings — do not "
                    "report a gap your own search showed is already well covered:\n\n"
                    f"{grounding_text}"
                )
        else:
            emit({
                "event": "gap_finder_grounding_skipped",
                "domain_id": domain_id,
                "round": rnd,
                "reason": "no_bibliography_fetch" if no_bibliography_fetch else "agent_router_unavailable",
            })

        finder_msg = [{"role": "user", "content": finder_prompt}]
        finder_result: GapAnalysisResult = await router.call(finder_msg, GapAnalysisResult)
        emit({
            "event": "gap_finder_complete",
            "domain_id": domain_id,
            "round": rnd,
            "gap_count": len(finder_result.gaps),
            "tool_calls_used": finder_tool_calls,
        })

        if not finder_result.gaps:
            rebuttal_result = finder_result
            current_gap_ids = set()
            termination_reason = "no_gaps"
            break

        gap_findings_str = json.dumps(
            [g.model_dump() for g in finder_result.gaps], indent=2
        )
        emit({"event": "gap_defender_start", "domain_id": domain_id, "round": rnd, "gap_count": len(finder_result.gaps)})

        defender_prompt = _GAP_DEFENDER_PROMPT.format(
            domain_label=domain_label,
            domain_id=domain_id,
            graph_terms=terms_str,
            gap_findings=gap_findings_str,
        )
        defender_tool_calls = 0
        if can_ground:
            emit({"event": "gap_defender_grounding_start", "domain_id": domain_id, "round": rnd})
            grounding_text, defender_tool_calls = await _run_react_grounding(
                agent_router,
                [
                    {"role": "system", "content": _DEFENDER_REACT_SYSTEM},
                    {"role": "user", "content": defender_prompt},
                ],
                synonym_tool_spec(),
                SYNONYM_TOOL_NAME,
                _defender_tool_fn,
                domain_id, "defender", rnd, cfg,
            )
            emit({
                "event": "gap_defender_grounding_done",
                "domain_id": domain_id,
                "round": rnd,
                "tool_calls": defender_tool_calls,
                "grounded": bool(grounding_text),
            })
            if grounding_text:
                defender_prompt = (
                    f"{defender_prompt}\n\n"
                    "You already investigated the gaps below using a live tool that checks "
                    "the domain's existing graph terms and Wikipedia. Base your "
                    "defender_argument on these findings — concede honestly where the tool "
                    "found no plausible synonym or rebrand:\n\n"
                    f"{grounding_text}"
                )
        else:
            emit({
                "event": "gap_defender_grounding_skipped",
                "domain_id": domain_id,
                "round": rnd,
                "reason": "no_bibliography_fetch" if no_bibliography_fetch else "agent_router_unavailable",
            })

        defender_msg = [{"role": "user", "content": defender_prompt}]
        defender_result: GapAnalysisResult = await router.call(defender_msg, GapAnalysisResult)
        emit({
            "event": "gap_defender_complete",
            "domain_id": domain_id,
            "round": rnd,
            "tool_calls_used": defender_tool_calls,
        })

        defended_gaps = defender_result.gaps if defender_result.gaps else finder_result.gaps
        defended_str = json.dumps(
            [g.model_dump() for g in defended_gaps], indent=2
        )
        emit({"event": "gap_rebuttal_start", "domain_id": domain_id, "round": rnd})
        rebuttal_msg = [{
            "role": "user",
            "content": _GAP_FINDER_REBUTTAL_PROMPT.format(
                domain_label=domain_label,
                domain_id=domain_id,
                gap_findings_with_defenses=defended_str,
            ),
        }]
        rebuttal_result = await router.call(rebuttal_msg, GapAnalysisResult)
        emit({
            "event": "gap_rebuttal_complete",
            "domain_id": domain_id,
            "round": rnd,
            "real_gaps": sum(1 for g in rebuttal_result.gaps if g.verdict == "real_gap"),
            "not_gaps": sum(1 for g in rebuttal_result.gaps if g.verdict == "not_a_gap"),
            "ambiguous": sum(1 for g in rebuttal_result.gaps if g.verdict == "ambiguous"),
        })

        current_gap_ids = {g.gap_description for g in rebuttal_result.gaps if g.verdict == "real_gap"}

        if not current_gap_ids:
            termination_reason = "no_gaps"
            break
        if prev_gap_ids is not None and current_gap_ids == prev_gap_ids:
            termination_reason = "stalled"
            emit({"event": "s3_gap_loop_stalled", "domain_id": domain_id, "round": rnd})
            break
        prev_gap_ids = current_gap_ids

    emit({
        "event": "s3_gap_loop_done",
        "domain_id": domain_id,
        "termination_reason": termination_reason,
        "rounds_run": rnd,
        "real_gaps": len(current_gap_ids),
    })

    if rebuttal_result is None:
        # No rounds ran (rounds=0); return empty result
        return GapAnalysisResult(domain_id=domain_id, gaps=[])
    return rebuttal_result


async def _process_domain(
    domain: dict,
    graph_terms: list[str],
    audit_dir: Path,
    depth: str,
    cfg: Config,
    router,
    http: httpx.AsyncClient,
    domain_nodes: list[dict] | None = None,
    no_bibliography_fetch: bool = False,
    agent_router=None,
) -> GapAnalysisResult:
    domain_id = domain["id"]
    domain_label = domain["label"]
    t0 = time.monotonic()

    # Load term_type lookup from terms.json (best-effort)
    terms_path = audit_dir.parent / "terms.json"
    term_type_map: dict[str, str] = {}
    if terms_path.exists():
        try:
            for t in json.loads(terms_path.read_text()):
                term_type_map[t["name"]] = t.get("term_type", "academic")
        except Exception as exc:
            _logger.warning("s4 term_type_map load failed: %s", exc)

    # Fetch knowledge sources for non-trivial terms in this domain (best-effort)
    sources_dir = audit_dir / "sources"
    sources_dir.mkdir(exist_ok=True)
    sources_cache = sources_dir / f"sources_{domain_id}.json"

    if not sources_cache.exists():
        if no_bibliography_fetch:
            sources_cache.write_text(json.dumps({}, indent=2))
        else:
            try:
                from know_expand.sources import fetch_sources_for_terms  # noqa: PLC0415
                # Only fetch core + supporting terms, not incidental
                nodes_for_fetch = domain_nodes or []
                terms_to_fetch = [
                    (n["name"], term_type_map.get(n["name"], "academic"))
                    for n in nodes_for_fetch
                    if n.get("centrality") in ("core", "supporting")
                ]
                # Fallback: if no node dicts available, use all graph_terms as academic
                if not terms_to_fetch and graph_terms:
                    terms_to_fetch = [
                        (name, term_type_map.get(name, "academic")) for name in graph_terms
                    ]
                if terms_to_fetch:
                    emit({
                        "event": "s4_sources_fetch_start",
                        "domain_id": domain_id,
                        "term_count": len(terms_to_fetch),
                    })
                    fetched = await fetch_sources_for_terms(
                        terms_to_fetch,
                        http,
                        concurrency=4,
                        domain_label=domain_label,
                        openalex_budget_retry_after_threshold_s=cfg.timeouts.get(
                            "openalex_budget_retry_after_threshold_s", 600
                        ),
                    )
                    raw_dump = {k: [s.model_dump() for s in v] for k, v in fetched.items()}
                    sources_cache.write_text(json.dumps(raw_dump, indent=2))
                    emit({
                        "event": "s4_sources_fetch_done",
                        "domain_id": domain_id,
                        "terms_with_sources": len(fetched),
                    })
            except Exception as exc:
                _logger.warning("s4 sources fetch failed for domain %r: %s", domain_id, exc)

    bib_path = audit_dir / f"bibliography_{domain_id}.json"
    if bib_path.exists() and bib_path.stat().st_size > 0:
        emit({
            "event": "domain_skipped",
            "domain_id": domain_id,
            "reason": "bibliography_sentinel_exists",
        })
        anchors_path = audit_dir / f"anchors_{domain_id}.json"
        anchors = json.loads(anchors_path.read_text()) if anchors_path.exists() else []
        gap_result = await _run_gap_loop(
            domain_id, domain_label, graph_terms, anchors, router,
            rounds=cfg.adversarial_rounds.get(depth, 3),
            agent_router=agent_router, http=http, cfg=cfg,
            no_bibliography_fetch=no_bibliography_fetch,
        )
        return gap_result

    emit({
        "event": "domain_start",
        "domain_id": domain_id,
        "label": domain_label,
        "term_count": len(graph_terms),
    })

    if no_bibliography_fetch:
        emit({
            "event": "bibliography_fetch_skipped",
            "domain_id": domain_id,
            "reason": "no_bibliography_fetch_flag",
        })
        anchors_dicts = []
        bibliography = []
        anchors_path = audit_dir / f"anchors_{domain_id}.json"
        anchors_path.write_text(json.dumps([], indent=2))
        bib_path.write_text(json.dumps([], indent=2))
    else:
        emit({"event": "domain_fetch_start", "domain_id": domain_id, "depth": depth})
        (anchors_list, anchor_ss_ids), bibliography = await asyncio.gather(
            fetch_anchors(domain_label, http, cfg),
            fetch_bibliography(domain_label, depth, cfg, http),
        )
        neighbor_papers = await fetch_anchor_neighbors(anchor_ss_ids, bibliography, http, cfg)
        if neighbor_papers:
            bibliography = bibliography + neighbor_papers
        emit({
            "event": "domain_fetch_done",
            "domain_id": domain_id,
            "elapsed_s": round(time.monotonic() - t0, 2),
            "neighbor_count": len(neighbor_papers),
        })

        anchors_dicts = [a.model_dump() for a in anchors_list]
        anchors_path = audit_dir / f"anchors_{domain_id}.json"
        anchors_path.write_text(json.dumps(anchors_dicts, indent=2))
        emit({
            "event": "anchors_fetched",
            "domain_id": domain_id,
            "anchor_count": len(anchors_dicts),
            "artifact": str(anchors_path),
        })

        bib_path.write_text(json.dumps([b.model_dump() for b in bibliography], indent=2))
        emit({
            "event": "bibliography_fetched",
            "domain_id": domain_id,
            "entry_count": len(bibliography),
            "artifact": str(bib_path),
        })

    gap_result = await _run_gap_loop(
        domain_id, domain_label, graph_terms, anchors_dicts, router,
        rounds=cfg.adversarial_rounds.get(depth, 3),
        agent_router=agent_router, http=http, cfg=cfg,
        no_bibliography_fetch=no_bibliography_fetch,
    )
    emit({
        "event": "domain_complete",
        "domain_id": domain_id,
        "elapsed_s": round(time.monotonic() - t0, 2),
        "real_gaps": sum(1 for g in gap_result.gaps if g.verdict == "real_gap"),
    })
    return gap_result


def _build_gap_markdown(results: list[GapAnalysisResult], domains_by_id: dict) -> str:
    lines = ["# Gap Analysis — Real Gaps\n"]
    for result in results:
        label = domains_by_id.get(result.domain_id, result.domain_id)
        real_gaps = [g for g in result.gaps if g.verdict == "real_gap"]
        if not real_gaps:
            continue
        lines.append(f"## {label} (`{result.domain_id}`)\n")
        for g in real_gaps:
            lines.append(f"- **{g.gap_description}**")
            if g.evidence_anchor_ids:
                lines.append(f"  Evidence: {', '.join(g.evidence_anchor_ids)}")
            if g.finder_rebuttal:
                lines.append(f"  Rebuttal: {g.finder_rebuttal}")
            lines.append("")
    return "\n".join(lines)


def _build_corrections_markdown(
    results: list[GapAnalysisResult], domains_by_id: dict
) -> str:
    lines = ["# Gap Analysis — Corrections (Defender Won)\n"]
    for result in results:
        label = domains_by_id.get(result.domain_id, result.domain_id)
        not_gaps = [g for g in result.gaps if g.verdict == "not_a_gap"]
        if not not_gaps:
            continue
        lines.append(f"## {label} (`{result.domain_id}`)\n")
        for g in not_gaps:
            lines.append(f"- **{g.gap_description}**")
            if g.defender_argument:
                lines.append(f"  Defender: {g.defender_argument}")
            lines.append("")
    return "\n".join(lines)


async def run(state: PipelineState, cfg: Config) -> None:
    state_dir = Path(state["state_dir"])
    audit_dir = state_dir / "audit"
    audit_dir.mkdir(exist_ok=True)

    if stage_is_complete(state_dir, 4):
        emit({"event": "stage_skipped", "stage": 4, "reason": "already_complete"})
        return

    emit({"event": "stage_start", "stage": 4})
    reset_ss_limiter()

    input_path = state["input_path"]
    if not input_path.lower().endswith(".pdf"):
        emit({
            "event": "source_refs_skipped",
            "reason": "not_pdf",
        })

    taxonomy = json.loads((state_dir / "taxonomy.json").read_text())
    domains = taxonomy["domains"]

    graph_data = json.loads((state_dir / "graph.json").read_text())
    nodes = graph_data.get("nodes", [])

    conflicts_path = state_dir / "audit" / "classification_conflicts.json"
    conflicts: list[dict] = []
    if conflicts_path.exists():
        conflicts = json.loads(conflicts_path.read_text())

    conflict_term_names = {c.get("term_name", "") for c in conflicts}

    domains_by_id = {d["id"]: d["label"] for d in domains}
    depth = state["depth"]
    router = make_router("critic", cfg)
    # "agent" role models are used only for the Finder/Defender ReAct grounding
    # tool calls (see _run_react_grounding) — the final structured verdict
    # always goes through the "critic" router above, unchanged.
    agent_router = make_router("agent", cfg)

    emit({
        "event": "stage3_start",
        "domain_count": len(domains),
        "node_count": len(nodes),
        "conflict_count": len(conflicts),
    })

    gap_results: list[GapAnalysisResult] = []
    async with httpx.AsyncClient(timeout=cfg.timeouts.get("http_async_seconds", 30)) as http:
        # Process domains serially: concurrent SS fetches from multiple domains
        # trigger 429 storms because all coroutines share the global rate limiter
        # and pile up retries in lock-step.  One domain at a time keeps the SS
        # request queue shallow and avoids the retry thundering-herd.
        for domain in domains:
            domain_id = domain["id"]
            from know_expand.state import active_domain
            active_domain.set(domain_id)
            domain_node_list = [n for n in nodes if n.get("domain") == domain_id]
            graph_terms = [n["name"] for n in domain_node_list]
            gap_candidates = [
                t for t in conflict_term_names
                if any(n["name"] == t and n.get("domain") == domain_id for n in nodes)
            ]
            all_terms = list(dict.fromkeys(graph_terms + gap_candidates))
            try:
                result = await _process_domain(
                    domain, all_terms, audit_dir, depth, cfg, router, http,
                    domain_nodes=domain_node_list,
                    no_bibliography_fetch=state.get("no_bibliography_fetch", False),
                    agent_router=agent_router,
                )
                gap_results.append(result)
            except Exception as exc:
                emit({
                    "event": "domain_failed",
                    "domain_id": domain_id,
                    "error": str(exc)[:200],
                })

    gap_md = _build_gap_markdown(gap_results, domains_by_id)
    corrections_md = _build_corrections_markdown(gap_results, domains_by_id)

    gap_md_path = audit_dir / "gap_analysis.md"
    corrections_md_path = audit_dir / "corrections.md"
    gap_md_path.write_text(gap_md)
    corrections_md_path.write_text(corrections_md)

    total_real = sum(
        sum(1 for g in r.gaps if g.verdict == "real_gap") for r in gap_results
    )
    total_not = sum(
        sum(1 for g in r.gaps if g.verdict == "not_a_gap") for r in gap_results
    )
    total_ambiguous = sum(
        sum(1 for g in r.gaps if g.verdict == "ambiguous") for r in gap_results
    )

    # Merge and write bibliography.json so downstream stages have it
    try:
        from know_expand.stages.s10_assemble import _merge_bibliographies
        merged_bib = _merge_bibliographies(audit_dir)
        bib_path = state_dir / "bibliography.json"
        from know_expand.state import atomic_write
        atomic_write(bib_path, json.dumps(merged_bib, indent=2))
        emit({
            "event": "s4_bibliography_merged",
            "entry_count": len(merged_bib),
            "artifact": str(bib_path),
        })
    except Exception as exc:
        _logger.warning("Failed to merge bibliographies in S4: %s", exc)

    mark_stage_complete(state_dir, 4)
    emit({
        "event": "stage_complete",
        "stage": 4,
        "real_gaps": total_real,
        "not_gaps": total_not,
        "ambiguous": total_ambiguous,
        "artifact_gap_analysis": str(gap_md_path),
        "artifact_corrections": str(corrections_md_path),
    })
