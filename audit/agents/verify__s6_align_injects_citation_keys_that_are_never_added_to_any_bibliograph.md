# Agent: verify__s6_align_injects_citation_keys_that_are_never_added_to_any_bibliograph
**ID:** `agent-aad75639bdca01fd6`  **Turns:** 43

## Prompt
```
You are an adversarial verifier. A code auditor claims the following about the repo at /Users/cyan/ccr/know-expand:

TITLE: S6 align injects citation keys that are never added to any bibliography — guaranteed broken citations in the PDF
LAYER: execution
SEVERITY: high
CLAIM: _SYSTEM_PROMPT_PASS2 instructs the model to cite SS results 'in the form [@<author>_<year>_<word>]' (s6_align.py:82) and patches insert those keys into section files, but _persist_bibliography is a literal no-op pass (s6_align.py:227-231). The keys therefore exist in no bibliography_{domain}.json: S8 flags them unknown_key (report-only), and pandoc --citeproc in S10 renders them as bold 'key?' placeholders in the final PDF. The stage's stated purpose — 'resolve [NEEDS_CITATION] via Semantic Scholar search and bibliography append' (README.md:489) — is half-implemented in a way that actively damages output.
FILES: /Users/cyan/ccr/know-expand/know_expand/stages/s6_align.py:82, /Users/cyan/ccr/know-expand/know_expand/stages/s6_align.py:227-231, /Users/cyan/ccr/know-expand/README.md:489

Read the actual code and try to REFUTE this claim. Check: does the cited code actually behave as claimed? Is the severity inflated? Is there mitigating code elsewhere (search the repo)? If the claim is materially correct, confirm it. Default to refuted if the evidence is ambiguous.
```

**[4] tool→Read:** `/Users/cyan/ccr/know-expand/know_expand/stages/s6_align.py` 

<details><summary>[5] tool result (15064 chars)</summary>

```
1	"""Stage 6 — Section Alignment Agent.
2	
3	Uses a two-pass structured-output approach (no ReAct loops):
4	
5	Pass 1: LLM reads section text and returns an AlignmentPlan (patches + search
6	        queries + checklist).
7	Pass 2: If search_queries non-empty, execute SS searches concurrently, then
8	        call again with results appended. Returns a refined AlignmentPlan.
9	
10	Python applies patches deterministically via _apply_patch().
11	
12	Checklist items the agent fixes:
13	  1. "What is X?" plain-English intro
14	  2. Symbol tables before every equation
15	  3. Worked examples after every major concept
16	  4. "Where to Go Next" section (open problems + start-here + 3 papers)
17	  5. [NEEDS_CITATION] markers resolved via Semantic Scholar search
18	"""
19	
20	import asyncio
21	import json
22	import logging
23	import re
24	import time
25	from pathlib import Path
26	
27	import httpx
28	
29	from know_expand.agents.base import make_router
30	from know_expand.agents.schemas import AlignmentPlan
31	from know_expand.bibliography import _ss_search, reset_ss_limiter
32	from know_expand.config import Config
33	from know_expand.state import PipelineState, atomic_write, emit, mark_stage_complete, stage_is_complete
34	
35	_logger = logging.getLogger("know_expand.s6_align")
36	
37	_MAX_SEARCH_QUERIES = 3
38	
39	_SYSTEM_PROMPT = """\
40	You are a pedagogical alignment agent for the domain: {domain_label}.
41	Here is the current section text:
42	---
43	{section_text}
44	---
45	CHECKLIST — identify what's missing and provide patches to fix it:
46	1. "What is {domain_label}?" — plain-English intro for a newcomer?
47	2. Symbol tables — does every equation have a preceding Markdown table defining symbols?
48	3. Worked examples — does every major concept have a concrete numerical/code example?
49	4. "Where to Go Next" — open problems, start-here resource, 3 essential papers?
50	5. Citations — any [NEEDS_CITATION] markers to resolve?
51	
52	Return:
53	- patches: list of insertions...[truncated]
```
</details>

**[6] tool→Bash:** `grep -n "Semantic Scholar\|NEEDS_CITATION\|S6" /Users/cyan/ccr/know-expand/README.md | head -40` — Find README claims about S6 align stage

<details><summary>[7] tool result (3297 chars)</summary>

```
45:**Latency:** 1–3 hours end-to-end at deep depth on a standard 8-domain paper. Bottlenecks are Semantic Scholar rate limits and LLM call volume. Survey depth on a short paper: 10–20 minutes.
55:         → S6 Align → S7 Synthesize → S8 Verify → S9 Prereq → S10 Assemble
120:│ S6: ALIGN (Pedagogical Alignment)                     │
137:│ Crossref → Semantic Scholar → arXiv (CS/Math only)    │
190:  --no-bibliography-fetch   Skip Semantic Scholar / Crossref fetches (air-gapped mode)
305:| `SS_API_KEY` | Semantic Scholar (optional; raises rate limit from 0.1 → 1 req/s) |
370:**Output:** `state/user_profile.json` — propagated as `unknown_concepts`, `known_concepts`, `math_mode`, `learning_goal`, and `effective_depth` into S5, S6, S7, and S10.
381:- **Lumper:** fewest domains that cleanly partition core terms (max 8; each must map to a Semantic Scholar `fieldsOfStudy`)
422:1. **Anchors** (3 papers): Semantic Scholar top-cited papers for the domain label — used for gap diff
429:**Anchor-neighbor expansion:** after bibliography fetch, the top-3 anchor `paperId` values are used to query Semantic Scholar's `/paper/{id}/citations` endpoint. Recent papers (last 3 years) citing a top anchor are added to the frontier bucket (deduped by DOI). This catches state-of-the-art work that doesn't surface on a domain-label query.
438:**External API rate limiting:** 8 concurrent domain fetches hit Semantic Scholar (100 req/5 min unauthenticated), Crossref, and OpenAlex simultaneously. `aiolimiter` (async token bucket) serializes at the API call level without blocking domain-level parallelism.
481:<summary><strong>S6 — Align (Pedagogical Alignment)</strong></summary>
489:5. `[NEEDS_CITATION]` markers — resolved via Semantic Scholar search and bibliography append
525:2. Semantic Scholar Graph API — CS/ML papers, citation counts
528:`[NEEDS_CITATION]` markers are logged to `state/audit/needs_citation.md` and flagged for human review. They do not block the build.
530:**Acceptance criterion:**...[truncated]
```
</details>

**[10] tool→Bash:** `grep -rn "_persist_bibliography\|_make_citation_id" /Users/cyan/ccr/know-expand/know_expand /Users/cyan/ccr/know-expand/` — Find all uses of _persist_bibliography and _make_citation_id

<details><summary>[11] tool result (285 chars)</summary>

```
/Users/cyan/ccr/know-expand/know_expand/stages/s6_align.py:86:def _make_citation_id(title: str, year: int, first_author: str) -> str:
/Users/cyan/ccr/know-expand/know_expand/stages/s6_align.py:227:def _persist_bibliography(plan: AlignmentPlan, domain_id: str, audit_dir: Path) -> None:
```
</details>

**[12] tool→Bash:** `grep -rn "unknown_key\|citation_key\|@\[" /Users/cyan/ccr/know-expand/know_expand/stages/s8_verify.py | head -30` — Check S8 unknown key handling

<details><summary>[13] tool result (877 chars)</summary>

```
/Users/cyan/ccr/know-expand/know_expand/stages/s8_verify.py:75:            status = "unknown_key"
/Users/cyan/ccr/know-expand/know_expand/stages/s8_verify.py:104:_CLAIM_CONTEXT_RE = re.compile(r"[^.!?\n]{0,300}\[@[^\]]+\][^.!?\n]{0,300}")
/Users/cyan/ccr/know-expand/know_expand/stages/s8_verify.py:174:        f"**Unknown citation keys:** {result.total_unknown_keys}",
/Users/cyan/ccr/know-expand/know_expand/stages/s8_verify.py:185:    unknown_items = [i for i in result.items if i.status == "unknown_key"]
/Users/cyan/ccr/know-expand/know_expand/stages/s8_verify.py:237:    total_unknown_keys = sum(1 for i in all_items if i.status == "unknown_key")
/Users/cyan/ccr/know-expand/know_expand/stages/s8_verify.py:243:        total_unknown_keys=total_unknown_keys,
/Users/cyan/ccr/know-expand/know_expand/stages/s8_verify.py:305:        "total_unknown_keys": total_unknown_keys,
```
</details>

**[15] tool→Bash:** `grep -n "citation_id\|def \|cite_key\|key" /Users/cyan/ccr/know-expand/know_expand/bibliography.py | head -40` — Inspect bibliography.py key generation

<details><summary>[16] tool result (1261 chars)</summary>

```
43:def _mncs_score(citation_count: int, field: str, year: int, field_baselines: dict) -> float:
50:    key = (field.lower(), year)
51:    baseline = field_baselines.get(key)
70:def reset_ss_limiter() -> None:
77:def _get_ss_limiter(cfg: Config) -> AsyncLimiter:
97:def _parse_author(name: str) -> dict:
104:def _make_id(paper: dict, seen: set[str]) -> str:
121:def _to_citation_record(paper: dict, bucket: str, seen: set[str]) -> CitationRecord:
138:async def _ss_search(
150:    headers = {"x-api-key": _SS_API_KEY} if _SS_API_KEY else {}
213:async def _ss_paper_edges(
223:    headers = {"x-api-key": _SS_API_KEY} if _SS_API_KEY else {}
232:            key = "citingPaper" if edge == "citations" else "citedPaper"
233:            return [item[key] for item in data if item.get(key)]
239:async def fetch_anchor_neighbors(
285:async def fetch_anchors(
294:    papers.sort(key=lambda p: p.get("citationCount") or 0, reverse=True)
313:def _clean_ref_query(raw: str, max_chars: int = 160) -> str:
319:async def fetch_source_refs(
357:async def fetch_bibliography(
380:    foundational_raw.sort(key=lambda p: p.get("citationCount") or 0, reverse=True)
383:        key=lambda p: _mncs_score(
396:    def _consume(papers: list[dict], bucket: str, limit: int) -> None:
```
</details>

**[17] tool→Bash:** `grep -n "citeproc\|csl\|bibliography\|\[@\|unknown\|strip" /Users/cyan/ccr/know-expand/know_expand/stages/s10_assemble.p` — Inspect S10 assemble citation handling

<details><summary>[18] tool result (2472 chars)</summary>

```
80:    pandoc citeproc rejects entries that have null for string/number fields.
84:    cleaned.setdefault("id", "unknown")
100:    Load all bibliography_{domain_id}.json files, deduplicate by DOI then title.
107:    for bib_file in sorted(audit_dir.glob("bibliography_*.json")):
111:            title = (entry.get("title") or "").strip().lower()
155:def _bibliography_to_markdown(bibliography: list[dict]) -> str:
157:    for entry in bibliography:
280:        stripped = line.lstrip()
282:            if stripped.startswith("```") or stripped.startswith("~~~"):
284:                fence_marker = stripped[:3]
288:            if stripped.startswith(fence_marker):
317:    def _strip_inline_dollars(m: re.Match) -> str:
321:    text = re.sub(r'\\\[(.*?)\\\]', _strip_inline_dollars, text, flags=re.DOTALL)
324:    def _strip_display_in_dmath(m: re.Match) -> str:
329:    text = re.sub(r'\$\$(.*?)\$\$', _strip_display_in_dmath, text, flags=re.DOTALL)
343:    keys = [k.strip() for k in keys_str.split(',')]
344:    return '[@' + '; @'.join(keys) + ']'
381:        if block.strip().startswith("> **Primer:**"):
382:            key = block.strip()
420:    # Merge bibliography
421:    bibliography = _merge_bibliographies(audit_dir)
422:    bib_path = state_dir / "bibliography.json"
423:    bib_path.write_text(json.dumps(bibliography, indent=2))
426:        "event": "s7_bibliography_merged",
427:        "entry_count": len(bibliography),
462:                    # Deduplicate and strip any leading "N. " numbering the LLM may have added
467:                        clean = _re.sub(r'^\d+[\.\)]\s*', '', step.strip())
495:                            while insert_at < len(syn_lines) and syn_lines[insert_at].strip() == "":
506:    # Add bibliography section
507:    doc_parts.append(_bibliography_to_markdown(bibliography))
523:    # Write bibliography in CSL-JSON format for pandoc citeproc
524:    output_bib = output_dir / "bibliography.json"
525:    output_bib.write_text(json.dumps(bibliography,...[truncated]
```
</details>

**[20] tool→Bash:** `sed -n 95,135p /Users/cyan/ccr/know-expand/know_expand/bibliography.py` — Show _make_id key format in bibliography.py

<details><summary>[21] tool result (1326 chars)</summary>

```
def _parse_author(name: str) -> dict:
    parts = name.rsplit(" ", 1)
    if len(parts) == 2:
        return {"given": parts[0], "family": parts[1]}
    return {"family": name, "given": ""}


def _make_id(paper: dict, seen: set[str]) -> str:
    authors = paper.get("authors") or []
    last = authors[-1]["name"].rsplit(" ", 1)[-1] if authors else "unknown"
    year = paper.get("year") or 0
    title = paper.get("title") or ""
    m = re.search(r"[A-Za-z0-9]+", title)
    first_word = m.group() if m else "x"
    base = re.sub(r"[^a-z0-9_]", "", f"{last}_{year}_{first_word}".lower())
    candidate = base
    n = 2
    while candidate in seen:
        candidate = f"{base}_{n}"
        n += 1
    seen.add(candidate)
    return candidate


def _to_citation_record(paper: dict, bucket: str, seen: set[str]) -> CitationRecord:
    year = paper.get("year") or 0
    doi = (paper.get("externalIds") or {}).get("DOI")
    if doi:
        doi = doi.lower()
    return CitationRecord(
        id=_make_id(paper, seen),
        title=paper.get("title") or "",
        author=[_parse_author(a["name"]) for a in (paper.get("authors") or [])],
        issued={"date-parts": [[year]]},
        DOI=doi,
        abstract=paper.get("abstract") or "",
        bucket=bucket,
        citation_count=paper.get("citationCount") or 0,
    )
```
</details>

**[22] tool→Bash:** `sed -n 40,110p /Users/cyan/ccr/know-expand/know_expand/stages/s8_verify.py` — Show S8 unknown_key detection logic

<details><summary>[23] tool result (2252 chars)</summary>

```
def _audit_section_file(
    section_path: Path,
    valid_citation_ids: set[str],
) -> list[CitationAuditItem]:
    items: list[CitationAuditItem] = []
    text = section_path.read_text()
    fname = section_path.name

    # Strip code blocks before scanning to avoid false positives on [@variable] patterns
    scan_text = _FENCED_CODE_RE.sub("", text)
    scan_text = _INLINE_CODE_RE.sub("", scan_text)

    # Find [NEEDS_CITATION] markers
    for match in _NEEDS_CITATION_RE.finditer(scan_text):
        context = _extract_context(text, match.start())
        items.append(CitationAuditItem(
            section_file=fname,
            marker="[NEEDS_CITATION]",
            context=context,
            status="needs_citation",
        ))

    # Find citation keys [@key] or [key]
    for match in _CITATION_KEY_RE.finditer(scan_text):
        key = match.group(1)
        if key in _PIPELINE_MARKERS:
            continue
        # Skip single lowercase words — likely markdown link syntax, not citations
        if key.islower() and "_" not in key and "-" not in key:
            continue
        context = _extract_context(text, match.start())
        if key in valid_citation_ids:
            status = "verified"
        else:
            status = "unknown_key"
        items.append(CitationAuditItem(
            section_file=fname,
            marker=f"[@{key}]",
            context=context,
            status=status,
        ))

    return items


_CITATION_CLASSIFIER_PROMPT = """\
You are checking whether a cited paper actually supports the claim made in a document.

Claim sentence (from the document):
{claim_sentence}

Cited paper abstract:
{abstract}

Does the abstract support, contradict, or merely mention (without supporting) the claim?
Return a CitationCheck with:
- relation: "supports", "contradicts", or "mentions"
- reason: one sentence explaining why
"""

# Regex to find citation markers [@key] in assembled markdown
_CITATION_MARKER_RE = re.compile(r"\[@([^\]]+)\]")
...[truncated]
```
</details>

**[24] tool→Bash:** `sed -n 330,350p /Users/cyan/ccr/know-expand/know_expand/stages/s10_assemble.py` — Show S10 citation key rewrite at line 343

<details><summary>[25] tool result (738 chars)</summary>

```
    return text


_UNVALIDATED_RE = re.compile(r"\s*\[UNVALIDATED\]\s*", re.IGNORECASE)
_NEEDS_CITATION_RE = re.compile(r"\s*\[NEEDS_CITATION\]\s*", re.IGNORECASE)
_CITATION_NEEDED_RE = re.compile(r"\s*\[citation needed\]\s*", re.IGNORECASE)
_INFERRED_MARKER_RE = re.compile(r"\s*\[INFERRED\]\s*", re.IGNORECASE)
_INFERRED_COMMENT_RE = re.compile(r"[ \t]*#[ \t]*INFERRED:[^\n]*", re.IGNORECASE)
_CITE_SYNTAX_RE = re.compile(r'\[cite:\s*([^\]]+)\]')
_ORPHAN_SPACE_PUNCT_RE = re.compile(r' +([.,;:!?])')


def _convert_cite_syntax(keys_str: str) -> str:
    keys = [k.strip() for k in keys_str.split(',')]
    return '[@' + '; @'.join(keys) + ']'


def _is_slug_title(title: str) -> bool:
    return bool(re.match(r'^[A-Z0-9_\-]+$', title))
```
</details>

**[28] tool→Bash:** `sed -n 160,250p /Users/cyan/ccr/know-expand/know_expand/stages/s8_verify.py` — Show S8 report generation and run flow

<details><summary>[29] tool result (3462 chars)</summary>

```
                    "citation_id": citation_id,
                    "claim": claim_sentence[:300],
                    "reason": check.reason,
                })
        except Exception as exc:
            _logger.debug("s8 citation classify failed for %r: %s", citation_id, exc)

    return contradictions


def _build_needs_citation_md(result: CitationAuditResult) -> str:
    lines = [
        "# Citation Audit\n",
        f"**Total [NEEDS_CITATION]:** {result.total_needs_citation}",
        f"**Unknown citation keys:** {result.total_unknown_keys}",
        f"**Verified citations:** {result.total_verified}\n",
    ]

    needs_items = [i for i in result.items if i.status == "needs_citation"]
    if needs_items:
        lines.append("## Claims Needing Citations\n")
        for item in needs_items:
            lines.append(f"**File:** `{item.section_file}`")
            lines.append(f"> {item.context}\n")

    unknown_items = [i for i in result.items if i.status == "unknown_key"]
    if unknown_items:
        lines.append("## Unknown Citation Keys\n")
        for item in unknown_items:
            lines.append(f"**File:** `{item.section_file}`  **Key:** `{item.marker}`")
            lines.append(f"> {item.context}\n")

    return "\n".join(lines)


async def run(state: PipelineState, cfg: Config) -> None:
    state_dir = Path(state["state_dir"])
    audit_dir = state_dir / "audit"
    sections_dir = state_dir / "sections"

    audit_dir.mkdir(exist_ok=True)

    if stage_is_complete(state_dir, 8):
        emit({"event": "stage_skipped", "stage": 8, "reason": "already_complete"})
        return

    emit({"event": "stage_start", "stage": 8})
    emit({"event": "s8_start"})

    # Build valid citation ID set from all bibliography JSONs, keyed by domain
    valid_citation_ids: set[str] = set()
    bibliography_index: dict[str, dict] = {}
    domain_bibliographies: dict[str, list[dict]] = {}

    for bib_file in sorted(audit_dir.glob("bibliography_*.json")):
        # Ex...[truncated]
```
</details>

**[30] tool→Bash:** `grep -n "valid_citation\|unknown\|\[@" /Users/cyan/ccr/know-expand/know_expand/stages/s10_assemble.py` — Check S10 for unknown-key sanitization

<details><summary>[31] tool result (187 chars)</summary>

```
84:    cleaned.setdefault("id", "unknown")
344:    return '[@' + '; @'.join(keys) + ']'
578:                "--citeproc",           # process [@key] citations via frontmatter bibliography
```
</details>

**[34] tool→Bash:** `grep -rn "bibliography_" /Users/cyan/ccr/know-expand/know_expand --include="*.py" -l; echo ---; grep -rn "bibliography_.` — Find all writers of per-domain bibliography files

<details><summary>[35] tool result (741 chars)</summary>

```
/Users/cyan/ccr/know-expand/know_expand/observe.py
/Users/cyan/ccr/know-expand/know_expand/state.py
/Users/cyan/ccr/know-expand/know_expand/stages/s10_assemble.py
/Users/cyan/ccr/know-expand/know_expand/stages/s7_synthesize.py
/Users/cyan/ccr/know-expand/know_expand/stages/s4_audit.py
/Users/cyan/ccr/know-expand/know_expand/stages/s5_research.py
/Users/cyan/ccr/know-expand/know_expand/stages/s8_verify.py
/Users/cyan/ccr/know-expand/know_expand/agents/schemas.py
---
/Users/cyan/ccr/know-expand/know_expand/stages/s4_audit.py:262:    bib_path = audit_dir / f"bibliography_{domain_id}.json"
/Users/cyan/ccr/know-expand/know_expand/stages/s4_audit.py:309:    bib_path.write_text(json.dumps([b.model_dump() for b in bibliography], indent=2))
```
</details>

**[36] tool→Bash:** `ls /Users/cyan/ccr/know-expand/runs 2>/dev/null | head; echo ---; find /Users/cyan/ccr/know-expand/runs -name "needs_cit` — Look for run artifacts with citation audit reports

<details><summary>[37] tool result (3 chars)</summary>

```
---
```
</details>

## Structured Output
```json
{
  "verdict": "confirmed",
  "reasoning": "Every load-bearing element of the claim checks out against the code. (1) s6_align.py:82 contains the exact instruction \"Use citation keys in the form [@<author>_<year>_<word>]\" in _SYSTEM_PROMPT_PASS2, and _align_domain applies the resulting patches to section files via _apply_patch with no key validation. (2) _persist_bibliography (s6_align.py:227-231) is a literal `pass` no-op \u2014 and the claim actually understates it: the function is never called anywhere, and the companion _make_citation_id helper is dead code too. (3) Repo-wide search shows the only writer of bibliography_{domain_id}.json is S4 (s4_audit.py:262,309), which runs before S6; nothing appends S6-discovered papers. Coincidental key matches are further blocked by a format mismatch: bibliography.py _make_id keys use the LAST author's surname (authors[-1]) while the S6 prompt format implies the first author. (4) S8 (s8_verify.py:75) builds valid_citation_ids solely from bibliography_*.json and marks anything else unknown_key, written to audit/needs_citation.md \u2014 report-only, no repair, no build failure (consistent with the project's own \"never a build failure\" constraint). (5) S10 merges only S4 bibliographies into the CSL-JSON and runs pandoc --citeproc (s10_assemble.py:578) with no unknown-key stripping; pandoc citeproc renders missing keys as a bold \"key?\" placeholder, as claimed. (6) README.md:489 does promise \"resolved via Semantic Scholar search and bibliography append\" \u2014 the append half is unimplemented. Severity \"high\" is fair: the S6 checklist (3 essential papers, citation resolution) makes pass-2 citation insertion the expected path in normal runs, so visibly broken citations in the final PDF are the designed-in outcome, not an edge case. The only minor hedge is \"guaranteed\" \u2014 breakage technically requires the LLM to emit a key for a newly-found paper \u2014 but since the prompt explicitly instructs that and no resolution path exists for any such key, this is not a material overstatement. No run artifacts existed to verify empirically, but the code path is unambiguous."
}
```

<details><summary>[41] tool result (39 chars)</summary>

```
Structured output provided successfully
```
</details>

**[42] assistant:** Verification complete — the claim is confirmed.
