"""The Assistant control surface (POST /api/assistant/chat).

One-shot conversational control of the whole platform: each request runs a
single agent-SDK query whose in-process MCP tools ARE the platform's tool
layer (start runs, pause/resume, list runs, rank survivors, read usage). The
response carries the final prose plus every tool call that actually ran, so
the UI can render honest "✓ ran" command blocks instead of pretending.

The router owns no exploration logic — every tool handler delegates to the
same ExplorerService / TreeStore / UsageGovernor the REST routes use.
"""

from __future__ import annotations

import json
import logging
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..autonomous.governor import get_governor
from ..autonomous.schemas import Budget, CreateProjectRequest, SteeringContext
from ..autonomous.service import get_service
from ..autonomous.store import get_store
from ..llm.client import ToolSpec, get_client

logger = logging.getLogger("gapfinder.api")

router = APIRouter(tags=["assistant"])


# --------------------------------------------------------------------------- #
# Wire models                                                                 #
# --------------------------------------------------------------------------- #
class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    text: str = Field(max_length=8000)


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1, max_length=40)
    # When set, the chat is scoped to a library project: its documents enter the
    # context and the assistant gains doc.* tools to read and write them (W-6).
    project_slug: Optional[str] = None


class ChatAction(BaseModel):
    """One tool call that actually ran, for the UI's command blocks."""

    tool: str    # dotted display form, e.g. "gap.explore.start"
    args: dict
    result: str  # compact JSON/text the tool returned (what the model saw)


class ChatResponse(BaseModel):
    reply: str
    actions: list[ChatAction]
    backend: str  # which LLM backend actually served the turn


# --------------------------------------------------------------------------- #
# The tool layer                                                              #
# --------------------------------------------------------------------------- #
_MAX_RESULT_CHARS = 4000  # keep tool output model- and wire-friendly


def _compact(data: object) -> str:
    out = json.dumps(data, ensure_ascii=False, default=str)
    return out[: _MAX_RESULT_CHARS - 1] + "…" if len(out) > _MAX_RESULT_CHARS else out


def _project_row(p) -> dict:  # noqa: ANN001 - pydantic Project
    return {
        "id": p.id,
        "domain": p.domain,
        "status": str(getattr(p.status, "value", p.status)),
        "nodes": p.stats.nodes,
        "gaps": p.stats.gaps,
        "starred": p.stats.stars,
        "top_viability": p.stats.max_viability,
        "tokens_spent": p.stats.tokens_spent,
    }


def _shortlist_rows() -> list[dict]:
    """Every pressure-tested gap across projects, ranked by fit × viability."""
    store = get_store()
    rows: list[dict] = []
    for p in store.list_projects():
        snap = store.snapshot(p.id)
        if snap is None:
            continue
        for n in snap.nodes:
            pt = n.pressure_test
            if n.kind not in ("gap", "gap_candidate") or pt is None or not pt.lenses:
                continue
            viab = n.viability or 0
            score = viab * n.fit / 100 if n.fit is not None else viab
            rows.append(
                {
                    "project_id": p.id,
                    "node_id": n.id,
                    "title": (n.gap.title if n.gap else None) or n.title,
                    "domain": p.domain,
                    "viability": n.viability,
                    "fit": n.fit,
                    "confidence": n.confidence,
                    "red_team": f"{pt.survived}/{len(pt.lenses)}",
                    "rigor": pt.test_rigor,
                    "_score": score,
                }
            )
    rows.sort(key=lambda r: r["_score"], reverse=True)
    for r in rows:
        r.pop("_score")
    return rows[:12]


def _build_tools(actions: list[ChatAction]) -> list[ToolSpec]:
    """The platform tool layer, with every call recorded into ``actions``."""

    def _spec(name: str, display: str, description: str, schema: dict, fn) -> ToolSpec:  # noqa: ANN001
        async def handler(args: dict) -> str:
            try:
                out = _compact(await fn(args))
            except HTTPException as exc:
                out = _compact({"error": exc.detail})
            except Exception as exc:  # noqa: BLE001 - the model should see failures
                logger.exception("assistant tool %s failed", name)
                out = _compact({"error": f"{type(exc).__name__}: {exc}"})
            actions.append(ChatAction(tool=display, args=args, result=out))
            return out

        return ToolSpec(name=name, description=description, input_schema=schema, handler=handler)

    async def runs_list(_args: dict) -> list[dict]:
        return [_project_row(p) for p in get_store().list_projects()]

    async def explore_start(args: dict) -> dict:
        budget: Optional[Budget] = None
        if args.get("max_tokens") or args.get("max_nodes"):
            budget = Budget(
                max_tokens=args.get("max_tokens"),
                max_nodes=args.get("max_nodes") or 400,
            )
        steering = SteeringContext(brief=args["brief"]) if args.get("brief") else None
        p = get_service().create(
            CreateProjectRequest(
                domain=args["domain"],
                budget=budget,
                steering=steering,
                autostart=True,
            )
        )
        return {"project_id": p.id, "domain": p.domain, "status": "running"}

    async def run_pause(args: dict) -> dict:
        return _project_row(await get_service().pause(args["project_id"]))

    async def run_resume(args: dict) -> dict:
        return _project_row(await get_service().resume(args["project_id"]))

    async def shortlist(_args: dict) -> list[dict]:
        return _shortlist_rows()

    async def usage(_args: dict) -> dict:
        return get_governor().snapshot()

    pid_schema = {
        "type": "object",
        "properties": {"project_id": {"type": "string"}},
        "required": ["project_id"],
    }
    empty_schema = {"type": "object", "properties": {}}

    return [
        _spec(
            "runs_list", "gap.runs.list",
            "List every exploration run with its status and live stats.",
            empty_schema, runs_list,
        ),
        _spec(
            "explore_start", "gap.explore.start",
            "Start a new autonomous exploration of a market domain. Optional founder "
            "brief steers fit scoring; optional max_tokens/max_nodes cap the run.",
            {
                "type": "object",
                "properties": {
                    "domain": {"type": "string", "description": "The market space to hunt in."},
                    "brief": {"type": "string", "description": "Who the founder is (optional)."},
                    "max_tokens": {"type": "integer", "description": "Token cap (optional)."},
                    "max_nodes": {"type": "integer", "description": "Node cap (optional)."},
                },
                "required": ["domain"],
            },
            explore_start,
        ),
        _spec("run_pause", "gap.run.pause", "Pause a running exploration.", pid_schema, run_pause),
        _spec("run_resume", "gap.run.resume", "Resume a paused exploration.", pid_schema, run_resume),
        _spec(
            "shortlist", "gap.shortlist",
            "The pressure-tested ideas across all runs, ranked by fit × viability, "
            "with red-team survival and rigor per row.",
            empty_schema, shortlist,
        ),
        _spec("usage", "gap.usage", "Today's shared token usage and governor mode.", empty_schema, usage),
    ]


def _build_project_tools(slug: str, actions: list[ChatAction]) -> list[ToolSpec]:
    """doc.* tools scoped to one library project (W-6).

    These let the assistant read and edit the project's markdown — 'add what we
    learned to the plan' lands as a real diff in a file the user owns. Every path
    goes through the same library guard, so nothing here can write outside the
    project's directory.
    """
    from .. import library as lib

    def _spec(name: str, display: str, description: str, schema: dict, fn) -> ToolSpec:  # noqa: ANN001
        async def handler(args: dict) -> str:
            try:
                out = _compact(await fn(args))
            except lib.LibraryError as exc:
                out = _compact({"error": str(exc)})
            except Exception as exc:  # noqa: BLE001 - the model should see failures
                logger.exception("assistant doc tool %s failed", name)
                out = _compact({"error": f"{type(exc).__name__}: {exc}"})
            actions.append(ChatAction(tool=display, args=args, result=out))
            return out

        return ToolSpec(name=name, description=description, input_schema=schema, handler=handler)

    async def doc_list(_args: dict) -> list[dict]:
        return [{"path": d.path, "title": d.title} for d in lib.list_docs(slug)]

    async def doc_read(args: dict) -> dict:
        text, _mtime = lib.read_doc(slug, args["path"])
        _meta, body = lib.parse_frontmatter(text)
        return {"path": args["path"], "content": body.strip()}

    async def doc_write(args: dict) -> dict:
        # Force-write (no mtime precondition) — the assistant is acting on the
        # user's behalf in the same session, not racing an external editor.
        d = lib.write_doc(slug, args["path"], args["content"])
        return {"path": d.path, "title": d.title, "written": True}

    async def doc_append(args: dict) -> dict:
        # Append a section to an existing doc — the common 'add to the plan' move,
        # without making the model re-emit the whole file.
        try:
            existing, _ = lib.read_doc(slug, args["path"])
        except lib.LibraryError:
            existing = ""
        joined = existing.rstrip() + "\n\n" + args["content"].strip() + "\n" if existing else args["content"]
        d = lib.write_doc(slug, args["path"], joined)
        return {"path": d.path, "title": d.title, "appended": True}

    path_body = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Project-relative, e.g. 'project.md' or 'research/notes.md'. Must end in .md."},
            "content": {"type": "string"},
        },
        "required": ["path", "content"],
    }
    return [
        _spec("doc_list", "doc.list", "List this project's markdown documents.",
              {"type": "object", "properties": {}}, doc_list),
        _spec("doc_read", "doc.read", "Read one document's content (frontmatter stripped).",
              {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}, doc_read),
        _spec("doc_write", "doc.write",
              "Create or overwrite a markdown document in this project. Use for a new "
              "doc or a full rewrite; prefer doc.append to add to an existing one.",
              path_body, doc_write),
        _spec("doc_append", "doc.append",
              "Append a section to an existing document (creates it if missing). The "
              "usual way to 'add this to the plan'.",
              path_body, doc_append),
    ]


# --------------------------------------------------------------------------- #
# The endpoint                                                                #
# --------------------------------------------------------------------------- #
_SYSTEM = """You are the Gap Finder assistant — the conversational control \
surface of an autonomous market-gap research platform. The platform hunts for \
startup ideas, red-teams them through adversarial lenses, and ranks survivors \
by fit × viability.

Rules:
- Use your gap.* tools for every fact and every action. Never invent runs, \
scores, or ideas — if a tool returned it, you may say it.
- Be brief and plain: a couple of sentences, then the point. No headers.
- After starting a run, tell the founder it runs autonomously and where to \
watch it (the Explore screen). Quote budgets in tokens, not dollars.
- When asked to compare or recommend, call gap.shortlist first and lead with \
the top row, mentioning its red-team record.
- If a tool errors, say so plainly and suggest the working path."""


def _transcript(messages: list[ChatMessage]) -> str:
    turns = [f"{'Founder' if m.role == 'user' else 'Assistant'}: {m.text}" for m in messages]
    return "Conversation so far:\n" + "\n".join(turns) + "\n\nRespond to the founder's last message."


@router.post("/assistant/chat", response_model=ChatResponse)
async def assistant_chat(req: ChatRequest) -> ChatResponse:
    """One conversational turn. Runs a single agent query over the tool layer."""
    client = get_client()
    if client.backend == "fixture":
        return ChatResponse(
            reply=(
                "The assistant needs a live LLM backend and this server is running "
                "on canned fixtures. Start the backend with the agent-sdk or cli "
                "backend to chat."
            ),
            actions=[],
            backend="fixture",
        )
    actions: list[ChatAction] = []
    system = _SYSTEM
    tools = _build_tools(actions)

    # Project-scoped chat (W-6): add doc.* tools + the project's own context so
    # "add what we learned to the plan" works against a real file.
    if req.project_slug:
        from .. import library as lib

        try:
            project = lib.get_project(req.project_slug)
            docs = lib.list_docs(req.project_slug)
            plan_text, _ = lib.read_doc(req.project_slug, "project.md")
        except lib.LibraryError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        tools = tools + _build_project_tools(req.project_slug, actions)
        doc_list = "\n".join(f"- {d.path}: {d.title}" for d in docs)
        system += (
            f"\n\n# CURRENT PROJECT: {project.title} ({req.project_slug})\n"
            f"You are inside a library project — a folder of markdown the founder owns. "
            f"Its documents:\n{doc_list}\n\n"
            f"Its project.md right now:\n---\n{plan_text.strip()[:3000]}\n---\n"
            f"Use doc.* tools to read and edit these. When the founder asks to record, "
            f"add, or change something, WRITE it with doc.append/doc.write — don't just "
            f"say it. Read a document before rewriting it. Keep the founder's own words; "
            f"never launder the red team out of an idea."
        )

    try:
        result = await client.complete(
            _transcript(req.messages),
            system=system,
            tools=tools,
            max_turns=12,
            timeout=180,
        )
    except Exception as exc:  # noqa: BLE001 - clean 502, no stack trace
        logger.exception("assistant_chat failed")
        raise HTTPException(
            status_code=502,
            detail=f"The assistant could not reach the model: {type(exc).__name__}.",
        ) from exc
    return ChatResponse(reply=result.text, actions=actions, backend=result.backend)
