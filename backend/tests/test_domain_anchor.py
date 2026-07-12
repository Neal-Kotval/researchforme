"""Tests for domain-drift anchoring in structural decomposition.

Covers the quality fix for off-domain generalization (a run for "software for
independent physical therapy clinics" drifted into generic SMB gaps):

* prompt anchoring — the decomposition prompt carries the project's root domain,
  an explicit stay-inside-the-domain anchor clause at every depth, and requires
  each child to declare its buyer (``market``) and an ``in_domain`` boolean;
* drift flagging — the child's declared ``in_domain`` is the primary signal
  (token overlap was the wrong signal: on-domain branches with fresh vocabulary
  false-positived while generic-SMB drift under a domain-titled parent slipped
  through). The token check remains as a tiebreaker when the declaration is
  missing, and children under an ``off_domain`` parent inherit the marker unless
  they re-declare in-domain AND pass the tiebreaker (no drift laundering);
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
    # Each child must declare its buyer and whether it sits in the root domain.
    assert '"market"' in prompt
    assert '"in_domain"' in prompt


def test_decompose_system_prompt_anchors_to_root_domain():
    assert "root domain" in _DECOMPOSE_SYSTEM.lower()


# --------------------------------------------------------------------------- #
# Drift flag + priority penalty                                               #
# --------------------------------------------------------------------------- #
def _children_for(project: Project, raw: list) -> list:
    node = make_node(project.id, None, NodeKind.DOMAIN, project.domain, depth=0)
    node.priority = node_priority(node, None)
    return _children_from_raw(node, project, NodeKind.SUBAREA, 1, raw)


def test_missing_declaration_zero_overlap_is_flagged_via_tiebreaker():
    """No ``in_domain`` declaration -> the token check remains the tiebreaker:
    zero-overlap children are flagged (and penalized), overlapping ones not."""
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


def test_declared_off_domain_is_flagged_despite_token_overlap():
    """The model's own in_domain=false declaration is the primary signal — a
    domain-sounding title cannot launder a child the model itself says serves
    buyers outside the root domain."""
    project = _project()
    raw = [
        {
            "title": "Simplified billing & invoicing for independent clinics",
            "rationale": "Every small service business hates invoicing.",
            "keywords": ["clinic", "billing", "invoicing"],
            "market": "any small service business owner, not PT-specific",
            "in_domain": False,
        }
    ]
    (child,) = _children_for(project, raw)
    assert _OFF_DOMAIN_KEYWORD in child.keywords


def test_declared_in_domain_true_beats_zero_token_overlap():
    """An on-domain semantic child with fresh vocabulary (zero shared tokens)
    must NOT be flagged when the model declares in_domain=true — the empirical
    false-positive ('No-Show Prediction & Patient Retention')."""
    project = _project()
    raw = [
        {
            "title": "No-Show Prediction & Patient Retention",
            "rationale": "Missed appointments quietly drain revenue.",
            "keywords": ["no-show", "retention", "reminders"],
            "market": "owners of independent physical therapy clinics",
            "in_domain": True,
        }
    ]
    (child,) = _children_for(project, raw)
    assert _OFF_DOMAIN_KEYWORD not in child.keywords


def _children_under_off_domain_parent(project: Project, raw: list) -> list:
    """Children of a parent that already carries the off_domain marker."""
    root = make_node(project.id, None, NodeKind.DOMAIN, project.domain, depth=0)
    parent = make_node(
        project.id, root, NodeKind.SUBAREA,
        "Simplified Billing & Invoicing for Independent Clinics",
        keywords=["billing", "invoicing", _OFF_DOMAIN_KEYWORD], depth=1,
    )
    parent.priority = node_priority(parent, root)
    return _children_from_raw(parent, project, NodeKind.SEGMENT, 2, raw)


def test_off_domain_parent_marker_is_inherited():
    """Drift can't launder itself through a domain-titled parent: a child under
    an off_domain parent inherits the marker unless it BOTH re-declares
    in_domain=true AND passes the token tiebreaker."""
    project = _project()
    raw = [
        {  # no re-declaration -> inherits, even with generic-SMB tokens absent
            "title": "Recurring invoice templates",
            "rationale": "Generic SMB invoicing pain.",
            "keywords": ["invoices", "templates", "recurring"],
        },
        {  # re-declares true but zero overlap with the root domain -> still inherits
            "title": "Late-payment chasing automation",
            "rationale": "SMBs chase unpaid invoices by hand.",
            "keywords": ["late payment", "dunning", "automation"],
            "market": "any freelancer or small business owner",
            "in_domain": True,
        },
        {  # re-declares true AND overlaps the root domain -> earns its way back
            "title": "Insurance claim denials for physical therapy clinics",
            "rationale": "PT clinics drown in payer-specific denial codes.",
            "keywords": ["physical therapy", "claims", "denials"],
            "market": "billing staff at independent physical therapy clinics",
            "in_domain": True,
        },
    ]
    inherited, laundered, redeemed = _children_under_off_domain_parent(project, raw)
    assert _OFF_DOMAIN_KEYWORD in inherited.keywords
    assert _OFF_DOMAIN_KEYWORD in laundered.keywords
    assert _OFF_DOMAIN_KEYWORD not in redeemed.keywords


def test_off_domain_keyword_lowers_node_priority_directly():
    project = _project()
    node = make_node(project.id, None, NodeKind.DOMAIN, project.domain, depth=0)
    on = make_node(project.id, node, NodeKind.SUBAREA, "clinic scheduling",
                   keywords=["therapy", "scheduling"], depth=1)
    off = make_node(project.id, node, NodeKind.SUBAREA, "clinic scheduling twin",
                    keywords=["therapy", "scheduling", _OFF_DOMAIN_KEYWORD], depth=1)
    assert node_priority(off, node) < node_priority(on, node)
