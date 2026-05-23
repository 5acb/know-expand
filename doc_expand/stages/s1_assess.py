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
import re
import sys
import time
from pathlib import Path

from doc_expand.agents.base import make_router
from doc_expand.agents.schemas import ConceptList, InterviewDecision, UserProfile
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
    """Load key concepts from terms.json (produced by S2).

    Sorts core terms first, then supporting, each group by occurrence count.
    Prefers multi-word phrases and skips very short tokens that make poor
    interview options.
    """
    terms_path = state_dir / "terms.json"
    if not terms_path.exists():
        return []
    try:
        terms = json.loads(terms_path.read_text())
        # Tier ordering: core → supporting → incidental
        tier_rank = {"core": 0, "supporting": 1, "incidental": 2}
        sorted_terms = sorted(
            terms,
            key=lambda t: (tier_rank.get(t.get("centrality", "incidental"), 2),
                           -t.get("occurrence_count", 0)),
        )
        result = []
        for t in sorted_terms:
            name = t["name"]
            # Prefer multi-word concepts or meaningful single words (≥5 chars)
            if " " not in name and len(name) < 5:
                continue
            result.append(name)
            if len(result) >= 20:
                break
        return result
    except Exception:
        return []


def _get_raw_document_text(state_dir: Path, max_chars: int = 6000) -> str:
    """Best-effort: return a long slice of the document for concept extraction."""
    source = state_dir / "source.txt"
    if source.exists():
        return source.read_text(errors="replace")[:max_chars]
    # Fall back to stitching first few chunks
    chunks_dir = state_dir / "chunks"
    if chunks_dir.exists():
        parts = []
        total = 0
        for p in sorted(chunks_dir.glob("*.json")):
            try:
                text = json.loads(p.read_text()).get("text", "")
                parts.append(text)
                total += len(text)
                if total >= max_chars:
                    break
            except Exception:
                continue
        return " ".join(parts)[:max_chars]
    return ""


async def _identify_key_concepts(doc_text: str, router) -> list[str]:
    """Single LLM call: read the document and return the 6-10 most important concepts."""
    if not doc_text.strip():
        return []
    messages = [{
        "role": "user",
        "content": (
            "Read this document excerpt and identify the 6-10 most important specific "
            "technical concepts a reader would need to understand. "
            "Return them as a ConceptList. Each concept should be a precise noun phrase "
            "(e.g. 'knowledge graph', 'attention mechanism', 'topological sort') — "
            "not vague categories like 'mathematics' or 'algorithms'.\n\n"
            f"Document excerpt:\n{doc_text}"
        ),
    }]
    try:
        result: ConceptList = await router.call(messages, ConceptList)
        return result.concepts[:10]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Interview prompt builder
# ---------------------------------------------------------------------------

_INTERVIEW_SYSTEM = """\
You are personalising a technical document for one specific reader. \
Ask sharp, document-specific questions so the pipeline knows exactly what to explain, skip, and emphasise.

=== Document ===
{document_context}

=== Key concepts identified in this document ===
{key_concepts_str}

=== What you must learn (in roughly this order) ===
Turn 1 — FAMILIARITY: Ask "Which of these specific concepts from the document do you already know well?" \
Present ALL key concepts above as checkbox options. Include "None of these" and "All of them".

Turn 2 — GOAL: Ask what the reader wants to DO after reading this document. \
Options must be specific to what this document enables — not generic categories. \
Example options: "Build [X described in the doc]", "Evaluate whether to adopt this approach", \
"Understand it deeply enough to teach it", "Get a quick mental model to follow discussions", \
"Research extensions or open problems".

Turn 3 — DEPTH & TIME: Ask how deep they want to go. \
Options: "Quick overview (30 min)", "Solid working understanding (2–3 hrs)", \
"Deep mastery including the math (days)", "Just the parts relevant to [their goal from turn 2]".

Turn 4 — GAPS: Based on turn 1 answers, ask about the ONE concept they said they don't know \
that is most central to the document. Ask what specifically confuses them about it. Open question.

Turn 5+ — Fill remaining fields: \
- math_comfort (ask with reference to actual notation in this document, not generic "math in AI") \
- primary_use_case (what specific problem / project / decision will this knowledge feed into?) \
- preferred_analogy_domain (what field do they know very well — used to build analogies for them) \
- frustration_points (what has tripped them up before with topics like this?)

=== Interview so far (turn {turn}/{max_turns}) ===
{history_text}

=== Fields still uncertain ===
{remaining_fields}

=== Rules (NON-NEGOTIABLE) ===
1. `text` field = the question sentence ONLY. No options, no A)/B)/C)/D), no lists.
2. ALL choices go in the `options` list. Never in `text`.
3. MC questions must have 4–7 options including one free-form escape ("Other — describe below").
4. Never ask about job title or professional background as a standalone question. \
   Infer background from what they know and what they want to do.
5. You MUST NOT set interview_complete=true while "Fields still needed" lists anything. \
   The only exceptions are: (a) turn >= {max_turns}, or (b) the reader stopped responding. \
   If all fields show as covered, set interview_complete=true.
"""

_CRITICAL_FIELDS = [
    "familiarity_level",
    "learning_goal",
    "time_available",
    "unknown_concepts",
    "math_comfort",
    "primary_use_case",
    "preferred_analogy_domain",
    "frustration_points",
]

# Keywords whose presence in question OR answer text signals that a field was addressed.
# Match is done on the lowercased concatenation of the Q and A texts.
_FIELD_COVERAGE_KEYWORDS: dict[str, list[str]] = {
    "familiarity_level":      ["familiar", "already know", "know well", "experience with",
                                "expertise", "novice", "practitioner", "expert", "aware"],
    "learning_goal":          ["want to do", "after reading", "goal", "use this for", "plan to",
                                "build", "implement", "evaluate", "teach", "research", "apply"],
    "time_available":         ["how deep", "time", "overview", "mastery", "quick",
                                "30 min", "2 hr", "days", "depth"],
    "unknown_concepts":       ["don't know", "not sure", "confus", "explain", "understand",
                                "never heard", "unfamiliar", "struggling with", "gaps"],
    "math_comfort":           ["math", "equation", "notation", "formula", "derivation",
                                "formal", "proof", "symbol", "latex"],
    "primary_use_case":       ["problem", "project", "work on", "applying", "use case",
                                "decision", "system", "product", "research question"],
    "preferred_analogy_domain": ["know well", "background in", "field you", "expert in",
                                  "profession", "analogy", "compare to", "like a"],
    "frustration_points":     ["tripped up", "frustrat", "difficult", "hard to understand",
                                "confused by", "struggled", "barrier", "wall", "stuck"],
}


def _detect_covered_fields(qa_history: list[dict]) -> set[str]:
    """Return the set of _CRITICAL_FIELDS addressed in the conversation so far.

    Scans the lowercased concatenation of all questions and answers for the
    keyword signatures of each field. Imperfect but sufficient to detect when
    the agent has drifted past a dimension without covering it.
    """
    all_text = " ".join(
        (pair.get("question", "") + " " + pair.get("answer", "")).lower()
        for pair in qa_history
    )
    return {
        field
        for field, keywords in _FIELD_COVERAGE_KEYWORDS.items()
        if any(kw in all_text for kw in keywords)
    }


def _extract_embedded_options(text: str, existing_options: list[str]) -> tuple[str, list[str]]:
    """
    If the LLM embedded options in the question text (e.g. "A) Foo  B) Bar"),
    extract them into a list and strip them from the text.
    Returns (cleaned_text, options_list). No-ops if options already populated.
    """
    if existing_options:
        return text, existing_options

    # Match patterns: "A) ...", "A. ...", "1) ...", "1. ..." on same line or across lines
    pattern = re.compile(
        r'\b([A-Da-d])[).]\s+(.+?)(?=\s+[A-Da-d][).]\s+|\s*$)',
        re.DOTALL,
    )
    matches = pattern.findall(text)
    if len(matches) >= 2:
        options = [m[1].strip().rstrip('.') for m in matches]
        # Remove the options block from the text (everything from first match onwards)
        split_pos = text.find(matches[0][0] + ')')
        if split_pos == -1:
            split_pos = text.find(matches[0][0] + '.')
        clean = text[:split_pos].strip() if split_pos > 0 else text
        return clean, options

    # Numbered list: "1) ...\n2) ..."
    num_pattern = re.compile(r'(?:^|\n)\s*(\d+)[).]\s+(.+)', re.MULTILINE)
    num_matches = num_pattern.findall(text)
    if len(num_matches) >= 2:
        options = [m[1].strip() for m in num_matches]
        split_pos = re.search(r'\n\s*1[).]\s+', text)
        clean = text[:split_pos.start()].strip() if split_pos else text
        return clean, options

    return text, existing_options


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

    covered = _detect_covered_fields(qa_history)
    remaining = [f for f in _CRITICAL_FIELDS if f not in covered]
    remaining_fields = ", ".join(remaining) if remaining else "all covered — you MAY set interview_complete=true"

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

def _fallback_options(turn: int, key_concepts: list[str]) -> list[str]:
    """Hardcoded fallback options for the three structured turns."""
    if turn == 0:
        return (key_concepts or ["(none identified)"])[:10] + ["None of these", "All of them"]
    if turn == 1:
        return [
            "Build or implement what's described",
            "Evaluate whether to adopt this approach",
            "Understand it deeply enough to teach it",
            "Get a quick mental model",
            "Research extensions or open problems",
            "Other — describe below",
        ]
    if turn == 2:
        return [
            "Quick overview (~30 min)",
            "Solid working understanding (~2 hrs)",
            "Deep mastery including the math (days)",
            "Just the parts relevant to my goal",
        ]
    return []


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
            covered = _detect_covered_fields(qa_history)
            uncovered = [f for f in _CRITICAL_FIELDS if f not in covered]
            if uncovered and turn < _MIN_TURNS:
                # Agent declared complete but critical signals are missing.
                # Override — the next iteration rebuilds the prompt with accurate
                # remaining_fields so the agent knows what it still needs to ask.
                emit({
                    "event": "stage1_coverage_override",
                    "turn": turn,
                    "uncovered_fields": uncovered,
                })
                # Synthesise a stub answer so the loop can continue; the agent
                # declared complete rather than asking, so there is no real answer.
                # Next iteration's prompt will list the uncovered fields explicitly.
                qa_history.append({"question": "(coverage check)", "answer": "(agent skipped)"})
                turn += 1
                continue
            emit({"event": "stage1_interview_complete", "turns_completed": turn,
                  "reason": "agent_declared_complete",
                  "uncovered_fields": [f for f in _CRITICAL_FIELDS if f not in covered]})
            break

        q = decision.next_question
        clean_text, options = _extract_embedded_options(q.text, q.options)

        # LLMs reliably set question_type but often forget to populate options.
        # For the three fixed structured turns we know exactly what the options
        # should be. For later unstructured turns, downgrade to open.
        if not options:
            fallback = _fallback_options(turn, key_concepts)
            if fallback:
                options = fallback
            else:
                q.question_type = "open"

        q_dict = {
            "id": f"q{turn}",
            "text": clean_text,
            "question_type": "mc" if options else q.question_type,
            "options": options,
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

    # Check if the "agent" role is configured
    has_agent = "agent" in cfg.models and bool(cfg.models["agent"])

    if not has_agent:
        emit({"event": "stage1_interview_start", "use_web_ipc": use_web_ipc, "key_concept_count": 0})
        # Minimal fallback — no LLM for interviewing
        qa_pairs = await _minimal_fallback_interview(state_dir, use_web_ipc)
        _create_qa_complete_sentinel(state_dir)
        profile = _profile_from_fallback(qa_pairs)
        emit({"event": "stage1_profile_synthesized", "mode": "fallback_heuristic"})
    else:
        router = make_router("agent", cfg)

        # S2 (extract) now runs before S1, so terms.json already exists with
        # centrality-ranked concepts. Use it directly — no LLM call needed.
        # Fall back to LLM extraction only if terms.json is absent (e.g. resume
        # starting at S1 after clearing S2, or future pipeline variants).
        key_concepts = _get_key_concepts(state_dir)
        if not key_concepts:
            doc_text = _get_raw_document_text(state_dir)
            key_concepts = await _identify_key_concepts(doc_text, router)
            emit({"event": "stage1_concepts_from_llm", "key_concept_count": len(key_concepts)})
        else:
            emit({"event": "stage1_concepts_from_terms", "key_concept_count": len(key_concepts)})

        emit({"event": "stage1_interview_start", "use_web_ipc": use_web_ipc,
              "key_concept_count": len(key_concepts), "key_concepts": key_concepts})

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
