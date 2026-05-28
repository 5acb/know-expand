"""Tests for know_expand.agents.base — _msg_chars and QuotaAwareRouter."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from know_expand.agents.base import (
    QuotaAwareRouter,
    _msg_chars,
    _PROBED_UNAVAILABLE,
    _find_in_chain,
    _instructor_mode,
    make_router,
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

def test_instructor_mode_mistral():
    import instructor
    assert _instructor_mode("mistral/mistral-small") == instructor.Mode.JSON


def test_instructor_mode_groq():
    import instructor
    assert _instructor_mode("groq/llama3-70b") == instructor.Mode.JSON


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
# --free mode — make_router() model filtering
# ---------------------------------------------------------------------------

def test_make_router_free_mode_filters_to_llamacpp():
    """free_mode=True: only llamacpp/ entries survive into the router."""
    cfg = _make_cfg({"researcher": [
        "gemini/gemini-2.5-flash",
        "groq/llama-3.3-70b-versatile",
        "llamacpp/glm4",
    ]})
    cfg.free_mode = True
    router = make_router("researcher", cfg)
    assert router.models == ["llamacpp/glm4"]


def test_make_router_free_mode_no_llamacpp_raises():
    """free_mode=True with no llamacpp/ models raises a clear RuntimeError."""
    cfg = _make_cfg({"researcher": ["gemini/gemini-2.5-flash", "groq/llama-3.3-70b-versatile"]})
    cfg.free_mode = True
    with pytest.raises(RuntimeError, match="llamacpp"):
        make_router("researcher", cfg)


def test_make_router_normal_mode_unfiltered():
    """free_mode=False (default): all models are passed through unchanged."""
    models = ["gemini/gemini-2.5-flash", "llamacpp/glm4"]
    cfg = _make_cfg({"researcher": models})
    router = make_router("researcher", cfg)
    assert router.models == models
