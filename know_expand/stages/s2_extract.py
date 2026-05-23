import asyncio
import json
import logging
import re
import time
from pathlib import Path

import httpx
import numpy as np
import spacy
from keybert import KeyBERT
from sentence_transformers import SentenceTransformer

from know_expand.agents.base import make_router
from know_expand.agents.schemas import TermInventory, TermOccurrence, CanonicalTerm, TermKindBatch
from know_expand.bibliography import _ss_search, _get_ss_limiter
from know_expand.centrality import compute_centrality, BOILERPLATE
from know_expand.config import Config
from know_expand.state import (
    PipelineState, emit, mark_stage_complete, stage_is_complete,
)
from know_expand.union_find import UnionFind

_logger = logging.getLogger("know_expand.s2")

_MAP_PROMPT = """\
You are a domain expert reading a chunk of a technical document.
Extract every substantive concept that a domain expert would recognise as load-bearing.
Go beyond literal text: include implicit domain concepts implied by the content.

Strict exclusions (return nothing for these):
- Boilerplate structural terms: introduction, methodology, conclusion, etc.
- Code fragments, variable names, function names, class names, import paths
- JSON keys, string literals, Python tokens (str, None, True, False, etc.)
- File paths, URLs, version numbers, numeric literals
- Single-character tokens or generic NPs: "the user", "the output", "the default"

Chunk:
{text}

For each term, provide: name (clean English noun phrase, no code syntax), aliases \
(variant spellings/abbreviations), co_occurring_terms (terms that appear near it), \
a short context_snippet, and occurrence_count within this chunk.
"""


async def _map_chunk(
    chunk_path: Path,
    map_dir: Path,
    nlp,
    kw_model: KeyBERT,
    router,
    structural_zones: set[str],
    cfg: Config,
) -> list[TermOccurrence]:
    chunk = json.loads(chunk_path.read_text())
    chunk_id = chunk["chunk_id"]
    text = chunk["text"]
    nlp_text = _strip_code_from_text(text)  # code-free version for spaCy + KeyBERT
    t0 = time.monotonic()

    out_path = map_dir / f"map_{chunk_id}.json"
    done_path = out_path.with_suffix(".json.done")
    if done_path.exists():
        cached = json.loads(out_path.read_text())
        emit({"event": "chunk_map_cached", "chunk_id": chunk_id, "term_count": len(cached)})
        return cached

    emit({"event": "chunk_map_start", "chunk_id": chunk_id, "text_len": len(text)})

    # Signal A — spaCy lexical (on code-stripped text)
    doc = nlp(nlp_text)
    lexical: dict[str, int] = {}
    for chunk_span in doc.noun_chunks:
        t = chunk_span.text.lower().strip()
        if t and t not in BOILERPLATE:
            lexical[t] = lexical.get(t, 0) + 1
    for ent in doc.ents:
        t = ent.text.lower().strip()
        if t and t not in BOILERPLATE:
            lexical[t] = lexical.get(t, 0) + 1

    # Signal C — KeyBERT embedding (on code-stripped text)
    keywords = kw_model.extract_keywords(
        nlp_text,
        keyphrase_ngram_range=(cfg.keyword_extraction.ngram_min, cfg.keyword_extraction.ngram_max),
        stop_words="english",
        top_n=cfg.keyword_extraction.top_n,
    )
    keybert_terms = {kw.lower() for kw, _ in keywords}

    # Signal B — LLM conceptual
    messages = [{"role": "user", "content": _MAP_PROMPT.format(text=text[:cfg.chunking.llm_map_char_limit])}]
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
    emit({
        "event": "chunk_map_done",
        "chunk_id": chunk_id,
        "term_count": len(result),
        "elapsed_s": round(time.monotonic() - t0, 2),
    })
    return result


_STRIP_CHARS = "|*_#`- \t\n→←↑↓⇒⇐•·"
_BOX_DRAWING = set("─━│┃┄┅┆┇┈┉┊┋┌┍┎┏┐┑┒┓└┕┖┗┘┙┚┛├┝┞┟┠┡┢┣┤┥┦┧┨┩┪┫┬┭┮┯┰┱┲┳┴┵┶┷┸┹┺┻┼┽┾┿╀╁╂╃╄╅╆╇╈╉╊╋")
_SINGLE_TOKEN_STOPWORDS = frozenset({
    "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "is", "are", "was", "were", "be", "been",
    "being", "have", "has", "had", "do", "does", "did", "will", "would",
    "could", "should", "may", "might", "must", "can", "it", "its", "this",
    "that", "these", "those", "they", "them", "their", "we", "our", "you",
    "your", "he", "she", "him", "her", "what", "which", "who", "how",
    "when", "where", "why", "all", "each", "every", "both", "one", "two",
    "three", "four", "five", "six", "seven", "eight", "nine", "ten",
    "also", "then", "than", "such", "very", "more", "most", "any", "some",
    "not", "no", "nor", "so", "yet", "as", "if", "because", "since",
    "while", "although", "however", "therefore", "thus", "hence",
    # Python / code tokens
    "str", "int", "float", "bool", "none", "true", "false", "list", "dict",
    "tuple", "set", "type", "self", "cls", "args", "kwargs", "assert",
    "return", "yield", "raise", "pass", "break", "continue", "import",
    "from", "class", "def", "async", "await", "with", "lambda",
    # Generic document NPs that are noise
    "first", "second", "third", "last", "next", "new", "old", "same",
    "good", "bad", "large", "small", "high", "low", "right", "left",
    "zero", "null", "empty", "full", "default", "simple", "basic",
    "key", "value", "name", "item", "node", "edge", "path", "file",
    "doc", "page", "line", "text", "data", "code", "step", "stage",
    "state", "mode", "case", "type", "kind", "way", "part", "end",
    "note", "example", "result", "output", "input", "use", "user",
    "time", "number", "count", "size", "level", "order", "index",
    # Infrastructure / CLI junk
    "url", "uri", "sdk", "api", "cli", "uid", "id", "ids", "uuid",
    "phase", "trace", "flag", "log", "debug", "info", "warn", "error",
    "config", "param", "arg", "env", "var", "ref", "ptr", "buf",
    "signal", "signals", "progress", "version", "tag", "hash",
    "task", "tasks", "event", "events", "token", "tokens",
    "stage", "stages", "step", "steps", "run", "call", "calls",
    "chunk", "chunks", "batch", "retry", "timeout", "limit", "cap",
    "slot", "queue", "stack", "loop", "iter", "block", "lock",
    "format", "scheme", "spec", "template", "pattern", "model",
    "entry", "record", "row", "col", "field", "attr", "prop",
    "method", "func", "fn", "op", "ops", "cmd", "msg", "req", "resp",
    "src", "dst", "tmp", "dir", "dirs", "path", "paths",
    "mit", "gnu", "bsd",  # license abbreviations
})


_CODE_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`[^`\n]{1,80}`")
_JSON_FRAGMENT_RE = re.compile(r'^\s*[{"\[\]},:]')


def _strip_code_from_text(text: str) -> str:
    """Remove fenced code blocks and inline code for NLP signals (spaCy, KeyBERT)."""
    text = _CODE_FENCE_RE.sub(" ", text)
    text = _INLINE_CODE_RE.sub(" ", text)
    return text


def _clean_term(name: str) -> str | None:
    name = name.strip(_STRIP_CHARS)
    # Reject box-drawing characters
    if any(c in _BOX_DRAWING for c in name):
        return None
    # Reject terms that start with JSON/code fragment characters
    if _JSON_FRAGMENT_RE.match(name):
        return None
    # Reject terms containing backticks, braces, angle brackets (code artifacts)
    if any(c in name for c in "{}[]<>`\""):
        return None
    # Require enough alphabetic content
    alpha_count = sum(1 for c in name if c.isalpha())
    if alpha_count < 3:
        return None
    # Reject leading non-alphabetic Unicode (arrows, bullets absorbed into names)
    if name and not name[0].isalnum():
        return None
    # Reject single-token stopwords and code tokens; multi-word phrases are fine
    if " " not in name and name.lower() in _SINGLE_TOKEN_STOPWORDS:
        return None
    # Reject "the X", "a X", "an X" — article+noun pairs are never domain concepts
    words = name.lower().split()
    if len(words) == 2 and words[0] in ("the", "a", "an"):
        return None
    return name


_CLASSIFY_PROMPT = """\
Classify each term by its primary knowledge type:

"academic" — has a body of peer-reviewed research literature (papers, conferences, journals)
Examples: "transformer architecture", "knowledge graph", "rate limiting algorithms", "multi-agent systems"

"tool_library" — a specific software library, framework, CLI tool, file format, or named technology
Examples: "asyncio", "xelatex", "docling", "langgraph", "json", "pdf", "docker", "spacy"

"concept" — a design pattern, engineering concept, or domain idea explained from first principles
Examples: "pipeline orchestration", "token bucket", "alias clustering", "structural zone"

Terms: {terms_json}

Return a TermKindBatch. Every term in the input must appear in classifications.
"""


async def _classify_and_ground_terms(
    terms: list[CanonicalTerm],
    cfg: Config,
    router,
) -> list[CanonicalTerm]:
    """Classify terms by knowledge type, then ground academic core terms via SS.

    Steps:
    1. LLM batch classification into academic / tool_library / concept
    2. SS grounding for academic core terms only (tool_library/concept are not grounded)
    3. Apply term_type and grounding results to all terms
    """
    if not terms:
        return terms

    # Step 1: LLM classification in batches of 60
    all_term_names = [t.name for t in terms]
    batch_size = 60
    term_type_lookup: dict[str, str] = {}

    for batch_start in range(0, len(all_term_names), batch_size):
        batch = all_term_names[batch_start:batch_start + batch_size]
        try:
            msg = [{"role": "user", "content": _CLASSIFY_PROMPT.format(
                terms_json=json.dumps(batch, ensure_ascii=False)
            )}]
            result: TermKindBatch = await router.call(msg, TermKindBatch)
            for item in result.classifications:
                term_type_lookup[item.term.lower()] = item.term_type
        except Exception as exc:
            _logger.warning("s2 term classification batch failed: %s", exc)
            # Default to "academic" for unclassified terms in this batch

    type_counts = {"academic": 0, "tool_library": 0, "concept": 0}
    for t in terms:
        tt = term_type_lookup.get(t.name.lower(), "academic")
        type_counts[tt] = type_counts.get(tt, 0) + 1

    emit({
        "event": "s2_term_classification_done",
        "total": len(terms),
        "academic": type_counts["academic"],
        "tool_library": type_counts["tool_library"],
        "concept": type_counts["concept"],
    })

    # Step 2: SS grounding — only for academic core terms
    academic_core = [t for t in terms if t.centrality == "core"
                     and term_type_lookup.get(t.name.lower(), "academic") == "academic"]

    grounding: dict[str, bool] = {}

    if academic_core:
        emit({"event": "s1_concept_grounding_start", "core_term_count": len(academic_core)})

        async def _check_one(term: CanonicalTerm, http: httpx.AsyncClient) -> tuple[str, bool]:
            try:
                results = await _ss_search(term.name, http, cfg, limit=3)
                return (term.name, bool(results))
            except Exception as exc:
                _logger.warning("s1 grounding: SS search failed for %r: %s", term.name, exc)
                return (term.name, True)

        try:
            async with httpx.AsyncClient(timeout=15.0) as http:
                _get_ss_limiter(cfg)
                grounding_tasks = [_check_one(t, http) for t in academic_core]
                results_list = await asyncio.gather(*grounding_tasks, return_exceptions=True)
        except Exception as exc:
            _logger.warning("s1 concept grounding: SS unavailable, skipping (%s)", exc)
            emit({"event": "s1_concept_grounding_skipped", "reason": str(exc)[:120]})
            results_list = []

        for item in results_list:
            if isinstance(item, Exception):
                continue
            name, is_grounded = item
            grounding[name] = is_grounded

        ungrounded_names = [n for n, g in grounding.items() if not g]
        emit({
            "event": "s1_concept_grounding_done",
            "core_terms_checked": len(academic_core),
            "ungrounded": ungrounded_names,
        })

    # Step 3: Apply term_type and grounding to all terms
    updated: list[CanonicalTerm] = []
    for t in terms:
        tt = term_type_lookup.get(t.name.lower(), "academic")
        is_grounded = grounding.get(t.name, True)
        # Only demote academic core terms that failed SS grounding
        if tt == "academic" and not is_grounded and t.centrality == "core":
            _logger.warning(
                "s2 concept grounding: demoting %r core→supporting (no SS hits)", t.name
            )
            updated.append(t.model_copy(update={
                "centrality": "supporting",
                "grounded": False,
                "term_type": tt,
            }))
        else:
            updated.append(t.model_copy(update={
                "grounded": is_grounded if tt == "academic" else True,
                "term_type": tt,
            }))

    return updated


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

    if stage_is_complete(state_dir, 2):
        emit({"event": "stage_skipped", "stage": 2, "reason": "already_complete"})
        return

    emit({"event": "stage_start", "stage": 2})

    chunks_dir = state_dir / "chunks"
    map_dir = state_dir / "map_outputs"
    map_dir.mkdir(exist_ok=True)

    structural_zones: set[str] = set(
        json.loads((state_dir / "structural_zones.json").read_text())
    )

    nlp = spacy.load(cfg.nlp_models.spacy)
    embedding_model = SentenceTransformer(cfg.nlp_models.embedding)
    kw_model = KeyBERT(model=embedding_model)
    router = make_router("extractor", cfg)

    chunk_paths = sorted(chunks_dir.glob("chunk_*.json"))
    emit({"event": "stage1_map_start", "chunk_count": len(chunk_paths)})

    # Map phase — parallel per chunk
    async with asyncio.TaskGroup() as tg:
        tasks = [
            tg.create_task(
                _map_chunk(cp, map_dir, nlp, kw_model, router, structural_zones, cfg)
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
        name = _clean_term(t.name.lower())
        if name is None:
            continue
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

    # Classify terms by knowledge type and ground academic core terms via SS.
    classifier_router = make_router("classifier", cfg)
    canonical_terms = await _classify_and_ground_terms(canonical_terms, cfg, classifier_router)

    terms_path = state_dir / "terms.json"
    terms_path.write_text(
        json.dumps([t.model_dump() for t in canonical_terms], indent=2)
    )

    core_count = sum(1 for t in canonical_terms if t.centrality == "core")
    mark_stage_complete(state_dir, 2)
    emit({
        "event": "stage_complete",
        "stage": 2,
        "total_terms": len(canonical_terms),
        "core_terms": core_count,
        "supporting_terms": sum(1 for t in canonical_terms if t.centrality == "supporting"),
        "artifact": str(terms_path),
    })
