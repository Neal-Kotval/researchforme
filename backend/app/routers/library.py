"""HTTP surface for the library — projects as folders, documents as markdown.

Phase 5 W-2/W-3. Thin wrappers over :mod:`app.library` (filesystem) plus the
idea-import path, which is the join between the engine and the workbench: a
starred gap becomes ``ideas/<slug>.md`` inside a project the user owns.

Every path goes through ``library.safe_path``; a traversal attempt is a clean
400, never a stack trace and never a write outside the root.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from .. import library as lib

logger = logging.getLogger("gapfinder.library")

router = APIRouter(tags=["library"])


# --------------------------------------------------------------------------- #
# Wire models                                                                  #
# --------------------------------------------------------------------------- #
class ProjectOut(BaseModel):
    slug: str
    title: str
    created_at: str
    updated_at: str
    doc_count: int
    idea_count: int


class DocOut(BaseModel):
    path: str
    title: str
    updated_at: str
    size: int


class DocContent(BaseModel):
    path: str
    title: str
    content: str
    mtime: float


class CreateProjectBody(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    thesis: str = ""


class WriteDocBody(BaseModel):
    path: str = Field(min_length=1, max_length=200)
    content: str
    # The mtime the client last read. Omit to force-write (last-writer-wins).
    base_mtime: Optional[float] = None


class ImportIdea(BaseModel):
    """One gap to import. ``markdown`` is the deterministic client-side export —
    the fallback the developed pass degrades to, so an import can never fail."""

    project_id: str          # the EXPLORATION the gap came from
    node_id: str
    title: str
    markdown: str


class ImportBody(BaseModel):
    ideas: list[ImportIdea] = Field(min_length=1, max_length=25)
    # Opt-in: one strong-model pass per idea. Never implicit — it costs tokens.
    develop: bool = False
    # Create a new project from these ideas instead of adding to an existing one.
    new_project_title: str = ""


class ImportedIdea(BaseModel):
    path: str
    title: str
    developed: bool
    # Why a requested development pass didn't happen — surfaced, never swallowed.
    note: str = ""


class ImportResult(BaseModel):
    project: ProjectOut
    imported: list[ImportedIdea]


def _project_out(p: lib.ProjectInfo) -> ProjectOut:
    return ProjectOut(**p.__dict__)


def _doc_out(d: lib.DocInfo) -> DocOut:
    return DocOut(**d.__dict__)


def _handle(exc: Exception) -> HTTPException:
    if isinstance(exc, lib.ConflictError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, lib.LibraryError):
        return HTTPException(status_code=400, detail=str(exc))
    logger.exception("library operation failed")
    return HTTPException(status_code=500, detail=f"Library error: {type(exc).__name__}.")


# --------------------------------------------------------------------------- #
# Projects                                                                     #
# --------------------------------------------------------------------------- #
@router.get("/library/root")
def get_root() -> dict:
    """Where the library lives on disk — shown in the UI so it is never a mystery."""
    return {"root": str(lib.library_root())}


@router.get("/library/projects", response_model=list[ProjectOut])
def list_library_projects() -> list[ProjectOut]:
    try:
        return [_project_out(p) for p in lib.list_projects()]
    except Exception as exc:  # noqa: BLE001
        raise _handle(exc) from exc


@router.post("/library/projects", response_model=ProjectOut)
def create_library_project(body: CreateProjectBody) -> ProjectOut:
    try:
        return _project_out(lib.create_project(body.title, body.thesis))
    except Exception as exc:  # noqa: BLE001
        raise _handle(exc) from exc


@router.get("/library/projects/{slug}", response_model=ProjectOut)
def get_library_project(slug: str) -> ProjectOut:
    try:
        return _project_out(lib.get_project(slug))
    except Exception as exc:  # noqa: BLE001
        raise _handle(exc) from exc


# --------------------------------------------------------------------------- #
# Documents                                                                    #
# --------------------------------------------------------------------------- #
@router.get("/library/projects/{slug}/docs", response_model=list[DocOut])
def list_library_docs(slug: str) -> list[DocOut]:
    try:
        return [_doc_out(d) for d in lib.list_docs(slug)]
    except Exception as exc:  # noqa: BLE001
        raise _handle(exc) from exc


@router.get("/library/projects/{slug}/doc", response_model=DocContent)
def read_library_doc(slug: str, path: str = Query(min_length=1)) -> DocContent:
    try:
        text, mtime = lib.read_doc(slug, path)
        meta, _ = lib.parse_frontmatter(text)
        return DocContent(
            path=path,
            title=str(meta.get("title") or path),
            content=text,
            mtime=mtime,
        )
    except Exception as exc:  # noqa: BLE001
        raise _handle(exc) from exc


@router.put("/library/projects/{slug}/doc", response_model=DocOut)
def write_library_doc(slug: str, body: WriteDocBody) -> DocOut:
    """Write a document. A concurrent external edit is a 409, never a clobber."""
    try:
        return _doc_out(
            lib.write_doc(slug, body.path, body.content, base_mtime=body.base_mtime)
        )
    except Exception as exc:  # noqa: BLE001
        raise _handle(exc) from exc


# --------------------------------------------------------------------------- #
# Import (W-3) — the join between the engine and the workbench                 #
# --------------------------------------------------------------------------- #
@router.post("/library/import", response_model=ImportResult)
async def import_ideas(body: ImportBody) -> ImportResult:
    """Write starred gaps into a project's ``ideas/`` folder.

    ``develop: true`` runs ONE strong-model pass per idea that takes it further
    than the engine's output. It degrades honestly: if no backend can produce a
    real development, the raw deterministic export is written instead, with
    ``developed: false`` in the frontmatter and the reason surfaced in ``note``.
    An import never fails because the LLM was unavailable.
    """
    from ..autonomous.develop import DevelopUnavailable, develop_idea
    from ..autonomous.intake import steering_context_block
    from ..autonomous.store import get_store
    from ..config import get_settings
    from ..llm.client import get_client

    try:
        if body.new_project_title.strip():
            project = lib.create_project(body.new_project_title.strip())
        else:
            raise HTTPException(
                status_code=400,
                detail="Supply new_project_title (adding to an existing project "
                       "uses POST /library/projects/{slug}/import).",
            )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _handle(exc) from exc

    return await _do_import(project.slug, body, get_store(), get_client(),
                            get_settings(), develop_idea, DevelopUnavailable,
                            steering_context_block)


@router.post("/library/projects/{slug}/import", response_model=ImportResult)
async def import_ideas_into(slug: str, body: ImportBody) -> ImportResult:
    """Add starred gaps to an EXISTING project (a project holds many ideas)."""
    from ..autonomous.develop import DevelopUnavailable, develop_idea
    from ..autonomous.intake import steering_context_block
    from ..autonomous.store import get_store
    from ..config import get_settings
    from ..llm.client import get_client

    try:
        lib.get_project(slug)
    except Exception as exc:  # noqa: BLE001
        raise _handle(exc) from exc

    return await _do_import(slug, body, get_store(), get_client(), get_settings(),
                            develop_idea, DevelopUnavailable, steering_context_block)


async def _do_import(
    slug: str, body: ImportBody, store, client, settings,
    develop_idea, DevelopUnavailable, steering_context_block,  # noqa: N803
) -> ImportResult:
    imported: list[ImportedIdea] = []

    for idea in body.ideas:
        markdown = idea.markdown
        developed = False
        note = ""

        if body.develop:
            node = store.get_node(idea.node_id)
            if node is None:
                note = "Could not load the gap from the store — exported the raw idea."
            else:
                project = store.get_project(idea.project_id)
                steering = steering_context_block(project) if project else ""
                try:
                    markdown = await develop_idea(
                        node, client, settings.llm_model, steering=steering
                    )
                    developed = True
                except DevelopUnavailable as exc:
                    note = str(exc)
                except Exception as exc:  # noqa: BLE001 - never lose the idea.
                    logger.exception("develop_idea failed for node=%r", idea.node_id)
                    note = f"Development pass failed ({type(exc).__name__}) — exported the raw idea."

        try:
            doc = lib.write_idea(slug, idea.title, markdown, developed=developed)
        except Exception as exc:  # noqa: BLE001
            raise _handle(exc) from exc

        imported.append(
            ImportedIdea(path=doc.path, title=doc.title, developed=developed, note=note)
        )

    return ImportResult(
        project=_project_out(lib.get_project(slug)), imported=imported
    )
