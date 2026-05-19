import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import httpx

from doc_expand.agents.base import make_router
from doc_expand.agents.schemas import (
    TaxonomyProposal, KnowledgeGraph,
    GraphNode, DomainProposal,
)
from doc_expand.config import Config
from doc_expand.state import PipelineState, emit, mark_stage_complete, stage_is_complete

_BOLD  = "\033[1m"
_CYAN  = "\033[36m"
_GREEN = "\033[32m"
_RESET = "\033[0m"


def _print_proposal(label: str, proposal: TaxonomyProposal) -> None:
    print(f"\n{_CYAN}::{_RESET} {_BOLD}{label}{_RESET} ({len(proposal.domains)} domains)")
    for i, d in enumerate(proposal.domains, 1):
        oa = f"  openalex L{d.openalex_level}" if d.openalex_level is not None else ""
        print(f"   {i:2}.  {d.label}{oa}")


async def _explain_taxonomy(
    lumper: TaxonomyProposal,
    splitter: TaxonomyProposal,
    router,
) -> None:
    from doc_expand.agents.schemas import TaxonomyExplanation
    msg = [{
        "role": "user",
        "content": _EXPLAIN_PROMPT.format(
            lumper=lumper.model_dump_json(),
            splitter=splitter.model_dump_json(),
        ),
    }]
    explanation = await router.call(msg, TaxonomyExplanation)
    print(f"\n{_CYAN}{'─' * 60}{_RESET}")
    print(f"{_BOLD}What each option means for this document:{_RESET}\n")
    print(explanation.body)
    print(f"{_CYAN}{'─' * 60}{_RESET}\n")


async def _prompt_taxonomy(
    lumper: TaxonomyProposal,
    splitter: TaxonomyProposal,
    taxonomy_path: Path,
    router,
    issues: list[str],
    cfg,
) -> dict:
    """
    yay-style stdin prompt. Returns an approved taxonomy dict.
    Falls back to auto-merge if stdin is not a TTY.
    """
    if not sys.stdin.isatty():
        return await _auto_merge(lumper, splitter, issues, router, cfg)

    _print_proposal("Lumper  — broad domains", lumper)
    _print_proposal("Splitter — fine-grained", splitter)

    loop = asyncio.get_event_loop()
    while True:
        print(f"\n{_BOLD}==>{_RESET} l/s/m/e/x (lumper/splitter/merge/edit/explain) [l]: ", end="", flush=True)
        choice = (await loop.run_in_executor(None, sys.stdin.readline)).strip().lower() or "l"

        if choice in ("x", "explain"):
            print(f"{_CYAN}==>{_RESET} Asking LLM to explain...", flush=True)
            await _explain_taxonomy(lumper, splitter, router)
            _print_proposal("Lumper  — broad domains", lumper)
            _print_proposal("Splitter — fine-grained", splitter)
            continue

        if choice in ("s", "splitter"):
            domains = splitter.domains
            mode = "splitter"
        elif choice in ("e", "edit"):
            taxonomy_path.write_text(json.dumps(
                {"status": "pending", "domains": [d.model_dump() for d in lumper.domains]},
                indent=2,
            ))
            editor = os.environ.get("EDITOR", "vi")
            subprocess.call([editor, str(taxonomy_path)])
            data = json.loads(taxonomy_path.read_text())
            data["status"] = "approved"
            data.setdefault("mode", "edited")
            return data
        elif choice in ("m", "merge"):
            return await _auto_merge(lumper, splitter, issues, router, cfg)
        else:
            domains = lumper.domains
            mode = "lumper"

        print(f"{_GREEN}==>{_RESET} Approved ({mode}, {len(domains)} domains)\n", flush=True)
        return {"status": "approved", "mode": mode, "domains": [d.model_dump() for d in domains]}


async def _auto_merge(lumper, splitter, issues, router, cfg) -> dict:
    merge_msg = [{
        "role": "user",
        "content": _AUTO_TAXONOMY_PROMPT.format(
            lumper=lumper.model_dump_json(),
            splitter=splitter.model_dump_json(),
            issues=json.dumps(issues),
        ),
    }]

    class _DomainList(TaxonomyProposal):
        pass

    merged = await router.call(merge_msg, _DomainList)
    return {"status": "approved", "mode": "auto", "domains": [d.model_dump() for d in merged.domains]}

_LUMPER_PROMPT = """\
You are a domain ontologist. Given these core terms from a technical document, \
propose the FEWEST domains that cleanly partition all terms.
Maximum 8 domains. Each proposed domain must correspond to a real academic field.
Strategy: LUMPER — prefer broad domains over narrow ones.

Core terms: {terms}

Respond with a TaxonomyProposal with strategy="lumper".
"""

_SPLITTER_PROMPT = """\
You are a domain ontologist. Given these core terms from a technical document, \
propose the finest-grained domain distinctions the terms support.
Maximum 8 domains. Each proposed domain must correspond to a real academic field.
Strategy: SPLITTER — prefer specific subfields over broad categories.

Core terms: {terms}

Respond with a TaxonomyProposal with strategy="splitter".
"""

_CLASSIFIER_PROMPT = """\
Classify each term into exactly one domain from the locked taxonomy.
No new domains. Use the domain IDs exactly as given.

Taxonomy domains: {domains}
Terms to classify: {terms}

Respond with a list of TermClassification objects.
"""

_AUTO_TAXONOMY_PROMPT = """\
You are reviewing two domain taxonomy proposals for a technical document.
Choose the better proposal or merge them. Return a single list of DomainProposal objects.

Lumper proposal: {lumper}
Splitter proposal: {splitter}
OpenAlex validation issues: {issues}

Prefer domains that are broad enough to be meaningful but specific enough to partition \
the terms cleanly. Validate that each domain maps to a real academic field.
"""

_EXPLAIN_PROMPT = """\
You are helping a user decide how to organize a knowledge expansion pipeline for a technical document.
Two taxonomy strategies were proposed — lumper (broad) and splitter (fine-grained).

Lumper proposal: {lumper}
Splitter proposal: {splitter}

Write a plain-English explanation (no JSON, no headers) covering:
1. What each proposed domain actually covers, in 1-2 sentences each.
2. The practical difference between lumper and splitter for this specific document:
   what does the user gain or lose by choosing broad vs. fine-grained domains?
3. Downstream impact: how does the choice affect the research depth, bibliography, \
   and the final knowledge document the pipeline produces?
4. Your recommendation and why.

Keep it concise — around 200-300 words. Write directly to the user as "you".
"""


def _validate_openalex(domain_label: str, timeout: int = 10) -> dict | None:
    try:
        resp = httpx.get(
            "https://api.openalex.org/concepts",
            params={"search": domain_label, "per-page": "1"},
            timeout=timeout,
        )
        data = resp.json()
        results = data.get("results", [])
        if results:
            return {"id": results[0]["id"], "level": results[0].get("level")}
    except Exception:
        pass
    return None


def _taxonomy_is_approved(state_dir: Path) -> bool:
    p = state_dir / "taxonomy.json"
    if not p.exists():
        return False
    data = json.loads(p.read_text())
    return data.get("status") == "approved"


async def run(
    state: PipelineState,
    cfg: Config,
    auto_taxonomy: bool = False,
) -> None:
    state_dir = Path(state["state_dir"])
    audit_dir = state_dir / "audit"
    audit_dir.mkdir(exist_ok=True)

    if stage_is_complete(state_dir, 2):
        emit({"event": "stage_skipped", "stage": 2, "reason": "already_complete"})
        return

    emit({"event": "stage_start", "stage": 2})

    terms_data = json.loads((state_dir / "terms.json").read_text())
    core_terms = [t["name"] for t in terms_data if t["centrality"] == "core"]
    core_terms_str = ", ".join(core_terms)

    router = make_router("classifier", cfg)

    # --- Phase 1: Ontology lock ---
    if not _taxonomy_is_approved(state_dir):
        emit({"event": "stage2_phase1_start", "core_term_count": len(core_terms)})

        lumper_msg = [{"role": "user", "content": _LUMPER_PROMPT.format(terms=core_terms_str)}]
        splitter_msg = [{"role": "user", "content": _SPLITTER_PROMPT.format(terms=core_terms_str)}]

        lumper_result, splitter_result = await asyncio.gather(
            router.call(lumper_msg, TaxonomyProposal),
            router.call(splitter_msg, TaxonomyProposal),
        )

        # OpenAlex validation — record issues but do NOT mutate domain labels
        # (labels propagate into section headings; [UNVALIDATED] pollutes the output)
        issues = []
        openalex_timeout = cfg.timeouts.get("openalex_seconds", 10)
        for domain in lumper_result.domains + splitter_result.domains:
            openalex = _validate_openalex(domain.label, timeout=openalex_timeout)
            if openalex:
                domain.openalex_concept_id = openalex["id"]
                domain.openalex_level = openalex["level"]
            else:
                issues.append(f"{domain.label} (no OpenAlex match)")

        (audit_dir / "taxonomy_a.json").write_text(lumper_result.model_dump_json(indent=2))
        (audit_dir / "taxonomy_b.json").write_text(splitter_result.model_dump_json(indent=2))

        if auto_taxonomy:
            taxonomy = await _auto_merge(
                lumper_result, splitter_result, issues, router, cfg
            )
        else:
            taxonomy = await _prompt_taxonomy(
                lumper_result, splitter_result,
                state_dir / "taxonomy.json",
                router, issues, cfg,
            )

        (state_dir / "taxonomy.json").write_text(json.dumps(taxonomy, indent=2))
        emit({
            "event": "taxonomy_approved",
            "mode": taxonomy.get("mode", "manual"),
            "domain_count": len(taxonomy["domains"]),
        })

    taxonomy = json.loads((state_dir / "taxonomy.json").read_text())
    domains = taxonomy["domains"]
    domain_ids = [d["id"] for d in domains]

    # --- Phase 2: Classification ---
    emit({"event": "stage2_phase2_start", "domain_count": len(domains)})

    # Build per-domain keyword sets from example_terms + label words for scoring
    domain_keywords: list[tuple[str, set[str]]] = []
    for d in domains:
        kws: set[str] = set()
        for t in d.get("example_terms", []):
            kws.update(t.lower().split())
            kws.add(t.lower())
        for word in d["label"].lower().split():
            if len(word) > 3:
                kws.add(word)
        for word in d.get("definition", "").lower().split():
            if len(word) > 4:
                kws.add(word)
        domain_keywords.append((d["id"], kws))

    def _classify_term(name: str) -> str | None:
        name_lower = name.lower()
        name_tokens = set(name_lower.split())
        scores: dict[str, int] = {}
        for did, kws in domain_keywords:
            score = 0
            # Full phrase match scores highest
            if name_lower in kws:
                score += 5
            # Token overlap
            score += len(name_tokens & kws)
            # Substring match on multi-word example terms
            for kw in kws:
                if " " in kw and kw in name_lower:
                    score += 2
            scores[did] = score
        best_id = max(scores, key=lambda d: scores[d])
        if scores[best_id] > 0:
            return best_id
        # No keyword match — fall back to least-populated domain to avoid catch-all
        # bias (a domain with zero matches shouldn't absorb all unclassified terms)
        if domain_counts:
            return min(domain_counts, key=lambda did: domain_counts.get(did, 0))
        return domains[0]["id"] if domains else None

    nodes: list[GraphNode] = []
    conflicts = []
    domain_counts: dict[str, int] = {d["id"]: 0 for d in domains}

    for term in terms_data:
        name = term["name"]
        domain_id = _classify_term(name)
        if domain_id is None:
            continue
        domain_counts[domain_id] = domain_counts.get(domain_id, 0) + 1

        node = GraphNode(
            id=f"n_{name.replace(' ', '_').replace('-', '_')[:40]}",
            name=name,
            domain=domain_id,
            tier=cfg.graph_defaults.node_tier,
            xp=cfg.graph_defaults.node_xp,
            from_source_doc=True,
            centrality=term["centrality"],
        )
        nodes.append(node)

    graph = KnowledgeGraph(
        nodes=nodes,
        edges=[],
        domains=[DomainProposal(**d) for d in domains],
    )

    (state_dir / "graph.json").write_text(graph.model_dump_json(indent=2, by_alias=True))
    (audit_dir / "classification_conflicts.json").write_text(json.dumps(conflicts, indent=2))

    # Update state with domain IDs
    state["domain_ids"] = domain_ids

    mark_stage_complete(state_dir, 2)
    emit({
        "event": "stage_complete",
        "stage": 2,
        "node_count": len(nodes),
        "domain_count": len(domains),
        "conflict_count": len(conflicts),
        "domain_term_counts": domain_counts,
        "artifact": str(state_dir / "graph.json"),
    })
