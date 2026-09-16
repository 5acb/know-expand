import json
from pathlib import Path
from know_expand.stages.s6_align import _persist_bibliography
from know_expand.agents.schemas import AlignmentPlan, PedagogicalPatch

def test_persist_bibliography_adds_correct_citations(tmp_path):
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    
    # 1. Create a dummy existing bibliography
    bib_file = audit_dir / "bibliography_domain_a.json"
    existing_bib = [
        {
            "id": "smith_2023_title",
            "type": "article-journal",
            "title": "Some Paper",
            "author": [{"family": "Smith", "given": "John"}],
            "issued": {"date-parts": [[2023]]},
            "bucket": "foundational",
            "citation_count": 5
        }
    ]
    bib_file.write_text(json.dumps(existing_bib, indent=2))

    # 2. Formulate AlignmentPlan with a patch referencing a new citation
    plan = AlignmentPlan(
        patches=[
            PedagogicalPatch(
                position="end",
                content="This is supported by [@kwon_2023_vllm] and also [@smith_2023_title].",
                checklist_item="citations_resolved"
            )
        ],
        search_queries=[],
        checklist={}
    )

    # 3. Formulate fetched papers from Semantic Scholar
    fetched_papers = [
        {
            "paperId": "kwon_paper_id",
            "title": "vLLM: Easy, Fast, and Cheap LLM Serving with PagedAttention",
            "year": 2023,
            "authors": [{"name": "Woosuk Kwon"}, {"name": "Zhuohan Li"}],
            "externalIds": {"DOI": "10.1145/3600006.3613162"},
            "abstract": "We present vLLM...",
            "citationCount": 150
        }
    ]

    # 4. Invoke persist_bibliography
    _persist_bibliography(plan, "domain_a", audit_dir, fetched_papers)

    # 5. Read back the bibliography and verify the citation was matched and added
    updated_bib = json.loads(bib_file.read_text())
    assert len(updated_bib) == 2
    
    ids = [item["id"] for item in updated_bib]
    assert "smith_2023_title" in ids
    assert "kwon_2023_vllm" in ids

    # Check structure of the matched paper record
    kwon_record = next(item for item in updated_bib if item["id"] == "kwon_2023_vllm")
    assert kwon_record["title"] == "vLLM: Easy, Fast, and Cheap LLM Serving with PagedAttention"
    assert kwon_record["issued"]["date-parts"] == [[2023]]
    assert kwon_record["author"] == [{"family": "Kwon", "given": "Woosuk"}, {"family": "Li", "given": "Zhuohan"}]
    assert kwon_record["DOI"] == "10.1145/3600006.3613162"
    assert kwon_record["bucket"] == "frontier"
