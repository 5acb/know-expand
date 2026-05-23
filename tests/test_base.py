"""Tests for know_expand.agents.base — _msg_chars and QuotaAwareRouter."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from know_expand.agents.base import (
    QuotaAwareRouter,
    _msg_chars,
    _PROBED_UNAVAILABLE,
    _parse_geminicli_stderr,
    _find_in_chain,
    _instructor_mode,
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
        timeouts={"default": 30, "geminicli_timeout_s": 600},
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


@pytest.mark.asyncio
async def test_router_geminicli_error_skips_and_continues():
    """Any RuntimeError from geminicli causes the model to be skipped; next model is tried."""
    from pydantic import BaseModel

    class Dummy(BaseModel):
        value: str

    cfg = _make_cfg({"researcher": ["geminicli/gemini-3.5-flash", "fallback-model"]})
    router = QuotaAwareRouter("researcher", ["geminicli/gemini-3.5-flash", "fallback-model"], cfg)

    fallback_result = Dummy(value="from_fallback")

    async def fake_do_call(model, messages, schema, cfg_, role="?", budget=0):
        if "geminicli" in model:
            raise RuntimeError("geminicli quota exhausted resets in 12.0h")
        return fallback_result

    with patch("know_expand.agents.base._do_call_with_thinking", side_effect=fake_do_call):
        result = await router.call([{"role": "user", "content": "test"}], Dummy)

    assert result.value == "from_fallback"
    assert "geminicli/gemini-3.5-flash" in router._skip


@pytest.mark.asyncio
async def test_geminicli_quota_adds_original_model_key_to_probed_unavailable():
    """
    _PROBED_UNAVAILABLE must receive the original models.yaml key
    (e.g. "geminicli/gemini-3.5-flash"), NOT the internal cli_model name
    ("geminicli/gemini-3.1-pro-preview").  The router's _next_model() looks
    up the models.yaml key, so the wrong key silently never fires.
    """
    import know_expand.agents.base as base_mod
    from unittest.mock import MagicMock

    original = set(base_mod._PROBED_UNAVAILABLE)
    try:
        # Build a fake subprocess result: empty stdout + TerminalQuotaError stderr
        quota_stderr = b"Error: TerminalQuotaError retryDelayMs: 3600000"
        proc = MagicMock()
        proc.returncode = 1

        async def fake_communicate():
            return b"", quota_stderr

        proc.communicate = fake_communicate

        async def fake_create_subprocess(*args, **kwargs):
            return proc

        from know_expand.config import load_config
        cfg = load_config()

        from pydantic import BaseModel as PBM

        class _Dummy(PBM):
            value: str

        with patch("asyncio.create_subprocess_exec", side_effect=fake_create_subprocess):
            with pytest.raises(RuntimeError, match="geminicli quota exhausted"):
                await base_mod._do_geminicli_call(
                    model="geminicli/gemini-3.5-flash",
                    messages=[{"role": "user", "content": "hi"}],
                    schema=_Dummy,
                    cfg=cfg,
                    role="researcher",
                )

        # The original model string must be in PROBED_UNAVAILABLE, not the cli model name
        assert "geminicli/gemini-3.5-flash" in base_mod._PROBED_UNAVAILABLE
        assert "geminicli/gemini-3.1-pro-preview" not in base_mod._PROBED_UNAVAILABLE
    finally:
        base_mod._PROBED_UNAVAILABLE.clear()
        base_mod._PROBED_UNAVAILABLE.update(original)


# ---------------------------------------------------------------------------
# _parse_geminicli_stderr — quota detection
# ---------------------------------------------------------------------------

def test_parse_geminicli_stderr_detects_terminal_quota():
    stderr = "Error: TerminalQuotaError — you have exhausted your capacity. retryDelayMs: 86400000"
    is_quota, retry_s = _parse_geminicli_stderr(stderr)
    assert is_quota is True
    assert retry_s == pytest.approx(86400.0)


def test_parse_geminicli_stderr_no_quota():
    stderr = "Warning: some MCP server issue\nConnected to 3 servers"
    is_quota, retry_s = _parse_geminicli_stderr(stderr)
    assert is_quota is False
    assert retry_s is None


def test_parse_geminicli_stderr_quota_without_retry_delay():
    stderr = "TerminalQuotaError: capacity exceeded"
    is_quota, retry_s = _parse_geminicli_stderr(stderr)
    assert is_quota is True
    assert retry_s is None


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


def test_instructor_mode_geminicli_not_json():
    """geminicli models bypass instructor entirely, but _instructor_mode should
    return TOOLS (not JSON) for them since the geminicli path never uses instructor."""
    import instructor
    # geminicli does NOT contain plain "gemini" — starts with "geminicli/"
    # The JSON mode check is only for "gemini" AND NOT geminicli
    result = _instructor_mode("geminicli/gemini-3.5-flash")
    # Should be TOOLS because the geminicli check is `not _is_geminicli(model)`
    assert result == instructor.Mode.TOOLS
