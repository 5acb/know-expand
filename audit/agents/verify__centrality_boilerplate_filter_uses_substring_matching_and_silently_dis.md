# Agent: verify__centrality_boilerplate_filter_uses_substring_matching_and_silently_dis
**ID:** `agent-a43edf29c866b8aa0`  **Turns:** 25

## Prompt
```
You are an adversarial verifier. A code auditor claims the following about the repo at /Users/cyan/ccr/know-expand:

TITLE: Centrality boilerplate filter uses substring matching and silently discards legitimate core terms
LAYER: execution
SEVERITY: high
CLAIM: compute_centrality rejects any term whose lowercased form *contains* a boilerplate string (centrality.py:20-22: `any(bp in name for bp in BOILERPLATE)`). Verified empirically: 'hash table', 'lookup table', 'zero-knowledge proof', 'proof of stake', and 'configure' all return None (dropped). For a paper on cryptography or data structures, the document's central terms are silently erased before extraction, degrading every downstream stage (graph, audit, research) with no event emitted. tests/test_centrality.py:48-49 even enshrines this ('Terms containing boilerplate substrings are excluded') — the bug is codified as intent. The filter should match whole tokens (or exact term equality), not substrings.
FILES: /Users/cyan/ccr/know-expand/know_expand/centrality.py:20-22, /Users/cyan/ccr/know-expand/tests/test_centrality.py:48-49

Read the actual code and try to REFUTE this claim. Check: does the cited code actually behave as claimed? Is the severity inflated? Is there mitigating code elsewhere (search the repo)? If the claim is materially correct, confirm it. Default to refuted if the evidence is ambiguous.
```

**[4] tool→Read:** `/Users/cyan/ccr/know-expand/know_expand/centrality.py` 

**[5] tool→Read:** `/Users/cyan/ccr/know-expand/tests/test_centrality.py` 

<details><summary>[6] tool result (1190 chars)</summary>

```
1	from know_expand.config import CentralityConfig
2	
3	_DEFAULT = CentralityConfig()
4	
5	BOILERPLATE: frozenset[str] = frozenset({
6	    "introduction", "methodology", "conclusion", "figure", "table",
7	    "theorem", "proof", "lemma", "et al", "appendix", "section",
8	    "related work", "abstract", "references", "background", "overview",
9	})
10	
11	
12	def compute_centrality(
13	    term: str,
14	    occurrences: int,
15	    chunk_count: int,
16	    structural_zones: set[str],
17	    cfg: CentralityConfig = _DEFAULT,
18	) -> str | None:
19	    """Return 'core', 'supporting', 'incidental', or None (boilerplate)."""
20	    name = term.lower()
21	    if any(bp in name for bp in BOILERPLATE):
22	        return None
23	
24	    in_structure = name in {z.lower() for z in structural_zones}
25	    effective = occurrences * (cfg.structural_weight if in_structure else 1.0)
26	
27	    if effective >= cfg.core_effective_threshold:
28	        return "core"
29	    if chunk_count > 0 and occurrences / chunk_count >= cfg.core_chunk_fraction:
30	        return "core"
31	    if effective >= cfg.supporting_effective_threshold:
32	        return "supporting"
33	    return "incidental"
34	
```
</details>

<details><summary>[7] tool result (1954 chars)</summary>

```
1	from know_expand.centrality import compute_centrality
2	from know_expand.config import CentralityConfig
3	
4	_CFG = CentralityConfig()
5	
6	
7	def test_boilerplate_excluded():
8	    assert compute_centrality("introduction", 50, 10, set(), _CFG) is None
9	    assert compute_centrality("methodology", 30, 10, set(), _CFG) is None
10	    assert compute_centrality("conclusion", 20, 10, set(), _CFG) is None
11	
12	
13	def test_core_by_frequency():
14	    # 6 occurrences across 10 chunks → effective=6 >= threshold 5 → core
15	    assert compute_centrality("PagedAttention", 6, 10, set(), _CFG) == "core"
16	
17	
18	def test_core_by_chunk_fraction():
19	    # 4 occurrences in 10 chunks → 0.4 >= 0.3 → core
20	    assert compute_centrality("LangGraph", 4, 10, set(), _CFG) == "core"
21	
22	
23	def test_structural_weight_promotes():
24	    # 2 occurrences normally → supporting; in structural zone → effective=4 → supporting still
25	    result = compute_centrality("DSPy", 2, 20, {"dspy"}, _CFG)
26	    assert result in ("supporting", "core")
27	
28	
29	def test_structural_weight_lifts_to_core():
30	    # 3 occurrences in structural zone → effective=6 >= 5 → core
31	    assert compute_centrality("Docling", 3, 20, {"docling"}, _CFG) == "core"
32	
33	
34	def test_structural_weight_is_multiplier_not_bypass():
35	    # 1 occurrence in structural zone → effective=2 → supporting, not core
36	    result = compute_centrality("some_dataset_name", 1, 20, {"some_dataset_name"}, _CFG)
37	    assert result == "supporting"
38	
39	
40	def test_incidental():
41	    assert compute_centrality("widget", 1, 20, set(), _CFG) == "incidental"
42	
43	
44	def test_supporting():
45	    assert compute_centrality("attention_mask", 2, 20, set(), _CFG) == "supporting"
46	
47	
48	def test_boilerplate_partial_match():
49	    # Terms containing boilerplate substrings are excluded
50	    assert compute_centrality("introduction section", 10, 10, set(), _CFG) is None
51	
```
</details>

**[10] tool→Bash:** `grep -rn "compute_centrality\|BOILERPLATE\|centrality" /Users/cyan/ccr/know-expand/know_expand --include="*.py" | grep -` — Find usages of compute_centrality in the codebase

<details><summary>[11] tool result (3454 chars)</summary>

```
/Users/cyan/ccr/know-expand/know_expand/config.py:100:    centrality: CentralityConfig
/Users/cyan/ccr/know-expand/know_expand/config.py:129:        centrality=CentralityConfig(**raw["centrality"]),
/Users/cyan/ccr/know-expand/know_expand/centrality.py:5:BOILERPLATE: frozenset[str] = frozenset({
/Users/cyan/ccr/know-expand/know_expand/centrality.py:12:def compute_centrality(
/Users/cyan/ccr/know-expand/know_expand/centrality.py:21:    if any(bp in name for bp in BOILERPLATE):
/Users/cyan/ccr/know-expand/know_expand/stages/s1_assess.py:109:            key=lambda t: (tier_rank.get(t.get("centrality", "incidental"), 2),
/Users/cyan/ccr/know-expand/know_expand/stages/s1_assess.py:707:        # centrality-ranked concepts. Use it directly — no LLM call needed.
/Users/cyan/ccr/know-expand/know_expand/stages/s3_graph.py:280:    core_terms = [t["name"] for t in terms_data if t["centrality"] == "core"]
/Users/cyan/ccr/know-expand/know_expand/stages/s3_graph.py:395:            centrality=term["centrality"],
/Users/cyan/ccr/know-expand/know_expand/stages/s2_extract.py:17:from know_expand.centrality import compute_centrality, BOILERPLATE
/Users/cyan/ccr/know-expand/know_expand/stages/s2_extract.py:76:        if t and t not in BOILERPLATE:
/Users/cyan/ccr/know-expand/know_expand/stages/s2_extract.py:80:        if t and t not in BOILERPLATE:
/Users/cyan/ccr/know-expand/know_expand/stages/s2_extract.py:282:    academic_core = [t for t in terms if t.centrality == "core"
/Users/cyan/ccr/know-expand/know_expand/stages/s2_extract.py:327:        if tt == "academic" and not is_grounded and t.centrality == "core":
/Users/cyan/ccr/know-expand/know_expand/stages/s2_extract.py:332:                "centrality": "supporting",
/Users/cyan/ccr/know-expand/know_expand/stages/s2_extract.py:424:    clusters = _alias_clusters(flat, cfg.centrality.alias_cosine_threshold, embedding_model)
/Users/cyan/ccr/know-expand/know_expand/stages/s2_extract.py:443:    # Compute centrality
/Users/cyan/ccr/know-exp...[truncated]
```
</details>

**[12] tool→Bash:** `uv run python -c "
from know_expand.centrality import compute_centrality
for t in ['hash table', 'lookup table', 'zero-k` — Empirically test boilerplate filter against the claimed terms

<details><summary>[13] tool result (253 chars)</summary>

```
'hash table' -> None
'lookup table' -> None
'zero-knowledge proof' -> None
'proof of stake' -> None
'configure' -> None
'tablet' -> None
'proofreading' -> None
'cross-section' -> None
'intersection' -> None
'figure-eight knot' -> None
'overview' -> None
```
</details>

**[16] tool→Read:** `/Users/cyan/ccr/know-expand/know_expand/stages/s2_extract.py` 

<details><summary>[17] tool result (1720 chars)</summary>

```
60	    t0 = time.monotonic()
61	
62	    out_path = map_dir / f"map_{chunk_id}.json"
63	    done_path = out_path.with_suffix(".json.done")
64	    if done_path.exists():
65	        cached = json.loads(out_path.read_text())
66	        emit({"event": "chunk_map_cached", "chunk_id": chunk_id, "term_count": len(cached)})
67	        return cached
68	
69	    emit({"event": "chunk_map_start", "chunk_id": chunk_id, "text_len": len(text)})
70	
71	    # Signal A — spaCy lexical (on code-stripped text)
72	    doc = nlp(nlp_text)
73	    lexical: dict[str, int] = {}
74	    for chunk_span in doc.noun_chunks:
75	        t = chunk_span.text.lower().strip()
76	        if t and t not in BOILERPLATE:
77	            lexical[t] = lexical.get(t, 0) + 1
78	    for ent in doc.ents:
79	        t = ent.text.lower().strip()
80	        if t and t not in BOILERPLATE:
81	            lexical[t] = lexical.get(t, 0) + 1
82	
83	    # Signal C — KeyBERT embedding (on code-stripped text)
84	    keywords = kw_model.extract_keywords(
85	        nlp_text,
86	        keyphrase_ngram_range=(cfg.keyword_extraction.ngram_min, cfg.keyword_extraction.ngram_max),
87	        stop_words="english",
88	        top_n=cfg.keyword_extraction.top_n,
89	    )
90	    keybert_terms = {kw.lower() for kw, _ in keywords}
91	
92	    # Signal B — LLM conceptual
93	    messages = [{"role": "user", "content": _MAP_PROMPT.format(text=text[:cfg.chunking.llm_map_char_limit])}]
94	    llm_result: TermInventory = await router.call(messages, TermInventory)
95	
96	    # Merge: lexical + keybert into base set; LLM adds conceptual terms
97	    merged: dict[str, TermOccurrence] = {}
98	    for term, count in lexical.items():
99	        merged[term] = TermOccurrence(
```
</details>

**[18] tool→Read:** `/Users/cyan/ccr/know-expand/know_expand/stages/s2_extract.py` 

<details><summary>[19] tool result (2515 chars)</summary>

```
420	
421	    flat = list(aggregated.values())
422	
423	    # Alias clustering via BGE-M3 cosine + Union-Find
424	    clusters = _alias_clusters(flat, cfg.centrality.alias_cosine_threshold, embedding_model)
425	
426	    # Merge clusters: keep the longest name as canonical
427	    canonical_map: dict[str, str] = {}  # member -> canonical
428	    for canonical, members in clusters.items():
429	        best = max(members, key=len)
430	        for m in members:
431	            canonical_map[m] = best
432	
433	    merged: dict[str, TermOccurrence] = {}
434	    for t in flat:
435	        canon = canonical_map.get(t.name, t.name)
436	        if canon not in merged:
437	            merged[canon] = TermOccurrence(
438	                name=canon,
439	                aliases=[m for m in clusters.get(canon, [canon]) if m != canon],
440	            )
441	        merged[canon].occurrence_count += t.occurrence_count
442	
443	    # Compute centrality
444	    chunk_count = len(chunk_paths)
445	    canonical_terms: list[CanonicalTerm] = []
446	    for name, t in merged.items():
447	        tier = compute_centrality(
448	            name, t.occurrence_count, chunk_count, structural_zones, cfg.centrality
449	        )
450	        if tier is None:
451	            continue
452	        canonical_terms.append(CanonicalTerm(
453	            name=name,
454	            aliases=t.aliases,
455	            centrality=tier,
456	            occurrence_count=t.occurrence_count,
457	            in_structural_zones=name.lower() in {z.lower() for z in structural_zones},
458	        ))
459	
460	    canonical_terms.sort(key=lambda t: t.occurrence_count, reverse=True)
461	
462	    # Classify terms by knowledge type and ground academic core terms via SS.
463	    classifier_router = make_router("classifier", cfg)
464	    canonical_terms = await _classify_and_ground_terms(canonical_terms, cfg, classifier_router)
465	
466	    terms_path = state_dir / "terms.json"
467	    terms_path.write_text(
468	        jso...[truncated]
```
</details>

## Structured Output
```json
{
  "verdict": "confirmed",
  "reasoning": "Empirically verified: compute_centrality (know_expand/centrality.py:20-22) uses substring containment (`any(bp in name for bp in BOILERPLATE)`), and all five claimed terms ('hash table', 'lookup table', 'zero-knowledge proof', 'proof of stake', 'configure') return None, plus collateral like 'intersection', 'cross-section', 'tablet', 'figure-eight knot', 'pumping lemma'. The drop is on the live extraction path: s2_extract.py:447-451 does `if tier is None: continue` with no event emitted, so the term never reaches terms.json \u2014 the sole input for S1 core-term interview, S3 graph, S4 audit, and S5 research. No mitigating or recovery code exists anywhere in the repo. Notably, the per-chunk filters in the same stage (s2_extract.py:76,80) use exact membership (`t not in BOILERPLATE`), so the substring semantics in the centrality pass contradict the codebase's own earlier filter \u2014 evidence the behavior is a bug, not design. tests/test_centrality.py:48-50 (test_boilerplate_partial_match) does codify the substring behavior as intent ('Terms containing boilerplate substrings are excluded'). Severity 'high' is justified: for crypto/data-structure/math documents the central terms are silently erased before extraction, degrading every downstream stage with zero observability. Only trivial inaccuracy: the test assert is at line 50, not 48-49 (the comment is at 49) \u2014 immaterial."
}
```

<details><summary>[23] tool result (39 chars)</summary>

```
Structured output provided successfully
```
</details>

**[24] assistant:** Verification complete — verdict: confirmed.
