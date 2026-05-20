"""Stage 1 — User Assessment (Agentic Interview).

Conducts a dynamic 10–15 question interview tailored to the document content
and the user's evolving answers, then synthesises a structured UserProfile.

Two delivery modes:
  - Terminal (TTY present): prompts are printed, answers read from stdin.
  - Web IPC (no TTY / headless): questions written to qa_queue.jsonl, answers
    polled from qa_answers.jsonl; sentinel qa_complete created when done.

IPC file protocol (all files inside state_dir/):
  qa_queue.jsonl   — one JSON line per question appended by the pipeline
  qa_answers.jsonl — one JSON line per answer appended by the web server
  qa_complete      — sentinel: pipeline creates this when Q&A is finished
"""

import asyncio
import json
import sys
import time
from pathlib import Path

from doc_expand.agents.base import make_router
from doc_expand.agents.schemas import InterviewDecision, UserProfile
from doc_expand.config import Config
from doc_expand.state import (
    PipelineState,
    atomic_write,
    emit,
    mark_stage_complete,
    stage_is_complete,
)

_MAX_TURNS = 15
_MIN_TURNS = 10
_WEB_POLL_INTERVAL = 0.5   # seconds between polls for qa_answers.jsonl
_WEB_TIMEOUT = 300         # seconds to wait for a web answer before giving up

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
    primary_use_case="",
    time_available="study",
    prior_exposure=[],
    preferred_analogy_domain="",
    frustration_points=[],
)


# ---------------------------------------------------------------------------
# Document context helpers
# ---------------------------------------------------------------------------

def _get_document_context(state_dir: Path) -> str:
    """Load document context for the interviewer.

    Tries (in order):
    1. state/source_meta.json  → title, url/file, token_count
    2. state/chunks/chunk_0000.json → first chunk text
    3. state/source.txt → first 2000 chars
    Returns a formatted string.
    """
    title = "Unknown document"
    text = ""

    meta_path = state_dir / "source_meta.json"
    if meta_path.exists():
        meta = json.loads(meta_path.read_text())
        title = meta.get("title") or meta.get("url") or meta.get("file") or title

    chunk_path = state_dir / "chunks" / "chunk_0000.json"
    if chunk_path.exists():
        chunk = json.loads(chunk_path.read_text())
        text = chunk.get("text", "")[:2000]
    elif (state_dir / "source.txt").exists():
        text = (state_dir / "source.txt").read_text()[:2000]

    return f"Title: {title}\n\nDocument excerpt:\n{text}"


def _get_key_concepts(state_dir: Path) -> list[str]:
    """Return top 15 terms by occurrence_count from state/terms.json, or []."""
    terms_path = state_dir / "terms.json"
    if not terms_path.exists():
        return []
    try:
        terms = json.loads(terms_path.read_text())
        # terms is a list of dicts with at least "name" and "occurrence_count"
        sorted_terms = sorted(terms, key=lambda t: t.get("occurrence_count", 0), reverse=True)
        return [t["name"] for t in sorted_terms[:15]]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Interview prompt builder
# ---------------------------------------------------------------------------

_INTERVIEW_SYSTEM = """\
You are conducting a precise user research interview to tailor a complex technical document to one reader.

Document context:
{document_context}

Key concepts to assess familiarity with: {key_concepts_str}

Fields you must assess to build the reader profile:
- familiarity_level: novice/aware/practitioner/expert in this domain
- background_field: their professional background (ML engineer, biologist, student, etc.)
- math_comfort: intuition_only/skim/engage/formal
- learning_goal: explain (to others) / critique / apply / research
- known_concepts: which of the key concepts they already understand well
- unknown_concepts: which concepts they specifically need explained from scratch
- time_available: skim (30min) / study (2-3hrs) / deep_dive (days)
- primary_use_case: what they will do with this knowledge
- preferred_analogy_domain: their home domain for cross-domain analogies
- frustration_points: what usually confuses them in this area

Interview so far (turn {turn}/{max_turns}):
{history_text}

Fields still uncertain: {remaining_fields}

Generate the single best next question. Rules:
- Ask about the most uncertain field first
- For domain-specific concepts, ask about THIS document's concepts (not generic questions)
- Multiple-choice questions are preferred (easier to answer); include an "other / write your own" option
- If all critical fields are assessed OR turn >= {max_turns}, set interview_complete=true and next_question=null
- Do not repeat questions already asked
"""

_CRITICAL_FIELDS = [
    "familiarity_level",
    "background_field",
    "math_comfort",
    "learning_goal",
    "time_available",
    "primary_use_case",
    "known_concepts",
    "unknown_concepts",
    "preferred_analogy_domain",
    "frustration_points",
]


def _build_interview_prompt(
    document_context: str,
    key_concepts: list[str],
    qa_history: list[dict],
    turn: int,
    max_turns: int,
) -> str:
    key_concepts_str = ", ".join(key_concepts) if key_concepts else "(not yet extracted)"

    history_lines = []
    for i, pair in enumerate(qa_history):
        history_lines.append(f"Q{i + 1}: {pair['question']}")
        history_lines.append(f"A{i + 1}: {pair['answer']}")
    history_text = "\n".join(history_lines) if history_lines else "(no questions asked yet)"

    # Guess which fields have been covered based on history length
    asked_count = len(qa_history)
    covered = _CRITICAL_FIELDS[:asked_count] if asked_count < len(_CRITICAL_FIELDS) else _CRITICAL_FIELDS
    remaining = [f for f in _CRITICAL_FIELDS if f not in covered]
    remaining_fields = ", ".join(remaining) if remaining else "all covered — may wrap up"

    return _INTERVIEW_SYSTEM.format(
        document_context=document_context,
        key_concepts_str=key_concepts_str,
        turn=turn,
        max_turns=max_turns,
        history_text=history_text,
        remaining_fields=remaining_fields,
    )


# ---------------------------------------------------------------------------
# Terminal and web question delivery
# ---------------------------------------------------------------------------

def _ask_terminal(q: dict) -> str:
    """Print question, read answer from stdin. Returns "" if stdin is not a TTY."""
    if not sys.stdin.isatty():
        return ""

    print()
    print(q["text"])
    options = q.get("options", [])
    if options:
        for i, opt in enumerate(options, 1):
            print(f"  {i}. {opt}")
        raw = input("Your answer (number or text): ").strip()
        # If they typed a number, resolve it to the option text
        try:
            idx = int(raw)
            if 1 <= idx <= len(options):
                return options[idx - 1]
        except ValueError:
            pass
        return raw
    else:
        return input("Your answer: ").strip()


async def _ask_web(q: dict, state_dir: Path) -> str:
    """Append question to qa_queue.jsonl; poll qa_answers.jsonl for matching answer.

    Returns the answer string, or "" on timeout (300s).
    """
    queue_path = state_dir / "qa_queue.jsonl"
    answers_path = state_dir / "qa_answers.jsonl"

    # Append question to the queue
    with queue_path.open("a") as fh:
        fh.write(json.dumps(q) + "\n")

    # Poll for the answer
    deadline = time.monotonic() + _WEB_TIMEOUT
    seen_ids: set[str] = set()

    while time.monotonic() < deadline:
        await asyncio.sleep(_WEB_POLL_INTERVAL)
        if not answers_path.exists():
            continue
        try:
            lines = answers_path.read_text().splitlines()
        except OSError:
            continue
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                ans = json.loads(line)
            except json.JSONDecodeError:
                continue
            if ans.get("id") == q["id"] and ans["id"] not in seen_ids:
                return str(ans.get("answer", ""))
    return ""


# ---------------------------------------------------------------------------
# Core interview loop
# ---------------------------------------------------------------------------

async def _conduct_interview(
    document_context: str,
    key_concepts: list[str],
    router,
    state_dir: Path,
    use_web_ipc: bool,
) -> list[dict]:
    """Run the agentic interview loop. Returns list of {"question": str, "answer": str}."""
    qa_history: list[dict] = []
    turn = 0

    while turn < _MAX_TURNS:
        system_prompt = _build_interview_prompt(
            document_context, key_concepts, qa_history, turn, _MAX_TURNS
        )
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": "What is the next question to ask? (Or declare the interview complete.)"},
        ]

        decision: InterviewDecision = await router.call(messages, InterviewDecision)

        if decision.interview_complete or decision.next_question is None:
            emit({"event": "stage1_interview_complete", "turns_completed": turn, "reason": "agent_declared_complete"})
            break

        q = decision.next_question
        q_dict = {
            "id": f"q{turn}",
            "text": q.text,
            "question_type": q.question_type,
            "options": q.options,
            "turn": turn,
        }

        emit({"event": "stage1_question_asked", "turn": turn, "question": q.text[:120]})

        if use_web_ipc:
            answer = await _ask_web(q_dict, state_dir)
        else:
            answer = _ask_terminal(q_dict)
            if not sys.stdin.isatty() and not answer:
                emit({"event": "stage1_headless_no_answer", "turn": turn})

        qa_history.append({"question": q.text, "answer": answer})
        turn += 1

        # After minimum turns, the agent can wrap up early — already handled above
        # on next iteration. Force stop if we hit the hard limit.
        if turn >= _MAX_TURNS:
            emit({"event": "stage1_interview_complete", "turns_completed": turn, "reason": "max_turns_reached"})
            break

    return [{"question": p["question"], "answer": p["answer"]} for p in qa_history]


# ---------------------------------------------------------------------------
# Profile synthesis
# ---------------------------------------------------------------------------

_SYNTHESIS_SYSTEM = """\
You are synthesizing a user research interview into a structured reader profile.

Document context:
{document_context}

Interview transcript:
{transcript}

Based on this interview, produce a complete UserProfile. For fields not covered by the
interview, make reasonable inferences from what was said.
- For `effective_depth`: map familiarity+time_available to survey/standard/deep.
  (novice+skim → survey; practitioner+study → standard; expert+deep_dive → deep)
- For `math_mode`: map math_comfort to intuition/equations_explained/full_derivations.
  (intuition_only → intuition; skim/engage → equations_explained; formal → full_derivations)
- For legacy fields q3_correct, q6_response, q6_known, q7_correct: set to reasonable
  defaults (q3_correct=true, q6_response="", q6_known="partial", q7_correct="partial").
- For `reading_goal_note`: write a 1-sentence description of what the reader will do
  with this knowledge, in their own words where possible.
"""


async def _synthesize_profile(
    qa_pairs: list[dict],
    document_context: str,
    router,
) -> UserProfile:
    """Single structured call to turn Q&A transcript into a UserProfile."""
    transcript_lines = []
    for i, pair in enumerate(qa_pairs, 1):
        transcript_lines.append(f"Q{i}: {pair['question']}")
        transcript_lines.append(f"A{i}: {pair['answer']}")
    transcript = "\n".join(transcript_lines) if transcript_lines else "(no interview conducted)"

    system_prompt = _SYNTHESIS_SYSTEM.format(
        document_context=document_context,
        transcript=transcript,
    )
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": "Produce the UserProfile."},
    ]
    return await router.call(messages, UserProfile)


# ---------------------------------------------------------------------------
# Minimal fallback interview (no agent model available)
# ---------------------------------------------------------------------------

async def _minimal_fallback_interview(state_dir: Path, use_web_ipc: bool) -> list[dict]:
    """3-question hardcoded fallback when no agent model is configured."""
    fallback_questions = [
        {
            "id": "q0",
            "text": "How familiar are you with this topic overall?",
            "question_type": "mc",
            "options": ["Novice — I'm just starting out", "Aware — I know the basics", "Practitioner — I use it regularly", "Expert — I know it deeply"],
            "turn": 0,
        },
        {
            "id": "q1",
            "text": "What is your professional background?",
            "question_type": "open",
            "options": [],
            "turn": 1,
        },
        {
            "id": "q2",
            "text": "How comfortable are you with mathematical notation and proofs?",
            "question_type": "mc",
            "options": ["I prefer intuition and diagrams", "I skim equations but don't work through them", "I engage with equations as needed", "I prefer full formal derivations"],
            "turn": 2,
        },
    ]
    qa_pairs = []
    for q in fallback_questions:
        emit({"event": "stage1_question_asked", "turn": q["turn"], "question": q["text"]})
        if use_web_ipc:
            answer = await _ask_web(q, state_dir)
        else:
            answer = _ask_terminal(q)
        qa_pairs.append({"question": q["text"], "answer": answer})
    emit({"event": "stage1_interview_complete", "turns_completed": len(qa_pairs), "reason": "fallback_complete"})
    return qa_pairs


def _profile_from_fallback(qa_pairs: list[dict]) -> UserProfile:
    """Build a UserProfile from the 3-question fallback answers."""
    familiarity = "practitioner"
    background = "unknown"
    math_comfort = "engage"

    if len(qa_pairs) >= 1:
        ans0 = qa_pairs[0]["answer"].lower()
        if "novice" in ans0 or "just starting" in ans0:
            familiarity = "novice"
        elif "aware" in ans0 or "basics" in ans0:
            familiarity = "aware"
        elif "expert" in ans0 or "deeply" in ans0:
            familiarity = "expert"

    if len(qa_pairs) >= 2:
        background = qa_pairs[1]["answer"].strip() or "unknown"

    if len(qa_pairs) >= 3:
        ans2 = qa_pairs[2]["answer"].lower()
        if "intuition" in ans2 or "diagram" in ans2:
            math_comfort = "intuition_only"
        elif "skim" in ans2:
            math_comfort = "skim"
        elif "formal" in ans2 or "derivation" in ans2:
            math_comfort = "formal"

    depth_map = {"novice": "survey", "aware": "survey", "practitioner": "standard", "expert": "deep"}
    math_map = {"intuition_only": "intuition", "skim": "equations_explained", "engage": "equations_explained", "formal": "full_derivations"}

    return UserProfile(
        familiarity_level=familiarity,
        background_field=background,
        math_comfort=math_comfort,
        learning_goal="apply",
        known_concepts=[],
        unknown_concepts=[],
        effective_depth=depth_map.get(familiarity, "standard"),
        math_mode=math_map.get(math_comfort, "equations_explained"),
        reading_goal_note="Apply the techniques described in the document.",
    )


# ---------------------------------------------------------------------------
# IPC cleanup
# ---------------------------------------------------------------------------

def _cleanup_ipc_files(state_dir: Path) -> None:
    """Delete Q&A IPC files from any prior run before starting a new one."""
    for name in ("qa_queue.jsonl", "qa_answers.jsonl", "qa_complete"):
        p = state_dir / name
        if p.exists():
            p.unlink()


def _create_qa_complete_sentinel(state_dir: Path) -> None:
    """Create the qa_complete sentinel file."""
    (state_dir / "qa_complete").touch()


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

async def run(state: PipelineState, cfg: Config) -> None:
    state_dir = Path(state["state_dir"])
    profile_path = state_dir / "user_profile.json"

    if stage_is_complete(state_dir, 1):
        emit({"event": "stage_skipped", "stage": 1, "reason": "already_complete"})
        return

    emit({"event": "stage_start", "stage": 1, "mode": "agentic_interview"})

    # Determine delivery mode
    use_web_ipc = not sys.stdin.isatty()

    # Clean up any leftover IPC files from a prior run
    _cleanup_ipc_files(state_dir)

    # Gather document context
    document_context = _get_document_context(state_dir)
    key_concepts = _get_key_concepts(state_dir)

    emit({"event": "stage1_interview_start", "use_web_ipc": use_web_ipc, "key_concept_count": len(key_concepts)})

    # Check if the "agent" role is configured
    has_agent = "agent" in cfg.models and bool(cfg.models["agent"])

    if not has_agent:
        # Minimal fallback — no LLM for interviewing
        qa_pairs = await _minimal_fallback_interview(state_dir, use_web_ipc)
        _create_qa_complete_sentinel(state_dir)
        profile = _profile_from_fallback(qa_pairs)
        emit({"event": "stage1_profile_synthesized", "mode": "fallback_heuristic"})
    else:
        router = make_router("agent", cfg)

        qa_pairs = await _conduct_interview(
            document_context=document_context,
            key_concepts=key_concepts,
            router=router,
            state_dir=state_dir,
            use_web_ipc=use_web_ipc,
        )

        _create_qa_complete_sentinel(state_dir)

        profile = await _synthesize_profile(qa_pairs, document_context, router)
        emit({"event": "stage1_profile_synthesized", "mode": "llm_synthesis", "qa_turns": len(qa_pairs)})

    atomic_write(profile_path, profile.model_dump_json(indent=2))
    mark_stage_complete(state_dir, 1)
    emit({
        "event": "stage_complete",
        "stage": 1,
        "mode": "agentic_interview",
        "artifact": str(profile_path),
    })
