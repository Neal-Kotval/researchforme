"""``gapfinder-mcp`` — MCP (Model Context Protocol) server for the engine.

Exposes the Market Gap Finder engine as MCP tools over stdio so an AI agent
(Claude Code, Claude Desktop, …) can drive the same backend the web UI uses.
Everything goes through the HTTP API via :class:`app.apiclient.GapFinderClient`,
so agent actions and UI actions share one store: a project the agent creates
appears live in the browser, and the agent can read back anything the user
does there (triage verdicts, stars, watch flags, …).

Run directly::

    python -m app.mcp_server

or register in a client's MCP config (see the repo's ``.mcp.json``). The
backend must be running (``just backend``); point at a non-default port with
``GAPFINDER_API_URL``.

Design notes:

* Tool results are JSON strings (FastMCP serializes dicts, but explicit
  ``json.dumps`` keeps output shape predictable across SDK versions).
* Tree reads return the *compact* view (``app.treeview``) — full pressure-test
  payloads would blow the agent's context for no reasoning benefit. The full
  node is available via ``get_node`` when depth is needed.
* Backend errors surface as readable tool errors (the API's ``detail``
  string), never stack traces.
"""

from __future__ import annotations

import json
from typing import Optional

from mcp.server.fastmcp import FastMCP

from .apiclient import ApiError, GapFinderClient
from .treeview import compact_tree, top_gaps

mcp = FastMCP(
    "gapfinder",
    instructions=(
        "Tools for the Market Gap Finder engine — an autonomous market-gap "
        "exploration system. Typical flow: scout() or intake_questions() to "
        "shape a domain, create_project() to launch an exploration, then poll "
        "get_project()/get_top_gaps() while it runs, and research_pack() for a "
        "deep dive on a promising gap. The user watches the same data live in "
        "the web UI, and their triage/star/watch actions are visible to you "
        "via get_tree()."
    ),
)

_client: Optional[GapFinderClient] = None


def client() -> GapFinderClient:
    global _client
    if _client is None:
        _client = GapFinderClient()
    return _client


def _run(fn):
    """Execute an API call, converting ApiError into a readable tool error."""
    try:
        return json.dumps(fn(), indent=2, default=str)
    except ApiError as exc:
        raise RuntimeError(exc.detail) from exc


# --------------------------------------------------------------------------- #
# Status                                                                       #
# --------------------------------------------------------------------------- #
@mcp.tool()
def health() -> str:
    """Backend status: resolved LLM backend, live vs mock sources, models."""
    return _run(lambda: client().health())


@mcp.tool()
def usage() -> str:
    """Global token-usage snapshot: spend, rate, governor mode, backoff."""
    return _run(lambda: client().usage())


# --------------------------------------------------------------------------- #
# Shaping a run                                                                #
# --------------------------------------------------------------------------- #
@mcp.tool()
def scout(brief: str = "", avoid: Optional[list[str]] = None) -> str:
    """Propose candidate domains from what is trending right now.

    Args:
        brief: Optional founder context (skills, constraints, tastes).
        avoid: Spaces to exclude from the proposals.
    """
    return _run(lambda: client().scout(brief, avoid))


@mcp.tool()
def intake_questions(domain: str, brief: str = "") -> str:
    """Preflight clarifying questions for a domain, with suggested answers.

    Ask the user these before create_project to sharpen the run; pass their
    answers as create_project's `intake` dict.
    """
    return _run(lambda: client().intake(domain, brief))


@mcp.tool()
def sort_research(text: str) -> str:
    """Sort a raw wall of the founder's own research into a launchable job:
    inferred domain, sub-segments, brief, and preserved steering research."""
    return _run(lambda: client().sort_research(text))


@mcp.tool()
def analyze(area: str, sub_segments: Optional[list[str]] = None) -> str:
    """One-shot (non-autonomous) gap analysis of an area. Slow: runs source
    fetches plus LLM synthesis; expect up to a few minutes."""
    return _run(lambda: client().analyze(area, sub_segments))


# --------------------------------------------------------------------------- #
# Projects                                                                     #
# --------------------------------------------------------------------------- #
@mcp.tool()
def create_project(
    domain: str,
    sub_segments: Optional[list[str]] = None,
    brief: str = "",
    advantages: Optional[list[str]] = None,
    constraints: Optional[list[str]] = None,
    avoid: Optional[list[str]] = None,
    intake: Optional[dict] = None,
    max_nodes: Optional[int] = None,
    max_tokens: Optional[int] = None,
    pace: str = "balanced",
    autostart: bool = True,
) -> str:
    """Create an autonomous exploration project (visible immediately in the
    user's web UI) and, by default, start exploring.

    Args:
        domain: The market/space to explore, e.g. "ai tooling for accountants".
        sub_segments: Optional starting sub-segments to seed the tree.
        brief: Steering brief — the founder's context in free text.
        advantages: The founder's unfair advantages (steering).
        constraints: Hard constraints, e.g. "solo dev", "no hardware".
        avoid: Spaces to steer away from.
        intake: Answers to intake_questions, as {question: answer}.
        max_nodes: Budget — node ceiling for the run (default 400).
        max_tokens: Budget — hard token ceiling for the run.
        pace: "eco" | "balanced" | "sprint".
        autostart: False creates the project paused (spends nothing).
    """
    req: dict = {
        "domain": domain,
        "sub_segments": sub_segments or [],
        "intake": intake or {},
        "autostart": autostart,
    }
    steering = {
        "brief": brief,
        "advantages": advantages or [],
        "constraints": constraints or [],
        "avoid": avoid or [],
    }
    if any(steering.values()):
        req["steering"] = steering
    budget: dict = {"pace": pace}
    if max_nodes is not None:
        budget["max_nodes"] = max_nodes
    if max_tokens is not None:
        budget["max_tokens"] = max_tokens
    req["budget"] = budget
    return _run(lambda: client().create_project(req))


@mcp.tool()
def list_projects() -> str:
    """Every project (newest first) with status and stats."""
    return _run(lambda: client().list_projects())


@mcp.tool()
def get_project(project_id: str) -> str:
    """One project's metadata and live stats (status, node/gap counts, spend)."""
    return _run(lambda: client().get_project(project_id))


@mcp.tool()
def get_tree(
    project_id: str,
    starred_only: bool = False,
    min_viability: Optional[int] = None,
) -> str:
    """The project's exploration tree in compact form (id/title/state/scores/
    user flags per node — heavy payloads omitted; use get_node for one node's
    full detail). Includes the user's triage/star/watch signals."""
    def go():
        snapshot = client().get_tree(project_id)
        return compact_tree(
            snapshot, starred_only=starred_only, min_viability=min_viability
        )
    return _run(go)


@mcp.tool()
def get_top_gaps(project_id: str, limit: int = 10) -> str:
    """The project's highest-viability scored gaps, best first."""
    return _run(lambda: top_gaps(client().get_tree(project_id), limit=limit))


@mcp.tool()
def get_node(project_id: str, node_id: str) -> str:
    """One node's FULL payload: gap details, pressure test, fit reasoning,
    research pack if generated."""
    def go():
        snapshot = client().get_tree(project_id)
        for node in snapshot.get("nodes", []):
            if node.get("id") == node_id:
                return node
        raise ApiError(404, f"Node {node_id} not found in project {project_id}.")
    return _run(go)


@mcp.tool()
def control_project(
    project_id: str,
    action: str,
    node_id: str = "",
    pace: str = "",
    triage: str = "",
    triage_reason: str = "",
    stage: str = "",
    learnings: str = "",
    max_tokens: Optional[int] = None,
    max_nodes: Optional[int] = None,
) -> str:
    """Apply a run-control action. Actions: pause, resume, continue_milestone,
    set_budget (max_tokens/max_nodes), set_pace (pace), pin_node/unpin_node,
    set_triage (triage="interested"|"passed" + triage_reason), set_stage
    (stage + learnings), watch_node/unwatch_node, continue_deepening.
    Node-scoped actions require node_id."""
    body: dict = {"action": action}
    if node_id:
        body["node_id"] = node_id
    if pace:
        body["pace"] = pace
    if triage:
        body["triage"] = triage
    if triage_reason:
        body["triage_reason"] = triage_reason
    if stage:
        body["stage"] = stage
    if learnings:
        body["learnings"] = learnings
    if max_tokens is not None or max_nodes is not None:
        body["budget"] = {
            k: v for k, v in
            {"max_tokens": max_tokens, "max_nodes": max_nodes}.items()
            if v is not None
        }
    return _run(lambda: client().control(project_id, body))


@mcp.tool()
def delete_project(project_id: str) -> str:
    """Stop and permanently forget a project. Irreversible — confirm with the
    user before calling."""
    return _run(lambda: client().delete_project(project_id))


@mcp.tool()
def rerun_project(project_id: str, autostart: bool = False) -> str:
    """Clone a project into a fresh linked run (same domain/steering/budget).
    autostart=False (default) creates it paused — it spends nothing until
    resumed."""
    return _run(lambda: client().rerun(project_id, autostart))


@mcp.tool()
def diff_projects(project_id: str, against: str) -> str:
    """Node-level diff of two runs' scored gaps: new / gone / moved with
    viability and fit deltas."""
    return _run(lambda: client().diff(project_id, against))


@mcp.tool()
def research_pack(project_id: str, node_id: str, refresh: bool = False) -> str:
    """The gap's markdown research pack (cached; refresh=True regenerates).
    Slow on a cache miss — one strong-model call."""
    return _run(lambda: client().research_pack(project_id, node_id, refresh))


# --------------------------------------------------------------------------- #
# Cross-project views                                                          #
# --------------------------------------------------------------------------- #
@mcp.tool()
def portfolio() -> str:
    """Every scored gap across every project — the viability×fit 2x2 dataset."""
    return _run(lambda: client().portfolio())


@mcp.tool()
def graveyard(query: str = "", limit: int = 50) -> str:
    """Rejected gaps across all projects plus the curated post-mortem corpus.
    Check here before pitching an idea — it may already have died."""
    return _run(lambda: client().graveyard(q=query, limit=limit))


@mcp.tool()
def watch_status() -> str:
    """Every watched node across projects with its most recent alert."""
    return _run(lambda: client().watch())


@mcp.tool()
def watch_sweep() -> str:
    """Manually sweep every watched node's sources now (fetch-only, no LLM)."""
    return _run(lambda: client().watch_sweep())


@mcp.tool()
def get_preferences() -> str:
    """The user's learned preferences (pending/active/dismissed) + triage count."""
    return _run(lambda: client().preferences())


# --------------------------------------------------------------------------- #
# Library — projects as folders, documents as markdown (Phase 5)              #
# The user edits these same files in the web UI; you and the browser share one #
# library, so a doc you write here appears there and vice versa.              #
# --------------------------------------------------------------------------- #
@mcp.tool()
def list_projects_library() -> str:
    """Every library project (a folder of markdown on disk) + the library root path."""
    return _run(lambda: {"root": client().library_root().get("root"),
                         "projects": client().list_library_projects()})


@mcp.tool()
def create_project_folder(title: str, thesis: str = "") -> str:
    """Create a library project — a real folder with a project.md. Returns its slug."""
    return _run(lambda: client().create_library_project(title, thesis))


@mcp.tool()
def list_documents(slug: str) -> str:
    """List a project's markdown documents (path + title)."""
    return _run(lambda: client().list_docs(slug))


@mcp.tool()
def read_document(slug: str, path: str) -> str:
    """Read one document's content, e.g. path='project.md' or 'ideas/kernel-ci.md'."""
    return _run(lambda: client().read_doc(slug, path))


@mcp.tool()
def write_document(slug: str, path: str, content: str) -> str:
    """Create or overwrite a markdown document in a project. path must end in .md
    and stays inside the project (traversal is rejected server-side). This edits a
    real file the user also sees in the web UI."""
    return _run(lambda: client().write_doc(slug, path, content))


@mcp.tool()
def consolidate_project(slug: str) -> str:
    """Consolidate a project's ideas into one thesis + plan (writes consolidation.md).
    Names where the ideas conflict and refuses to merge ones that don't belong.
    Slow (one strong-model pass); 400 if <2 ideas, 503 if no LLM backend."""
    return _run(lambda: client().consolidate_project(slug))


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
