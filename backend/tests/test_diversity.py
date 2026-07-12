"""Tests for decomposition diversity + LLM temperature plumbing.

Covers the quality fixes for consensus decomposition:

* persona rotation — `_persona_for` is deterministic per node id, and distinct
  nodes rotate through more than one ordinary-practitioner vantage point;
* prompt diversity — the decomposition prompt injects the persona and the
  anti-consensus / contrarian-tagging instructions (no MECE demand);
* temperature — `LLM_TEMPERATURE` reaches the api backend's `messages.create`
  kwargs when set, and is absent when unset (provider default).
"""

from __future__ import annotations

import asyncio
import sys
import types

import pytest

from app.autonomous.engine import (
    _DECOMPOSE_PERSONAS,
    _DECOMPOSE_SYSTEM,
    _decompose_prompt,
    _persona_for,
    make_node,
)
from app.autonomous.schemas import NodeKind, Project


def _project() -> Project:
    return Project(id="proj-1", domain="bookkeeping software")


# --------------------------------------------------------------------------- #
# Persona rotation                                                            #
# --------------------------------------------------------------------------- #
def test_persona_rotation_is_deterministic_and_varied():
    project = _project()
    nodes = [
        make_node(project.id, None, NodeKind.SUBAREA, f"sub-area {i}", depth=1)
        for i in range(12)
    ]
    personas = [_persona_for(n) for n in nodes]

    # Deterministic: same node -> same persona on every call.
    assert personas == [_persona_for(n) for n in nodes]
    # Varied: across a dozen distinct nodes we must see multiple vantage points.
    assert len(set(personas)) > 1
    # Every pick comes from the fixed persona list.
    assert set(personas) <= set(_DECOMPOSE_PERSONAS)


def test_decompose_prompt_carries_persona_and_diversity_instructions():
    project = _project()
    node = make_node(project.id, None, NodeKind.DOMAIN, project.domain, depth=0)
    prompt = _decompose_prompt(node, project, "sub-areas")

    # The node's rotated persona is injected into the prompt.
    assert _persona_for(node) in prompt
    # Anti-consensus clause + contrarian keyword tagging are present.
    assert "non-obvious angle" in prompt
    assert '"contrarian"' in prompt
    # The system prompt dropped the MECE demand and demands non-obvious children.
    assert "MECE" not in _DECOMPOSE_SYSTEM
    assert "non-obvious" in _DECOMPOSE_SYSTEM


def test_different_nodes_get_different_personas_in_prompt():
    project = _project()
    prompts = set()
    for i in range(12):
        node = make_node(project.id, None, NodeKind.SUBAREA, f"branch {i}", depth=1)
        prompts.add(_persona_for(node))
    assert len(prompts) > 1


# --------------------------------------------------------------------------- #
# Temperature plumbing (api backend)                                          #
# --------------------------------------------------------------------------- #
class _FakeBlock:
    type = "text"
    text = "ok"


class _FakeResp:
    stop_reason = "end_turn"
    content = [_FakeBlock()]


def _install_fake_anthropic(monkeypatch, captured: dict):
    """Inject a fake `anthropic` module whose messages.create records kwargs."""

    class _Messages:
        async def create(self, **kwargs):
            captured.update(kwargs)
            return _FakeResp()

    class _FakeAsyncAnthropic:
        def __init__(self, api_key=None):
            self.messages = _Messages()

    module = types.ModuleType("anthropic")
    module.AsyncAnthropic = _FakeAsyncAnthropic
    monkeypatch.setitem(sys.modules, "anthropic", module)


@pytest.fixture()
def api_env(monkeypatch, tmp_path):
    """Force the api backend with a hermetic settings cache."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("LLM_BACKEND", "api")
    monkeypatch.setenv("CACHE_PATH", str(tmp_path / "cache.db"))

    from app import config as config_mod
    from app.llm import client as client_mod

    def _reset() -> None:
        config_mod.get_settings.cache_clear()
        client_mod._client = None

    _reset()
    yield monkeypatch
    monkeypatch.delenv("LLM_TEMPERATURE", raising=False)
    _reset()


def test_temperature_reaches_api_kwargs_when_set(api_env):
    from app import config as config_mod
    from app.llm.client import ClaudeClient

    api_env.setenv("LLM_TEMPERATURE", "0.9")
    config_mod.get_settings.cache_clear()

    captured: dict = {}
    _install_fake_anthropic(api_env, captured)

    client = ClaudeClient()
    result = asyncio.run(client.complete("hello"))
    assert result.backend == "api"
    assert captured["temperature"] == 0.9


def test_temperature_absent_when_unset(api_env):
    from app.llm.client import ClaudeClient

    captured: dict = {}
    _install_fake_anthropic(api_env, captured)

    client = ClaudeClient()
    result = asyncio.run(client.complete("hello"))
    assert result.backend == "api"
    assert "temperature" not in captured


def test_llm_temperature_env_parsing(api_env):
    from app import config as config_mod

    api_env.setenv("LLM_TEMPERATURE", "not-a-number")
    config_mod.get_settings.cache_clear()
    assert config_mod.get_settings().llm_temperature is None  # degrades, not raises

    api_env.setenv("LLM_TEMPERATURE", "0.25")
    config_mod.get_settings.cache_clear()
    assert config_mod.get_settings().llm_temperature == 0.25
