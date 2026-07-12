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
    try:
        result = await client.complete(
            _transcript(req.messages),
            system=_SYSTEM,
            tools=_build_tools(actions),
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
