"""Stage 8 — Verify: structural citation audit, URL/DOI resolution, and L5 claim-level entailment checking."""

import json
import logging
import re
import asyncio
from pathlib import Path
import httpx
from pydantic import BaseModel, Field
from typing import Literal

from know_expand.agents.base import make_router
from know_expand.agents.schemas import CitationAuditItem, CitationAuditResult, CitationCheck
from know_expand.config import Config
from know_expand.state import (
    PipelineState,
    emit,
    mark_stage_complete,
    stage_is_complete,
)

_logger = logging.getLogger("know_expand.s8")

# Pattern for [NEEDS_CITATION] markers
_NEEDS_CITATION_RE = re.compile(r"\[NEEDS_CITATION\]")
# Pattern for citation keys: [@key] or [key] where key looks like an identifier
# (contains letters, digits, underscores, hyphens; at least 3 chars)
_CITATION_KEY_RE = re.compile(r"\[@?([\w\-]{3,})\]")
# Strip fenced code blocks before scanning (avoids [@variable] false positives)
_FENS_CODE_RE = re.compile(r"```.*?```", re.DOTALL)
_INL_CODE_RE = re.compile(r"`[^`\n]{1,80}`")

# Pipeline artifact markers that should never appear as citation keys
_PIPELINE_MARKERS = frozenset({"NEEDS_CITATION", "INFERRED", "UNVALIDATED", "VERIFIED"})


class ClaimVerification(BaseModel):
    claim: str
    relation: Literal["supports", "contradicts", "neutral"]
    reason: str


class SentenceVerification(BaseModel):
    claims: list[ClaimVerification]


def _extract_context(text: str, pos: int, window: int = 120) -> str:
    start = max(0, pos - window)
    end = min(len(text), pos + window)
    return text[start:end].replace("\n", " ").strip()


def _find_enclosing_sentence(text: str, pos: int) -> str:
    start = pos
    while start > 0:
        if text[start - 1] in (".", "!", "?", "\n") and (start == 1 or text[start].isspace() or text[start-1] == "\n"):
            break
        start -= 1
    end = pos
    while end < len(text):
        if text[end] in (".", "!", "?", "\n"):
            end += 1
            break
        end += 1
    return text[start:end].strip()


def _audit_section_file(
    section_path: Path,
    valid_citation_ids: set[str],
) -> list[CitationAuditItem]:
    items: list[CitationAuditItem] = []
    text = section_path.read_text()
    fname = section_path.name

    scan_text = _FENS_CODE_RE.sub("", text)
    scan_text = _INL_CODE_RE.sub("", scan_text)

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


async def _resolve_url(client: httpx.AsyncClient, url: str, sem: asyncio.Semaphore) -> tuple[str, bool, int | None]:
    async with sem:
        try:
            resp = await client.head(url, timeout=5.0)
            if resp.status_code < 400:
                return url, True, resp.status_code
            if resp.status_code in (405, 501):
                resp = await client.get(url, headers={"Range": "bytes=0-0"}, timeout=5.0)
                if resp.status_code < 400:
                    return url, True, resp.status_code
            return url, False, resp.status_code
        except Exception:
            try:
                resp = await client.get(url, headers={"Range": "bytes=0-0"}, timeout=5.0)
                return url, resp.status_code < 400, resp.status_code
            except Exception:
                return url, False, None


async def resolve_bibliography_links(bibliography: list[dict]) -> dict[str, dict]:
    urls_to_check = set()
    for entry in bibliography:
        url = entry.get("URL") or entry.get("url")
        if url:
            urls_to_check.add(url)
        doi = entry.get("DOI") or entry.get("doi")
        if doi:
            if doi.startswith("10."):
                urls_to_check.add(f"https://doi.org/{doi}")
            else:
                urls_to_check.add(doi)
                
    sem = asyncio.Semaphore(10)
    results = {}
    async with httpx.AsyncClient(follow_redirects=True, timeout=5.0) as client:
        tasks = [_resolve_url(client, u, sem) for u in urls_to_check]
        done = await asyncio.gather(*tasks)
        for url, ok, status in done:
            results[url] = {"resolved": ok, "status_code": status}
    return results


_CLAIM_DECOMPOSE_VERIFY_PROMPT = """\
Decompose the citing sentence into a list of individual, atomic, testable claims.
For each atomic claim, determine whether the cited paper abstract supports, contradicts, or is neutral to it.

Citing sentence (from the document):
"{citing_sentence}"

Cited paper abstract:
"{abstract}"

Respond with a SentenceVerification containing a list of claims, each with its relation ("supports", "contradicts", "neutral") and reason.
"""


async def run(state: PipelineState, cfg: Config) -> None:
    state_dir = Path(state["state_dir"])
    output_dir = Path(state["output_dir"])
    audit_dir = state_dir / "audit"
    sections_dir = state_dir / "sections"

    audit_dir.mkdir(exist_ok=True)
    output_dir.mkdir(exist_ok=True)

    if stage_is_complete(state_dir, 8):
        emit({"event": "stage_skipped", "stage": 8, "reason": "already_complete"})
        return

    emit({"event": "stage_start", "stage": 8})
    emit({"event": "s8_start"})

    # Load bibliography
    valid_citation_ids: set[str] = set()
    bibliography_index: dict[str, dict] = {}
    all_bib_entries: list[dict] = []

    for bib_file in sorted(audit_dir.glob("bibliography_*.json")):
        entries = json.loads(bib_file.read_text())
        for entry in entries:
            cid = entry.get("id", "")
            if cid:
                valid_citation_ids.add(cid)
                bibliography_index[cid] = entry
                all_bib_entries.append(entry)

    # 1. Resolve URLs/DOIs asynchronously (urlhealth check)
    url_resolutions = await resolve_bibliography_links(all_bib_entries)

    # Collect all section files
    section_files: list[Path] = []
    if sections_dir.exists():
        section_files.extend(sorted(sections_dir.glob("section_*.md")))

    all_items: list[CitationAuditItem] = []
    for section_path in section_files:
        items = _audit_section_file(section_path, valid_citation_ids)
        all_items.extend(items)

    total_needs_citation = sum(1 for i in all_items if i.status == "needs_citation")
    total_unknown_keys = sum(1 for i in all_items if i.status == "unknown_key")
    total_verified = sum(1 for i in all_items if i.status == "verified")

    audit_result = CitationAuditResult(
        items=all_items,
        total_needs_citation=total_needs_citation,
        total_unknown_keys=total_unknown_keys,
        total_verified=total_verified,
    )

    needs_citation_path = audit_dir / "needs_citation.md"
    needs_citation_md = _build_needs_citation_md(audit_result)

    # 2. Decompose citing sentences and check entailment against abstracts
    classifier_router = make_router("classifier", cfg)
    claim_verifications = []

    # Find citing sentences in all sections
    citing_pairs = []
    seen_pairs = set()
    for section_path in section_files:
        if not section_path.exists():
            continue
        text = section_path.read_text()
        
        scan_text = _FENS_CODE_RE.sub("", text)
        scan_text = _INL_CODE_RE.sub("", scan_text)
        
        for match in _CITATION_KEY_RE.finditer(scan_text):
            key = match.group(1)
            if key in _PIPELINE_MARKERS:
                continue
            if key.islower() and "_" not in key and "-" not in key:
                continue
            sentence = _find_enclosing_sentence(scan_text, match.start())
            if sentence:
                pair_key = (sentence[:200], key)
                if pair_key not in seen_pairs:
                    seen_pairs.add(pair_key)
                    citing_pairs.append((section_path.name, sentence, key))

    # Cap at 50 total to prevent excessive runtimes/cost
    citing_pairs = citing_pairs[:50]

    for section_file, sentence, key in citing_pairs:
        entry = bibliography_index.get(key)
        if not entry:
            # Unknown key citation
            claim_verifications.append({
                "section_file": section_file,
                "citation_key": key,
                "sentence": sentence,
                "decomposed_claims": [
                    {
                        "claim": sentence,
                        "relation": "neutral",
                        "reason": "Citation key not found in bibliography."
                    }
                ]
            })
            continue

        abstract = entry.get("abstract", "")
        if not abstract:
            # No abstract available
            claim_verifications.append({
                "section_file": section_file,
                "citation_key": key,
                "sentence": sentence,
                "decomposed_claims": [
                    {
                        "claim": sentence,
                        "relation": "neutral",
                        "reason": "No abstract available for this citation."
                    }
                ]
            })
            continue

        prompt = _CLAIM_DECOMPOSE_VERIFY_PROMPT.format(
            citing_sentence=sentence[:600],
            abstract=abstract[:800],
        )
        try:
            res: SentenceVerification = await classifier_router.call(
                [{"role": "user", "content": prompt}], SentenceVerification
            )
            claim_verifications.append({
                "section_file": section_file,
                "citation_key": key,
                "sentence": sentence,
                "decomposed_claims": [
                    {
                        "claim": c.claim,
                        "relation": c.relation,
                        "reason": c.reason
                    }
                    for c in res.claims
                ]
            })
        except Exception as exc:
            _logger.warning("s8 claim entailment check failed for %s: %s", key, exc)
            claim_verifications.append({
                "section_file": section_file,
                "citation_key": key,
                "sentence": sentence,
                "decomposed_claims": [
                    {
                        "claim": sentence,
                        "relation": "neutral",
                        "reason": f"Verification failed due to error: {exc}"
                    }
                ]
            })

    # Compute stats for summary
    total_claims = 0
    supported_claims = 0
    neutral_claims = 0
    contradicted_claims = 0

    for item in claim_verifications:
        for claim in item["decomposed_claims"]:
            total_claims += 1
            rel = claim["relation"]
            if rel == "supports":
                supported_claims += 1
            elif rel == "contradicts":
                contradicted_claims += 1
            else:
                neutral_claims += 1

    entailment_rate = (supported_claims / total_claims) if total_claims > 0 else 1.0

    # Write verification report
    verification_report = {
        "run_id": state["run_id"],
        "timestamp": json.dumps(None), # Default placeholder for now or utc timestamp
        "urls": url_resolutions,
        "claims": claim_verifications,
        "summary": {
            "total_urls_checked": len(url_resolutions),
            "resolved_urls": sum(1 for r in url_resolutions.values() if r["resolved"]),
            "total_claims_checked": total_claims,
            "supported_claims": supported_claims,
            "neutral_claims": neutral_claims,
            "contradicted_claims": contradicted_claims,
            "entailment_rate": round(entailment_rate, 4),
        }
    }

    report_path = output_dir / "verification_report.json"
    report_path.write_text(json.dumps(verification_report, indent=2))

    # Audit report mismatch output
    all_mismatches = []
    for item in claim_verifications:
        for claim in item["decomposed_claims"]:
            if claim["relation"] == "contradicts":
                all_mismatches.append(
                    f"**[CITATION_MISMATCH]** file=`{item['section_file']}` "
                    f"key=`{item['citation_key']}`\n"
                    f"> Claim: {claim['claim']}\n"
                    f"> Reason: {claim['reason']}\n"
                )

    if all_mismatches:
        needs_citation_md += "\n\n## Citation Mismatches\n\n" + "\n".join(all_mismatches)

    needs_citation_path.write_text(needs_citation_md)

    citation_index_path = audit_dir / "citation_index.json"
    citation_index_path.write_text(json.dumps(bibliography_index, indent=2))

    mark_stage_complete(state_dir, 8)
    emit({
        "event": "s8_complete",
        "stage": 8,
        "total_needs_citation": total_needs_citation,
        "total_unknown_keys": total_unknown_keys,
        "total_verified": total_verified,
        "entailment_rate": entailment_rate,
        "artifact_needs_citation": str(needs_citation_path),
        "artifact_citation_index": str(citation_index_path),
        "artifact_verification_report": str(report_path),
    })
