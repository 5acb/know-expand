from pathlib import Path

from langgraph.graph import StateGraph, END

from doc_expand.config import Config, load_config
from doc_expand.state import PipelineState, emit, load_pipeline_json
from doc_expand.stages import s0_ingest, s0_5_assess, s1_extract, s2_graph


def build_graph(cfg: Config, interactive: bool, auto_taxonomy: bool) -> StateGraph:
    graph = StateGraph(PipelineState)

    async def node_ingest(state: PipelineState) -> PipelineState:
        await s0_ingest.run(state)
        return state

    async def node_assess(state: PipelineState) -> PipelineState:
        await s0_5_assess.run(state, interactive=interactive)
        return state

    async def node_extract(state: PipelineState) -> PipelineState:
        await s1_extract.run(state, cfg)
        return state

    async def node_graph(state: PipelineState) -> PipelineState:
        await s2_graph.run(state, cfg, auto_taxonomy=auto_taxonomy)
        return state

    graph.add_node("ingest", node_ingest)
    graph.add_node("assess", node_assess)
    graph.add_node("extract", node_extract)
    graph.add_node("graph", node_graph)

    graph.set_entry_point("ingest")
    graph.add_edge("ingest", "assess")
    graph.add_edge("assess", "extract")
    graph.add_edge("extract", "graph")
    graph.add_edge("graph", END)

    return graph


async def run_pipeline(
    input_path: str,
    state_dir: Path,
    output_dir: Path,
    depth: str,
    cfg: Config,
    interactive: bool = False,
    auto_taxonomy: bool = False,
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

    graph = build_graph(cfg, interactive=interactive, auto_taxonomy=auto_taxonomy)
    compiled = graph.compile()
    await compiled.ainvoke(state)
