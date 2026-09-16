import os
from pathlib import Path

from langgraph.graph import StateGraph, END

from know_expand.config import Config
from know_expand.state import PipelineState, emit, load_pipeline_json
from know_expand.stages import (
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
    only_stage: int | None = None,
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

    if only_stage is not None:
        node_names = {
            0: "ingest",
            1: "assess",
            2: "extract",
            3: "graph",
            4: "audit",
            5: "research",
            6: "align",
            7: "synthesize",
            8: "verify",
            9: "prereq",
            10: "assemble",
        }
        target_node = node_names.get(only_stage)
        if not target_node:
            raise ValueError(f"Unknown stage index: {only_stage}")
        graph.set_entry_point(target_node)
        graph.add_edge(target_node, END)
    else:
        graph.set_entry_point("ingest")
        graph.add_edge("ingest", "extract")
        graph.add_edge("extract", "assess")
        graph.add_edge("assess", "graph")
        graph.add_edge("graph", "audit")
        graph.add_edge("audit", "research")
        graph.add_edge("research", "align")
        graph.add_edge("align", "synthesize")
        graph.add_edge("synthesize", "prereq")
        graph.add_edge("prereq", "verify")
        graph.add_edge("verify", "assemble")
        graph.add_edge("assemble", END)

    return graph


async def run_pipeline(
    input_path: str,
    state_dir: Path,
    output_dir: Path,
    depth: str,
    cfg: Config,
    auto_taxonomy: bool = False,
    primary_model: str | None = None,
    no_pdf: bool = False,
    resume_stage: int | None = None,
    only_stage: int | None = None,
    user_profile_path: str | None = None,
    no_bibliography_fetch: bool = False,
    bypass_license_gate: bool = False,
) -> None:
    import uuid

    if primary_model:
        for role in cfg.models:
            models = cfg.models[role]
            if primary_model not in models:
                cfg.models[role] = [primary_model] + models
            else:
                cfg.models[role] = [primary_model] + [m for m in models if m != primary_model]

    pipeline_json = load_pipeline_json(state_dir)
    run_id = pipeline_json.get("run_id") or f"run_{uuid.uuid4().hex[:8]}"

    pipeline_json["run_id"] = run_id
    pipeline_json.setdefault("input_path", input_path)
    pipeline_json.setdefault("depth", depth)
    pipeline_json.setdefault("auto_taxonomy", auto_taxonomy)
    pipeline_json.setdefault("no_pdf", no_pdf)
    pipeline_json.setdefault("no_bibliography_fetch", no_bibliography_fetch)
    pipeline_json.setdefault("bypass_license_gate", bypass_license_gate)

    # Handle --user-profile flag
    if user_profile_path:
        up_path = Path(user_profile_path)
        if not up_path.exists():
            raise FileNotFoundError(f"User profile path not found: {user_profile_path}")
        dest = state_dir / "user_profile.json"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(up_path.read_text())
        from know_expand.state import mark_stage_complete
        mark_stage_complete(state_dir, 1)

    # Handle --resume flag
    if resume_stage is not None:
        execution_order = [0, 2, 1, 3, 4, 5, 6, 7, 9, 8, 10]
        if resume_stage in execution_order:
            resume_idx = execution_order.index(resume_stage)
            stages_data = pipeline_json.setdefault("stages", {})
            # Clear all stages from resume_stage onwards in the execution order
            for idx in range(resume_idx, len(execution_order)):
                s = execution_order[idx]
                stages_data.pop(str(s), None)
            # Mark all stages before resume_stage as complete
            for idx in range(0, resume_idx):
                s = execution_order[idx]
                if str(s) not in stages_data:
                    from know_expand.state import _utc
                    stages_data[str(s)] = {
                        "status": "complete",
                        "completed_at": _utc(),
                    }

    # Generate or reuse virtual key on LiteLLM proxy if configured
    proxy_url = os.environ.get("LITELLM_PROXY_URL") or os.environ.get("LITELLM_PROXY_API_BASE")
    master_key = os.environ.get("LITELLM_PROXY_MASTER_KEY") or os.environ.get("LITELLM_MASTER_KEY")
    if proxy_url and master_key:
        # Check if we already generated a key for this run
        virtual_key = pipeline_json.get("litellm_virtual_key")
        if not virtual_key:
            import httpx
            try:
                # Generate key with $150 budget cap
                with httpx.Client() as client:
                    resp = client.post(
                        f"{proxy_url.rstrip('/')}/key/generate",
                        headers={"Authorization": f"Bearer {master_key}"},
                        json={
                            "key_alias": run_id,
                            "max_budget": 150.0,
                            "duration": "24h"
                        },
                        timeout=10.0
                    )
                    if resp.status_code == 200:
                        virtual_key = resp.json().get("key")
                        pipeline_json["litellm_virtual_key"] = virtual_key
                        pipeline_json["litellm_api_base"] = proxy_url
                        save_pipeline_json(state_dir, pipeline_json)
            except Exception as e:
                print(f"Failed to generate virtual key on LiteLLM proxy: {e}")
        if virtual_key:
            cfg.litellm_virtual_key = virtual_key
            cfg.litellm_api_base = proxy_url

    # For fresh runs or stage overrides, wipe checkpoint DB to force clean routing
    db_path = state_dir / "checkpoints.db"
    if resume_stage is not None or only_stage is not None or not resume_stage:
        if db_path.exists():
            try:
                db_path.unlink()
            except Exception:
                pass

    from know_expand.state import save_pipeline_json
    save_pipeline_json(state_dir, pipeline_json)

    # Inject virtual key configurations into State dict
    state: PipelineState = {
        "run_id": run_id,
        "state_dir": str(state_dir),
        "output_dir": str(output_dir),
        "input_path": input_path,
        "depth": depth,
        "domain_ids": pipeline_json.get("domain_ids", []),
        "no_bibliography_fetch": no_bibliography_fetch,
        "bypass_license_gate": bypass_license_gate,
    }

    emit({
        "event": "pipeline_init",
        "run_id": run_id,
        "input": input_path,
        "depth": depth,
        "state_dir": str(state_dir),
    })

    from know_expand.agents.base import probe_models
    await probe_models(cfg)

    import sqlite3
    from langgraph.checkpoint.sqlite import SqliteSaver
    
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    memory = SqliteSaver(conn)

    graph = build_graph(cfg, auto_taxonomy=auto_taxonomy, no_pdf=no_pdf, only_stage=only_stage)
    compiled = graph.compile(checkpointer=memory)

    config = {"configurable": {"thread_id": run_id}}
    
    # Read history to determine if we resume or start fresh
    state_history = list(compiled.get_state_history(config))
    
    try:
        if state_history:
            # Check for resume inputs/values
            resume_val = None
            choice_path = state_dir / "taxonomy_choice.json"
            if choice_path.exists():
                try:
                    choice = json.loads(choice_path.read_text()).get("choice")
                    if choice:
                        resume_val = choice
                except Exception:
                    pass
            if not resume_val:
                answers_path = state_dir / "qa_answers.jsonl"
                if answers_path.exists():
                    try:
                        lines = answers_path.read_text().splitlines()
                        if lines:
                            last_line = lines[-1].strip()
                            if last_line:
                                ans_data = json.loads(last_line)
                                resume_val = ans_data.get("answer")
                    except Exception:
                        pass
            
            from langgraph.types import Command
            if resume_val is not None:
                res = await compiled.ainvoke(Command(resume=resume_val), config)
            else:
                res = await compiled.ainvoke(None, config)
        else:
            res = await compiled.ainvoke(state, config)
            
        # Check if graph execution paused for user input/interrupt
        if isinstance(res, dict) and "__interrupt__" in res and res["__interrupt__"]:
            emit({
                "event": "pipeline_paused",
                "reason": "interrupt",
                "instructions": "Process suspended. Supply answers in UI to resume.",
            })
            pid_file = state_dir / "pipeline_pid"
            if pid_file.exists():
                try:
                    pid_file.unlink()
                except Exception:
                    pass
            print("Pipeline paused for human input.", flush=True)
            return

        # Emit run completion event
        emit({
            "event": "run_complete",
            "run_id": run_id,
        })

    except Exception as e:
        # Wrap everything else (except GraphInterrupt which doesn't occur for types.interrupt)
        raise e
