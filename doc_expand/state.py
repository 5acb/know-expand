import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import TypedDict


class PipelineState(TypedDict):
    run_id: str
    state_dir: str       # absolute path string; Path not JSON-serialisable in LangGraph
    output_dir: str
    input_path: str
    depth: str           # survey | standard | deep
    domain_ids: list[str]


# ---------------------------------------------------------------------------
# Sentinel helpers
# ---------------------------------------------------------------------------

def sentinel_path(base: Path) -> Path:
    return base.with_suffix(base.suffix + ".done")


def sentinel_exists(path: Path) -> bool:
    return sentinel_path(path).exists()


def write_sentinel(path: Path) -> None:
    sentinel_path(path).touch()


# ---------------------------------------------------------------------------
# pipeline.json helpers
# ---------------------------------------------------------------------------

def load_pipeline_json(state_dir: Path) -> dict:
    p = state_dir / "pipeline.json"
    if p.exists():
        return json.loads(p.read_text())
    return {}


def save_pipeline_json(state_dir: Path, data: dict) -> None:
    p = state_dir / "pipeline.json"
    p.write_text(json.dumps(data, indent=2))


def mark_stage_complete(state_dir: Path, stage: str | int) -> None:
    data = load_pipeline_json(state_dir)
    data.setdefault("stages", {})[str(stage)] = {
        "status": "complete",
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
    save_pipeline_json(state_dir, data)


def stage_is_complete(state_dir: Path, stage: str | int) -> bool:
    data = load_pipeline_json(state_dir)
    return data.get("stages", {}).get(str(stage), {}).get("status") == "complete"


# ---------------------------------------------------------------------------
# Structured event emission (JSON Lines to stdout)
# ---------------------------------------------------------------------------

def emit(event: dict) -> None:
    print(json.dumps(event), flush=True)


def emit_human(message: str) -> None:
    """Used only when --human flag is active; Rich formatting goes here."""
    print(message, flush=True)
