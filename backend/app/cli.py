"""``gapfinder`` — command-line surface for the Market Gap Finder engine.

A thin wrapper over the same HTTP API the web UI uses (via
:class:`app.apiclient.GapFinderClient`), so anything you can do in the browser
you can do from a terminal or a script. The backend must be running
(``just backend``); point at a non-default port with ``GAPFINDER_API_URL``.

Examples::

    gapfinder health
    gapfinder analyze "personal finance for freelancers"
    gapfinder scout --brief "solo dev, B2B, hates hardware"
    gapfinder create "ai tooling for accountants" --seg bookkeeping --seg audit
    gapfinder projects
    gapfinder tree <pid>            # indented live tree with badges
    gapfinder gaps <pid>            # top scored gaps
    gapfinder events <pid>          # tail the live SSE stream
    gapfinder pack <pid> <nid>      # the gap's markdown research pack
    gapfinder pause <pid> / resume <pid> / delete <pid>

Every command accepts ``--json`` to print the raw API response instead of the
human rendering.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Optional

from .apiclient import ApiError, GapFinderClient
from .treeview import compact_tree, render_tree, top_gaps


# --------------------------------------------------------------------------- #
# Output helpers                                                              #
# --------------------------------------------------------------------------- #
def _dump(data: Any) -> None:
    print(json.dumps(data, indent=2, default=str))


def _project_line(p: dict) -> str:
    stats = p.get("stats") or {}
    bits = [
        p.get("id", "?")[:8],
        f"[{p.get('status', '?')}]",
        p.get("domain", ""),
    ]
    if stats:
        bits.append(
            f"(nodes={stats.get('nodes', 0)} gaps={stats.get('gaps', 0)} "
            f"stars={stats.get('stars', 0)} tokens={stats.get('tokens_spent', 0)})"
        )
    return "  ".join(str(b) for b in bits)


def _gap_line(n: dict) -> str:
    v = n.get("viability")
    f = n.get("fit")
    star = " *" if n.get("star") else ""
    fit = f" fit={f}" if f is not None else ""
    return f"  v={v}{fit}{star}  {n.get('title')}  ({n.get('id')})"


# --------------------------------------------------------------------------- #
# Command handlers — each takes (client, args) and returns the raw payload    #
# so the shared --json path stays trivial.                                    #
# --------------------------------------------------------------------------- #
def cmd_health(c: GapFinderClient, a) -> Any:
    data = c.health()
    if not a.json:
        print(f"backend: {data.get('llm_backend')}  model: {data.get('default_model')}")
        for name, src in (data.get("sources") or {}).items():
            print(f"  {name}: {src.get('mode')}")
        return None
    return data


def cmd_usage(c: GapFinderClient, a) -> Any:
    if a.cap is not None or a.limit_pct is not None:
        return c.set_usage_policy(daily_cap_tokens=a.cap, limit_pct=a.limit_pct)
    return c.usage()


def cmd_analyze(c: GapFinderClient, a) -> Any:
    report = c.analyze(a.area, a.seg)
    if a.json:
        return report
    for ranked in report.get("gaps", []):
        gap = ranked.get("gap", {})
        print(f"{ranked.get('rank', '?'):>3}. [{ranked.get('composite', 0):.2f}] "
              f"{gap.get('title')}")
        if gap.get("thesis"):
            print(f"     {gap['thesis']}")
        if gap.get("wedge"):
            print(f"     wedge: {gap['wedge']}")
    return None


def cmd_scout(c: GapFinderClient, a) -> Any:
    data = c.scout(a.brief or "", a.avoid)
    if a.json:
        return data
    for cand in data.get("candidates", []):
        flag = " (degraded)" if cand.get("degraded") else ""
        print(f"- {cand.get('domain')}{flag}")
        if cand.get("rationale"):
            print(f"    {cand['rationale']}")
        if cand.get("suggested_sub_segments"):
            print(f"    segments: {', '.join(cand['suggested_sub_segments'])}")
    return None


def cmd_intake(c: GapFinderClient, a) -> Any:
    data = c.intake(a.domain, a.brief or "")
    if a.json:
        return data
    for q in data.get("questions", []):
        print(f"- {q.get('question')}")
        for s in q.get("suggestions", []):
            print(f"    · {s}")
    return None


def cmd_sort_research(c: GapFinderClient, a) -> Any:
    text = open(a.file).read() if a.file else sys.stdin.read()
    if not text.strip():
        raise ApiError(0, "No research text supplied (pass --file or pipe stdin).")
    return c.sort_research(text)


def cmd_create(c: GapFinderClient, a) -> Any:
    req: dict[str, Any] = {
        "domain": a.domain,
        "sub_segments": a.seg,
        "autostart": not a.no_autostart,
    }
    budget: dict[str, Any] = {}
    if a.max_nodes is not None:
        budget["max_nodes"] = a.max_nodes
    if a.max_tokens is not None:
        budget["max_tokens"] = a.max_tokens
    if a.pace:
        budget["pace"] = a.pace
    if budget:
        req["budget"] = budget
    if a.brief:
        req["steering"] = {"brief": a.brief}
    project = c.create_project(req)
    if a.json:
        return project
    print(_project_line(project))
    print(f"project id: {project.get('id')}")
    return None


def cmd_projects(c: GapFinderClient, a) -> Any:
    projects = c.list_projects()
    if a.json:
        return projects
    if not projects:
        print("No projects yet — start one with `gapfinder create <domain>`.")
    for p in projects:
        print(_project_line(p))
    return None


def cmd_show(c: GapFinderClient, a) -> Any:
    return c.get_project(a.pid)


def cmd_tree(c: GapFinderClient, a) -> Any:
    snapshot = c.get_tree(a.pid)
    if a.json:
        return compact_tree(
            snapshot, starred_only=a.starred, min_viability=a.min_viability
        )
    print(render_tree(snapshot, max_depth=a.depth))
    return None


def cmd_gaps(c: GapFinderClient, a) -> Any:
    gaps = top_gaps(c.get_tree(a.pid), limit=a.limit)
    if a.json:
        return gaps
    if not gaps:
        print("No scored gaps yet.")
    for n in gaps:
        print(_gap_line(n))
    return None


def cmd_delete(c: GapFinderClient, a) -> Any:
    if not a.yes:
        reply = input(f"Delete project {a.pid}? This cannot be undone. [y/N] ")
        if reply.strip().lower() not in ("y", "yes"):
            print("Aborted.")
            return None
    return c.delete_project(a.pid)


def _simple_control(action: str):
    def handler(c: GapFinderClient, a) -> Any:
        return c.control(a.pid, {"action": action})
    return handler


def cmd_control(c: GapFinderClient, a) -> Any:
    body: dict[str, Any] = {"action": a.action}
    if a.node:
        body["node_id"] = a.node
    if a.pace:
        body["pace"] = a.pace
    if a.triage:
        body["triage"] = a.triage
    if a.triage_reason:
        body["triage_reason"] = a.triage_reason
    if a.stage:
        body["stage"] = a.stage
    if a.learnings:
        body["learnings"] = a.learnings
    if a.max_tokens is not None or a.max_nodes is not None:
        body["budget"] = {
            k: v for k, v in
            {"max_tokens": a.max_tokens, "max_nodes": a.max_nodes}.items()
            if v is not None
        }
    return c.control(a.pid, body)


def cmd_rerun(c: GapFinderClient, a) -> Any:
    return c.rerun(a.pid, autostart=a.autostart)


def cmd_diff(c: GapFinderClient, a) -> Any:
    return c.diff(a.pid, a.against)


def cmd_events(c: GapFinderClient, a) -> Any:
    print(f"Tailing events for {a.pid} — Ctrl-C to stop.")
    try:
        for event in c.stream_events(a.pid, after=a.after):
            if a.json:
                _dump(event)
            else:
                kind = event.get("kind") or event.get("type") or "event"
                title = (event.get("node") or {}).get("title") or event.get("message", "")
                print(f"[{event.get('seq', '?')}] {kind}  {title}")
    except KeyboardInterrupt:
        pass
    return None


def cmd_pack(c: GapFinderClient, a) -> Any:
    data = c.research_pack(a.pid, a.nid, refresh=a.refresh)
    if a.json:
        return data
    print(data.get("markdown", ""))
    return None


def cmd_portfolio(c: GapFinderClient, a) -> Any:
    items = c.portfolio()
    if a.json:
        return items
    items.sort(key=lambda it: (it.get("viability") or 0), reverse=True)
    for it in items:
        star = " *" if it.get("star") else ""
        fit = f" fit={it['fit']}" if it.get("fit") is not None else ""
        print(f"  v={it.get('viability')}{fit}{star}  {it.get('title')}"
              f"  [{it.get('domain') or '?'}]  ({it.get('project_id', '')[:8]}/"
              f"{it.get('node_id')})")
    return None


def cmd_graveyard(c: GapFinderClient, a) -> Any:
    items = c.graveyard(q=a.query or "", limit=a.limit)
    if a.json:
        return items
    for it in items:
        ext = " (external)" if it.get("external") else ""
        print(f"- {it.get('title')}{ext}: {it.get('reason', '')[:120]}")
    return None


def cmd_watch(c: GapFinderClient, a) -> Any:
    if a.sweep:
        return c.watch_sweep()
    return c.watch()


def cmd_prefs(c: GapFinderClient, a) -> Any:
    if a.prefs_action == "distill":
        return c.distill_preferences()
    if a.prefs_action in ("confirm", "dismiss"):
        current = (c.preferences().get("preferences") or {})
        text = a.text or current.get("learned_preferences", "")
        status = "active" if a.prefs_action == "confirm" else "dismissed"
        return c.update_preferences(text, status)
    return c.preferences()


# --------------------------------------------------------------------------- #
# Parser                                                                      #
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gapfinder",
        description="CLI for the Market Gap Finder engine (wraps the local API).",
    )
    parser.add_argument("--url", help="API base URL (default: $GAPFINDER_API_URL "
                                      "or http://127.0.0.1:8000/api)")
    sub = parser.add_subparsers(dest="command", required=True)

    def add(name: str, handler, help_: str) -> argparse.ArgumentParser:
        p = sub.add_parser(name, help=help_)
        p.set_defaults(handler=handler)
        p.add_argument("--json", action="store_true", help="print raw JSON")
        return p

    add("health", cmd_health, "backend/LLM/source status")

    p = add("usage", cmd_usage, "global usage snapshot; set policy with flags")
    p.add_argument("--cap", type=int, help="daily cap in tokens")
    p.add_argument("--limit-pct", type=float, help="target fraction of the cap")

    p = add("analyze", cmd_analyze, "one-shot gap analysis of an area")
    p.add_argument("area")
    p.add_argument("--seg", action="append", default=[], help="sub-segment (repeatable)")

    p = add("scout", cmd_scout, "propose candidate domains from what's trending")
    p.add_argument("--brief", help="founder context")
    p.add_argument("--avoid", action="append", default=[], help="space to exclude (repeatable)")

    p = add("intake", cmd_intake, "preflight clarifying questions for a domain")
    p.add_argument("domain")
    p.add_argument("--brief", help="context to sharpen the questions")

    p = add("sort-research", cmd_sort_research,
            "sort a raw research paste into a launchable job (stdin or --file)")
    p.add_argument("--file", help="read the paste from a file instead of stdin")

    p = add("create", cmd_create, "create (and start) an autonomous exploration")
    p.add_argument("domain")
    p.add_argument("--seg", action="append", default=[], help="sub-segment (repeatable)")
    p.add_argument("--brief", help="steering brief")
    p.add_argument("--max-nodes", type=int)
    p.add_argument("--max-tokens", type=int)
    p.add_argument("--pace", choices=["eco", "balanced", "sprint"])
    p.add_argument("--no-autostart", action="store_true", help="create paused")

    add("projects", cmd_projects, "list every project (newest first)")

    p = add("show", cmd_show, "one project's metadata + stats")
    p.add_argument("pid")

    p = add("tree", cmd_tree, "the project's exploration tree")
    p.add_argument("pid")
    p.add_argument("--depth", type=int, help="max depth to render")
    p.add_argument("--starred", action="store_true", help="starred nodes only (--json)")
    p.add_argument("--min-viability", type=int, help="viability floor (--json)")

    p = add("gaps", cmd_gaps, "top scored gaps of a project")
    p.add_argument("pid")
    p.add_argument("--limit", type=int, default=10)

    p = add("pause", _simple_control("pause"), "pause a running exploration")
    p.add_argument("pid")
    p = add("resume", _simple_control("resume"), "resume a paused exploration")
    p.add_argument("pid")
    p = add("continue", _simple_control("continue_milestone"),
            "acknowledge a milestone check-in")
    p.add_argument("pid")

    p = add("control", cmd_control, "any control action (pin/triage/stage/watch/…)")
    p.add_argument("pid")
    p.add_argument("action", choices=[
        "pause", "resume", "continue_milestone", "set_budget", "set_pace",
        "pin_node", "unpin_node", "set_triage", "set_stage",
        "watch_node", "unwatch_node", "continue_deepening",
    ])
    p.add_argument("--node", help="node id (node-scoped actions)")
    p.add_argument("--pace", choices=["eco", "balanced", "sprint"])
    p.add_argument("--triage", choices=["interested", "passed"])
    p.add_argument("--triage-reason", default="")
    p.add_argument("--stage")
    p.add_argument("--learnings", default="")
    p.add_argument("--max-tokens", type=int)
    p.add_argument("--max-nodes", type=int)

    p = add("delete", cmd_delete, "stop and forget a project")
    p.add_argument("pid")
    p.add_argument("--yes", action="store_true", help="skip the confirmation prompt")

    p = add("rerun", cmd_rerun, "clone a project into a fresh linked run")
    p.add_argument("pid")
    p.add_argument("--autostart", action="store_true")

    p = add("diff", cmd_diff, "diff two runs' scored gaps")
    p.add_argument("pid")
    p.add_argument("against", help="baseline project id")

    p = add("events", cmd_events, "tail the live SSE event stream")
    p.add_argument("pid")
    p.add_argument("--after", type=int, help="replay events after this seq")

    p = add("pack", cmd_pack, "a gap's markdown research pack")
    p.add_argument("pid")
    p.add_argument("nid")
    p.add_argument("--refresh", action="store_true", help="regenerate, bypass cache")

    add("portfolio", cmd_portfolio, "every scored gap across every project")

    p = add("graveyard", cmd_graveyard, "rejected gaps + post-mortem corpus")
    p.add_argument("--query", "-q", help="text filter")
    p.add_argument("--limit", type=int, default=50)

    p = add("watch", cmd_watch, "watched nodes and their latest alerts")
    p.add_argument("--sweep", action="store_true", help="run a manual sweep now")

    p = add("prefs", cmd_prefs, "learned preferences (show/distill/confirm/dismiss)")
    p.add_argument("prefs_action", nargs="?", default="show",
                   choices=["show", "distill", "confirm", "dismiss"])
    p.add_argument("--text", help="override text when confirming")

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    client = GapFinderClient(args.url)
    try:
        result = args.handler(client, args)
    except ApiError as exc:
        print(f"error: {exc.detail}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    if result is not None:
        _dump(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
