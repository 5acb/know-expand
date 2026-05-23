from know_expand.union_find import UnionFind


def test_single_element():
    uf = UnionFind()
    assert uf.find("a") == "a"


def test_union_merges():
    uf = UnionFind()
    uf.union("a", "b")
    assert uf.find("a") == uf.find("b")


def test_clusters():
    uf = UnionFind()
    uf.union("PagedAttention", "paged attention")
    uf.union("LLM", "large language model")
    clusters = uf.clusters()
    # Both aliases share a canonical root
    roots = {uf.find("PagedAttention"), uf.find("paged attention")}
    assert len(roots) == 1
    roots2 = {uf.find("LLM"), uf.find("large language model")}
    assert len(roots2) == 1


def test_path_compression():
    uf = UnionFind()
    uf.union("a", "b")
    uf.union("b", "c")
    uf.union("c", "d")
    root = uf.find("a")
    # After find, all should point directly to root
    assert uf.find("b") == root
    assert uf.find("c") == root
    assert uf.find("d") == root


def test_no_cross_cluster_merge():
    uf = UnionFind()
    uf.union("x", "y")
    uf.union("p", "q")
    assert uf.find("x") != uf.find("p")
