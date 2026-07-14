"""web_search / web_fetch are wired into the agent-sdk backend (opt-in).

The engine's fixed API adapters (arXiv/GitHub/HN/jobs) cannot see a company that
only has a marketing site. The built-in server tools close that gap. We can't
hit the network in a unit test, so we assert the WIRING: `web=True` must add
`web_search`/`web_fetch` to the agent's allowed tools, and `web=False` must not.
"""
import pytest

from app.llm.client import ClaudeClient


class _Recorder:
    """Captures the ClaudeAgentOptions the client would run the agent with."""

    def __init__(self):
        self.allowed = None


@pytest.fixture()
def recorder(monkeypatch):
    rec = _Recorder()
    import claude_agent_sdk as sdk

    class _FakeOptions:
        def __init__(self, **kw):
            rec.allowed = kw.get("allowed_tools")

    async def _fake_query(prompt, options):  # noqa: ARG001
        # One text message, then stop — enough for _via_agent_sdk to return.
        class _Block:
            text = "ok"

        class _Msg:
            content = [_Block()]

        yield _Msg()

    monkeypatch.setattr(sdk, "ClaudeAgentOptions", _FakeOptions)
    monkeypatch.setattr(sdk, "query", _fake_query)
    return rec


async def _run(web: bool):
    client = ClaudeClient.__new__(ClaudeClient)
    # Minimal shim: _via_agent_sdk only touches these.
    from app.config import get_settings

    client.settings = get_settings()
    return await client._via_agent_sdk(
        "hi", "sys", None, 6, 30, "claude-haiku-4-5-20251001", web
    )


async def test_web_true_allows_the_open_web_tools(recorder):
    await _run(web=True)
    assert "web_search" in recorder.allowed
    assert "web_fetch" in recorder.allowed


async def test_web_false_does_not(recorder):
    await _run(web=False)
    assert "web_search" not in (recorder.allowed or [])
    assert "web_fetch" not in (recorder.allowed or [])
