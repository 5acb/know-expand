"""Tests for know_expand.agents.base — _msg_chars, QuotaAwareRouter, ensemble_verify."""

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import BaseModel

from know_expand.agents import base as base_mod
from know_expand.agents.base import (
    EnsembleVerdict,
    QuotaAwareRouter,
    _msg_chars,
    _PROBED_UNAVAILABLE,
    _find_in_chain,
    _instructor_mode,
    ensemble_verify,
)
from know_expand.config import (
    BibliographyConfig,
    CentralityConfig,
    ConcurrencyConfig,
    Config,
    RateLimitConfig,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_cfg(models: dict[str, list[str]] | None = None) -> Config:
    return Config(
        bibliography=BibliographyConfig(),
        centrality=CentralityConfig(),
        rate_limits={"semantic_scholar": RateLimitConfig(max_rate=100, time_period=300)},
        concurrency=ConcurrencyConfig(default=4),
        timeouts={"default": 30},
        adversarial_rounds={"survey": 1, "standard": 2, "deep": 3},
        boilerplate_stop_list=[],
        models=models or {"researcher": ["claude-3-haiku"]},
    )


# ---------------------------------------------------------------------------
# _msg_chars — character counter
# ---------------------------------------------------------------------------

def test_msg_chars_string_content():
    """Single string content — returns len of that string."""
    msgs = [{"role": "user", "content": "hello world"}]
    assert _msg_chars(msgs) == len("hello world")


def test_msg_chars_list_content():
    """List-block content — sums text blocks, ignores non-text blocks."""
    msgs = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "foo bar"},
                {"type": "image_url", "url": "https://example.com/img.png"},
                {"type": "text", "text": "baz"},
            ],
        }
    ]
    assert _msg_chars(msgs) == len("foo bar") + len("baz")


def test_msg_chars_mixed_messages():
    """Multiple messages with mixed string and list content are summed."""
    msgs = [
        {"role": "system", "content": "You are helpful."},
        {"role": "user", "content": [
            {"type": "text", "text": "What is attention?"},
        ]},
        {"role": "assistant", "content": "Attention is a mechanism..."},
    ]
    expected = len("You are helpful.") + len("What is attention?") + len("Attention is a mechanism...")
    assert _msg_chars(msgs) == expected


def test_msg_chars_empty_messages():
    assert _msg_chars([]) == 0


def test_msg_chars_missing_content_key():
    """Message with no content key contributes 0 chars."""
    msgs = [{"role": "user"}]
    assert _msg_chars(msgs) == 0


def test_msg_chars_empty_string_content():
    msgs = [{"role": "user", "content": ""}]
    assert _msg_chars(msgs) == 0


def test_msg_chars_list_with_no_text_blocks():
    """List content with only non-text blocks contributes 0 chars."""
    msgs = [{"role": "user", "content": [{"type": "image_url", "url": "x"}]}]
    assert _msg_chars(msgs) == 0


def test_msg_chars_cache_control_blocks():
    """Anthropic cache_control blocks (no 'text' key) contribute 0 chars."""
    msgs = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "cached prefix"},
                {"type": "text", "text": "", "cache_control": {"type": "ephemeral"}},
            ],
        }
    ]
    assert _msg_chars(msgs) == len("cached prefix")


# ---------------------------------------------------------------------------
# QuotaAwareRouter — _PROBED_UNAVAILABLE skip
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_router_skips_probed_unavailable_model():
    """Router skips models that appear in _PROBED_UNAVAILABLE at construction."""
    from know_expand.agents import base as base_mod
    from pydantic import BaseModel

    class Dummy(BaseModel):
        value: str

    cfg = _make_cfg({"researcher": ["bad-model", "good-model"]})

    original = set(base_mod._PROBED_UNAVAILABLE)
    base_mod._PROBED_UNAVAILABLE.add("bad-model")
    try:
        router = QuotaAwareRouter("researcher", ["bad-model", "good-model"], cfg)
        assert router._next_model() == "good-model"
    finally:
        base_mod._PROBED_UNAVAILABLE.clear()
        base_mod._PROBED_UNAVAILABLE.update(original)


@pytest.mark.asyncio
async def test_router_skips_model_added_to_probed_unavailable_after_construction():
    """_next_model() checks _PROBED_UNAVAILABLE live, so models added after
    construction are also skipped."""
    from know_expand.agents import base as base_mod

    cfg = _make_cfg({"researcher": ["model-a", "model-b"]})

    original = set(base_mod._PROBED_UNAVAILABLE)
    try:
        router = QuotaAwareRouter("researcher", ["model-a", "model-b"], cfg)
        # Before: model-a is first
        assert router._next_model() == "model-a"
        # Add model-a to the global set
        base_mod._PROBED_UNAVAILABLE.add("model-a")
        # Now model-b should be returned
        assert router._next_model() == "model-b"
    finally:
        base_mod._PROBED_UNAVAILABLE.clear()
        base_mod._PROBED_UNAVAILABLE.update(original)


@pytest.mark.asyncio
async def test_router_all_models_unavailable_raises():
    """When all models are in _skip, call() raises RuntimeError with quota_exhausted."""
    from know_expand.agents import base as base_mod
    from pydantic import BaseModel

    class Dummy(BaseModel):
        value: str

    cfg = _make_cfg({"researcher": ["model-x"]})

    original = set(base_mod._PROBED_UNAVAILABLE)
    base_mod._PROBED_UNAVAILABLE.add("model-x")
    try:
        router = QuotaAwareRouter("researcher", ["model-x"], cfg)
        with pytest.raises(RuntimeError, match="All models exhausted"):
            await router.call([{"role": "user", "content": "test"}], Dummy)
    finally:
        base_mod._PROBED_UNAVAILABLE.clear()
        base_mod._PROBED_UNAVAILABLE.update(original)


@pytest.mark.asyncio
async def test_router_auth_error_skips_model_and_tries_next():
    """Auth error permanently skips the failing model; next model is used."""
    import litellm
    from pydantic import BaseModel

    class Dummy(BaseModel):
        value: str

    cfg = _make_cfg({"researcher": ["bad-auth-model", "fallback-model"]})
    router = QuotaAwareRouter("researcher", ["bad-auth-model", "fallback-model"], cfg)

    fallback_result = Dummy(value="ok")

    call_count = {"n": 0}

    async def fake_do_call(model, messages, schema, cfg_, role="?", budget=0):
        call_count["n"] += 1
        if model == "bad-auth-model":
            raise litellm.AuthenticationError(
                message="Invalid API key", llm_provider="openai", model=model
            )
        return fallback_result

    with patch("know_expand.agents.base._do_call_with_thinking", side_effect=fake_do_call):
        result = await router.call([{"role": "user", "content": "test"}], Dummy)

    assert result.value == "ok"
    assert "bad-auth-model" in router._skip
    assert call_count["n"] == 2


# ---------------------------------------------------------------------------
# _find_in_chain — exception chain walker
# ---------------------------------------------------------------------------

def test_find_in_chain_direct_match():
    exc = ValueError("direct")
    assert _find_in_chain(exc, ValueError) is exc


def test_find_in_chain_cause():
    root = TypeError("root cause")
    wrapper = RuntimeError("wrapper")
    wrapper.__cause__ = root
    assert _find_in_chain(wrapper, TypeError) is root


def test_find_in_chain_no_match():
    exc = ValueError("no match")
    assert _find_in_chain(exc, KeyError) is None


def test_find_in_chain_context():
    root = KeyError("from context")
    wrapper = RuntimeError("wrapper")
    wrapper.__context__ = root
    assert _find_in_chain(wrapper, KeyError) is root


def test_find_in_chain_cycle_safe():
    """Circular exception chains don't loop forever."""
    exc = RuntimeError("a")
    exc2 = RuntimeError("b")
    exc.__cause__ = exc2
    exc2.__cause__ = exc  # cycle
    # Should return None (no ValueError in chain) without infinite loop
    result = _find_in_chain(exc, ValueError)
    assert result is None


# ---------------------------------------------------------------------------
# _instructor_mode — model routing
# ---------------------------------------------------------------------------

def test_instructor_mode_deepinfra():
    import instructor
    assert _instructor_mode("deepinfra/meta-llama/Llama-3.3-70B-Instruct-Turbo") == instructor.Mode.JSON


def test_instructor_mode_gemini_api():
    import instructor
    assert _instructor_mode("gemini/gemini-1.5-flash") == instructor.Mode.JSON


def test_instructor_mode_claude():
    import instructor
    assert _instructor_mode("claude-3-haiku") == instructor.Mode.TOOLS


def test_instructor_mode_openai():
    import instructor
    assert _instructor_mode("gpt-4o") == instructor.Mode.TOOLS


# ---------------------------------------------------------------------------
# ensemble_verify — debiased LLM-as-judge ensemble
# ---------------------------------------------------------------------------

class _Verdict(BaseModel):
    relation: str
    note: str = ""


def _ensemble_cfg() -> Config:
    return _make_cfg({
        "proposer_a": ["model-a"],
        "proposer_b": ["model-b"],
        "adjudicator": ["model-c"],
    })


@pytest.mark.asyncio
async def test_ensemble_verify_agreement_skips_adjudicator():
    """Two proposers agreeing on verdict_key return immediately — no third call."""
    cfg = _ensemble_cfg()
    calls: list[str] = []

    async def fake_call(self, messages, schema):
        calls.append(self.role)
        # Different free-text "note", same relation — should still count as agreement.
        return _Verdict(relation="supports", note=f"from {self.role}")

    with patch.object(QuotaAwareRouter, "call", new=fake_call):
        verdict = await ensemble_verify(
            proposer_roles=["proposer_a", "proposer_b"],
            adjudicator_role="adjudicator",
            messages=[{"role": "user", "content": "verify this"}],
            schema=_Verdict,
            cfg=cfg,
            verdict_key=lambda v: v.relation,
        )

    assert isinstance(verdict, EnsembleVerdict)
    assert verdict.agreed is True
    assert verdict.adjudicator_role is None
    assert verdict.result.relation == "supports"
    assert set(calls) == {"proposer_a", "proposer_b"}
    assert "adjudicator" not in calls


@pytest.mark.asyncio
async def test_ensemble_verify_default_verdict_key_uses_equality():
    """Without an explicit verdict_key, plain model equality decides agreement."""
    cfg = _ensemble_cfg()

    async def fake_call(self, messages, schema):
        return _Verdict(relation="neutral")

    with patch.object(QuotaAwareRouter, "call", new=fake_call):
        verdict = await ensemble_verify(
            proposer_roles=["proposer_a", "proposer_b"],
            adjudicator_role="adjudicator",
            messages=[{"role": "user", "content": "x"}],
            schema=_Verdict,
            cfg=cfg,
        )

    assert verdict.agreed is True


@pytest.mark.asyncio
async def test_ensemble_verify_disagreement_escalates_to_adjudicator():
    """Differing verdict_key values trigger exactly one third-model adjudicator call."""
    cfg = _ensemble_cfg()
    calls: list[str] = []

    async def fake_call(self, messages, schema):
        calls.append(self.role)
        if self.role == "proposer_a":
            return _Verdict(relation="supports")
        if self.role == "proposer_b":
            return _Verdict(relation="contradicts")
        return _Verdict(relation="contradicts")  # adjudicator's final pick

    with patch.object(QuotaAwareRouter, "call", new=fake_call):
        verdict = await ensemble_verify(
            proposer_roles=["proposer_a", "proposer_b"],
            adjudicator_role="adjudicator",
            messages=[{"role": "user", "content": "verify this"}],
            schema=_Verdict,
            cfg=cfg,
            verdict_key=lambda v: v.relation,
        )

    assert verdict.agreed is False
    assert verdict.adjudicator_role == "adjudicator"
    assert verdict.result.relation == "contradicts"
    assert calls.count("proposer_a") == 1
    assert calls.count("proposer_b") == 1
    assert calls.count("adjudicator") == 1
    # Raw proposer outputs are preserved even though the adjudicator overrode them.
    assert {r.relation for r in verdict.proposer_results} == {"supports", "contradicts"}


@pytest.mark.asyncio
async def test_ensemble_verify_requires_exactly_two_proposers():
    cfg = _ensemble_cfg()
    with pytest.raises(ValueError, match="exactly 2 proposer_roles"):
        await ensemble_verify(
            proposer_roles=["proposer_a"],
            adjudicator_role="adjudicator",
            messages=[{"role": "user", "content": "x"}],
            schema=_Verdict,
            cfg=cfg,
        )


@pytest.mark.asyncio
async def test_ensemble_verify_reuses_provided_routers():
    """Regression: passing `routers=` must reuse those instances instead of
    building fresh ones via make_router() — otherwise per-model failure/
    backoff state never survives across repeated calls in a loop (see
    s8_verify.py, which now builds these once per stage run)."""
    cfg = _ensemble_cfg()

    router_a = AsyncMock()
    router_a.call = AsyncMock(return_value=_Verdict(relation="supports"))
    router_b = AsyncMock()
    router_b.call = AsyncMock(return_value=_Verdict(relation="supports"))

    with patch("know_expand.agents.base.make_router", side_effect=AssertionError("should not build a fresh router")):
        verdict = await ensemble_verify(
            proposer_roles=["proposer_a", "proposer_b"],
            adjudicator_role="adjudicator",
            messages=[{"role": "user", "content": "verify this"}],
            schema=_Verdict,
            cfg=cfg,
            verdict_key=lambda v: v.relation,
            routers={"proposer_a": router_a, "proposer_b": router_b},
        )

    assert verdict.agreed is True
    router_a.call.assert_awaited_once()
    router_b.call.assert_awaited_once()


@pytest.mark.asyncio
async def test_ensemble_verify_gather_lets_both_proposers_finish_before_raising():
    """Regression: without return_exceptions=True, asyncio.gather propagates
    the first exception immediately while leaving the other task orphaned in
    the background (still holding its semaphore slot, still billed). Confirm
    both proposer calls actually complete before the exception surfaces."""
    cfg = _ensemble_cfg()
    finished = {"proposer_b": False}

    async def fake_call(self, messages, schema):
        if self.role == "proposer_a":
            raise RuntimeError("proposer_a exhausted")
        if self.role == "proposer_b":
            await asyncio.sleep(0.05)
            finished["proposer_b"] = True
            return _Verdict(relation="supports")
        return _Verdict(relation="supports")

    with patch.object(QuotaAwareRouter, "call", new=fake_call):
        with pytest.raises(RuntimeError, match="proposer_a exhausted"):
            await ensemble_verify(
                proposer_roles=["proposer_a", "proposer_b"],
                adjudicator_role="adjudicator",
                messages=[{"role": "user", "content": "verify this"}],
                schema=_Verdict,
                cfg=cfg,
                verdict_key=lambda v: v.relation,
            )

    assert finished["proposer_b"] is True, (
        "proposer_b's in-flight call must run to completion, not be left "
        "orphaned in the background when proposer_a raises first"
    )


@pytest.mark.asyncio
async def test_ensemble_verify_adjudicator_prompt_is_anonymized():
    """The adjudicator prompt must never leak which role/model produced which
    candidate — only anonymous 'Candidate 1' / 'Candidate 2' labels."""
    cfg = _make_cfg({
        "proposer_a": ["gemini/gemini-2.5-flash"],
        "proposer_b": ["gpt-4o-mini"],
        "adjudicator": ["claude-sonnet-4-6"],
    })
    captured: list[list[dict]] = []

    async def fake_call(self, messages, schema):
        if self.role == "adjudicator":
            captured.append(messages)
            return _Verdict(relation="contradicts")
        if self.role == "proposer_a":
            return _Verdict(relation="supports")
        return _Verdict(relation="contradicts")

    with patch.object(QuotaAwareRouter, "call", new=fake_call):
        await ensemble_verify(
            proposer_roles=["proposer_a", "proposer_b"],
            adjudicator_role="adjudicator",
            messages=[{"role": "user", "content": "verify this"}],
            schema=_Verdict,
            cfg=cfg,
            verdict_key=lambda v: v.relation,
        )

    assert len(captured) == 1
    full_prompt = json.dumps(captured[0])
    for leak in ("proposer_a", "proposer_b", "gemini", "gpt-4o", "claude-sonnet", "model-a", "model-b"):
        assert leak not in full_prompt, f"adjudicator prompt leaked identifying string {leak!r}"
    assert "Candidate 1" in full_prompt
    assert "Candidate 2" in full_prompt


@pytest.mark.asyncio
async def test_ensemble_verify_randomizes_candidate_order():
    """A per-call coin flip decides candidate presentation order — both
    orderings must be reachable, exercised here via a mocked random.random()."""
    cfg = _ensemble_cfg()
    captured_prompts: list[str] = []

    async def fake_call(self, messages, schema):
        if self.role == "adjudicator":
            captured_prompts.append(messages[-1]["content"])
            return _Verdict(relation="contradicts")
        if self.role == "proposer_a":
            return _Verdict(relation="supports")
        return _Verdict(relation="contradicts")

    with patch.object(QuotaAwareRouter, "call", new=fake_call):
        with patch.object(base_mod.random, "random", return_value=0.9):
            await ensemble_verify(
                proposer_roles=["proposer_a", "proposer_b"],
                adjudicator_role="adjudicator",
                messages=[{"role": "user", "content": "verify this"}],
                schema=_Verdict,
                cfg=cfg,
                verdict_key=lambda v: v.relation,
            )
        with patch.object(base_mod.random, "random", return_value=0.1):
            await ensemble_verify(
                proposer_roles=["proposer_a", "proposer_b"],
                adjudicator_role="adjudicator",
                messages=[{"role": "user", "content": "verify this"}],
                schema=_Verdict,
                cfg=cfg,
                verdict_key=lambda v: v.relation,
            )

    assert len(captured_prompts) == 2
    # ensemble_verify: `candidates = [a, b] if random() < 0.5 else [b, a]`.
    # random() == 0.9 (>= 0.5) -> swap: proposer_b ("contradicts") shown as Candidate 1.
    assert captured_prompts[0].index('"contradicts"') < captured_prompts[0].index('"supports"')
    # random() == 0.1 (< 0.5) -> no swap: proposer_a ("supports") shown as Candidate 1.
    assert captured_prompts[1].index('"supports"') < captured_prompts[1].index('"contradicts"')


