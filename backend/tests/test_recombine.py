"""The recombination GENERATION — the service-level evolutionary round.

`test_evolve.py` covers the operators in isolation; this covers the integration
logic in `ExplorerService._recombine_generation` that runs unattended when a
frontier drains: select parents → mint offspring as GAP_CANDIDATE nodes → dedup
against the pool → score each through the shared gauntlet → count. The heavy
sub-calls (operators, pressure test, scoring, enrichment) are monkeypatched so
the generation's OWN logic is what's exercised — parent gating, dedup, the
generation cap, and the fixture guard.

Hermetic — a fake client, an on-disk temp store, no LLM, no network.
"""

from __future__ import annotations

import asyncio

import pytest

from tests.test_autonomous import _tiny_gap


def _seed_gap_node(store, project_id, title, viability):
    from app.autonomous.engine import make_node
    from app.autonomous.schemas import NodeKind

    n = make_node(project_id, None, NodeKind.GAP, title)
    g = _tiny_gap(sub_segment=title.lower())
    g.title = title
    n.gap = g
    n.viability = viability
    n.confidence = "medium"
    store.upsert_node(n)
    return n


def _patch_heavy(monkeypatch, service_mod, service, offspring_title="Fused Child"):
    """Replace the expensive calls so only the generation's own logic runs."""
    from app.autonomous.schemas import PressureTest

    async def _fake_cross(a, b, client, model, steering=""):  # noqa: ANN001
        g = _tiny_gap()
        g.title = offspring_title
        g.tags = ["crossover"]
        return g

    async def _fake_mut(gap, strat, client, model, steering=""):  # noqa: ANN001
        g = _tiny_gap()
        g.title = f"{offspring_title} [{strat}]"
        g.tags = ["mutation", f"mut:{strat}"]
        return g

    async def _fake_pt(*a, **k):  # noqa: ANN002, ANN003
        return PressureTest(lenses=[], survived=0, weakened=0, killed=0,
                            unmeasured=0, test_rigor="standard", summary="ok")

    async def _fake_drop(project, children):  # noqa: ANN001
        return children

    async def _fake_enrich(project, child):  # noqa: ANN001
        return 0

    monkeypatch.setattr(service_mod, "crossover_gaps", _fake_cross)
    monkeypatch.setattr(service_mod, "mutate_gap", _fake_mut)
    monkeypatch.setattr(service_mod, "pressure_test", _fake_pt)
    monkeypatch.setattr(service_mod, "score_viability", lambda gap, test: (80, "high"))
    monkeypatch.setattr(service, "_drop_semantic_duplicates", _fake_drop)
    monkeypatch.setattr(service, "_enrich_winner", _fake_enrich)


def _make_service(tmp_path, backend="api"):
    from app.autonomous.governor import UsageGovernor
    from app.autonomous.service import ExplorerService
    from app.autonomous.store import TreeStore
    from app.autonomous.schemas import Budget, CreateProjectRequest

    store = TreeStore(path=str(tmp_path / "rec.db"))
    service = ExplorerService(store, UsageGovernor())

    class _Client:
        pass

    client = _Client()
    client.backend = backend
    service.client = client

    project = service.create(
        CreateProjectRequest(
            domain="edge inference runtimes",
            budget=Budget(max_nodes=400, milestone_tokens=0),
            autostart=False,
        )
    )
    return service, store, project


@pytest.mark.asyncio
async def test_generation_mints_scores_and_counts_offspring(tmp_path, monkeypatch):
    from app.autonomous import service as service_mod
    from app.autonomous.schemas import ExplorerMode, NodeKind

    service, store, project = _make_service(tmp_path)
    _patch_heavy(monkeypatch, service_mod, service)
    for i, v in enumerate([70, 66, 58, 44]):
        _seed_gap_node(store, project.id, f"Parent idea {i}", v)

    rt = service_mod._Runtime()
    produced = await service._recombine_generation(
        rt, project, ExplorerMode.SPRINTING, 0
    )

    assert produced > 0, "a healthy pool must yield scored offspring"
    assert rt.generations == 1
    # Offspring were promoted to real GAP nodes tagged with their lineage.
    offspring = [
        n for n in store.get_nodes(project.id)
        if n.kind is NodeKind.GAP and n.gap
        and ("crossover" in (n.gap.tags or []) or "mutation" in (n.gap.tags or []))
    ]
    assert len(offspring) == produced
    assert all(n.viability == 80 for n in offspring)


@pytest.mark.asyncio
async def test_generation_cap_stops_the_spin(tmp_path, monkeypatch):
    from app.autonomous import service as service_mod
    from app.autonomous.schemas import ExplorerMode

    service, store, project = _make_service(tmp_path)
    _patch_heavy(monkeypatch, service_mod, service)
    for i, v in enumerate([70, 66]):
        _seed_gap_node(store, project.id, f"Parent {i}", v)

    rt = service_mod._Runtime()
    rt.generations = service_mod._MAX_GENERATIONS  # already at the cap
    produced = await service._recombine_generation(
        rt, project, ExplorerMode.SPRINTING, 0
    )
    assert produced == 0, "past the generation cap the spin must stop"


@pytest.mark.asyncio
async def test_fixture_backend_refuses_to_recombine(tmp_path, monkeypatch):
    from app.autonomous import service as service_mod
    from app.autonomous.schemas import ExplorerMode

    service, store, project = _make_service(tmp_path, backend="fixture")
    _patch_heavy(monkeypatch, service_mod, service)
    for i, v in enumerate([70, 66]):
        _seed_gap_node(store, project.id, f"Parent {i}", v)

    rt = service_mod._Runtime()
    produced = await service._recombine_generation(
        rt, project, ExplorerMode.SPRINTING, 0
    )
    assert produced == 0
    assert rt.generations == 0, "the fixture guard must fire before counting a gen"


@pytest.mark.asyncio
async def test_occupied_novelty_pulls_the_star(tmp_path, monkeypatch):
    """A high-viability gap the novelty scan confirms occupied loses its ⭐."""
    import app.autonomous.novelty as novelty_mod
    import app.autonomous.valuemodel as vm_mod
    from app.autonomous.novelty import NoveltyScan, NearestCompany

    service, store, project = _make_service(tmp_path)

    async def _occupied(gap, client, model):  # noqa: ANN001
        return NoveltyScan(
            nearest_known=[NearestCompany(name="BigCo", why_similar="ships this")],
            novelty_0_100=12, verdict="occupied",
            rationale="BigCo already sells exactly this to the same buyer.",
        )

    async def _no_value(gap, project, client, model):  # noqa: ANN001
        return None

    monkeypatch.setattr(novelty_mod, "novelty_scan", _occupied)
    monkeypatch.setattr(vm_mod, "model_value", _no_value)

    node = _seed_gap_node(store, project.id, "Occupied idea", 90)
    node.star = True
    await service._enrich_winner(project, node)
    assert node.star is False, "an occupied space must not keep the engine's star"
    assert node.novelty_scan["verdict"] == "occupied"


@pytest.mark.asyncio
async def test_open_novelty_keeps_the_star(tmp_path, monkeypatch):
    """An open/adjacent verdict leaves a legitimately-starred gap starred."""
    import app.autonomous.novelty as novelty_mod
    import app.autonomous.valuemodel as vm_mod
    from app.autonomous.novelty import NoveltyScan

    service, store, project = _make_service(tmp_path)

    async def _open(gap, client, model):  # noqa: ANN001
        return NoveltyScan(nearest_known=[], novelty_0_100=70, verdict="open",
                           rationale="Real room on a distinct angle.")

    async def _no_value(gap, project, client, model):  # noqa: ANN001
        return None

    monkeypatch.setattr(novelty_mod, "novelty_scan", _open)
    monkeypatch.setattr(vm_mod, "model_value", _no_value)

    node = _seed_gap_node(store, project.id, "Open idea", 88)
    node.star = True
    await service._enrich_winner(project, node)
    assert node.star is True, "novelty gates by demotion only — open keeps the star"


@pytest.mark.asyncio
async def test_thin_pool_does_not_recombine(tmp_path, monkeypatch):
    from app.autonomous import service as service_mod
    from app.autonomous.schemas import ExplorerMode

    service, store, project = _make_service(tmp_path)
    _patch_heavy(monkeypatch, service_mod, service)
    _seed_gap_node(store, project.id, "Lonely idea", 70)  # only one gap

    rt = service_mod._Runtime()
    produced = await service._recombine_generation(
        rt, project, ExplorerMode.SPRINTING, 0
    )
    assert produced == 0, "one gap cannot be crossed with anything"
