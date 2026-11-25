import asyncio
import json
import logging
import os
import re
import time
from typing import TypeVar

import httpx
import litellm
from pydantic import BaseModel

from know_expand.config import Config
from know_expand.state import emit

_logger = logging.getLogger("know_expand.router")

T = TypeVar("T", bound=BaseModel)

_LLAMACPP_PREFIX = "llamacpp/"


def _is_llamacpp(model: str) -> bool:
    return model.startswith(_LLAMACPP_PREFIX)

# ---------------------------------------------------------------------------
# llamacpp provider  (prefix "llamacpp/")
# ---------------------------------------------------------------------------
# Routes to a llama-server instance via OpenAI-compatible API.
# Base URL is read from cfg.llamacpp.base_url (default: http://192.168.1.117:8080).


async def _llamacpp_is_available(cfg) -> bool:
    """True if the llama-server /health endpoint is reachable."""
    base_url: str = cfg.llamacpp.base_url
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            r = await client.get(f"{base_url}/health")
            return r.status_code < 500
    except Exception:
        return False


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


async def _do_llamacpp_call(
    model: str,
    messages: list[dict],
    schema: type[T],
    cfg,
    role: str = "?",
) -> T:
    """Structured call to a llama-server via OpenAI-compat API (instructor + litellm)."""
    import instructor

    model_name = model[len(_LLAMACPP_PREFIX):]
    base_url: str = cfg.llamacpp.base_url
    api_base = base_url.rstrip("/") + "/v1"

    # Suppress thinking for bulk structured-output roles — saves 3-5k tokens/call.
    # /no_think is the Qwen3/GLM convention; also remind the model to output valid JSON only.
    _NO_THINK_ROLES = {"extractor", "classifier"}
    if role in _NO_THINK_ROLES and not any(m.get("role") == "system" for m in messages):
        messages = [{"role": "system", "content": (
            "/no_think\n"
            f"Respond ONLY with a valid JSON object matching the {schema.__name__} schema. "
            "No explanation, no markdown fences, no extra keys."
        )}] + messages

    client = instructor.from_litellm(litellm.acompletion, mode=instructor.Mode.JSON)
    t0 = time.monotonic()
    emit({
        "event": "llm_call_start",
        "role": role,
        "model": model,
        "schema": schema.__name__,
        "msg_chars": _msg_chars(messages),
    })
    try:
        result, completion = await client.chat.completions.create_with_completion(
            model=f"openai/{model_name}",
            api_base=api_base,
            api_key="not-needed",
            messages=messages,
            response_model=schema,
            max_tokens=cfg.llamacpp.max_output_tokens,
            timeout=cfg.llamacpp.timeout_s,
        )
        elapsed = time.monotonic() - t0
        usage = getattr(completion, "usage", None)
        tok_out = getattr(usage, "completion_tokens", None)
        tok_in = getattr(usage, "prompt_tokens", None)
        tok_s = round(tok_out / elapsed, 1) if tok_out and elapsed > 0 else None
        # Extract <think>...</think> reasoning block if present
        raw_content = ""
        try:
            raw_content = completion.choices[0].message.content or ""
        except Exception:
            pass
        import re as _re
        thinking_match = _re.search(r"<think>(.*?)</think>", raw_content, _re.DOTALL)
        thinking_text = thinking_match.group(1).strip() if thinking_match else None
        emit({
            "event": "llm_call_done",
            "role": role,
            "model": model,
            "schema": schema.__name__,
            "elapsed_s": round(elapsed, 2),
            "tok_in": tok_in,
            "tok_out": tok_out,
            "tok_s": tok_s,
            "thinking": thinking_text,
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
    if "groq" in model:
        return bool(os.environ.get("GROQ_API_KEY"))
    if "mistral" in model:
        return bool(os.environ.get("MISTRAL_API_KEY"))
    # llamacpp: probe the actual server health endpoint
    if model.startswith("llamacpp/"):
        return await _llamacpp_is_available(cfg)
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

    assignments: dict[str, str | None] = {}
    for role, models in cfg.models.items():
        chosen = next((m for m in models if m not in unavailable), None)
        assignments[role] = chosen

    emit({
        "event": "model_role_assignments",
        "assignments": assignments,
    })


_SEMAPHORE: asyncio.Semaphore | None = None
# Separate, tighter semaphores for rate-limited free-tier providers.
# Groq free tier: ~30 req/min on 8B, ~30 req/min on 70B — cap at 3 concurrent
# to avoid bursting all 20 map-phase tasks into them simultaneously.
_GROQ_SEMAPHORE: asyncio.Semaphore | None = None
_MISTRAL_SEMAPHORE: asyncio.Semaphore | None = None
# llamacpp runs on a single local server — serialize by default (concurrency=1)
# so parallel map-phase tasks queue rather than thrash a single GPU.
_LLAMACPP_SEMAPHORE: asyncio.Semaphore | None = None


def _get_semaphore(model: str, cfg: Config) -> asyncio.Semaphore:
    global _SEMAPHORE, _GROQ_SEMAPHORE, _MISTRAL_SEMAPHORE, _LLAMACPP_SEMAPHORE
    if model.startswith(_LLAMACPP_PREFIX):
        if _LLAMACPP_SEMAPHORE is None:
            _LLAMACPP_SEMAPHORE = asyncio.Semaphore(cfg.llamacpp.concurrency)
        return _LLAMACPP_SEMAPHORE
    if "groq" in model:
        if _GROQ_SEMAPHORE is None:
            _GROQ_SEMAPHORE = asyncio.Semaphore(3)
        return _GROQ_SEMAPHORE
    if "mistral" in model:
        if _MISTRAL_SEMAPHORE is None:
            _MISTRAL_SEMAPHORE = asyncio.Semaphore(3)
        return _MISTRAL_SEMAPHORE
    if _SEMAPHORE is None:
        _SEMAPHORE = asyncio.Semaphore(cfg.concurrency.default)
    return _SEMAPHORE


def _instructor_mode(model: str):
    """Return the right instructor mode for a given model.

    TOOLS mode (default) sends the schema as a function definition and expects
    exactly one tool call back.  Mistral, Groq (Llama), and llamacpp-served models
    (e.g. GLM) return parallel tool calls for single-schema requests, which
    instructor rejects.  Use JSON mode for all of these.
    """
    import instructor
    if "mistral" in model:
        # MISTRAL_STRUCTURED_OUTPUTS requires the mistralai SDK.
        # We route through litellm, so JSON mode (response_format json_object) works.
        return instructor.Mode.JSON
    if "groq" in model:
        return instructor.Mode.JSON
    if "gemini" in model:
        # gemini/ API models route through litellm, not the native Google SDK.
        # GEMINI_TOOLS mode requires model set at client-patch time (native SDK only);
        # JSON mode works fine through litellm's openai-compat translation layer.
        return instructor.Mode.JSON
    if model.startswith("llamacpp/"):
        # GLM (and likely other local models) return parallel tool calls in TOOLS mode,
        # which instructor rejects. JSON mode works correctly via the openai-compat layer.
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


async def _do_call(
    model: str,
    messages: list[dict],
    schema: type[T],
    cfg: Config,
    role: str = "?",
) -> T:
    if _is_llamacpp(model):
        return await _do_llamacpp_call(model, messages, schema, cfg, role)

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
    try:
        result, completion = await client.chat.completions.create_with_completion(
            model=model,
            messages=messages,
            response_model=schema,
            thinking={"type": "enabled", "budget_tokens": budget},
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
                # llamacpp tool-calling: route to foiba OpenAI-compat endpoint
                if _is_llamacpp(model):
                    model_name = model[len(_LLAMACPP_PREFIX):]
                    base_url: str = self.cfg.llamacpp.base_url
                    api_base = base_url.rstrip("/") + "/v1"
                    async with sem:
                        response = await litellm.acompletion(
                            model=f"openai/{model_name}",
                            api_base=api_base,
                            api_key="not-needed",
                            messages=messages,
                            tools=tools,
                            tool_choice="auto",
                            timeout=self.cfg.llamacpp.timeout_s,
                        )
                else:
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


def make_router(role: str, cfg: Config, thinking_budget: int = 0) -> QuotaAwareRouter:
    models = cfg.models[role]
    if cfg.free_mode:
        models = [m for m in models if m.startswith(_LLAMACPP_PREFIX)]
        if not models:
            raise RuntimeError(
                f"--free mode: no llamacpp/ model configured for role '{role}'. "
                f"Add a 'llamacpp/<name>' entry to models.yaml for this role."
            )
    return QuotaAwareRouter(role=role, models=models, cfg=cfg, thinking_budget=thinking_budget)
