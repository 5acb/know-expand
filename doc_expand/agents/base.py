import asyncio
import logging
import time
from typing import TypeVar

import litellm
from pydantic import BaseModel

from doc_expand.config import Config
from doc_expand.state import emit

_logger = logging.getLogger("doc_expand.router")


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

T = TypeVar("T", bound=BaseModel)

_CLOUD_SEMAPHORE: asyncio.Semaphore | None = None
_LOCAL_SEMAPHORE: asyncio.Semaphore | None = None


def _get_semaphore(model: str, cfg: Config) -> asyncio.Semaphore:
    global _CLOUD_SEMAPHORE, _LOCAL_SEMAPHORE
    if model.startswith("ollama/") or model.startswith("llamacpp/"):
        if _LOCAL_SEMAPHORE is None:
            _LOCAL_SEMAPHORE = asyncio.Semaphore(cfg.concurrency.local_default)
        return _LOCAL_SEMAPHORE
    else:
        if _CLOUD_SEMAPHORE is None:
            _CLOUD_SEMAPHORE = asyncio.Semaphore(cfg.concurrency.cloud_default)
        return _CLOUD_SEMAPHORE


async def _do_call(
    model: str,
    messages: list[dict],
    schema: type[T],
    cfg: Config,
    role: str = "?",
) -> T:
    import instructor
    client = instructor.from_litellm(litellm.acompletion)
    extra: dict = {}
    display_model = model
    if model.startswith("llamacpp/"):
        extra["api_base"] = cfg.llamacpp.base_url
        extra["api_key"] = "not-needed"
        extra["max_tokens"] = cfg.llamacpp.max_tokens
        model = "openai/" + model[len("llamacpp/"):]
    t0 = time.monotonic()
    emit({
        "event": "llm_call_start",
        "role": role,
        "model": display_model,
        "schema": schema.__name__,
        "msg_chars": sum(len(m.get("content", "")) for m in messages),
    })
    try:
        result = await client.chat.completions.create(
            model=model,
            messages=messages,
            response_model=schema,
            **extra,
        )
        elapsed = time.monotonic() - t0
        emit({
            "event": "llm_call_done",
            "role": role,
            "model": display_model,
            "schema": schema.__name__,
            "elapsed_s": round(elapsed, 2),
        })
        return result
    except Exception as exc:
        elapsed = time.monotonic() - t0
        emit({
            "event": "llm_call_error",
            "role": role,
            "model": display_model,
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
        # _skip is shared across concurrent callers; _lock guards mutations only.
        # Using a skip-set (not an advancing index) means concurrent tasks don't
        # race past valid models: they each independently discover the same model
        # is bad and add it to the set, then all land on the same next candidate.
        self._skip: set[str] = set()
        self._lock = asyncio.Lock()

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
                    return await _do_call(model, messages, schema, self.cfg, role=self.role)
            except Exception as e:
                auth_err = _is_auth_error(e)
                rate_err = _find_in_chain(e, litellm.RateLimitError)
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
                elif rate_err:
                    retry_after = (
                        getattr(rate_err, "response", None)
                        and rate_err.response.headers.get("retry-after")
                    )
                    if retry_after:
                        await asyncio.sleep(int(retry_after))
                        continue
                    async with self._lock:
                        self._skip.add(model)
                        next_model = self._next_model()
                    emit({
                        "event": "model_quota_switch",
                        "role": self.role,
                        "exhausted_model": model,
                        "next_model": next_model,
                        "reason": str(rate_err),
                    })
                else:
                    raise

        emit({
            "event": "quota_exhausted",
            "role": self.role,
            "all_models_tried": list(self._skip),
        })
        raise RuntimeError(f"All models exhausted for role: {self.role}")


def make_router(role: str, cfg: Config) -> QuotaAwareRouter:
    return QuotaAwareRouter(role=role, models=cfg.models[role], cfg=cfg)
