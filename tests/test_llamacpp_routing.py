"""Tests for llamacpp/ model prefix routing in QuotaAwareRouter."""
import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from know_expand.config import load_config, LlamaCppConfig, Config


def _cfg_with_llamacpp(base_url: str = "http://localhost:8080") -> Config:
    cfg = load_config()
    cfg.llamacpp = LlamaCppConfig(base_url=base_url)
    return cfg


def test_llamacpp_uses_local_semaphore():
    from know_expand.agents.base import _get_semaphore
    cfg = _cfg_with_llamacpp()
    sem_local = _get_semaphore("llamacpp/llama-3.2-3b", cfg)
    sem_ollama = _get_semaphore("ollama/llama3.2:3b", cfg)
    sem_cloud = _get_semaphore("claude-sonnet-4-6", cfg)
    assert sem_local is sem_ollama       # both local
    assert sem_local is not sem_cloud    # not the cloud semaphore


@pytest.mark.asyncio
async def test_llamacpp_transforms_model_and_passes_api_base():
    cfg = _cfg_with_llamacpp("http://localhost:9999")
    captured = {}

    async def fake_create(**kwargs):
        captured.update(kwargs)
        raise RuntimeError("stop")

    with patch("instructor.from_litellm") as mock_instructor:
        mock_client = AsyncMock()
        mock_client.chat.completions.create.side_effect = fake_create
        mock_instructor.return_value = mock_client

        from know_expand.agents.base import _do_call
        from know_expand.agents.schemas import TermInventory
        try:
            await _do_call("llamacpp/llama-3.2-3b", [], TermInventory, cfg)
        except RuntimeError:
            pass

    call_kwargs = mock_client.chat.completions.create.call_args.kwargs
    assert call_kwargs["model"] == "openai/llama-3.2-3b"
    assert call_kwargs["api_base"] == "http://localhost:9999"
    assert call_kwargs["api_key"] == "not-needed"
    assert call_kwargs["max_tokens"] == cfg.llamacpp.max_tokens


@pytest.mark.asyncio
async def test_non_llamacpp_no_api_base():
    cfg = _cfg_with_llamacpp()
    captured_kwargs = {}

    async def fake_create(**kwargs):
        captured_kwargs.update(kwargs)
        raise RuntimeError("stop")

    with patch("instructor.from_litellm") as mock_instructor:
        mock_client = AsyncMock()
        mock_client.chat.completions.create.side_effect = fake_create
        mock_instructor.return_value = mock_client

        from know_expand.agents.base import _do_call
        from know_expand.agents.schemas import TermInventory
        try:
            await _do_call("claude-sonnet-4-6", [], TermInventory, cfg)
        except RuntimeError:
            pass

    call_kwargs = mock_client.chat.completions.create.call_args.kwargs
    assert call_kwargs["model"] == "claude-sonnet-4-6"
    assert "api_base" not in call_kwargs
