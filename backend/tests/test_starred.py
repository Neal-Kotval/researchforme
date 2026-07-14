"""The starred shortlist reads the USER's star, never the engine's."""
import pytest

from app.autonomous.portfolio import starred_items
from app.autonomous.schemas import CreateProjectRequest
from app.autonomous.service import get_service


@pytest.fixture()
def svc_project():
    svc = get_service()
    p = svc.create(CreateProjectRequest(domain="starred shortlist domain", autostart=False))
    yield svc, p
    svc.delete(p.id)


def _root(svc, pid):
    return svc.store.snapshot(pid).nodes[0]


def test_empty_until_the_user_stars_something(svc_project):
    svc, p = svc_project
    ours = [i for i in starred_items(svc.store) if i.project_id == p.id]
    assert ours == []


def test_user_starred_node_appears(svc_project):
    svc, p = svc_project
    node = _root(svc, p.id)
    svc.star_node(p.id, node.id, True)

    ours = [i for i in starred_items(svc.store) if i.project_id == p.id]
    assert len(ours) == 1
    assert ours[0].node_id == node.id
    assert ours[0].user_star is True
    assert ours[0].domain == "starred shortlist domain"


def test_engine_star_alone_does_NOT_put_it_on_the_shortlist(svc_project):
    """The engine rating a gap highly is not the founder starring it."""
    svc, p = svc_project
    node = _root(svc, p.id)
    node.star = True                      # engine's threshold verdict
    svc.store.upsert_node(node)

    ours = [i for i in starred_items(svc.store) if i.project_id == p.id]
    assert ours == []


def test_unstarring_removes_it(svc_project):
    svc, p = svc_project
    node = _root(svc, p.id)
    svc.star_node(p.id, node.id, True)
    svc.star_node(p.id, node.id, False)
    ours = [i for i in starred_items(svc.store) if i.project_id == p.id]
    assert ours == []
