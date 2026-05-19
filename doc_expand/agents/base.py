import asyncio
import json
from typing import TypeVar

import litellm
from pydantic import BaseModel

from doc_expand.config import Config
from doc_expand.state import emit

T = TypeVar("T", bound=BaseModel)

_CLOUD_SEMAPHORE: asyncio.Semaphore | None = None
_LOCAL_SEMAPHORE: asyncio.Semaphore | None = None


def _get_semaphore(model: str, cfg: Config) -> asyncio.Semaphore:
    global _CLOUD_SEMAPHORE, _LOCAL_SEMAPHORE
    if model.startswith("ollama/"):
        if _LOCAL_SEMAPHORE is None:
            _LOCAL_SEMAPHORE = asyncio.Semaphore(cfg.concurrency.local_default)
        return _LOCAL_SEMAPHORE
    else:
        if _CLOUD_SEMAPHORE is None:
            _CLOUD_SEMAPHORE = asyncio.Semaphore(cfg.concurrency.cloud_default)
        return _CLOUD_SEMAPHORE


async def _do_call(model: str, messages: list[dict], schema: type[T]) -> T:
    import instructor
    client = instructor.from_litellm(litellm.acompletion)
    return await client.chat.completions.create(
        model=model,
        messages=messages,
        response_model=schema,
    )


class QuotaAwareRouter:
    def __init__(self, role: str, models: list[str], cfg: Config) -> None:
        self.role = role
        self.models = models
        self.cfg = cfg
        self.current_index = 0
        self.exhausted: set[str] = set()

    async def call(self, messages: list[dict], schema: type[T]) -> T:
        while self.current_index < len(self.models):
            model = self.models[self.current_index]
            sem = _get_semaphore(model, self.cfg)
            try:
                async with sem:
                    return await _do_call(model, messages, schema)
            except litellm.RateLimitError as e:
                retry_after = (
                    getattr(e, "response", None)
                    and e.response.headers.get("retry-after")
                )
                if retry_after:
                    await asyncio.sleep(int(retry_after))
                    continue
                self.exhausted.add(model)
                self.current_index += 1
                next_model = (
                    self.models[self.current_index]
                    if self.current_index < len(self.models)
                    else None
                )
                emit({
                    "event": "model_quota_switch",
                    "role": self.role,
                    "exhausted_model": model,
                    "next_model": next_model,
                    "reason": str(e),
                })

        emit({
            "event": "quota_exhausted",
            "role": self.role,
            "all_models_tried": list(self.exhausted),
        })
        raise RuntimeError(f"All models exhausted for role: {self.role}")


def make_router(role: str, cfg: Config) -> QuotaAwareRouter:
    return QuotaAwareRouter(role=role, models=cfg.models[role], cfg=cfg)
