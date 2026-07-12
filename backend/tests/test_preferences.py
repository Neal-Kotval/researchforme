"""Phase 4 H3 preference distillation — contract tests.

Contract (docs/strategy/phase234-build.md H3):

* ``POST /api/preferences/distill`` — cheap-model pass over accumulated triage
  verdicts → a PENDING proposal in the single ``ap_preferences`` row. Zero
  triage verdicts → 400; a backend that can't produce a real distillation (the
  fixture backend included) → honest 503, NEVER an invented preference.
* ``GET /api/preferences`` / ``POST /api/preferences`` — review / edit /
  confirm (``active``) / dismiss (``dismissed``).
* ONLY ``status == "active"`` text is appended to ``steering_context_block``
  output, under a "LEARNED PREFERENCES (user-confirmed)" heading. Pending or
  dismissed text is never injected.

Everything runs against private temp stores — hermetic, no network.
"""

from __future__ import annotations

import json

import pytest

MARKER = "Avoid consumer-social spaces; lean into B2B fintech wedges."
HEADING = "LEARNED PREFERENCES (user-confirmed)"


@pytest.fixture()
def fixture_env(tmp_path, monkeypatch):
    """Fixture LLM backend + hermetic temp cache/store; reset memoized singletons."""
    monkeypatch.setenv("LLM_BACKEND", "fixture")
    monkeypatch.setenv("CACHE_PATH", str(tmp_path / "test_cache.db"))
    for var in (
        "REDDIT_CLIENT_ID",
        "REDDIT_CLIENT_SECRET",
        "GITHUB_TOKEN",
        "ANTHROPIC_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)

    from app import cache as cache_mod
    from app import config as config_mod
    from app.llm import client as client_mod

    def _reset() -> None:
        config_mod.get_settings.cache_clear()
        cache_mod._cache = None
        client_mod._client = None

    _reset()

    import app.autonomous.store as store_mod
    from app.autonomous.store import TreeStore

    store = TreeStore(path=str(tmp_path / "prefs.db"))
    monkeypatch.setattr(store_mod, "_store", store, raising=False)

    yield store
    _reset()


def _triaged_node(store, pid: str, title: str, triage: str, reason: str = ""):
    from app.autonomous.engine import make_node
    from app.autonomous.schemas import NodeKind, NodeState

    node = make_node(pid, None, NodeKind.GAP, title, keywords=["k"])
    node.state = NodeState.SCORED
    node.triage = triage
    node.triage_reason = reason
    store.upsert_node(node)
    return node


def _save_prefs(store, status: str, text: str = MARKER):
    from app.autonomous.schemas import Preferences

    store.save_preferences(Preferences(learned_preferences=text, status=status))


def _steered_project():
    from app.autonomous.schemas import Project, SteeringContext

    return Project(
        id="p1",
        domain="bookkeeping tools",
        steering=SteeringContext(brief="solo dev with tax-domain depth"),
    )


# --------------------------------------------------------------------------- #
# Store level                                                                  #
# --------------------------------------------------------------------------- #
def test_preferences_roundtrip_and_triaged_nodes(fixture_env):
    store = fixture_env
    assert store.get_preferences() is None

    from app.autonomous.schemas import Project

    store.create_project(Project(id="p1", domain="bookkeeping tools"))
    kept = _triaged_node(store, "p1", "receipt OCR", "passed", "too_crowded")
    _triaged_node(store, "p1", "tax autopilot", "interested")
    untriaged = _triaged_node(store, "p1", "no verdict", "passed")
    untriaged.triage = None
    store.upsert_node(untriaged)

    triaged = store.triaged_nodes()
    assert {n.title for n, _d in triaged} == {"receipt OCR", "tax autopilot"}
    assert dict((n.id, d) for n, d in triaged)[kept.id] == "bookkeeping tools"

    _save_prefs(store, "pending")
    got = store.get_preferences()
    assert got is not None
    assert got.status == "pending" and got.learned_preferences == MARKER


# --------------------------------------------------------------------------- #
# Injection: ONLY active text rides in steering_context_block                  #
# --------------------------------------------------------------------------- #
def test_pending_preferences_never_injected(fixture_env):
    from app.autonomous.intake import steering_context_block
    from app.autonomous.schemas import Project

    _save_prefs(fixture_env, "pending")
    # With founder steering present, the block exists but carries no prefs.
    block = steering_context_block(_steered_project())
    assert "solo dev with tax-domain depth" in block
    assert MARKER not in block and HEADING not in block
    # With no steering at all, pending prefs must not conjure a block.
    assert steering_context_block(Project(id="p2", domain="d")) == ""


def test_dismissed_preferences_never_injected(fixture_env):
    from app.autonomous.intake import steering_context_block

    _save_prefs(fixture_env, "dismissed")
    block = steering_context_block(_steered_project())
    assert MARKER not in block and HEADING not in block


def test_active_preferences_injected_under_heading(fixture_env):
    from app.autonomous.intake import steering_context_block
    from app.autonomous.schemas import Project

    _save_prefs(fixture_env, "active")
    block = steering_context_block(_steered_project())
    assert HEADING in block and MARKER in block
    assert "solo dev with tax-domain depth" in block
    # Active prefs steer even a project with no steering of its own.
    bare = steering_context_block(Project(id="p2", domain="d"))
    assert HEADING in bare and MARKER in bare


# --------------------------------------------------------------------------- #
# Distillation                                                                 #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_distill_prompt_grounded_in_verdicts_and_stores_nothing_itself(
    fixture_env,
):
    from app.autonomous.preferences import distill_preferences

    store = fixture_env
    from app.autonomous.schemas import Project

    store.create_project(Project(id="p1", domain="bookkeeping tools"))
    _triaged_node(store, "p1", "receipt OCR", "passed", "too_crowded")

    prompts: list[str] = []

    class _FakeResult:
        text = json.dumps({"learned_preferences": MARKER})
        backend = "test"

    class _FakeClient:
        async def complete(self, prompt, **kwargs):
            prompts.append(prompt)
            return _FakeResult()

    text = await distill_preferences(store.triaged_nodes(), _FakeClient())
    assert text == MARKER
    # The verdict corpus (titles + reasons + domain) rode in the prompt.
    assert "receipt OCR" in prompts[0]
    assert "too_crowded" in prompts[0]
    assert "bookkeeping tools" in prompts[0]


def test_distill_endpoint_zero_triage_events_is_400(fixture_env):
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        r = client.post("/api/preferences/distill")
        assert r.status_code == 400
        assert fixture_env.get_preferences() is None


def test_distill_endpoint_fixture_backend_is_honest_503(fixture_env):
    """The fixture backend's canned gaps array is not a distillation → 503,
    and nothing is stored — NEVER an invented preference."""
    from app.autonomous.schemas import Project
    from fastapi.testclient import TestClient

    from app.main import app

    store = fixture_env
    store.create_project(Project(id="p1", domain="bookkeeping tools"))
    _triaged_node(store, "p1", "receipt OCR", "passed", "too_crowded")

    with TestClient(app) as client:
        r = client.post("/api/preferences/distill")
        assert r.status_code == 503
        assert store.get_preferences() is None


def test_distill_review_confirm_dismiss_flow(fixture_env, monkeypatch):
    """distill → pending (not injected) → edit+confirm → active (injected) →
    dismiss → not injected."""
    from app.autonomous.intake import steering_context_block
    from app.autonomous.schemas import Project
    from app.llm import client as client_mod
    from fastapi.testclient import TestClient

    from app.main import app

    store = fixture_env
    store.create_project(Project(id="p1", domain="bookkeeping tools"))
    _triaged_node(store, "p1", "receipt OCR", "passed", "too_crowded")
    _triaged_node(store, "p1", "tax autopilot", "interested")

    class _FakeResult:
        text = json.dumps({"learned_preferences": MARKER})
        backend = "test"

    class _FakeClient:
        async def complete(self, prompt, **kwargs):
            return _FakeResult()

    monkeypatch.setattr(client_mod, "_client", _FakeClient(), raising=False)

    with TestClient(app) as client:
        # Distill → PENDING proposal stored, not injected.
        r = client.post("/api/preferences/distill")
        assert r.status_code == 200
        assert r.json()["status"] == "pending"
        assert r.json()["learned_preferences"] == MARKER
        assert HEADING not in steering_context_block(_steered_project())

        # GET reflects the pending row + the triage count for the UI card.
        r = client.get("/api/preferences")
        assert r.status_code == 200
        assert r.json()["preferences"]["status"] == "pending"
        assert r.json()["triage_count"] == 2

        # Edit + confirm → ACTIVE, injected under the contract heading.
        edited = MARKER + " Also avoid hardware."
        r = client.post(
            "/api/preferences",
            json={"learned_preferences": edited, "status": "active"},
        )
        assert r.status_code == 200 and r.json()["status"] == "active"
        block = steering_context_block(_steered_project())
        assert HEADING in block and edited in block

        # Dismiss → nothing injected anymore.
        r = client.post(
            "/api/preferences",
            json={"learned_preferences": edited, "status": "dismissed"},
        )
        assert r.status_code == 200 and r.json()["status"] == "dismissed"
        assert HEADING not in steering_context_block(_steered_project())

        # Activating empty text is a 400 — there is nothing to inject.
        r = client.post(
            "/api/preferences", json={"learned_preferences": "  ", "status": "active"}
        )
        assert r.status_code == 400
