"""Tests for deepinfra/ model prefix routing in QuotaAwareRouter."""
from unittest.mock import AsyncMock, patch

import pytest

from know_expand.config import load_config


@pytest.fixture
def cfg():
    return load_config()


def test_deepinfra_gets_own_semaphore(cfg):
    """deepinfra/ models get a tighter dedicated semaphore; cloud models
    share the global default semaphore."""
    from know_expand.agents.base import _get_semaphore
    sem_deepinfra = _get_semaphore("deepinfra/meta-llama/Llama-3.3-70B-Instruct-Turbo", cfg)
    sem_cloud = _get_semaphore("claude-sonnet-5", cfg)
    assert sem_deepinfra is not sem_cloud


def test_instructor_mode_deepinfra():
    from know_expand.agents.base import _instructor_mode
    import instructor
    assert _instructor_mode("deepinfra/meta-llama/Meta-Llama-3.1-8B-Instruct-Turbo") == instructor.Mode.JSON


def test_instructor_mode_gemini():
    from know_expand.agents.base import _instructor_mode
    import instructor
    assert _instructor_mode("gemini/gemini-2.5-flash") == instructor.Mode.JSON


def test_instructor_mode_claude_default_tools():
    from know_expand.agents.base import _instructor_mode
    import instructor
    assert _instructor_mode("claude-sonnet-5") == instructor.Mode.TOOLS


@pytest.mark.asyncio
async def test_deepinfra_call_uses_plain_model_string(cfg):
    """deepinfra/ models route through litellm directly (no custom transport,
    unlike the old llamacpp/geminicli providers) — the model string is passed
    through unchanged."""
    captured = {}

    async def fake_create_with_completion(**kwargs):
        captured.update(kwargs)
        raise RuntimeError("stop")

    with patch("instructor.from_litellm") as mock_instructor:
        mock_client = AsyncMock()
        mock_client.chat.completions.create_with_completion.side_effect = fake_create_with_completion
        mock_instructor.return_value = mock_client

        from know_expand.agents.base import _do_call
        from know_expand.agents.schemas import TermInventory
        try:
            await _do_call("deepinfra/meta-llama/Llama-3.3-70B-Instruct-Turbo", [], TermInventory, cfg)
        except RuntimeError:
            pass

    call_kwargs = mock_client.chat.completions.create_with_completion.call_args.kwargs
    assert call_kwargs["model"] == "deepinfra/meta-llama/Llama-3.3-70B-Instruct-Turbo"
    assert "api_base" not in call_kwargs
