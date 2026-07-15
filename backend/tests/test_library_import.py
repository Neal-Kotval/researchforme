"""Importing starred ideas into a library project (Phase 5 W-3) — contract tests.

The contract that matters:

* An import writes ``ideas/<slug>.md`` into a real project folder.
* ``develop: true`` runs ONE strong-model pass — but it CANNOT FAIL. When no
  backend can produce a real development (the fixture backend included), the raw
  deterministic export is written instead, with ``developed: false`` in the
  frontmatter and the reason surfaced. Never canned prose, never a lost idea.
* A project holds MANY ideas.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

RAW = "## Thesis\nThe raw client-side export of the gap, with its red team intact.\n"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_BACKEND", "fixture")   # cannot produce a real development
    monkeypatch.setenv("CACHE_PATH", str(tmp_path / "c.db"))
    monkeypatch.setenv("RESEARCHFORME_DIR", str(tmp_path / "researchforme"))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

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

    store = TreeStore(path=str(tmp_path / "t.db"))
    monkeypatch.setattr(store_mod, "_store", store, raising=False)

    from app.main import app

    yield TestClient(app), store, tmp_path
    _reset()


def _idea(node_id="n1", title="Kernel Equivalence Harness"):
    return {
        "project_id": "p1",
        "node_id": node_id,
        "title": title,
        "markdown": RAW,
    }


def test_import_creates_a_project_folder_with_the_idea(env):
    client, _store, tmp = env
    r = client.post(
        "/api/library/import",
        json={"ideas": [_idea()], "new_project_title": "Kernel CI", "develop": False},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["project"]["slug"] == "kernel-ci"
    assert body["imported"][0]["path"] == "ideas/kernel-equivalence-harness.md"

    md = (tmp / "researchforme" / "kernel-ci" / "ideas"
          / "kernel-equivalence-harness.md").read_text()
    assert "red team intact" in md          # the export survived verbatim
    assert "developed: false" in md


def test_develop_true_falls_back_honestly_when_the_llm_cannot_develop(env):
    """The fixture backend can't develop — so we export the RAW idea and say so.

    This is the load-bearing guarantee: an import never fails because the LLM was
    unavailable, and it never invents a 'developed' document that isn't one.
    """
    client, _store, tmp = env
    r = client.post(
        "/api/library/import",
        json={"ideas": [_idea()], "new_project_title": "Kernel CI", "develop": True},
    )
    assert r.status_code == 200, r.text
    imported = r.json()["imported"][0]

    assert imported["developed"] is False          # never claimed falsely
    assert imported["note"]                        # the reason is surfaced, not swallowed

    md = (tmp / "researchforme" / "kernel-ci" / "ideas"
          / "kernel-equivalence-harness.md").read_text()
    assert "red team intact" in md                 # the raw idea, not canned prose
    assert "developed: false" in md


def test_a_project_holds_many_ideas(env):
    client, _store, tmp = env
    r = client.post(
        "/api/library/import",
        json={
            "ideas": [
                _idea("n1", "Kernel Equivalence Harness"),
                _idea("n2", "The Serving-Config Compiler"),
            ],
            "new_project_title": "AI Hardware",
            "develop": False,
        },
    )
    assert r.status_code == 200, r.text
    assert len(r.json()["imported"]) == 2
    assert r.json()["project"]["idea_count"] == 2

    # ...and more can be added to the SAME project later.
    r2 = client.post(
        "/api/library/projects/ai-hardware/import",
        json={"ideas": [_idea("n3", "Verified Kernel Porting")], "develop": False},
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["project"]["idea_count"] == 3


def test_import_into_an_unknown_project_is_a_clean_400(env):
    client, _store, _tmp = env
    r = client.post(
        "/api/library/projects/nope/import",
        json={"ideas": [_idea()], "develop": False},
    )
    assert r.status_code == 400


def test_traversal_in_the_project_slug_is_rejected(env):
    client, _store, _tmp = env
    r = client.post(
        "/api/library/projects/..%2F..%2Fetc/import",
        json={"ideas": [_idea()], "develop": False},
    )
    assert r.status_code in (400, 404)


def test_docs_endpoints_round_trip(env):
    client, _store, _tmp = env
    client.post("/api/library/projects", json={"title": "Proj", "thesis": "T"})

    docs = client.get("/api/library/projects/proj/docs").json()
    assert docs[0]["path"] == "project.md"

    doc = client.get("/api/library/projects/proj/doc", params={"path": "project.md"}).json()
    assert "T" in doc["content"]

    w = client.put(
        "/api/library/projects/proj/doc",
        json={"path": "notes.md", "content": "# Notes\nhi", "base_mtime": None},
    )
    assert w.status_code == 200
    back = client.get("/api/library/projects/proj/doc", params={"path": "notes.md"}).json()
    assert "hi" in back["content"]


def test_stale_write_is_a_409_not_a_clobber(env):
    client, _store, tmp = env
    client.post("/api/library/projects", json={"title": "Proj"})
    client.put("/api/library/projects/proj/doc",
               json={"path": "notes.md", "content": "v1"})
    doc = client.get("/api/library/projects/proj/doc", params={"path": "notes.md"}).json()

    # An external edit lands after we read.
    import os
    p = tmp / "researchforme" / "proj" / "notes.md"
    p.write_text("edited in vim")
    os.utime(p, (doc["mtime"] + 10, doc["mtime"] + 10))

    r = client.put(
        "/api/library/projects/proj/doc",
        json={"path": "notes.md", "content": "v2", "base_mtime": doc["mtime"]},
    )
    assert r.status_code == 409
    assert p.read_text() == "edited in vim"


# --------------------------------------------------------------------------- #
# Consolidation (W-4)                                                          #
# --------------------------------------------------------------------------- #
def _seed_two_ideas(client):
    client.post("/api/library/projects", json={"title": "AI Hardware"})
    for slug, title in [("a", "Kernel CI"), ("b", "Serving Config Compiler")]:
        client.post(
            "/api/library/projects/ai-hardware/import",
            json={"ideas": [{"project_id": "p", "node_id": slug, "title": title,
                             "markdown": f"## Thesis\n{title} thesis.\n"}],
                  "develop": False},
        )


def test_consolidate_needs_at_least_two_ideas(env):
    client, _store, _tmp = env
    client.post("/api/library/projects", json={"title": "Solo"})
    client.post("/api/library/projects/solo/import",
                json={"ideas": [{"project_id": "p", "node_id": "n", "title": "One",
                                 "markdown": "## Thesis\nonly one\n"}], "develop": False})
    r = client.post("/api/library/projects/solo/consolidate")
    assert r.status_code == 400


def test_consolidate_503_when_no_real_backend(env):
    """Fixture mode cannot consolidate — honest 503, never a canned merge."""
    client, _store, _tmp = env
    _seed_two_ideas(client)
    r = client.post("/api/library/projects/ai-hardware/consolidate")
    assert r.status_code == 503
    # And nothing was written.
    docs = client.get("/api/library/projects/ai-hardware/docs").json()
    assert not any(d["path"] == "consolidation.md" for d in docs)


def test_consolidate_unknown_project_is_clean_error(env):
    client, _store, _tmp = env
    r = client.post("/api/library/projects/nope/consolidate")
    assert r.status_code in (400, 404)


# --------------------------------------------------------------------------- #
# Auto-synthesized project plan (#7) + lifecycle status (#10)                  #
# --------------------------------------------------------------------------- #
def test_new_project_keeps_stub_plan_when_no_llm(env):
    """Fixture backend can't synthesize a plan → the honest stub project.md stays,
    never canned filler."""
    client, _store, tmp = env
    r = client.post("/api/library/import", json={
        "ideas": [_idea()], "new_project_title": "Kernel CI", "develop": False})
    assert r.status_code == 200
    plan = (tmp / "researchforme" / "kernel-ci" / "project.md").read_text()
    # The create_project stub, not a fabricated plan.
    assert "## Plan" in plan


def test_project_status_lifecycle(env):
    client, _store, _tmp = env
    client.post("/api/library/projects", json={"title": "Proj"})
    r = client.post("/api/library/projects/proj/status", json={"status": "validating"})
    assert r.status_code == 200 and r.json()["status"] == "validating"
    # persists
    assert client.get("/api/library/projects/proj").json()["status"] == "validating"


def test_bad_status_is_rejected(env):
    client, _store, _tmp = env
    client.post("/api/library/projects", json={"title": "Proj"})
    assert client.post("/api/library/projects/proj/status",
                       json={"status": "on-fire"}).status_code == 400
