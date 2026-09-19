import asyncio
import logging
import os
import time
from typing import TypeVar

import litellm
from pydantic import BaseModel, ValidationError

from know_expand.config import Config
from know_expand.state import emit

_logger = logging.getLogger("know_expand.router")

T = TypeVar("T", bound=BaseModel)

# ---------------------------------------------------------------------------
# DeepInfra provider  (prefix "deepinfra/")
# ---------------------------------------------------------------------------
# OpenAI-compatible, pay-as-you-go host for open-weight models (Meta Llama,
# Mistral, etc). litellm supports it natively via the "deepinfra/" model
# prefix — no custom transport code needed, unlike the geminicli/llamacpp
# providers this replaced. Auth is a single DEEPINFRA_API_KEY env var, which
# litellm reads automatically for this prefix.

_DEEPINFRA_PREFIX = "deepinfra/"


def _is_deepinfra(model: str) -> bool:
    return model.startswith(_DEEPINFRA_PREFIX)


def _msg_chars(messages: list[dict]) -> int:
    """Total character count across all message content, handling list-block content."""
    total = 0
    for m in messages:
        content = m.get("content", "")
        if isinstance(content, list):
            total += sum(len(b.get("text", "")) for b in content if isinstance(b, dict))
        else:
            total += len(content)
    return total


# Populated once by probe_models() at pipeline startup.
# Also updated at runtime when a terminal quota error is detected so that ALL
# routers (not just the one that saw the error) stop trying the model immediately.
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
    if "gemini" in model or "google" in model:
        return bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))
    if _is_deepinfra(model):
        return bool(os.environ.get("DEEPINFRA_API_KEY"))
    if model.startswith("nvidia_nim/"):
        return bool(os.environ.get("NVIDIA_API_KEY"))
    # Other local providers — skip unless explicitly configured
    if model.startswith(("ollama/", "lm_studio/", "local/")):
        return False
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

    # Compute the first available model for each role and emit as an assignment map.
    assignments: dict[str, str | None] = {}
    for role, models in cfg.models.items():
        chosen = next((m for m in models if m not in unavailable), None)
        assignments[role] = chosen

    emit({
        "event": "model_role_assignments",
        "assignments": assignments,
    })


_SEMAPHORE: asyncio.Semaphore | None = None
# DeepInfra is pay-as-you-go with per-model rate limits well above what a
# single run needs, but we still cap concurrency to avoid bursting all
# map-phase tasks into one model simultaneously.
_DEEPINFRA_SEMAPHORE: asyncio.Semaphore | None = None


def _get_semaphore(model: str, cfg: Config) -> asyncio.Semaphore:
    global _SEMAPHORE, _DEEPINFRA_SEMAPHORE
    if _is_deepinfra(model):
        if _DEEPINFRA_SEMAPHORE is None:
            _DEEPINFRA_SEMAPHORE = asyncio.Semaphore(5)
        return _DEEPINFRA_SEMAPHORE
    if _SEMAPHORE is None:
        _SEMAPHORE = asyncio.Semaphore(cfg.concurrency.default)
    return _SEMAPHORE


def _instructor_mode(model: str):
    """Return the right instructor mode for a given model.

    TOOLS mode (default) sends the schema as a function definition and expects
    exactly one tool call back. The open-weight Llama/Mistral models served
    through DeepInfra sometimes return parallel tool calls for a
    single-schema request, which instructor rejects — same behaviour we used
    to see from these model families via Groq/Mistral's own APIs. Use native
    JSON mode for them so instructor never touches the tool-calling path.
    """
    import instructor
    if _is_deepinfra(model):
        return instructor.Mode.JSON
    if "gemini" in model:
        # gemini/ API models route through litellm, not the native Google SDK.
        # GEMINI_TOOLS mode requires model set at client-patch time (native SDK only);
        # JSON mode works fine through litellm's openai-compat translation layer.
        return instructor.Mode.JSON
    return instructor.Mode.TOOLS


def _make_sanitized_completion():
    """Wrap litellm.acompletion to repair invalid JSON escape sequences.

    Gemini in JSON mode emits bare LaTeX backslashes (\\frac, \\mid, etc.)
    inside JSON string values. These are invalid JSON escapes and cause
    instructor's pydantic validation to fail with 'Invalid JSON: invalid escape'.
    We repair them by doubling any backslash not already part of a valid JSON
    escape sequence before instructor parses the response.
    """
    _valid_escapes = set('"\\' + '/bfnrtu')

    def _repair(text: str) -> str:
        # Replace \X where X is not a valid JSON escape char with \\X.
        out: list[str] = []
        i = 0
        while i < len(text):
            if text[i] == '\\' and i + 1 < len(text):
                nxt = text[i + 1]
                if nxt not in _valid_escapes:
                    out.append('\\\\')
                    i += 1
                    continue
            out.append(text[i])
            i += 1
        return ''.join(out)

    async def _wrapper(*args, **kwargs):
        resp = await litellm.acompletion(*args, **kwargs)
        try:
            for choice in getattr(resp, 'choices', []):
                msg = getattr(choice, 'message', None)
                if msg is not None:
                    content = getattr(msg, 'content', None)
                    if content:
                        msg.content = _repair(content)
        except Exception:
            pass  # never block on sanitization failure
        return resp
    return _wrapper


def _api_kwargs(cfg: Config) -> dict:
    kwargs = {}
    virtual_key = getattr(cfg, "litellm_virtual_key", None)
    api_base = getattr(cfg, "litellm_api_base", None)
    if virtual_key:
        kwargs["api_key"] = virtual_key
    if api_base:
        kwargs["api_base"] = api_base
    return kwargs


async def _do_call(
    model: str,
    messages: list[dict],
    schema: type[T],
    cfg: Config,
    role: str = "?",
) -> T:
    import instructor
    client = instructor.from_litellm(_make_sanitized_completion(), mode=_instructor_mode(model))
    t0 = time.monotonic()
    emit({
        "event": "llm_call_start",
        "role": role,
        "model": model,
        "schema": schema.__name__,
        "msg_chars": _msg_chars(messages),
    })
    extra_kwargs = _api_kwargs(cfg)
    try:
        result, completion = await client.chat.completions.create_with_completion(
            model=model,
            messages=messages,
            response_model=schema,
            **extra_kwargs
        )
        elapsed = time.monotonic() - t0
        usage = getattr(completion, "usage", None)
        tok_out = getattr(usage, "completion_tokens", None)
        tok_in = getattr(usage, "prompt_tokens", None)
        tok_s = round(tok_out / elapsed, 1) if tok_out and elapsed > 0 else None
        cache_write = getattr(usage, "cache_creation_input_tokens", None)
        cache_read = getattr(usage, "cache_read_input_tokens", None)
        emit({
            "event": "llm_call_done",
            "role": role,
            "model": model,
            "schema": schema.__name__,
            "elapsed_s": round(elapsed, 2),
            "tok_in": tok_in,
            "tok_out": tok_out,
            "tok_s": tok_s,
            "cache_write_tok": cache_write,
            "cache_read_tok": cache_read,
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


async def _do_call_with_thinking(
    model: str,
    messages: list[dict],
    schema: type[T],
    cfg: Config,
    role: str = "?",
    budget: int = 0,
) -> T:
    """Like _do_call but enables extended thinking for Claude/Anthropic models.

    Extended thinking is incompatible with TOOLS mode, so we force JSON mode.
    For non-Claude models or budget=0, delegates to _do_call().
    """
    if budget <= 0 or not ("claude" in model or "anthropic" in model):
        return await _do_call(model, messages, schema, cfg, role)

    import instructor
    client = instructor.from_litellm(_make_sanitized_completion(), mode=instructor.Mode.JSON)
    t0 = time.monotonic()
    emit({
        "event": "llm_call_start",
        "role": role,
        "model": model,
        "schema": schema.__name__,
        "msg_chars": _msg_chars(messages),
        "thinking_budget": budget,
    })
    extra_kwargs = _api_kwargs(cfg)
    try:
        result, completion = await client.chat.completions.create_with_completion(
            model=model,
            messages=messages,
            response_model=schema,
            thinking={"type": "enabled", "budget_tokens": budget},
            **extra_kwargs
        )
        elapsed = time.monotonic() - t0
        usage = getattr(completion, "usage", None)
        tok_out = getattr(usage, "completion_tokens", None)
        tok_in = getattr(usage, "prompt_tokens", None)
        tok_s = round(tok_out / elapsed, 1) if tok_out and elapsed > 0 else None
        cache_write = getattr(usage, "cache_creation_input_tokens", None)
        cache_read = getattr(usage, "cache_read_input_tokens", None)
        emit({
            "event": "llm_call_done",
            "role": role,
            "model": model,
            "schema": schema.__name__,
            "elapsed_s": round(elapsed, 2),
            "tok_in": tok_in,
            "tok_out": tok_out,
            "tok_s": tok_s,
            "cache_write_tok": cache_write,
            "cache_read_tok": cache_read,
            "thinking_budget": budget,
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


def _parse_retry_after(retry_after: str) -> int:
    try:
        return int(retry_after)
    except ValueError:
        try:
            import email.utils
            import datetime
            parsed_time = email.utils.parsedate_to_datetime(retry_after)
            now = datetime.datetime.now(datetime.timezone.utc)
            delta = int((parsed_time - now).total_seconds())
            return max(1, delta)
        except Exception:
            return 30


class QuotaAwareRouter:
    def __init__(self, role: str, models: list[str], cfg: Config, thinking_budget: int = 0) -> None:
        self.role = role
        self.models = models
        self.cfg = cfg
        self.thinking_budget = thinking_budget
        # Seed from probe results so unavailable models are never attempted.
        self._skip: set[str] = set(_PROBED_UNAVAILABLE)
        self._lock = asyncio.Lock()
        # Per-model consecutive-failure counter for headerless 429/500/529.
        self._fail_counts: dict[str, int] = {}

    def _next_model(self) -> str | None:
        for m in self.models:
            if m not in self._skip and m not in _PROBED_UNAVAILABLE:
                return m
        return None

    def _permanently_skip(self, model: str) -> None:
        """Skip this model for the rest of this router's lifetime AND record
        it in the global _PROBED_UNAVAILABLE set, so concurrently-running
        routers (other domains, ensemble_verify's per-call routers) learn
        about the exhaustion immediately instead of independently
        rediscovering it via their own failure/backoff cycle."""
        self._skip.add(model)
        _PROBED_UNAVAILABLE.add(model)

    async def call(self, messages: list[dict], schema: type[T]) -> T:
        while True:
            model = self._next_model()
            if model is None:
                break
            sem = _get_semaphore(model, self.cfg)
            try:
                async with sem:
                    result = await _do_call_with_thinking(
                        model, messages, schema, self.cfg, role=self.role, budget=self.thinking_budget
                    )
                self._fail_counts.pop(model, None)
                return result
            except Exception as e:
                auth_err = _is_auth_error(e)
                rate_err = _find_in_chain(e, litellm.RateLimitError)
                server_err = _find_in_chain(e, litellm.ServiceUnavailableError)
                transient_err = rate_err or server_err
                if auth_err:
                    async with self._lock:
                        self._permanently_skip(model)
                        next_model = self._next_model()
                    emit({
                        "event": "model_auth_skip",
                        "role": self.role,
                        "skipped_model": model,
                        "next_model": next_model,
                        "reason": str(auth_err)[:120],
                    })
                elif isinstance(e, ValidationError):
                    async with self._lock:
                        self._permanently_skip(model)
                        next_model = self._next_model()
                    emit({
                        "event": "model_validation_failed",
                        "role": self.role,
                        "skipped_model": model,
                        "next_model": next_model,
                        "reason": str(e)[:200],
                    })
                    continue
                else:
                    # Recognized transient errors (rate limit/server) AND any
                    # other exception (including provider-SDK-specific errors
                    # from newer providers like DeepInfra/NVIDIA NIM) share
                    # the same retry-with-backoff-then-permanently-skip path,
                    # instead of aborting the whole role's fallback chain.
                    if transient_err:
                        retry_after = (
                            getattr(transient_err, "response", None)
                            and transient_err.response.headers.get("retry-after")
                        )
                        if retry_after:
                            self._fail_counts.pop(model, None)
                            sleep_s = min(60, _parse_retry_after(retry_after))
                            retry_count = self._fail_counts.get(model + "_rate_retry", 0) + 1
                            self._fail_counts[model + "_rate_retry"] = retry_count
                            if retry_count > 3:
                                self._fail_counts.pop(model + "_rate_retry", None)
                                async with self._lock:
                                    self._permanently_skip(model)
                                    next_model = self._next_model()
                                emit({
                                    "event": "model_quota_switch",
                                    "role": self.role,
                                    "exhausted_model": model,
                                    "next_model": next_model,
                                    "reason": "Too many rate limit retries with Retry-After",
                                })
                                continue
                            await asyncio.sleep(sleep_s)
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
                        self._permanently_skip(model)
                        next_model = self._next_model()
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
                extra_kwargs = _api_kwargs(self.cfg)
                async with sem:
                    response = await litellm.acompletion(
                        model=model,
                        messages=messages,
                        tools=tools,
                        tool_choice="auto",
                        **extra_kwargs
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
                        self._permanently_skip(model)
                else:
                    # Recognized transient errors (rate limit/server) AND any
                    # other exception (including provider-SDK-specific errors
                    # from newer providers like DeepInfra/NVIDIA NIM) share
                    # the same retry-with-backoff-then-permanently-skip path,
                    # instead of aborting the whole role's fallback chain.
                    if transient_err:
                        retry_after = (
                            getattr(transient_err, "response", None)
                            and transient_err.response.headers.get("retry-after")
                        )
                        if retry_after:
                            self._fail_counts.pop(model, None)
                            sleep_s = min(60, _parse_retry_after(retry_after))
                            retry_count = self._fail_counts.get(model + "_rate_retry", 0) + 1
                            self._fail_counts[model + "_rate_retry"] = retry_count
                            if retry_count > 3:
                                self._fail_counts.pop(model + "_rate_retry", None)
                                async with self._lock:
                                    self._permanently_skip(model)
                                    next_model = self._next_model()
                                emit({
                                    "event": "model_quota_switch",
                                    "role": self.role,
                                    "exhausted_model": model,
                                    "next_model": next_model,
                                    "reason": "Too many rate limit retries with Retry-After",
                                })
                                continue
                            await asyncio.sleep(sleep_s)
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
                        self._permanently_skip(model)
                        next_model = self._next_model()
                    emit({
                        "event": "model_quota_switch",
                        "role": self.role,
                        "exhausted_model": model,
                        "next_model": next_model,
                        "reason": str(e),
                    })
        raise RuntimeError(f"All models exhausted for agent role: {self.role}")


def make_router(role: str, cfg: Config, thinking_budget: int = 0) -> QuotaAwareRouter:
    return QuotaAwareRouter(role=role, models=cfg.models[role], cfg=cfg, thinking_budget=thinking_budget)


# ---------------------------------------------------------------------------
# ensemble_verify — debiased LLM-as-judge ensemble adjudication
# ---------------------------------------------------------------------------
# Additive primitive, composed on top of QuotaAwareRouter/make_router above.
# Does not modify QuotaAwareRouter, call(), call_with_tools(), _instructor_mode(),
# _get_semaphore(), or probe_models() — see CLAUDE.md's S8 notes for the
# motivating use case (multi-model citation-claim verification in s8_verify.py).
#
# Extra imports for this section only (kept local to minimize diff overlap
# with the rest of this file, which other work may be touching concurrently):
import random  # noqa: E402
from dataclasses import dataclass, field  # noqa: E402
from typing import Callable  # noqa: E402


@dataclass
class EnsembleVerdict:
    """Outcome of an `ensemble_verify` call.

    `result` is the schema instance callers should use downstream — either
    the agreed proposer verdict or the adjudicator's final verdict.
    `agreed` is True only when both proposers matched and no adjudicator
    call was made. `proposer_results` preserves both raw proposer outputs
    for logging/debugging even when they were overridden by an adjudicator.
    """
    result: BaseModel
    agreed: bool
    proposer_roles: list[str]
    proposer_results: list[BaseModel] = field(default_factory=list)
    adjudicator_role: str | None = None


_ADJUDICATION_PREAMBLE = (
    "Two independent reviewers analyzed the task above and reached different "
    "conclusions. Their candidate answers are shown below, presented anonymously "
    "and in no meaningful order — do not assume Candidate 1 is more authoritative "
    "or was produced first. Weigh the evidence in each candidate yourself (not "
    "which one looks more confident or verbose) and produce a single final answer, "
    "synthesizing between the candidates where each is only partially right if "
    "appropriate. Respond using the exact same schema as the original task."
)


async def ensemble_verify(
    proposer_roles: list[str],
    adjudicator_role: str,
    messages: list[dict],
    schema: type[T],
    cfg: Config,
    verdict_key: Callable[[T], object] | None = None,
    thinking_budget: int = 0,
    routers: dict[str, "QuotaAwareRouter"] | None = None,
) -> EnsembleVerdict:
    """Fan a structured-output prompt out to two proposer models; adjudicate on disagreement.

    Implements the "judge panel" pattern from Zheng et al., "Judging LLM-as-a-
    Judge with MT-Bench and Chatbot Arena" (2023), applied to a single
    verification call instead of a whole benchmark:

    1. `messages` is sent, unmodified and concurrently, to both
       `proposer_roles` (models.yaml role names, resolved the same way
       `make_router()` does — each proposer keeps its own independent
       QuotaAwareRouter, so per-model fallback still applies to each side of
       the panel). Pick two roles backed by architecturally distinct model
       families (different vendors/weights), not two fallback rungs of the
       same family — the whole point is that the two judges have different
       blind spots.
    2. If the two results agree — `verdict_key(a) == verdict_key(b)`, or a
       plain `==` on the parsed result when `verdict_key` is omitted — the
       agreed result is returned immediately. No third call is made, so the
       common (agreeing) case costs exactly two calls instead of the one a
       naive single-model check would have made — not three.
    3. On disagreement, a third `adjudicator_role` — expected to be a THIRD
       model family, distinct from both proposers — is shown both candidate
       results ANONYMIZED (no role or model name ever appears in the prompt)
       and in a randomized order (an independent coin flip per call) to
       avoid the positional bias documented in the same paper. The
       adjudicator picks or synthesizes the final verdict.

    Never raises anything beyond what the underlying `QuotaAwareRouter.call()`
    calls can raise (auth/quota exhaustion etc.) — callers that want the
    existing "log, don't fail the build" behavior should wrap this call in
    their own try/except, exactly as they would a plain `router.call()`.

    Pass `routers` (a role -> QuotaAwareRouter map) when calling this
    repeatedly in a loop (e.g. once per claim in S8) so the same router
    instances are reused across calls — each QuotaAwareRouter's per-model
    failure/backoff state (`self._skip`, `self._fail_counts`) then persists
    across the whole batch instead of being rediscovered from scratch (full
    3-attempt exponential backoff) on every single call. Roles not present
    in `routers` fall back to building a fresh one via `make_router()`.
    """
    if len(proposer_roles) != 2:
        raise ValueError(f"ensemble_verify expects exactly 2 proposer_roles, got {proposer_roles!r}")

    key: Callable[[T], object] = verdict_key or (lambda r: r)

    def _router_for(role: str) -> "QuotaAwareRouter":
        if routers is not None and role in routers:
            return routers[role]
        return make_router(role, cfg, thinking_budget=thinking_budget)

    proposer_a_role, proposer_b_role = proposer_roles
    router_a = _router_for(proposer_a_role)
    router_b = _router_for(proposer_b_role)

    # return_exceptions=True lets both proposer calls run to completion even
    # if one raises first — otherwise plain asyncio.gather() would propagate
    # the first exception immediately while leaving the other's in-flight
    # request (holding a semaphore slot, making a real billed API call)
    # orphaned in the background with its result silently discarded.
    result_a, result_b = await asyncio.gather(
        router_a.call(messages, schema),
        router_b.call(messages, schema),
        return_exceptions=True,
    )
    if isinstance(result_a, BaseException):
        raise result_a
    if isinstance(result_b, BaseException):
        raise result_b

    if key(result_a) == key(result_b):
        emit({
            "event": "ensemble_verify_agreement",
            "proposer_roles": proposer_roles,
            "schema": schema.__name__,
        })
        return EnsembleVerdict(
            result=result_a,
            agreed=True,
            proposer_roles=proposer_roles,
            proposer_results=[result_a, result_b],
        )

    # Disagreement: escalate to the adjudicator with anonymized, order-randomized candidates.
    # Independent coin flip per call — never reuse a fixed order.
    candidates = [result_a, result_b] if random.random() < 0.5 else [result_b, result_a]
    candidates_block = "\n\n".join(
        f"--- Candidate {i + 1} ---\n{c.model_dump_json(indent=2)}"
        for i, c in enumerate(candidates)
    )
    adjudication_prompt = f"{_ADJUDICATION_PREAMBLE}\n\n{candidates_block}"
    adjudicator_messages = list(messages) + [{"role": "user", "content": adjudication_prompt}]

    adjudicator_router = _router_for(adjudicator_role)
    final = await adjudicator_router.call(adjudicator_messages, schema)

    emit({
        "event": "ensemble_verify_adjudicated",
        "proposer_roles": proposer_roles,
        "adjudicator_role": adjudicator_role,
        "schema": schema.__name__,
    })
    return EnsembleVerdict(
        result=final,
        agreed=False,
        proposer_roles=proposer_roles,
        proposer_results=[result_a, result_b],
        adjudicator_role=adjudicator_role,
    )
