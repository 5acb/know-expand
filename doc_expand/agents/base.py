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

from doc_expand.config import Config
from doc_expand.state import emit

_logger = logging.getLogger("doc_expand.router")

T = TypeVar("T", bound=BaseModel)

# ---------------------------------------------------------------------------
# Gemini CLI provider  (prefix "geminicli/")
# ---------------------------------------------------------------------------
# Uses `npx @google/gemini-cli` with cached OAuth — no API key required.
# Structured output is achieved by injecting the JSON schema into the prompt
# and extracting the first JSON object/array from the response.

_GEMINICLI_PREFIX = "geminicli/"
# Model passed to the CLI binary (free OAuth path).
_GEMINICLI_CLI_MODEL = "gemini-3.1-pro-preview"

_LLAMACPP_PREFIX = "llamacpp/"


def _is_geminicli(model: str) -> bool:
    return model.startswith(_GEMINICLI_PREFIX)


def _is_llamacpp(model: str) -> bool:
    return model.startswith(_LLAMACPP_PREFIX)


def _geminicli_model_name(model: str) -> str:
    """Return the model name to pass to the CLI binary."""
    return _GEMINICLI_CLI_MODEL


def _messages_to_prompt(messages: list[dict]) -> str:
    """Flatten a messages list into a single text prompt for the CLI."""
    parts: list[str] = []
    for m in messages:
        role = m.get("role", "user")
        content = m.get("content", "")
        if isinstance(content, list):
            # multi-part content blocks
            content = "\n".join(
                block.get("text", "") for block in content
                if isinstance(block, dict) and block.get("type") == "text"
            )
        if role == "system":
            parts.append(f"[System]\n{content}")
        elif role == "assistant":
            parts.append(f"[Assistant]\n{content}")
        else:
            parts.append(f"[User]\n{content}")
    return "\n\n".join(parts)


def _extract_json(text: str) -> str:
    """Pull the first JSON object or array out of CLI response text."""
    # Strip markdown code fences
    text = re.sub(r"^```(?:json)?\s*", "", text.strip(), flags=re.MULTILINE)
    text = re.sub(r"```\s*$", "", text.strip(), flags=re.MULTILINE)
    text = text.strip()
    # Find outermost { } or [ ]
    for start_char, end_char in [('{', '}'), ('[', ']')]:
        start = text.find(start_char)
        if start == -1:
            continue
        depth = 0
        in_str = False
        escape = False
        for i, ch in enumerate(text[start:], start):
            if escape:
                escape = False
                continue
            if ch == '\\' and in_str:
                escape = True
                continue
            if ch == '"' and not escape:
                in_str = not in_str
                continue
            if in_str:
                continue
            if ch == start_char:
                depth += 1
            elif ch == end_char:
                depth -= 1
                if depth == 0:
                    return text[start:i + 1]
    return text  # fall back to raw text; Pydantic will error cleanly


async def _do_geminicli_call(
    model: str,
    messages: list[dict],
    schema: type[T],
    cfg: Config,
    role: str = "?",
) -> "T":
    cli_model = _geminicli_model_name(model)
    schema_json = json.dumps(schema.model_json_schema(), indent=2)
    base_prompt = _messages_to_prompt(messages)
    full_prompt = (
        f"{base_prompt}\n\n"
        f"---\n"
        f"Respond with a single JSON object that strictly matches this JSON Schema "
        f"(no extra keys, no markdown, no explanation — raw JSON only):\n"
        f"{schema_json}"
    )

    t0 = time.monotonic()
    emit({
        "event": "llm_call_start",
        "role": role,
        "model": f"geminicli/{cli_model}",
        "schema": schema.__name__,
        "msg_chars": len(full_prompt),
    })

    try:
        proc = await asyncio.create_subprocess_exec(
            "npx", "--yes", "@google/gemini-cli",
            "-m", cli_model,
            "-p", full_prompt,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_b, stderr_b = await asyncio.wait_for(
            proc.communicate(),
            timeout=cfg.timeouts.get("geminicli_timeout_s", 120),
        )
        raw = stdout_b.decode(errors="replace").strip()
        # The CLI exits non-zero even on success when MCP warnings are present.
        # Treat empty stdout as the real failure signal; non-empty stdout = success.
        if not raw:
            err_text = stderr_b.decode(errors="replace")
            is_quota, retry_s = _parse_geminicli_stderr(err_text)
            if is_quota:
                # Terminal daily-cap exhausted — mark globally so every router
                # skips geminicli immediately without spawning more subprocesses.
                _PROBED_UNAVAILABLE.add(f"geminicli/{cli_model}")
                retry_info = f" resets in {retry_s / 3600:.1f}h" if retry_s else ""
                raise RuntimeError(f"geminicli quota exhausted{retry_info}")
            raise RuntimeError(f"gemini-cli returned no output (exit {proc.returncode}): {err_text[:400]}")

        json_text = _extract_json(raw)
        result = schema.model_validate_json(json_text)

        elapsed = time.monotonic() - t0
        emit({
            "event": "llm_call_done",
            "role": role,
            "model": f"geminicli/{cli_model}",
            "schema": schema.__name__,
            "elapsed_s": round(elapsed, 2),
            "tok_in": None,
            "tok_out": None,
            "tok_s": None,
        })
        return result

    except Exception as exc:
        elapsed = time.monotonic() - t0
        emit({
            "event": "llm_call_error",
            "role": role,
            "model": f"geminicli/{cli_model}",
            "schema": schema.__name__,
            "elapsed_s": round(elapsed, 2),
            "error": str(exc)[:200],
        })
        raise


async def _do_geminicli_tool_call(
    model: str,
    messages: list[dict],
    tools: list[dict],
    cfg: Config,
    role: str = "?",
) -> dict:
    """Tool-use via Gemini CLI: describe tools as JSON in the prompt, parse back."""
    cli_model = _geminicli_model_name(model)
    tools_json = json.dumps(tools, indent=2)
    base_prompt = _messages_to_prompt(messages)
    full_prompt = (
        f"{base_prompt}\n\n"
        f"---\n"
        f"You have access to the following tools:\n{tools_json}\n\n"
        f"If you need to call a tool, respond with a JSON object in this exact format "
        f"(raw JSON only, no markdown):\n"
        f'{{"tool_call": {{"name": "<tool_name>", "arguments": {{...}}}}}}\n\n'
        f"If no tool call is needed, respond normally as plain text."
    )

    reported_model = f"geminicli/{cli_model}"
    t0 = time.monotonic()
    emit({"event": "agent_tool_call_start", "role": role, "model": reported_model})

    try:
        proc = await asyncio.create_subprocess_exec(
            "npx", "--yes", "@google/gemini-cli",
            "-m", cli_model,
            "-p", full_prompt,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_b, stderr_b = await asyncio.wait_for(
            proc.communicate(),
            timeout=cfg.timeouts.get("geminicli_timeout_s", 120),
        )
        raw = stdout_b.decode(errors="replace").strip()
        if not raw:
            err_text = stderr_b.decode(errors="replace")
            is_quota, retry_s = _parse_geminicli_stderr(err_text)
            if is_quota:
                _PROBED_UNAVAILABLE.add(f"geminicli/{cli_model}")
                retry_info = f" resets in {retry_s / 3600:.1f}h" if retry_s else ""
                raise RuntimeError(f"geminicli quota exhausted{retry_info}")
            raise RuntimeError(f"gemini-cli returned no output (exit {proc.returncode}): {err_text[:400]}")

        elapsed = time.monotonic() - t0
        emit({
            "event": "agent_tool_call_done",
            "role": role,
            "model": reported_model,
            "elapsed_s": round(elapsed, 2),
            "tool_calls": 0,
        })

        # Try to parse a tool call from the response
        json_text = _extract_json(raw)
        try:
            parsed = json.loads(json_text)
            if "tool_call" in parsed:
                tc = parsed["tool_call"]
                return {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{
                        "id": f"cli_{int(t0)}",
                        "type": "function",
                        "function": {
                            "name": tc.get("name", ""),
                            "arguments": json.dumps(tc.get("arguments", {})),
                        },
                    }],
                }
        except Exception:
            pass

        # No tool call — plain text response
        return {"role": "assistant", "content": raw, "tool_calls": None}

    except Exception as exc:
        elapsed = time.monotonic() - t0
        emit({
            "event": "llm_call_error",
            "role": role,
            "model": reported_model,
            "schema": "tool_call",
            "elapsed_s": round(elapsed, 2),
            "error": str(exc)[:200],
        })
        raise

# ---------------------------------------------------------------------------
# llamacpp provider  (prefix "llamacpp/")
# ---------------------------------------------------------------------------
# Routes to a llama-server instance via OpenAI-compatible API.
# Base URL is read from cfg.llamacpp.base_url (default: http://127.0.0.1:8080).


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

    client = instructor.from_litellm(litellm.acompletion)
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


# Populated once by probe_models() at pipeline startup.
# Also updated at runtime when a terminal quota error is detected so that ALL
# routers (not just the one that saw the error) stop trying the model immediately.
_PROBED_UNAVAILABLE: set[str] = set()

# Exponential backoff delays (seconds) for consecutive headerless failures.
_BURST_BACKOFF = [2, 4, 8]


def _parse_geminicli_stderr(stderr_text: str) -> tuple[bool, float | None]:
    """Check stderr for a terminal quota error.

    Returns (is_quota_error, retry_after_seconds_or_None).
    TerminalQuotaError means the free-tier daily cap is hit — no amount of
    waiting within a run will fix it.  We detect it and mark geminicli
    globally unavailable so every subsequent _next_model() call skips it.
    """
    if "TerminalQuotaError" not in stderr_text and "exhausted your capacity" not in stderr_text:
        return False, None
    m = re.search(r"retryDelayMs[:\s]+([0-9.]+)", stderr_text)
    retry_s = float(m.group(1)) / 1000.0 if m else None
    return True, retry_s


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


def _geminicli_is_available() -> bool:
    """True if npx exists and the Gemini CLI OAuth credentials are cached."""
    import shutil
    if not shutil.which("npx"):
        return False
    # Gemini CLI stores OAuth creds in ~/.gemini/oauth_creds.json
    creds = os.path.expanduser("~/.gemini/oauth_creds.json")
    return os.path.exists(creds)


async def _probe_one(model: str, cfg: Config) -> bool:
    """Return True if model appears usable. Checks env vars only — no LLM call."""
    if _is_geminicli(model):
        return _geminicli_is_available()
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

    def _report_name(m: str) -> str:
        return f"geminicli/{_GEMINICLI_CLI_MODEL}" if _is_geminicli(m) else m

    emit({
        "event": "model_probe_done",
        "available": sorted(_report_name(m) for m in available),
        "unavailable": sorted(unavailable),
    })

    # Compute the first available model for each role and emit as an assignment map.
    # For geminicli/ entries, report the real CLI model name so the log is truthful.
    def _effective_name(m: str) -> str:
        if _is_geminicli(m):
            return f"geminicli/{_GEMINICLI_CLI_MODEL}"
        return m

    assignments: dict[str, str | None] = {}
    for role, models in cfg.models.items():
        chosen = next((m for m in models if m not in unavailable), None)
        assignments[role] = _effective_name(chosen) if chosen else None

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


def _get_semaphore(model: str, cfg: Config) -> asyncio.Semaphore:
    global _SEMAPHORE, _GROQ_SEMAPHORE, _MISTRAL_SEMAPHORE
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
    exactly one tool call back.  Mistral sometimes returns parallel tool calls
    for a single-schema request, which instructor rejects.  Groq (Llama) has
    the same parallel-tool-call behaviour.  Use native structured-output modes
    for both so instructor never touches the tool-calling path.
    """
    import instructor
    if "mistral" in model:
        # MISTRAL_STRUCTURED_OUTPUTS requires the mistralai SDK.
        # We route through litellm, so JSON mode (response_format json_object) works.
        return instructor.Mode.JSON
    if "groq" in model:
        return instructor.Mode.JSON
    if "gemini" in model and not _is_geminicli(model):
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


async def _do_call(
    model: str,
    messages: list[dict],
    schema: type[T],
    cfg: Config,
    role: str = "?",
) -> T:
    if _is_geminicli(model):
        return await _do_geminicli_call(model, messages, schema, cfg, role)
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
                    # geminicli errors are always terminal for this run (quota,
                    # session expired, empty output) — skip and try next model.
                    if _is_geminicli(model):
                        async with self._lock:
                            self._skip.add(model)
                            next_model = self._next_model()
                        emit({
                            "event": "model_geminicli_skip",
                            "role": self.role,
                            "skipped_model": model,
                            "next_model": next_model,
                            "reason": str(e)[:200],
                        })
                        continue
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
                if _is_geminicli(model):
                    async with sem:
                        result_msg = await _do_geminicli_tool_call(
                            model, messages, tools, self.cfg, role=self.role
                        )
                    self._fail_counts.pop(model, None)
                    return result_msg

                # llamacpp tool-calling: route to localhost OpenAI-compat endpoint
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
                    if _is_geminicli(model):
                        async with self._lock:
                            self._skip.add(model)
                            next_model = self._next_model()
                        emit({
                            "event": "model_geminicli_skip",
                            "role": self.role,
                            "skipped_model": model,
                            "next_model": next_model,
                            "reason": str(e)[:200],
                        })
                        continue
                    raise
        raise RuntimeError(f"All models exhausted for agent role: {self.role}")


def make_router(role: str, cfg: Config, thinking_budget: int = 0) -> QuotaAwareRouter:
    return QuotaAwareRouter(role=role, models=cfg.models[role], cfg=cfg, thinking_budget=thinking_budget)
