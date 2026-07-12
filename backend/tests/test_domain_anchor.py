"""Tests for domain-drift anchoring in structural decomposition.

Covers the quality fix for off-domain generalization (a run for "software for
independent physical therapy clinics" drifted into generic SMB gaps):

* prompt anchoring — the decomposition prompt carries the project's root domain
  and an explicit stay-inside-the-domain anchor clause at every depth;
* drift flagging — parsed children whose title/keywords share zero significant
  tokens with the root domain's scope are flagged ``off_domain``;
* drift penalty — the ``off_domain`` flag demonstrably lowers ``node_priority``
  versus an on-domain sibling, without dropping the child (contrarian adjacency
  is sometimes right; best-first just prefers on-domain branches).
"""

from __future__ import annotations

from app.autonomous.engine import (
    _OFF_DOMAIN_KEYWORD,
    _DECOMPOSE_SYSTEM,
    _children_from_raw,
    _decompose_prompt,
    make_node,
    node_priority,
)
from app.autonomous.schemas import NodeKind, Project

_DOMAIN = "software for independent physical therapy clinics"


def _project() -> Project:
    return Project(id="proj-anchor", domain=_DOMAIN)


def _deep_node(project: Project):
    """A depth-2 segment node, i.e. well below the root."""
    root = make_node(project.id, None, NodeKind.DOMAIN, project.domain, depth=0)
    sub = make_node(project.id, root, NodeKind.SUBAREA, "clinic back-office", depth=1)
    return make_node(project.id, sub, NodeKind.SEGMENT, "billing workflows", depth=2)


# --------------------------------------------------------------------------- #
# Prompt anchoring                                                            #
# --------------------------------------------------------------------------- #
def test_decompose_prompt_carries_root_domain_and_anchor_at_depth():
    project = _project()
    node = _deep_node(project)
    prompt = _decompose_prompt(node, project, "concrete segments")

    # The root domain string reaches the prompt even deep in the tree...
    assert project.domain in prompt
    # ...alongside an explicit anchor clause.
    assert "MUST remain inside" in prompt
    assert "ROOT DOMAIN" in prompt
    # Adjacent-industry analogies are framing only, never the child's market.
    assert "framing" in prompt


def test_decompose_system_prompt_anchors_to_root_domain():
    assert "root domain" in _DECOMPOSE_SYSTEM.lower()


# --------------------------------------------------------------------------- #
# Drift flag + priority penalty                                               #
# --------------------------------------------------------------------------- #
def _children_for(project: Project, raw: list) -> list:
    node = make_node(project.id, None, NodeKind.DOMAIN, project.domain, depth=0)
    node.priority = node_priority(node, None)
    return _children_from_raw(node, project, NodeKind.SUBAREA, 1, raw)


def test_zero_overlap_child_is_flagged_and_penalized():
    project = _project()
    raw = [
        {
            "title": "Insurance billing for small PT clinics",
            "rationale": "Physical therapy clinics drown in claim denials.",
            "keywords": ["physical therapy", "clinic billing", "claims"],
        },
        {
            "title": "Etsy seller cash flow forecasting",
            "rationale": "Handmade sellers cannot predict revenue.",
            "keywords": ["etsy", "handmade", "cash flow"],
        },
    ]
    on, off = _children_for(project, raw)

    assert _OFF_DOMAIN_KEYWORD not in on.keywords
    assert _OFF_DOMAIN_KEYWORD in off.keywords
    # Penalized, not dropped: the drifted child still exists...
    assert off.title
    # ...but best-first prefers the on-domain sibling.
    assert off.priority < on.priority


def test_on_domain_children_are_not_flagged():
    project = _project()
    raw = [
        {
            "title": "Home-exercise adherence tools for therapy patients",
            "rationale": "Clinics lose outcomes between visits.",
            "keywords": ["physical therapy", "adherence", "home exercise"],
        }
    ]
    (child,) = _children_for(project, raw)
    assert _OFF_DOMAIN_KEYWORD not in child.keywords


def test_off_domain_keyword_lowers_node_priority_directly():
    project = _project()
    node = make_node(project.id, None, NodeKind.DOMAIN, project.domain, depth=0)
    on = make_node(project.id, node, NodeKind.SUBAREA, "clinic scheduling",
                   keywords=["therapy", "scheduling"], depth=1)
    off = make_node(project.id, node, NodeKind.SUBAREA, "clinic scheduling twin",
                    keywords=["therapy", "scheduling", _OFF_DOMAIN_KEYWORD], depth=1)
    assert node_priority(off, node) < node_priority(on, node)
