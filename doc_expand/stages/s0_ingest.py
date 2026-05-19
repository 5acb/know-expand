import json
from pathlib import Path

import httpx
import spacy
from docling.datamodel.document import DocItemLabel
from docling.document_converter import DocumentConverter
from docling.chunking import HybridChunker

from doc_expand.state import PipelineState, emit, mark_stage_complete, stage_is_complete

_STRUCTURAL_LABELS = {DocItemLabel.SECTION_HEADER, DocItemLabel.TITLE}


def _extract_structural_zones(doc, nlp) -> set[str]:
    zones: set[str] = set()
    for item, _ in doc.iterate_items():
        if item.label in _STRUCTURAL_LABELS and item.text:
            parsed = nlp(item.text)
            zones.update(chunk.text.lower() for chunk in parsed.noun_chunks)
            zones.update(tok.text.lower() for tok in parsed if tok.pos_ == "NOUN")
    return zones


def _fetch_url(url: str) -> str:
    with httpx.Client(timeout=30, follow_redirects=True) as client:
        resp = client.get(url)
        resp.raise_for_status()
        return resp.text


async def run(state: PipelineState) -> None:
    state_dir = Path(state["state_dir"])

    if stage_is_complete(state_dir, 0):
        emit({"event": "stage_skipped", "stage": 0, "reason": "already_complete"})
        return

    emit({"event": "stage_start", "stage": 0})

    input_path = state["input_path"]
    chunks_dir = state_dir / "chunks"
    chunks_dir.mkdir(parents=True, exist_ok=True)

    nlp = spacy.load("en_core_web_sm")

    # --- Ingest source ---
    if input_path.startswith("http://") or input_path.startswith("https://"):
        raw_text = _fetch_url(input_path)
        source_txt = state_dir / "source.txt"
        source_txt.write_text(raw_text)
        meta = {"source_type": "url", "url": input_path, "title": input_path}
        structural_zones: set[str] = set()
        # For URLs, extract noun chunks from the full text as a proxy
        parsed = nlp(raw_text[:50_000])
        structural_zones = {chunk.text.lower() for chunk in parsed.noun_chunks}
        # Split into synthetic chunks of ~4000 chars
        words = raw_text.split()
        chunk_size = 800  # words
        raw_chunks = [
            " ".join(words[i:i + chunk_size])
            for i in range(0, len(words), chunk_size)
        ]
        for idx, text in enumerate(raw_chunks):
            chunk_file = chunks_dir / f"chunk_{idx:04d}.json"
            chunk_file.write_text(json.dumps({
                "chunk_id": f"chunk_{idx:04d}",
                "text": text,
                "section_path": [],
            }))
    else:
        source_path = Path(input_path)
        if not source_path.exists():
            raise FileNotFoundError(f"Input not found: {source_path}")

        if source_path.suffix.lower() == ".pdf":
            converter = DocumentConverter()
            doc = converter.convert(str(source_path)).document
            structural_zones = _extract_structural_zones(doc, nlp)

            chunker = HybridChunker(
                tokenizer="cl100k_base",
                max_tokens=4000,
                merge_peers=True,
            )
            raw_chunks = list(chunker.chunk(doc))
            for idx, chunk in enumerate(raw_chunks):
                chunk_file = chunks_dir / f"chunk_{idx:04d}.json"
                chunk_file.write_text(json.dumps({
                    "chunk_id": f"chunk_{idx:04d}",
                    "text": chunk.text,
                    "section_path": getattr(chunk.meta, "headings", []),
                }))
            meta = {
                "source_type": "pdf",
                "path": str(source_path),
                "title": source_path.stem,
                "chunk_count": len(raw_chunks),
            }
            # Also write source text for reference
            (state_dir / "source.txt").write_text(
                "\n\n".join(c.text for c in raw_chunks)
            )
        else:
            # Plain text / markdown
            raw_text = source_path.read_text()
            (state_dir / "source.txt").write_text(raw_text)
            parsed = nlp(raw_text[:50_000])
            structural_zones = {chunk.text.lower() for chunk in parsed.noun_chunks}
            words = raw_text.split()
            chunk_size = 800
            raw_chunks_text = [
                " ".join(words[i:i + chunk_size])
                for i in range(0, len(words), chunk_size)
            ]
            for idx, text in enumerate(raw_chunks_text):
                chunk_file = chunks_dir / f"chunk_{idx:04d}.json"
                chunk_file.write_text(json.dumps({
                    "chunk_id": f"chunk_{idx:04d}",
                    "text": text,
                    "section_path": [],
                }))
            meta = {
                "source_type": "text",
                "path": str(source_path),
                "title": source_path.stem,
                "chunk_count": len(raw_chunks_text),
            }

    (state_dir / "source_meta.json").write_text(json.dumps(meta, indent=2))
    (state_dir / "structural_zones.json").write_text(
        json.dumps(sorted(structural_zones), indent=2)
    )

    chunk_count = len(list(chunks_dir.glob("chunk_*.json")))
    mark_stage_complete(state_dir, 0)
    emit({
        "event": "stage_complete",
        "stage": 0,
        "chunk_count": chunk_count,
        "structural_zone_count": len(structural_zones),
        "artifact": str(state_dir / "structural_zones.json"),
    })
