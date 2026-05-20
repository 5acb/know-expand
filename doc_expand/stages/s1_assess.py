import json
from pathlib import Path

from doc_expand.agents.schemas import UserProfile
from doc_expand.state import PipelineState, emit, mark_stage_complete, stage_is_complete

_DEFAULT_PROFILE = UserProfile(
    familiarity_level="practitioner",
    background_field="unknown",
    q3_correct=True,
    math_comfort="engage",
    learning_goal="apply",
    q6_response="",
    q6_known="partial",
    q7_correct="partial",
    known_concepts=[],
    unknown_concepts=[],
    effective_depth="standard",
    math_mode="equations_explained",
    reading_goal_note="Apply the techniques described in the document.",
)


async def run(state: PipelineState, interactive: bool = False) -> None:
    state_dir = Path(state["state_dir"])
    profile_path = state_dir / "user_profile.json"

    if stage_is_complete(state_dir, 1):
        emit({"event": "stage_skipped", "stage": 1, "reason": "already_complete"})
        return

    if not interactive:
        profile_path.write_text(_DEFAULT_PROFILE.model_dump_json(indent=2))
        mark_stage_complete(state_dir, 1)
        emit({
            "event": "stage_complete",
            "stage": 1,
            "mode": "default_profile",
            "artifact": str(profile_path),
        })
        return

    # Interactive path — requires a TTY
    emit({"event": "stage_start", "stage": 1, "mode": "interactive"})
    zones = json.loads((state_dir / "structural_zones.json").read_text())
    meta = json.loads((state_dir / "source_meta.json").read_text())

    print(
        "\nBefore expanding this document, I have 7 quick questions.\n"
        "These calibrate how the knowledge expansion is written.\n"
        "There are no wrong answers.\n"
    )
    print(f"Document: {meta.get('title', 'unknown')}")
    print(f"Top structural terms: {', '.join(zones[:10])}\n")

    # Placeholder: in Phase 6, this calls the orchestrating LLM to generate
    # document-specific questions from structural_zones. For now, stub with
    # fixed questions so the interactive path is exercisable.
    print("(Interactive calibration not yet fully implemented — using default profile.)")
    profile_path.write_text(_DEFAULT_PROFILE.model_dump_json(indent=2))
    mark_stage_complete(state_dir, 1)
    emit({
        "event": "stage_complete",
        "stage": 1,
        "mode": "interactive_stub",
        "artifact": str(profile_path),
    })
