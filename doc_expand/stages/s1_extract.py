import asyncio
import json
from pathlib import Path

import numpy as np
import spacy
from keybert import KeyBERT
from sentence_transformers import SentenceTransformer

from doc_expand.agents.base import make_router
from doc_expand.agents.schemas import TermInventory, TermOccurrence, CanonicalTerm
from doc_expand.centrality import compute_centrality, BOILERPLATE
from doc_expand.config import Config
from doc_expand.state import (
    PipelineState, emit, mark_stage_complete, stage_is_complete,
    sentinel_exists, write_sentinel,
)
from doc_expand.union_find import UnionFind

_MAP_PROMPT = """\
You are a domain expert reading a chunk of a technical document.
Extract every substantive concept that a domain expert would recognise as load-bearing.
Go beyond literal text: include implicit domain concepts implied by the content.
Exclude boilerplate structural terms (introduction, methodology, conclusion, etc.).

Chunk:
{text}

For each term, provide: name, aliases (variant spellings/abbreviations), \
co_occurring_terms (terms that appear near it), a short context_snippet, \
and occurrence_count within this chunk.
"""


async def _map_chunk(
    chunk_path: Path,
    map_dir: Path,
    nlp,
    kw_model: KeyBERT,
    router,
    structural_zones: set[str],
) -> list[TermOccurrence]:
    chunk = json.loads(chunk_path.read_text())
    chunk_id = chunk["chunk_id"]
    text = chunk["text"]

    out_path = map_dir / f"map_{chunk_id}.json"
    done_path = out_path.with_suffix(".json.done")
    if done_path.exists():
        return json.loads(out_path.read_text())

    # Signal A — spaCy lexical
    doc = nlp(text)
    lexical: dict[str, int] = {}
    for chunk_span in doc.noun_chunks:
        t = chunk_span.text.lower().strip()
        if t and t not in BOILERPLATE:
            lexical[t] = lexical.get(t, 0) + 1
    for ent in doc.ents:
        t = ent.text.lower().strip()
        if t and t not in BOILERPLATE:
            lexical[t] = lexical.get(t, 0) + 1

    # Signal C — KeyBERT embedding
    keywords = kw_model.extract_keywords(
        text, keyphrase_ngram_range=(1, 3), stop_words="english", top_n=20
    )
    keybert_terms = {kw.lower() for kw, _ in keywords}

    # Signal B — LLM conceptual
    messages = [{"role": "user", "content": _MAP_PROMPT.format(text=text[:6000])}]
    llm_result: TermInventory = await router.call(messages, TermInventory)

    # Merge: lexical + keybert into base set; LLM adds conceptual terms
    merged: dict[str, TermOccurrence] = {}
    for term, count in lexical.items():
        merged[term] = TermOccurrence(
            name=term,
            occurrence_count=count,
            context_snippet=text[:100],
        )
    for term in keybert_terms:
        if term not in merged:
            merged[term] = TermOccurrence(name=term, occurrence_count=1)
        else:
            merged[term].occurrence_count += 1  # both signals → boost
    for llm_term in llm_result.terms:
        name = llm_term.name.lower()
        if name not in merged:
            merged[name] = llm_term
        else:
            existing = merged[name]
            existing.aliases = list(set(existing.aliases + llm_term.aliases))
            existing.co_occurring_terms = list(
                set(existing.co_occurring_terms + llm_term.co_occurring_terms)
            )

    result = list(merged.values())
    out_path.write_text(json.dumps([t.model_dump() for t in result], indent=2))
    done_path.touch()
    return result


def _alias_clusters(
    all_terms: list[TermOccurrence],
    cosine_threshold: float,
    embedding_model: SentenceTransformer,
) -> dict[str, list[str]]:
    names = [t.name for t in all_terms]
    if not names:
        return {}
    embeddings = embedding_model.encode(names, normalize_embeddings=True)
    sim = np.dot(embeddings, embeddings.T)

    uf = UnionFind()
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            if sim[i, j] >= cosine_threshold:
                uf.union(names[i], names[j])
    return uf.clusters()


async def run(state: PipelineState, cfg: Config) -> None:
    state_dir = Path(state["state_dir"])

    if stage_is_complete(state_dir, 1):
        emit({"event": "stage_skipped", "stage": 1, "reason": "already_complete"})
        return

    emit({"event": "stage_start", "stage": 1})

    chunks_dir = state_dir / "chunks"
    map_dir = state_dir / "map_outputs"
    map_dir.mkdir(exist_ok=True)

    structural_zones: set[str] = set(
        json.loads((state_dir / "structural_zones.json").read_text())
    )

    nlp = spacy.load("en_core_web_sm")
    embedding_model = SentenceTransformer("BAAI/bge-m3")
    kw_model = KeyBERT(model=embedding_model)
    router = make_router("extractor", cfg)

    chunk_paths = sorted(chunks_dir.glob("chunk_*.json"))
    emit({"event": "stage1_map_start", "chunk_count": len(chunk_paths)})

    # Map phase — parallel per chunk
    async with asyncio.TaskGroup() as tg:
        tasks = [
            tg.create_task(
                _map_chunk(cp, map_dir, nlp, kw_model, router, structural_zones)
            )
            for cp in chunk_paths
        ]

    all_terms_nested = [t.result() for t in tasks]
    all_occurrences: list[TermOccurrence] = [
        TermOccurrence(**t) if isinstance(t, dict) else t
        for sublist in all_terms_nested
        for t in sublist
    ]

    # Reduce phase
    emit({"event": "stage1_reduce_start", "raw_term_count": len(all_occurrences)})

    # Aggregate occurrence counts across chunks
    aggregated: dict[str, TermOccurrence] = {}
    for t in all_occurrences:
        name = t.name.lower()
        if name not in aggregated:
            aggregated[name] = TermOccurrence(
                name=name, aliases=t.aliases[:],
                co_occurring_terms=t.co_occurring_terms[:]
            )
        aggregated[name].occurrence_count += t.occurrence_count

    flat = list(aggregated.values())

    # Alias clustering via BGE-M3 cosine + Union-Find
    clusters = _alias_clusters(flat, cfg.centrality.alias_cosine_threshold, embedding_model)

    # Merge clusters: keep the longest name as canonical
    canonical_map: dict[str, str] = {}  # member -> canonical
    for canonical, members in clusters.items():
        best = max(members, key=len)
        for m in members:
            canonical_map[m] = best

    merged: dict[str, TermOccurrence] = {}
    for t in flat:
        canon = canonical_map.get(t.name, t.name)
        if canon not in merged:
            merged[canon] = TermOccurrence(
                name=canon,
                aliases=[m for m in clusters.get(canon, [canon]) if m != canon],
            )
        merged[canon].occurrence_count += t.occurrence_count

    # Compute centrality
    chunk_count = len(chunk_paths)
    canonical_terms: list[CanonicalTerm] = []
    for name, t in merged.items():
        tier = compute_centrality(
            name, t.occurrence_count, chunk_count, structural_zones, cfg.centrality
        )
        if tier is None:
            continue
        canonical_terms.append(CanonicalTerm(
            name=name,
            aliases=t.aliases,
            centrality=tier,
            occurrence_count=t.occurrence_count,
            in_structural_zones=name.lower() in {z.lower() for z in structural_zones},
        ))

    canonical_terms.sort(key=lambda t: t.occurrence_count, reverse=True)

    terms_path = state_dir / "terms.json"
    terms_path.write_text(
        json.dumps([t.model_dump() for t in canonical_terms], indent=2)
    )

    core_count = sum(1 for t in canonical_terms if t.centrality == "core")
    mark_stage_complete(state_dir, 1)
    emit({
        "event": "stage_complete",
        "stage": 1,
        "total_terms": len(canonical_terms),
        "core_terms": core_count,
        "supporting_terms": sum(1 for t in canonical_terms if t.centrality == "supporting"),
        "artifact": str(terms_path),
    })
