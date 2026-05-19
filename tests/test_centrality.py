from doc_expand.centrality import compute_centrality
from doc_expand.config import CentralityConfig

_CFG = CentralityConfig()


def test_boilerplate_excluded():
    assert compute_centrality("introduction", 50, 10, set(), _CFG) is None
    assert compute_centrality("methodology", 30, 10, set(), _CFG) is None
    assert compute_centrality("conclusion", 20, 10, set(), _CFG) is None


def test_core_by_frequency():
    # 6 occurrences across 10 chunks → effective=6 >= threshold 5 → core
    assert compute_centrality("PagedAttention", 6, 10, set(), _CFG) == "core"


def test_core_by_chunk_fraction():
    # 4 occurrences in 10 chunks → 0.4 >= 0.3 → core
    assert compute_centrality("LangGraph", 4, 10, set(), _CFG) == "core"


def test_structural_weight_promotes():
    # 2 occurrences normally → supporting; in structural zone → effective=4 → supporting still
    result = compute_centrality("DSPy", 2, 20, {"dspy"}, _CFG)
    assert result in ("supporting", "core")


def test_structural_weight_lifts_to_core():
    # 3 occurrences in structural zone → effective=6 >= 5 → core
    assert compute_centrality("Docling", 3, 20, {"docling"}, _CFG) == "core"


def test_structural_weight_is_multiplier_not_bypass():
    # 1 occurrence in structural zone → effective=2 → supporting, not core
    result = compute_centrality("some_dataset_name", 1, 20, {"some_dataset_name"}, _CFG)
    assert result == "supporting"


def test_incidental():
    assert compute_centrality("widget", 1, 20, set(), _CFG) == "incidental"


def test_supporting():
    assert compute_centrality("attention_mask", 2, 20, set(), _CFG) == "supporting"


def test_boilerplate_partial_match():
    # Terms containing boilerplate substrings are excluded
    assert compute_centrality("introduction section", 10, 10, set(), _CFG) is None
