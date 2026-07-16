"""LLM access that runs on the user's Claude *subscription* — no API key needed.

Backend resolution order (see Settings.resolve_llm_backend):
  1. 'api'       -> Anthropic API (only if ANTHROPIC_API_KEY is set)
  2. 'agent-sdk' -> claude-agent-sdk, authenticated via the local Claude Code
                    login. Supports in-process tool calling so the model can
                    fetch more Reddit/arXiv/Hacker News/GitHub/newsletter data
                    mid-synthesis.
  3. 'cli'       -> `claude -p` headless. Single-pass, no custom tools.
  4. 'fixture'   -> canned JSON, so the whole loop runs with zero deps.

Every path degrades to the next on ImportError / auth failure / timeout, with
one exception: a real run never degrades INTO 'fixture'. Fixtures are reachable
only by resolving to them explicitly (LLM_BACKEND=fixture); a live run that
exhausts its real backends raises. See _fallback_order.

The resolved backend is reported to the UI via GapReport.llm_mode.
"""

from __future__ import annotations

import asyncio
import json
import re
import subprocess
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional

from ..config import get_settings

# URLs appearing in a tool result (web_search/web_fetch) — captured so the
# synthesis grounding gate can accept web-found evidence as real.
_URL_RE = re.compile(r"https?://[^\s\"'<>)\]}]+")


@dataclass
class ToolSpec:
    """A tool the model may call during synthesis (agent-sdk / api backends)."""

    name: str
    description: str
    input_schema: dict           # JSON Schema for the tool's arguments
    handler: Callable[[dict], Awaitable[str]]  # async: args -> text result


@dataclass
class LLMResult:
    text: str
    backend: str                 # which backend actually served the call
    tool_calls: int = 0
    # URLs that appeared in tool results during the call (e.g. web_search hits).
    # A URL the model saw in a real tool result is real, not fabricated — the
    # grounding gate can accept these, which is what lets web_search evidence
    # survive. Empty unless web tools ran and returned links.
    tool_urls: list = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Rate-limit signalling. The client is the one place that actually sees a 429 /
# retry-after, but it must stay independent of the autonomous layer (no import
# cycle). So it exposes a tiny observer hook: the UsageGovernor registers its
# `note_rate_limit` here, and the client fires it whenever a call fails with a
# rate-limit-shaped error — turning the audit's "note_rate_limit is dead code"
# into a live, authoritative throttle signal (SPEC §6.1).
# --------------------------------------------------------------------------- #
_rate_limit_listeners: list[Callable[[Optional[float]], None]] = []


def register_rate_limit_listener(fn: Callable[[Optional[float]], None]) -> None:
    """Register a callback fired on every observed rate-limit signal (idempotent)."""
    if fn not in _rate_limit_listeners:
        _rate_limit_listeners.append(fn)


def _looks_rate_limited(exc: Exception) -> bool:
    """Best-effort: does this exception look like a 429 / overload across backends?"""
    if getattr(exc, "status_code", None) == 429:
        return True
    name = type(exc).__name__.lower()
    if "ratelimit" in name or "overload" in name:
        return True
    msg = str(exc).lower()
    return any(
        s in msg
        for s in ("rate limit", "rate_limit", "429", "too many requests", "overloaded")
    )


def _retry_after_of(exc: Exception) -> Optional[float]:
    """Pull a ``retry-after`` (seconds) off the exception's response, if present."""
    resp = getattr(exc, "response", None)
    headers = getattr(resp, "headers", None)
    if not headers:
        return None
    try:
        raw = headers.get("retry-after") or headers.get("Retry-After")
        return float(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def _notify_rate_limit(exc: Exception) -> None:
    """Fire the observer hook if ``exc`` is rate-limit-shaped. Never raises."""
    if not _looks_rate_limited(exc):
        return
    retry_after = _retry_after_of(exc)
    for fn in list(_rate_limit_listeners):
        try:
            fn(retry_after)
        except Exception:  # noqa: BLE001 - an observer must never break a call
            continue


class ClaudeClient:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.backend = self.settings.resolve_llm_backend()

    async def complete(
        self,
        prompt: str,
        *,
        system: Optional[str] = None,
        tools: Optional[list[ToolSpec]] = None,
        max_turns: int = 8,
        timeout: int = 240,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        web: bool = False,
    ) -> LLMResult:
        # Per-call model override; falls back to the configured default.
        model = model or self.settings.llm_model
        # Per-call temperature override; falls back to the configured default.
        # None = provider default. Only the 'api' backend can honor it: the
        # agent-sdk and cli backends expose no sampling controls (see below).
        if temperature is None:
            temperature = self.settings.llm_temperature
        order = self._fallback_order()
        last_err: Optional[Exception] = None
        for backend in order:
            try:
                if backend == "api":
                    return await self._via_api(
                        prompt, system, tools, max_turns, timeout, model, temperature
                    )
                if backend == "agent-sdk":
                    return await self._via_agent_sdk(
                        prompt, system, tools, max_turns, timeout, model, web
                    )
                if backend == "cli":
                    return await self._via_cli(prompt, system, timeout, model)
                if backend == "fixture":
                    return self._via_fixture()
            except Exception as exc:  # noqa: BLE001 - deliberately resilient
                last_err = exc
                # Surface a rate-limit signal to the governor before cascading —
                # the authoritative live throttle signal (SPEC §6.1).
                _notify_rate_limit(exc)
                continue
        raise RuntimeError(f"All LLM backends failed. Last error: {last_err}")

    def _fallback_order(self) -> list[str]:
        chain = ["api", "agent-sdk", "cli", "fixture"]
        # Start at the resolved backend, then cascade down the chain.
        start = self.backend if self.backend in chain else "agent-sdk"
        idx = chain.index(start)
        # Never attempt 'api' unless a key exists.
        order = chain[idx:]
        if not self.settings.anthropic_api_key:
            order = [b for b in order if b != "api"]
        # Never cascade INTO fixtures on a real run. _via_fixture ignores the
        # prompt and returns canned freelancer-finance gaps that parse as valid
        # output for any call — a rate-limited decompose would silently mint
        # fintech children in, e.g., an MoE-training tree. Only a deliberate
        # LLM_BACKEND=fixture run (which starts here) may serve them; a live run
        # that exhausts its real backends raises instead.
        if self.backend != "fixture":
            order = [b for b in order if b != "fixture"]
        return order

    # ------------------------------------------------------------------ #
    # agent-sdk (subscription + tools)                                   #
    # ------------------------------------------------------------------ #
    async def _via_agent_sdk(
        self,
        prompt: str,
        system: Optional[str],
        tools: Optional[list[ToolSpec]],
        max_turns: int,
        timeout: int,
        model: str,
        web: bool = False,
    ) -> LLMResult:
        # NOTE: ClaudeAgentOptions exposes no temperature/sampling parameter —
        # the agent SDK samples at the harness default. Temperature only takes
        # effect on the 'api' backend.
        from claude_agent_sdk import (  # type: ignore
            ClaudeAgentOptions,
            create_sdk_mcp_server,
            query,
            tool,
        )

        allowed: list[str] = []
        mcp_servers: dict = {}
        tool_count = {"n": 0}

        if tools:
            sdk_tools = []
            for spec in tools:
                def _make(spec: ToolSpec):
                    @tool(spec.name, spec.description, spec.input_schema)
                    async def _fn(args):  # noqa: ANN001
                        tool_count["n"] += 1
                        out = await spec.handler(args)
                        return {"content": [{"type": "text", "text": out}]}

                    return _fn

                sdk_tools.append(_make(spec))
                allowed.append(f"mcp__gapfinder__{spec.name}")
            mcp_servers["gapfinder"] = create_sdk_mcp_server(
                name="gapfinder", version="0.1.0", tools=sdk_tools
            )

        # Built-in server tools: let the model reach the OPEN web, not just our
        # curated API adapters. This is what lets the red team open a competitor's
        # actual website / pricing page and check whether a company already exists —
        # the gap our fixed sources (arXiv/GitHub/HN/jobs) structurally cannot see.
        if web:
            allowed += ["web_search", "web_fetch"]

        options = ClaudeAgentOptions(
            system_prompt=system,
            allowed_tools=allowed,
            mcp_servers=mcp_servers,
            max_turns=max_turns,
            model=model,
            permission_mode="bypassPermissions",
        )

        chunks: list[str] = []
        tool_urls: set[str] = set()

        async def _run() -> None:
            async for message in query(prompt=prompt, options=options):
                content = getattr(message, "content", None)
                if content is None:
                    continue
                if isinstance(content, str):
                    chunks.append(content)
                    continue
                for block in content:
                    text = getattr(block, "text", None)
                    if text:
                        chunks.append(text)
                    # Capture URLs the model actually received from a tool result
                    # (web_search / web_fetch). Real, not fabricated, so downstream
                    # grounding can trust them.
                    if type(block).__name__ == "ToolResultBlock":
                        tool_urls.update(_URL_RE.findall(str(getattr(block, "content", ""))))

        await asyncio.wait_for(_run(), timeout=timeout)
        text = "\n".join(chunks).strip()
        if not text:
            raise RuntimeError("agent-sdk returned empty text")
        return LLMResult(text=text, backend="agent-sdk",
                         tool_calls=tool_count["n"], tool_urls=sorted(tool_urls))

    # ------------------------------------------------------------------ #
    # cli (subscription, no custom tools)                                #
    # ------------------------------------------------------------------ #
    async def _via_cli(
        self, prompt: str, system: Optional[str], timeout: int, model: str
    ) -> LLMResult:
        # NOTE: `claude -p` has no temperature/sampling flag — the CLI backend
        # cannot honor LLM_TEMPERATURE. Temperature only takes effect on 'api'.
        cli = self.settings.claude_cli_path
        args = [cli, "-p", "--output-format", "json", "--model", model]
        if system:
            args += ["--append-system-prompt", system]

        def _run() -> str:
            proc = subprocess.run(
                args,
                input=prompt,
                text=True,
                capture_output=True,
                timeout=timeout,
            )
            if proc.returncode != 0:
                raise RuntimeError(f"claude cli failed: {proc.stderr[:400]}")
            return proc.stdout

        raw = await asyncio.to_thread(_run)
        text = raw
        try:
            payload = json.loads(raw)
            text = payload.get("result", raw) if isinstance(payload, dict) else raw
        except json.JSONDecodeError:
            pass
        if not text.strip():
            raise RuntimeError("cli returned empty text")
        return LLMResult(text=text.strip(), backend="cli")

    # ------------------------------------------------------------------ #
    # api (only when a key is present)                                   #
    # ------------------------------------------------------------------ #
    async def _via_api(
        self,
        prompt: str,
        system: Optional[str],
        tools: Optional[list[ToolSpec]],
        max_turns: int,
        timeout: int,
        model: str,
        temperature: Optional[float] = None,
    ) -> LLMResult:
        from anthropic import AsyncAnthropic  # type: ignore

        client = AsyncAnthropic(api_key=self.settings.anthropic_api_key)
        api_tools = [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": t.input_schema,
            }
            for t in (tools or [])
        ]
        by_name = {t.name: t for t in (tools or [])}
        messages: list[dict] = [{"role": "user", "content": prompt}]
        calls = 0

        # Only send temperature when explicitly set — None means provider
        # default, and recent models reject the parameter entirely.
        sampling: dict = {} if temperature is None else {"temperature": temperature}

        for _ in range(max_turns):
            resp = await asyncio.wait_for(
                client.messages.create(
                    model=model,
                    max_tokens=8000,
                    system=system or "",
                    tools=api_tools or None,
                    messages=messages,
                    **sampling,
                ),
                timeout=timeout,
            )
            if resp.stop_reason != "tool_use":
                text = "".join(
                    b.text for b in resp.content if getattr(b, "type", "") == "text"
                )
                return LLMResult(text=text.strip(), backend="api", tool_calls=calls)

            messages.append({"role": "assistant", "content": resp.content})
            results = []
            for block in resp.content:
                if getattr(block, "type", "") == "tool_use":
                    calls += 1
                    spec = by_name.get(block.name)
                    out = await spec.handler(dict(block.input)) if spec else "unknown tool"
                    results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": out,
                        }
                    )
            messages.append({"role": "user", "content": results})

        raise RuntimeError("api: exceeded max_turns without final answer")

    # ------------------------------------------------------------------ #
    # fixture (zero-dependency fallback)                                 #
    # ------------------------------------------------------------------ #
    def _via_fixture(self) -> LLMResult:
        from .fixture_synthesis import FIXTURE_GAPS_JSON

        return LLMResult(text=FIXTURE_GAPS_JSON, backend="fixture")


_client: Optional[ClaudeClient] = None


def get_client() -> ClaudeClient:
    global _client
    if _client is None:
        _client = ClaudeClient()
    return _client
