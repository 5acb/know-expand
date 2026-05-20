import asyncio
import logging
import os
import time
from typing import TypeVar

import httpx
import litellm
from pydantic import BaseModel

from doc_expand.config import Config
from doc_expand.state import emit

_logger = logging.getLogger("doc_expand.router")

# Populated once by probe_models() at pipeline startup.
# Every new QuotaAwareRouter copies this as its initial skip set.
_PROBED_UNAVAILABLE: set[str] = set()

# Exponential backoff delays (seconds) for consecutive headerless failures.
_BURST_BACKOFF = [2, 4, 8]


def _find_in_chain(exc: BaseException, *types) -> BaseException | None:
    """Walk __cause__ / __context__ chain; return first match for any of types."""
    seen = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, types):
            return current
        current = current.__cause__ or current.__context__
    return None


def _is_auth_error(exc: BaseException) -> BaseException | None:
    """Return the root auth exception if exc (or its chain) is a credentials failure."""
    import openai
    root = _find_in_chain(exc, litellm.AuthenticationError)
    if root:
        return root
    root = _find_in_chain(exc, openai.AuthenticationError, openai.PermissionDeniedError)
    if root:
        return root
    # openai raises a generic OpenAIError *before* the request when credentials
    # are missing (client init fails), so it never becomes a litellm error.
    root = _find_in_chain(exc, openai.OpenAIError)
    if root and any(kw in str(root).lower() for kw in ("credential", "api_key", "missing")):
        return root
    return None


async def _probe_one(model: str, cfg: Config) -> bool:
    """Return True if model appears usable. Checks env vars only — no LLM call."""
    if "claude" in model or "anthropic" in model:
        return bool(os.environ.get("ANTHROPIC_API_KEY"))
    if "gpt" in model or "openai" in model:
        return bool(os.environ.get("OPENAI_API_KEY"))
    if "gemini" in model:
        return bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))
    # Unknown remote provider — optimistically assume available
    return True


async def probe_models(cfg: Config) -> None:
    """
    Check every model across all roles once at startup.
    Unavailable models are added to _PROBED_UNAVAILABLE so routers skip them
    immediately without a noisy auth-fail-and-skip cascade on every call.
    """
    global _PROBED_UNAVAILABLE
    all_models: set[str] = set()
    for models in cfg.models.values():
        all_models.update(models)

    results = await asyncio.gather(*[_probe_one(m, cfg) for m in all_models])
    unavailable = {m for m, ok in zip(all_models, results) if not ok}
    available = all_models - unavailable
    _PROBED_UNAVAILABLE = unavailable

    emit({
        "event": "model_probe_done",
        "available": sorted(available),
        "unavailable": sorted(unavailable),
    })


T = TypeVar("T", bound=BaseModel)

_SEMAPHORE: asyncio.Semaphore | None = None


def _get_semaphore(model: str, cfg: Config) -> asyncio.Semaphore:
    global _SEMAPHORE
    if _SEMAPHORE is None:
        _SEMAPHORE = asyncio.Semaphore(cfg.concurrency.default)
    return _SEMAPHORE


async def _do_call(
    model: str,
    messages: list[dict],
    schema: type[T],
    cfg: Config,
    role: str = "?",
) -> T:
    import instructor
    client = instructor.from_litellm(litellm.acompletion)
    t0 = time.monotonic()
    emit({
        "event": "llm_call_start",
        "role": role,
        "model": model,
        "schema": schema.__name__,
        "msg_chars": sum(len(m.get("content", "")) for m in messages),
    })
    try:
        result, completion = await client.chat.completions.create_with_completion(
            model=model,
            messages=messages,
            response_model=schema,
        )
        elapsed = time.monotonic() - t0
        usage = getattr(completion, "usage", None)
        tok_out = getattr(usage, "completion_tokens", None)
        tok_in = getattr(usage, "prompt_tokens", None)
        tok_s = round(tok_out / elapsed, 1) if tok_out and elapsed > 0 else None
        emit({
            "event": "llm_call_done",
            "role": role,
            "model": model,
            "schema": schema.__name__,
            "elapsed_s": round(elapsed, 2),
            "tok_in": tok_in,
            "tok_out": tok_out,
            "tok_s": tok_s,
        })
        return result
    except Exception as exc:
        elapsed = time.monotonic() - t0
        emit({
            "event": "llm_call_error",
            "role": role,
            "model": model,
            "schema": schema.__name__,
            "elapsed_s": round(elapsed, 2),
            "error": str(exc)[:200],
        })
        raise


class QuotaAwareRouter:
    def __init__(self, role: str, models: list[str], cfg: Config) -> None:
        self.role = role
        self.models = models
        self.cfg = cfg
        # Seed from probe results so unavailable models are never attempted.
        self._skip: set[str] = set(_PROBED_UNAVAILABLE)
        self._lock = asyncio.Lock()
        # Per-model consecutive-failure counter for headerless 429/500/529.
        self._fail_counts: dict[str, int] = {}

    def _next_model(self) -> str | None:
        for m in self.models:
            if m not in self._skip:
                return m
        return None

    async def call(self, messages: list[dict], schema: type[T]) -> T:
        while True:
            model = self._next_model()
            if model is None:
                break
            sem = _get_semaphore(model, self.cfg)
            try:
                async with sem:
                    result = await _do_call(model, messages, schema, self.cfg, role=self.role)
                self._fail_counts.pop(model, None)
                return result
            except Exception as e:
                auth_err = _is_auth_error(e)
                rate_err = _find_in_chain(e, litellm.RateLimitError)
                server_err = _find_in_chain(e, litellm.ServiceUnavailableError)
                transient_err = rate_err or server_err
                if auth_err:
                    async with self._lock:
                        self._skip.add(model)
                        next_model = self._next_model()
                    emit({
                        "event": "model_auth_skip",
                        "role": self.role,
                        "skipped_model": model,
                        "next_model": next_model,
                        "reason": str(auth_err)[:120],
                    })
                elif transient_err:
                    retry_after = (
                        getattr(transient_err, "response", None)
                        and transient_err.response.headers.get("retry-after")
                    )
                    if retry_after:
                        self._fail_counts.pop(model, None)
                        await asyncio.sleep(int(retry_after))
                        continue
                    # Headerless failure: increment counter and backoff before skipping.
                    count = self._fail_counts.get(model, 0) + 1
                    self._fail_counts[model] = count
                    if count < 3:
                        sleep_s = _BURST_BACKOFF[min(count - 1, len(_BURST_BACKOFF) - 1)]
                        emit({
                            "event": "model_burst_retry",
                            "role": self.role,
                            "model": model,
                            "attempt": count,
                            "sleep_s": sleep_s,
                        })
                        await asyncio.sleep(sleep_s)
                        continue
                    # 3 consecutive failures — permanently skip this model.
                    self._fail_counts.pop(model, None)
                    async with self._lock:
                        self._skip.add(model)
                        next_model = self._next_model()
                    emit({
                        "event": "model_quota_switch",
                        "role": self.role,
                        "exhausted_model": model,
                        "next_model": next_model,
                        "reason": str(transient_err),
                    })
                else:
                    raise

        emit({
            "event": "quota_exhausted",
            "role": self.role,
            "all_models_tried": list(self._skip),
        })
        raise RuntimeError(f"All models exhausted for role: {self.role}")


    async def call_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
    ) -> dict:
        """Single LLM call with tool definitions. Returns the raw message dict
        (which may contain tool_calls). Handles model fallback like call()."""
        while True:
            model = self._next_model()
            if model is None:
                break
            sem = _get_semaphore(model, self.cfg)
            t0 = time.monotonic()
            emit({
                "event": "agent_tool_call_start",
                "role": self.role,
                "model": model,
            })
            try:
                async with sem:
                    response = await litellm.acompletion(
                        model=model,
                        messages=messages,
                        tools=tools,
                        tool_choice="auto",
                    )
                msg = response.choices[0].message
                elapsed = time.monotonic() - t0
                emit({
                    "event": "agent_tool_call_done",
                    "role": self.role,
                    "model": model,
                    "elapsed_s": round(elapsed, 2),
                    "tool_calls": len(msg.tool_calls) if msg.tool_calls else 0,
                })
                self._fail_counts.pop(model, None)
                # Normalise to plain dict for message history
                return {
                    "role": "assistant",
                    "content": msg.content or "",
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments,
                            },
                        }
                        for tc in (msg.tool_calls or [])
                    ] or None,
                }
            except Exception as e:
                auth_err = _is_auth_error(e)
                rate_err = _find_in_chain(e, litellm.RateLimitError)
                server_err = _find_in_chain(e, litellm.ServiceUnavailableError)
                transient_err = rate_err or server_err
                if auth_err:
                    async with self._lock:
                        self._skip.add(model)
                elif transient_err:
                    retry_after = (
                        getattr(transient_err, "response", None)
                        and transient_err.response.headers.get("retry-after")
                    )
                    if retry_after:
                        self._fail_counts.pop(model, None)
                        await asyncio.sleep(int(retry_after))
                        continue
                    # Headerless failure: increment counter and backoff before skipping.
                    count = self._fail_counts.get(model, 0) + 1
                    self._fail_counts[model] = count
                    if count < 3:
                        sleep_s = _BURST_BACKOFF[min(count - 1, len(_BURST_BACKOFF) - 1)]
                        emit({
                            "event": "model_burst_retry",
                            "role": self.role,
                            "model": model,
                            "attempt": count,
                            "sleep_s": sleep_s,
                        })
                        await asyncio.sleep(sleep_s)
                        continue
                    # 3 consecutive failures — permanently skip this model.
                    self._fail_counts.pop(model, None)
                    async with self._lock:
                        self._skip.add(model)
                    next_model = self._next_model()
                    emit({
                        "event": "model_quota_switch",
                        "role": self.role,
                        "exhausted_model": model,
                        "next_model": next_model,
                        "reason": str(transient_err),
                    })
                else:
                    raise
        raise RuntimeError(f"All models exhausted for agent role: {self.role}")


def make_router(role: str, cfg: Config) -> QuotaAwareRouter:
    return QuotaAwareRouter(role=role, models=cfg.models[role], cfg=cfg)
