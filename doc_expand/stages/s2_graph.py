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

    print(f"\n{_BOLD}==>{_RESET} l/s/m/e (lumper/splitter/merge/edit) [l]: ", end="", flush=True)

    loop = asyncio.get_event_loop()
    choice = (await loop.run_in_executor(None, sys.stdin.readline)).strip().lower() or "l"

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

        # OpenAlex validation
        issues = []
        openalex_timeout = cfg.timeouts.get("openalex_seconds", 10)
        for domain in lumper_result.domains + splitter_result.domains:
            openalex = _validate_openalex(domain.label, timeout=openalex_timeout)
            if openalex:
                domain.openalex_concept_id = openalex["id"]
                domain.openalex_level = openalex["level"]
            else:
                issues.append(f"{domain.label} [UNVALIDATED]")
                domain.label = f"{domain.label} [UNVALIDATED]"

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
    # TODO Phase 6: replace stub with proper TermClassification structured output.
    # Two parallel classifiers (lumper vs splitter strategy) will vote per-term;
    # conflicts go into classification_conflicts.json for the audit stage.
    emit({"event": "stage2_phase2_start", "domain_count": len(domains)})

    nodes: list[GraphNode] = []
    conflicts = []
    default_domain = domains[0]["id"] if domains else "general"

    for term in terms_data:
        name = term["name"]
        domain_a = default_domain

        node = GraphNode(
            id=f"n_{name.replace(' ', '_').replace('-', '_')[:40]}",
            name=name,
            domain=domain_a,
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
        "artifact": str(state_dir / "graph.json"),
    })
