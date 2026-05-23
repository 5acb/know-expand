from know_expand.config import CentralityConfig

_DEFAULT = CentralityConfig()

BOILERPLATE: frozenset[str] = frozenset({
    "introduction", "methodology", "conclusion", "figure", "table",
    "theorem", "proof", "lemma", "et al", "appendix", "section",
    "related work", "abstract", "references", "background", "overview",
})


def compute_centrality(
    term: str,
    occurrences: int,
    chunk_count: int,
    structural_zones: set[str],
    cfg: CentralityConfig = _DEFAULT,
) -> str | None:
    """Return 'core', 'supporting', 'incidental', or None (boilerplate)."""
    name = term.lower()
    if any(bp in name for bp in BOILERPLATE):
        return None

    in_structure = name in {z.lower() for z in structural_zones}
    effective = occurrences * (cfg.structural_weight if in_structure else 1.0)

    if effective >= cfg.core_effective_threshold:
        return "core"
    if chunk_count > 0 and occurrences / chunk_count >= cfg.core_chunk_fraction:
        return "core"
    if effective >= cfg.supporting_effective_threshold:
        return "supporting"
    return "incidental"
