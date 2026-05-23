"""Tests for llamacpp/ model prefix routing in QuotaAwareRouter."""
import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from know_expand.config import load_config, LlamaCppConfig, Config


def _cfg_with_llamacpp(base_url: str = "http://localhost:8080") -> Config:
    cfg = load_config()
    cfg.llamacpp = LlamaCppConfig(base_url=base_url)
    return cfg


def test_llamacpp_uses_global_semaphore():
    """llamacpp/ and cloud models both use the global default semaphore.
    Groq/Mistral get their own tighter semaphores."""
    from know_expand.agents.base import _get_semaphore, _GROQ_SEMAPHORE, _MISTRAL_SEMAPHORE
    cfg = _cfg_with_llamacpp()
    sem_local = _get_semaphore("llamacpp/llama-3.2-3b", cfg)
    sem_cloud = _get_semaphore("claude-sonnet-4-6", cfg)
    sem_groq = _get_semaphore("groq/llama-3.3-70b-versatile", cfg)
    sem_mistral = _get_semaphore("mistral/mistral-small-latest", cfg)
    # llamacpp and cloud models share the same global semaphore
    assert sem_local is sem_cloud
    # Groq and Mistral get their own tighter semaphores
    assert sem_groq is not sem_cloud
    assert sem_mistral is not sem_cloud
    assert sem_groq is not sem_mistral


@pytest.mark.asyncio
async def test_llamacpp_transforms_model_and_passes_api_base():
    cfg = _cfg_with_llamacpp("http://localhost:9999")
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
            await _do_call("llamacpp/llama-3.2-3b", [], TermInventory, cfg)
        except RuntimeError:
            pass

    # _do_llamacpp_call uses create_with_completion and appends /v1 to base_url
    call_kwargs = mock_client.chat.completions.create_with_completion.call_args.kwargs
    assert call_kwargs["model"] == "openai/llama-3.2-3b"
    assert call_kwargs["api_base"] == "http://localhost:9999/v1"
    assert call_kwargs["api_key"] == "not-needed"


@pytest.mark.asyncio
async def test_non_llamacpp_no_api_base():
    cfg = _cfg_with_llamacpp()
    captured_kwargs = {}

    async def fake_create_with_completion(**kwargs):
        captured_kwargs.update(kwargs)
        raise RuntimeError("stop")

    with patch("instructor.from_litellm") as mock_instructor:
        mock_client = AsyncMock()
        mock_client.chat.completions.create_with_completion.side_effect = fake_create_with_completion
        mock_instructor.return_value = mock_client

        from know_expand.agents.base import _do_call
        from know_expand.agents.schemas import TermInventory
        try:
            await _do_call("claude-sonnet-4-6", [], TermInventory, cfg)
        except RuntimeError:
            pass

    call_kwargs = mock_client.chat.completions.create_with_completion.call_args.kwargs
    assert call_kwargs["model"] == "claude-sonnet-4-6"
    assert "api_base" not in call_kwargs
