"""The user's star is orthogonal to the engine's star.

`Node.star` is a measurement (viability cleared the threshold); `Node.user_star`
is the founder's own shortlist. Overloading one field for both would corrupt the
`stars` stat and the idle-deepening rule, which read "the engine rated this
highly" — so starring an idea must never move the engine's verdict, and vice versa.
"""
import pytest

from app.autonomous.schemas import CreateProjectRequest
from app.autonomous.service import get_service


@pytest.fixture()
def project():
    svc = get_service()
    p = svc.create(CreateProjectRequest(domain="star test domain", autostart=False))
    yield svc, p
    svc.delete(p.id)


def _root(svc, pid):
    return svc.store.snapshot(pid).nodes[0]


def test_star_node_sets_user_star_only(project):
    svc, p = project
    node = _root(svc, p.id)
    assert node.user_star is False and node.star is False

    svc.star_node(p.id, node.id, True)
    after = _root(svc, p.id)
    assert after.user_star is True
    # The engine's own verdict must be untouched.
    assert after.star is False


def test_unstar_is_idempotent_and_reversible(project):
    svc, p = project
    node = _root(svc, p.id)
    svc.star_node(p.id, node.id, True)
    svc.star_node(p.id, node.id, False)
    assert _root(svc, p.id).user_star is False
    svc.star_node(p.id, node.id, False)
    assert _root(svc, p.id).user_star is False


def test_user_star_survives_a_reload_from_the_store(project):
    svc, p = project
    node = _root(svc, p.id)
    svc.star_node(p.id, node.id, True)
    reloaded = svc.store.get_node(node.id)
    assert reloaded is not None and reloaded.user_star is True


def test_engine_star_does_not_imply_user_star(project):
    svc, p = project
    node = _root(svc, p.id)
    node.star = True                      # engine rates it highly
    svc.store.upsert_node(node)
    assert _root(svc, p.id).user_star is False   # the user still hasn't starred it
