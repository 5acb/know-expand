from pathlib import Path

from langgraph.graph import StateGraph, END

from doc_expand.config import Config, load_config
from doc_expand.state import PipelineState, emit, load_pipeline_json
from doc_expand.stages import (
    s0_ingest,
    s1_assess,
    s2_extract,
    s3_graph,
    s4_audit,
    s5_research,
    s6_align,
    s7_synthesize,
    s8_verify,
    s9_prereq,
    s10_assemble,
)


def build_graph(
    cfg: Config,
    auto_taxonomy: bool,
    no_pdf: bool = False,
) -> StateGraph:
    graph = StateGraph(PipelineState)

    async def node_ingest(state: PipelineState) -> PipelineState:
        await s0_ingest.run(state, cfg)
        return state

    async def node_assess(state: PipelineState) -> PipelineState:
        await s1_assess.run(state, cfg)
        return state

    async def node_extract(state: PipelineState) -> PipelineState:
        await s2_extract.run(state, cfg)
        return state

    async def node_graph(state: PipelineState) -> PipelineState:
        await s3_graph.run(state, cfg, auto_taxonomy=auto_taxonomy)
        return state

    async def node_audit(state: PipelineState) -> PipelineState:
        await s4_audit.run(state, cfg)
        return state

    async def node_research(state: PipelineState) -> PipelineState:
        await s5_research.run(state, cfg)
        return state

    async def node_align(state: PipelineState) -> PipelineState:
        await s6_align.run(state, cfg)
        return state

    async def node_synthesize(state: PipelineState) -> PipelineState:
        await s7_synthesize.run(state, cfg)
        return state

    async def node_verify(state: PipelineState) -> PipelineState:
        await s8_verify.run(state, cfg)
        return state

    async def node_prereq(state: PipelineState) -> PipelineState:
        await s9_prereq.run(state, cfg)
        return state

    async def node_assemble(state: PipelineState) -> PipelineState:
        await s10_assemble.run(state, cfg, no_pdf=no_pdf)
        return state

    graph.add_node("ingest", node_ingest)
    graph.add_node("assess", node_assess)
    graph.add_node("extract", node_extract)
    graph.add_node("graph", node_graph)
    graph.add_node("audit", node_audit)
    graph.add_node("research", node_research)
    graph.add_node("align", node_align)
    graph.add_node("synthesize", node_synthesize)
    graph.add_node("verify", node_verify)
    graph.add_node("prereq", node_prereq)
    graph.add_node("assemble", node_assemble)

    graph.set_entry_point("ingest")
    graph.add_edge("ingest", "assess")
    graph.add_edge("assess", "extract")
    graph.add_edge("extract", "graph")
    graph.add_edge("graph", "audit")
    graph.add_edge("audit", "research")
    graph.add_edge("research", "align")
    graph.add_edge("align", "synthesize")
    graph.add_edge("synthesize", "verify")
    graph.add_edge("verify", "prereq")
    graph.add_edge("prereq", "assemble")
    graph.add_edge("assemble", END)

    return graph


async def run_pipeline(
    input_path: str,
    state_dir: Path,
    output_dir: Path,
    depth: str,
    cfg: Config,
    auto_taxonomy: bool = False,
    no_pdf: bool = False,
    resume_stage: int | None = None,
) -> None:
    import uuid

    pipeline_json = load_pipeline_json(state_dir)
    run_id = pipeline_json.get("run_id") or f"run_{uuid.uuid4().hex[:8]}"

    state: PipelineState = {
        "run_id": run_id,
        "state_dir": str(state_dir),
        "output_dir": str(output_dir),
        "input_path": input_path,
        "depth": depth,
        "domain_ids": pipeline_json.get("domain_ids", []),
    }

    emit({
        "event": "pipeline_init",
        "run_id": run_id,
        "input": input_path,
        "depth": depth,
        "state_dir": str(state_dir),
    })

    from doc_expand.agents.base import probe_models
    await probe_models(cfg)

    graph = build_graph(cfg, auto_taxonomy=auto_taxonomy, no_pdf=no_pdf)
    compiled = graph.compile()
    await compiled.ainvoke(state)
