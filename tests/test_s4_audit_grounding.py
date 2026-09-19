"""Tests for the S4 adversarial gap loop's ReAct tool-grounding.

Covers:
  - `s4_audit._run_react_grounding` — the bespoke propose->tool->observe->answer
    loop built on `QuotaAwareRouter.call_with_tools()`.
  - `s4_tools.run_coverage_check` / `run_synonym_check` — the thin tool
    dispatch functions the Finder/Defender call.
"""
import json
from unittest.mock import AsyncMock, patch

import pytest

from know_expand.config import (
    BibliographyConfig,
    CentralityConfig,
    Config,
    ConcurrencyConfig,
)
from know_expand.stages import s4_audit, s4_tools


def _cfg() -> Config:
    return Config(
        bibliography=BibliographyConfig(),
        centrality=CentralityConfig(),
        rate_limits={},
        concurrency=ConcurrencyConfig(),
        timeouts={"s4_gap_grounding_seconds": 5},
        adversarial_rounds={},
        boilerplate_stop_list=[],
        models={},
    )


def _tool_call_msg(name: str, args: dict) -> dict:
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {"id": "call_1", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}
        ],
    }


def _finish_msg(text: str) -> dict:
    return {"role": "assistant", "content": text, "tool_calls": None}


# ---------------------------------------------------------------------------
# _run_react_grounding
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_grounding_happy_path_invokes_tool_then_finishes():
    router = AsyncMock()
    router.call_with_tools.side_effect = [
        _tool_call_msg("my_tool", {"query": "foo"}),
        _finish_msg("investigated foo, still a gap"),
    ]
    tool_fn = AsyncMock(return_value="tool observation text")

    text, tool_calls = await s4_audit._run_react_grounding(
        router,
        [{"role": "user", "content": "find gaps"}],
        {"type": "function", "function": {"name": "my_tool"}},
        "my_tool",
        tool_fn,
        "domain-1", "finder", 1, _cfg(),
    )

    assert tool_calls == 1
    assert text == "investigated foo, still a gap"
    tool_fn.assert_awaited_once_with(query="foo")


@pytest.mark.asyncio
async def test_grounding_no_tool_call_returns_immediately():
    router = AsyncMock()
    router.call_with_tools.side_effect = [_finish_msg("no tools needed")]
    tool_fn = AsyncMock()

    text, tool_calls = await s4_audit._run_react_grounding(
        router, [{"role": "user", "content": "x"}],
        {"type": "function", "function": {"name": "my_tool"}}, "my_tool", tool_fn,
        "domain-1", "finder", 1, _cfg(),
    )

    assert tool_calls == 0
    assert text == "no tools needed"
    tool_fn.assert_not_awaited()


@pytest.mark.asyncio
async def test_grounding_router_exhaustion_is_swallowed():
    """When the agent role has no models left, grounding fails soft — no exception
    escapes, callers fall back to the ungrounded prompt."""
    router = AsyncMock()
    router.call_with_tools.side_effect = RuntimeError("All models exhausted for agent role: agent")
    tool_fn = AsyncMock()

    text, tool_calls = await s4_audit._run_react_grounding(
        router, [{"role": "user", "content": "x"}],
        {"type": "function", "function": {"name": "my_tool"}}, "my_tool", tool_fn,
        "domain-1", "finder", 1, _cfg(),
    )

    assert text == ""
    assert tool_calls == 0


@pytest.mark.asyncio
async def test_grounding_tool_error_is_fed_back_not_raised():
    router = AsyncMock()
    router.call_with_tools.side_effect = [
        _tool_call_msg("my_tool", {"query": "foo"}),
        _finish_msg("handled the error and answered anyway"),
    ]
    tool_fn = AsyncMock(side_effect=RuntimeError("network blip"))

    text, tool_calls = await s4_audit._run_react_grounding(
        router, [{"role": "user", "content": "x"}],
        {"type": "function", "function": {"name": "my_tool"}}, "my_tool", tool_fn,
        "domain-1", "finder", 1, _cfg(),
    )

    assert tool_calls == 1
    assert text == "handled the error and answered anyway"
    # The tool error was fed back as an observation, not raised.
    second_call_messages = router.call_with_tools.call_args_list[1].args[0]
    tool_messages = [m for m in second_call_messages if m.get("role") == "tool"]
    assert any("Tool error" in m["content"] for m in tool_messages)


@pytest.mark.asyncio
async def test_grounding_gives_up_after_max_turns():
    router = AsyncMock()
    # Always requests a tool call — never finishes on its own.
    router.call_with_tools.side_effect = lambda *a, **k: _tool_call_msg("my_tool", {"query": "foo"})
    tool_fn = AsyncMock(return_value="obs")

    text, tool_calls = await s4_audit._run_react_grounding(
        router, [{"role": "user", "content": "x"}],
        {"type": "function", "function": {"name": "my_tool"}}, "my_tool", tool_fn,
        "domain-1", "finder", 1, _cfg(), max_turns=3,
    )

    assert tool_calls == 3
    assert router.call_with_tools.call_count == 3
    # Regression: on max-turns exhaustion, working_messages[-1] is always the
    # last tool-observation ("obs"), never model-authored text. final_text
    # must NOT leak that raw observation into the caller's verdict prompt.
    assert text == ""


# ---------------------------------------------------------------------------
# s4_tools dispatch functions
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_coverage_check_prefers_semantic_scholar():
    with patch(
        "know_expand.stages.s4_tools._ss_search",
        return_value=[{"title": "A Paper", "year": 2024, "citationCount": 12}],
    ):
        result = await s4_tools.run_coverage_check("some gap", http=None, cfg=None)
    assert "A Paper" in result
    assert "Semantic Scholar" in result


@pytest.mark.asyncio
async def test_run_coverage_check_falls_back_to_arxiv():
    class _FakeSource:
        title = "arXiv Paper"
        content = "an abstract"

    with (
        patch("know_expand.stages.s4_tools._ss_search", return_value=[]),
        patch("know_expand.stages.s4_tools._fetch_arxiv", return_value=[_FakeSource()]),
    ):
        result = await s4_tools.run_coverage_check("some gap", http=None, cfg=None)
    assert "arXiv Paper" in result


@pytest.mark.asyncio
async def test_run_coverage_check_reports_no_coverage():
    with (
        patch("know_expand.stages.s4_tools._ss_search", return_value=[]),
        patch("know_expand.stages.s4_tools._fetch_arxiv", return_value=[]),
    ):
        result = await s4_tools.run_coverage_check("obscure gap", http=None, cfg=None)
    assert "No Semantic Scholar or arXiv coverage found" in result


@pytest.mark.asyncio
async def test_run_synonym_check_finds_similar_graph_term():
    with patch("know_expand.stages.s4_tools._fetch_wikipedia", return_value=None):
        result = await s4_tools.run_synonym_check(
            "gradient descent", ["Gradient Descent Optimization", "Unrelated Term"], http=None,
        )
    assert "Gradient Descent Optimization" in result


@pytest.mark.asyncio
async def test_run_synonym_check_no_similar_term_and_no_wikipedia():
    with patch("know_expand.stages.s4_tools._fetch_wikipedia", return_value=None):
        result = await s4_tools.run_synonym_check(
            "zzz totally unrelated concept", ["Alpha", "Beta"], http=None,
        )
    assert "No lexically similar graph terms found" in result
    assert "No Wikipedia match found" in result
