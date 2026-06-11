# Agent: verify__headline_product_claim_verified_citations___grounds_every_claim_is_n
**ID:** `agent-adbf9a58aef60d72e`  **Turns:** 37

## Prompt
```
You are an adversarial verifier. A code auditor claims the following about the repo at /Users/cyan/ccr/know-expand:

TITLE: Headline product claim 'verified citations / grounds every claim' is not what the system does
LAYER: objective
SEVERITY: critical
CLAIM: README.md:37-39 claims the product 'grounds every claim in verifiable, real citations' and ships 'verified citations'. What is actually verified: S8 marks a citation 'verified' iff its key exists in a bibliography JSON (s8_verify.py:72-75) — pure key membership, no check that the paper supports the claim. The semantic layer (s8_verify.py:107-167) only runs when an abstract exists, caps at 50 pairs/domain (line 139), and only surfaces relation=='contradicts' (line 158) — 'mentions' (paper does not support the claim) is silently dropped. Then S10 deletes every [NEEDS_CITATION]/[INFERRED]/[UNVALIDATED] marker before assembly (s10_assemble.py:333-362 _clean_section_text), so an unciteable claim is rendered indistinguishable from a cited one in the final PDF. All three hallucination paths (fabricated claim + irrelevant-but-real key; unciteable claim with stripped marker; invented key rendered as broken citeproc text) reach the reader. Either the claim must be weakened to 'citation keys are validated against a real bibliography' or the verification layer must be rebuilt.
FILES: /Users/cyan/ccr/know-expand/README.md:37-39, /Users/cyan/ccr/know-expand/know_expand/stages/s8_verify.py:72-75, /Users/cyan/ccr/know-expand/know_expand/stages/s8_verify.py:139-167, /Users/cyan/ccr/know-expand/know_expand/stages/s10_assemble.py:333-362

Read the actual code and try to REFUTE this claim. Check: does the cited code actually behave as claimed? Is the severity inflated? Is there mitigating code elsewhere (search the repo)? If the claim is materially correct, confirm it. Default to refuted if the evidence is ambiguous.
```

**[4] tool→Read:** `/Users/cyan/ccr/know-expand/README.md` 

<details><summary>[5] tool result (2242 chars)</summary>

```
25	```
26	
27	Each run writes to `runs/{run_id}/` containing `state/`, `logs/`, and `output/`.
28	
29	---
30	
31	## What it does
32	
33	A person reads a dense technical paper. They understand the words but not the field. They cannot ask good questions, cannot evaluate the claims, cannot know what to read next, cannot build on top of it.
34	
35	`know-expand` takes that document and produces a **navigable, ground-up knowledge expansion** — a structured field guide covering everything needed to understand the paper deeply, go beyond it, and do independent research.
36	
37	**Not a summary. Not a chatbot answer. Not a Q&A wrapper.** A full-stack knowledge construction pipeline that identifies every substantive concept in a document, maps those concepts into a principled domain taxonomy, augments each domain from first principles to the current research frontier, synthesizes cross-domain insights invisible within any single domain, and grounds every claim in verifiable, real citations.
38	
39	The reference output: a 480KB, 241-page, 81-node skill-tree document with verified citations, LaTeX mathematics, and technical diagrams — produced automatically from a single source paper.
40	
41	### Cost and latency
42	
43	**Cost:** a deep run is roughly 8 domains × (3 personas + 1 reconciler) × up to 3 critic rounds, plus adversarial loops in S4 and S7. ~150–300 LLM calls for the researcher/synthesizer role. At current Opus 4.7 pricing, expect **$50–150 per deep run** with Opus as primary researcher. Standard depth with Sonnet 4.6 is roughly $5–20. Monitor `state/audit/model_usage.jsonl` — every call is logged with `cost_usd`.
44	
45	**Latency:** 1–3 hours end-to-end at deep depth on a standard 8-domain paper. Bottlenecks are Semantic Scholar rate limits and LLM call volume. Survey depth on a short paper: 10–20 minutes.
46	
47	---
48	
49	## Pipeline
50	
51	### Stage order
52	
53	```
54	S0 Ingest → S2 Extract → S1 Assess → S3 Graph → S4 Audit → S5 Research
55	         → S6 Align → S...[truncated]
```
</details>

**[6] tool→Read:** `/Users/cyan/ccr/know-expand/know_expand/stages/s8_verify.py` 

<details><summary>[7] tool result (12293 chars)</summary>

```
1	"""Stage 6 — Verify: structural citation audit across all section files."""
2	
3	import json
4	import logging
5	import re
6	from pathlib import Path
7	
8	from know_expand.agents.base import make_router
9	from know_expand.agents.schemas import CitationAuditItem, CitationAuditResult, CitationCheck
10	from know_expand.config import Config
11	from know_expand.state import (
12	    PipelineState,
13	    emit,
14	    mark_stage_complete,
15	    stage_is_complete,
16	)
17	
18	_logger = logging.getLogger("know_expand.s8")
19	
20	# Pattern for [NEEDS_CITATION] markers
21	_NEEDS_CITATION_RE = re.compile(r"\[NEEDS_CITATION\]")
22	# Pattern for citation keys: [@key] or [key] where key looks like an identifier
23	# (contains letters, digits, underscores, hyphens; at least 3 chars)
24	_CITATION_KEY_RE = re.compile(r"\[@?([\w\-]{3,})\]")
25	# Strip fenced code blocks before scanning (avoids [@variable] false positives)
26	_FENCED_CODE_RE = re.compile(r"```.*?```", re.DOTALL)
27	_INLINE_CODE_RE = re.compile(r"`[^`\n]{1,80}`")
28	
29	_SENTENCE_RE = re.compile(r"[^.!?\n]{0,120}(?:\[[@\w\-]{3,}\]|\[NEEDS_CITATION\])[^.!?\n]{0,120}")
30	
31	# Pipeline artifact markers that should never appear as citation keys
32	_PIPELINE_MARKERS = frozenset({"NEEDS_CITATION", "INFERRED", "UNVALIDATED", "VERIFIED"})
33	
34	
35	def _extract_context(text: str, pos: int, window: int = 120) -> str:
36	    start = max(0, pos - window)
37	    end = min(len(text), pos + window)
38	    return text[start:end].replace("\n", " ").strip()
39	
40	
41	def _audit_section_file(
42	    section_path: Path,
43	    valid_citation_ids: set[str],
44	) -> list[CitationAuditItem]:
45	    items: list[CitationAuditItem] = []
46	    text = section_path.read_text()
47	    fname = section_path.name
48	
49	    # Strip code blocks before scanning to avoid false positives on [@variable] patterns
50	    scan_text = _FENCED_CODE_RE.sub("", text)
51	    scan_text = _INLINE_CODE_RE.sub("", scan_text)
52	
53	    # Find [NEEDS_CITATION] ...[truncated]
```
</details>

**[8] tool→Read:** `/Users/cyan/ccr/know-expand/know_expand/stages/s10_assemble.py` 

<details><summary>[9] tool result (4492 chars)</summary>

```
300	                fixed_segments.append(seg)
301	            else:
302	                # Even segments are plain text — wrap bare math commands
303	                fixed_segments.append(_wrap_bare_math_in_segment(seg))
304	        result_lines.append("".join(fixed_segments))
305	
306	    return "\n".join(result_lines)
307	
308	
309	def _fix_mixed_display_math(text: str) -> str:
310	    """Remove $...$ markers incorrectly embedded inside \\[...\\] display math blocks,
311	    and remove \\[...\\] markers incorrectly embedded inside $$...$$ display math blocks.
312	
313	    The LLM occasionally generates \\[expr = $\\frac{...}$\\] which is invalid
314	    LaTeX — display math cannot nest inline math delimiters.
315	    """
316	    # Fix \\[...$...\\] — remove lone $ (not $$) inside \\[...\\]
317	    def _strip_inline_dollars(m: re.Match) -> str:
318	        inner = re.sub(r'(?<!\$)\$(?!\$)', '', m.group(1))
319	        return r'\[' + inner + r'\]'
320	
321	    text = re.sub(r'\\\[(.*?)\\\]', _strip_inline_dollars, text, flags=re.DOTALL)
322	
323	    # Fix $$...\\[...\\]...$$ — remove \\[ \\] inside $$...$$
324	    def _strip_display_in_dmath(m: re.Match) -> str:
325	        inner = m.group(1)
326	        inner = inner.replace(r'\[', '').replace(r'\]', '')
327	        return '$$' + inner + '$$'
328	
329	    text = re.sub(r'\$\$(.*?)\$\$', _strip_display_in_dmath, text, flags=re.DOTALL)
330	    return text
331	
332	
333	_UNVALIDATED_RE = re.compile(r"\s*\[UNVALIDATED\]\s*", re.IGNORECASE)
334	_NEEDS_CITATION_RE = re.compile(r"\s*\[NEEDS_CITATION\]\s*", re.IGNORECASE)
335	_CITATION_NEEDED_RE = re.compile(r"\s*\[citation needed\]\s*", re.IGNORECASE)
336	_INFERRED_MARKER_RE = re.compile(r"\s*\[INFERRED\]\s*", re.IGNORECASE)
337	_INFERRED_COMMENT_RE = re.compile(r"[ \t]*#[ \t]*INFERRED:[^\n]*", re.IGNORECASE)
338	_CITE_SYNTAX_RE = re.compile(r'\[cite:\s*([^\]]+)\]')
339	_ORPHAN_SPACE_PUNCT_RE = re.compile(r' +([.,;:!?])')
340	
341	
342	def _convert_cite_syntax(keys_str: s...[truncated]
```
</details>

**[12] tool→Bash:** `grep -rn "NEEDS_CITATION\|needs_citation\|unknown_key\|CITATION_MISMATCH\|contradict" /Users/cyan/ccr/know-expand/know_e` — Find all citation-marker handling outside s8_verify.py

<details><summary>[13] tool result (5334 chars)</summary>

```
/Users/cyan/ccr/know-expand/know_expand/state.py:336:                f"needs_citation={ev.get('total_needs_citation')} "
/Users/cyan/ccr/know-expand/know_expand/state.py:337:                f"unknown_keys={ev.get('total_unknown_keys')} "
/Users/cyan/ccr/know-expand/know_expand/observe.py:1219:    ['citations', '[NEEDS_CITATION] resolved'],
/Users/cyan/ccr/know-expand/know_expand/observe.py:1256:    ['[NEEDS_CITATION] remaining', a.needs_citation_count ?? '—'],
/Users/cyan/ccr/know-expand/know_expand/observe.py:1257:    ['unknown citation keys', a.unknown_keys ?? '—'],
/Users/cyan/ccr/know-expand/know_expand/observe.py:2333:        nc = state_dir / "audit" / "needs_citation.md"
/Users/cyan/ccr/know-expand/know_expand/observe.py:2336:            out["needs_citation_count"] = text.count("[NEEDS_CITATION]")
/Users/cyan/ccr/know-expand/know_expand/observe.py:2346:                    out["needs_citation_count"] = evt.get("total_needs_citation", out.get("needs_citation_count"))
/Users/cyan/ccr/know-expand/know_expand/observe.py:2347:                    out["unknown_keys"] = evt.get("total_unknown_keys")
/Users/cyan/ccr/know-expand/know_expand/stages/s7_synthesize.py:67:Focus on SEMANTIC connections: shared unsolved problems, contradictions between \
/Users/cyan/ccr/know-expand/know_expand/stages/s10_assemble.py:334:_NEEDS_CITATION_RE = re.compile(r"\s*\[NEEDS_CITATION\]\s*", re.IGNORECASE)
/Users/cyan/ccr/know-expand/know_expand/stages/s10_assemble.py:354:    text = _NEEDS_CITATION_RE.sub(" ", text)
/Users/cyan/ccr/know-expand/know_expand/stages/s6_align.py:17:  5. [NEEDS_CITATION] markers resolved via Semantic Scholar search
/Users/cyan/ccr/know-expand/know_expand/stages/s6_align.py:50:5. Citations — any [NEEDS_CITATION] markers to resolve?
/Users/cyan/ccr/know-expand/know_expand/stages/s5_research.py:63:- Mark unciteable claims [NEEDS_CITATION].
/Users/cyan/ccr/know-expand/know_expand/stages/s5_research.py:303:1. Citations: unsupported claims not tagged [NEEDS_CITATION];...[truncated]
```
</details>

**[14] tool→Bash:** `grep -n "verif\|citation\|ground" /Users/cyan/ccr/know-expand/README.md | head -50` — Find verification/citation claims in README

<details><summary>[15] tool result (5759 chars)</summary>

```
35:`know-expand` takes that document and produces a **navigable, ground-up knowledge expansion** — a structured field guide covering everything needed to understand the paper deeply, go beyond it, and do independent research.
37:**Not a summary. Not a chatbot answer. Not a Q&A wrapper.** A full-stack knowledge construction pipeline that identifies every substantive concept in a document, maps those concepts into a principled domain taxonomy, augments each domain from first principles to the current research frontier, synthesizes cross-domain insights invisible within any single domain, and grounds every claim in verifiable, real citations.
39:The reference output: a 480KB, 241-page, 81-node skill-tree document with verified citations, LaTeX mathematics, and technical diagrams — produced automatically from a single source paper.
122:│ tables, worked examples, Where-to-Go-Next, citations  │
136:│ Structural citation audit (keys vs bibliography)      │
139:                            │  state/audit/needs_citation.md
361:**Purpose:** 10–15 turn LLM-driven interview that establishes a `UserProfile` — familiarity level, background field, math comfort, known/unknown concepts — which calibrates every downstream stage. Questions reference real terms from `state/terms.json`, not LLM guesses.
363:**Why after S2:** Q3, Q6, and Q7 must offer grounded options from the document's actual vocabulary. Generic questions like "do you know what a neural network is?" on a transformer paper are uncalibrated and useless.
423:2. **Bibliography** (10/30/50 papers by depth): two-bucket fetch — foundational (65%, all-time citation rank) + frontier (35%, last 24 months, citation-ranked within window)
425:The bibliography is the **only citation pool Stage 5 agents may draw from.** This prevents hallucination by construction.
427:**Why two buckets:** sorting by `citationCount` descending guarantees blindness to the last 24 months. A 2017 survey with 4,000 citations outranks a 2025 breakthrough wi...[truncated]
```
</details>

**[18] tool→Bash:** `grep -n "csl\|CSL\|bibliography\|citeproc\|@\|valid_citation\|unknown" /Users/cyan/ccr/know-expand/know_expand/stages/s1` — Find citation/bibliography handling in s10_assemble.py

<details><summary>[19] tool result (1647 chars)</summary>

```
79:    Remove null values and ensure required CSL-JSON fields are present.
80:    pandoc citeproc rejects entries that have null for string/number fields.
84:    cleaned.setdefault("id", "unknown")
100:    Load all bibliography_{domain_id}.json files, deduplicate by DOI then title.
107:    for bib_file in sorted(audit_dir.glob("bibliography_*.json")):
155:def _bibliography_to_markdown(bibliography: list[dict]) -> str:
157:    for entry in bibliography:
344:    return '[@' + '; @'.join(keys) + ']'
420:    # Merge bibliography
421:    bibliography = _merge_bibliographies(audit_dir)
422:    bib_path = state_dir / "bibliography.json"
423:    bib_path.write_text(json.dumps(bibliography, indent=2))
426:        "event": "s7_bibliography_merged",
427:        "entry_count": len(bibliography),
506:    # Add bibliography section
507:    doc_parts.append(_bibliography_to_markdown(bibliography))
523:    # Write bibliography in CSL-JSON format for pandoc citeproc
524:    output_bib = output_dir / "bibliography.json"
525:    output_bib.write_text(json.dumps(bibliography, indent=2))
528:    # Embedding bibliography in frontmatter avoids the pandoc 3.x bug where
529:    # --bibliography with a JSON array triggers a YAML parse exception.
530:    csl_path = Path("style") / "chicago-author-date.csl"
534:        f'bibliography: "{output_bib}"',
543:    if csl_path.exists():
544:        frontmatter_lines.append(f'csl: "{csl_path.resolve()}"')
578:                "--citeproc",           # process [@key] citations via frontmatter bibliography
620:        bibliography_count=len(bibliography),
636:        "bibliography_count": len(bibliography),
```
</details>

**[20] tool→Bash:** `sed -n 1,120p /Users/cyan/ccr/know-expand/know_expand/stages/s6_align.py` — Read S6 align header and NEEDS_CITATION resolution logic

<details><summary>[21] tool result (4578 chars)</summary>

```
"""Stage 6 — Section Alignment Agent.

Uses a two-pass structured-output approach (no ReAct loops):

Pass 1: LLM reads section text and returns an AlignmentPlan (patches + search
        queries + checklist).
Pass 2: If search_queries non-empty, execute SS searches concurrently, then
        call again with results appended. Returns a refined AlignmentPlan.

Python applies patches deterministically via _apply_patch().

Checklist items the agent fixes:
  1. "What is X?" plain-English intro
  2. Symbol tables before every equation
  3. Worked examples after every major concept
  4. "Where to Go Next" section (open problems + start-here + 3 papers)
  5. [NEEDS_CITATION] markers resolved via Semantic Scholar search
"""

import asyncio
import json
import logging
import re
import time
from pathlib import Path

import httpx

from know_expand.agents.base import make_router
from know_expand.agents.schemas import AlignmentPlan
from know_expand.bibliography import _ss_search, reset_ss_limiter
from know_expand.config import Config
from know_expand.state import PipelineState, atomic_write, emit, mark_stage_complete, stage_is_complete

_logger = logging.getLogger("know_expand.s6_align")

_MAX_SEARCH_QUERIES = 3

_SYSTEM_PROMPT = """\
You are a pedagogical alignment agent for the domain: {domain_label}.
Here is the current section text:
---
{section_text}
---
CHECKLIST — identify what's missing and provide patches to fix it:
1. "What is {domain_label}?" — plain-English intro for a newcomer?
2. Symbol tables — does every equation have a preceding Markdown table defining symbols?
3. Worked examples — does every major concept have a concrete numerical/code example?
4. "Where to Go Next" — open problems, start-here resource, 3 essential papers?
5. Citations — any [NEEDS_CITATION] markers to resolve?

Return:
- patches: list of insertions (position + content) to add. Only ADD, never delete.
- search_queries: SS queries needed to find papers (for #4 and #5). Max {max_queries}.
- checkli...[truncated]
```
</details>

**[24] tool→Bash:** `grep -n "_clean_section_text" /Users/cyan/ccr/know-expand/know_expand/stages/s10_assemble.py` — Find where _clean_section_text is called

<details><summary>[25] tool result (203 chars)</summary>

```
351:def _clean_section_text(text: str) -> str:
440:            doc_parts.append(_clean_section_text(section_path.read_text()))
452:        synthesis_text = _clean_section_text(synthesis_path.read_text())
```
</details>

**[26] tool→Bash:** `sed -n 410,500p /Users/cyan/ccr/know-expand/know_expand/stages/s5_research.py` — Read S5 citation gap-fix pass

<details><summary>[27] tool result (3776 chars)</summary>

```
    if not missing_ids and not needs_citation_contexts:
        return bibliography, personas

    emit({
        "event": "s5_bibliography_expansion_start",
        "domain_id": domain_id,
        "missing_citation_ids": len(missing_ids),
        "needs_citation_markers": len(needs_citation_contexts),
    })

    _get_ss_limiter(cfg)

    async def _search_one(query: str, http: httpx.AsyncClient) -> list[dict]:
        try:
            return await _ss_search(query, http, cfg, limit=5)
        except Exception as exc:
            _logger.warning("s4 bib expansion: SS search failed for %r: %s", query[:60], exc)
            return []

    try:
        async with httpx.AsyncClient(timeout=15.0) as http:
            # Search for missing citation IDs
            missing_id_tasks = {mid: asyncio.create_task(_search_one(mid, http)) for mid in missing_ids}
            # Search for [NEEDS_CITATION] contexts
            nc_tasks = [
                asyncio.create_task(_search_one(ctx, http))
                for (_, _, ctx) in needs_citation_contexts
            ]
            await asyncio.gather(
                *missing_id_tasks.values(), *nc_tasks, return_exceptions=True
            )
    except Exception as exc:
        _logger.warning("s4 bibliography expansion: SS unavailable, skipping (%s)", exc)
        emit({"event": "s5_bibliography_expansion_skipped", "domain_id": domain_id, "reason": str(exc)[:120]})
        return bibliography, personas

    # Process missing-ID results
    for mid, task in missing_id_tasks.items():
        if task.exception():
            continue
        papers = task.result()
        for paper in papers:
            title = paper.get("title") or ""
            if _title_relevance(mid, title) >= 0.4:
                rec = _to_citation_record(paper, "frontier", seen_ids)
                new_papers.append(rec.model_dump())
                break  # take the first confident match per missing ID

    # Process [NEEDS_CITATION] results — build replac...[truncated]
```
</details>

**[30] tool→Bash:** `grep -rn "needs_citation.md\|citation_index\|CitationAuditResult\|mismatch" /Users/cyan/ccr/know-expand/know_expand/stag` — Check whether S8 audit output gates or edits anything downstream

<details><summary>[31] tool result (509 chars)</summary>

```
---
80:    pandoc citeproc rejects entries that have null for string/number fields.
444:                "event": "s7_section_missing",
523:    # Write bibliography in CSL-JSON format for pandoc citeproc
562:            missing = []
564:                missing.append("pandoc")
566:                missing.append("xelatex")
569:                "reason": "missing_tools",
570:                "missing": missing,
578:                "--citeproc",           # process [@key] citations via frontmatter bibliography
```
</details>

## Structured Output
```json
{
  "verdict": "confirmed",
  "reasoning": "Every code-level assertion checks out at the cited lines. (1) /Users/cyan/ccr/know-expand/README.md:37-39 contains the exact headline text. (2) /Users/cyan/ccr/know-expand/know_expand/stages/s8_verify.py:72-75 sets status=\"verified\" purely on key membership in the set built from bibliography_*.json ids (lines 210-224); no semantic result ever changes a \"verified\" status. (3) The semantic layer (lines 107-167) is abstract-gated at both entry level (146-148) and domain level (255-262), capped at 50 pairs/domain (line 139: pairs = pairs[:50]), and records only relation==\"contradicts\" (line 158) \u2014 \"mentions\" is silently dropped; even caught contradictions only land in the report file audit/needs_citation.md and never modify section text. (4) /Users/cyan/ccr/know-expand/know_expand/stages/s10_assemble.py:333-362 _clean_section_text strips [NEEDS_CITATION]/[INFERRED]/[UNVALIDATED]/[citation needed] and is applied to every section and the synthesis (lines 440, 452) before pandoc --citeproc (line 578); unknown keys are not filtered, so they render as broken citeproc text. I verified nothing downstream consumes the S8 audit: needs_citation.md/citation_index.json are read only by observe.py for dashboard counters; S9/S10/pipeline.py never gate or edit on them, and human gates exist only at S1/S3. All three hallucination paths therefore reach the final PDF as described. Attempted refutations that failed: S5's critic loop and gap-fix pass (s5_research.py:379-497) and S6's alignment pass do resolve [NEEDS_CITATION] via Semantic Scholar \u2014 but the S5 match criterion is title-token overlap >= 0.4 against the claim sentence (lexical, not semantic support), which reinforces rather than refutes the claim; the bibliography-only citation pool makes citations \"real\" but does not verify that any paper supports its citing claim. Caveats that temper but do not overturn: the README's own pipeline section is honest (line 136/521: \"Structural citation audit (keys vs bibliography)\"; line 528: markers \"flagged for human review. They do not block the build\"), so the misrepresentation is confined to the headline; and \"indistinguishable from a cited one\" is slightly overstated \u2014 a stripped-marker claim carries no [@key], so it is distinguishable from a visibly cited claim, though indistinguishable from prose the system never flagged. The proposed remedy (weaken the headline to key-validation, or rebuild verification) follows from the confirmed facts.",
  "correction": "The claim stands as materially correct. Two refinements: (a) \"rendered indistinguishable from a cited one\" should read \"indistinguishable from unflagged prose\" \u2014 stripped-marker claims carry no citation marker, so they differ visibly from cited claims; (b) the misrepresentation is specific to the README headline (lines 37-39) \u2014 the README's own S8 section (lines 521, 528) and pipeline diagram (line 136) accurately describe the audit as structural key-checking with non-blocking [NEEDS_CITATION] markers, and S5/S6 do attempt SS-search-based citation resolution upstream (though via lexical title overlap, not semantic support verification)."
}
```

<details><summary>[35] tool result (39 chars)</summary>

```
Structured output provided successfully
```
</details>

**[36] assistant:** Verification complete. The auditor's claim is confirmed: all cited line numbers and behaviors are accurate — S8's "verified" status is pure key membership, the semantic check is abstract-gated/capped/contradicts-only and report-only, and S10 strips all uncertainty markers before assembly with no downstream consumer of the audit. The only refinements are that the README's deeper sections honestly disclose the structural nature of the audit (the gap is specific to the headline), and "indistinguishable from a cited one" slightly overstates — stripped claims are indistinguishable from unflagged prose, not from visibly cited claims.
