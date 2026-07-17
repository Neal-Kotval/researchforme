"""The library — projects as real directories, documents as real markdown files.

Phase 5 W-1/W-2 (see ``docs/strategy/phase5-workbench.md``). The engine already
has a CLI and an MCP server; if a project is a folder of ``.md`` files rather
than rows in SQLite, then Claude Code, Cursor, git, any editor, and the user's
own agents can all read and write it. The app is a *view* onto files the user
owns — never their jailer.

    ~/researchforme/                  # the library root (RESEARCHFORME_DIR)
      kernel-ci/                      # a project = a directory
        project.md                    # frontmatter + thesis + plan
        ideas/
          serving-config-compiler.md  # one imported gap, red team intact
        research/
        notes.md

Security. A local file API's one real attack surface is **path traversal**, so
every path is resolved (symlinks included) and must land under the library root
or it is rejected — see :func:`safe_path`. Nothing here trusts a caller-supplied
path.

Honesty. Files edited outside the app are first-class: reads always hit the
disk, and writes carry an mtime precondition so a concurrent external edit
surfaces as a conflict instead of being silently clobbered.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Documents are markdown. Anything else is not ours to render or write.
DOC_SUFFIXES = (".md", ".markdown")

# Directories a fresh project gets. Empty dirs are cheap and tell the user where
# things go — an empty `ideas/` is a better affordance than a blank folder.
PROJECT_DIRS = ("ideas", "research")


class LibraryError(RuntimeError):
    """A bad request against the library (unsafe path, missing project, …)."""


class ConflictError(LibraryError):
    """The file changed on disk since the caller last read it."""


# NOT ``~/researchforme``: that is this app's own source repo on the author's
# machine, and pointing the library at a code checkout would list `backend/` and
# `frontend/` as "projects" and write documents into the source tree. The library
# is the user's *writing*, and it gets its own directory.
DEFAULT_ROOT = "~/researchforme-library"


def library_root() -> Path:
    """The library directory. ``RESEARCHFORME_DIR`` overrides the default."""
    raw = os.getenv("RESEARCHFORME_DIR", DEFAULT_ROOT)
    return Path(raw).expanduser()


def ensure_root() -> Path:
    """Create (if needed) and validate the library root.

    Guard: a directory containing ``.git`` is a code repository, not a document
    library. Writing projects into one would scatter markdown through someone's
    source tree and list their packages as "projects". Refuse loudly and tell the
    user how to point us somewhere sane — this is cheap to check and expensive to
    get wrong.
    """
    root = library_root()
    if (root / ".git").exists():
        raise LibraryError(
            f"{root} looks like a code repository (it contains .git), not a document "
            "library. Set RESEARCHFORME_DIR to a directory for your projects."
        )
    root.mkdir(parents=True, exist_ok=True)
    return root


def slugify(name: str) -> str:
    """A filesystem-safe, human-readable directory/file name.

    Deliberately conservative: lowercase, ASCII-ish, hyphen-separated. A slug is
    a *file name the user will see and type*, not an opaque id.
    """
    s = (name or "").strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    s = re.sub(r"-{2,}", "-", s)
    return s[:60] or "untitled"


def safe_path(root: Path, *parts: str) -> Path:
    """Resolve ``parts`` under ``root``, or raise.

    This is the security boundary. ``..``, absolute paths, and symlinks that
    point outside the root are all rejected — we compare the *fully resolved*
    path against the *fully resolved* root, so a symlink cannot smuggle a write
    out of the library.
    """
    root_resolved = root.resolve()
    candidate = root_resolved
    for part in parts:
        if part is None:
            raise LibraryError("Empty path segment.")
        p = str(part).strip()
        if not p:
            raise LibraryError("Empty path segment.")
        if Path(p).is_absolute() or p.startswith("~"):
            raise LibraryError(f"Absolute paths are not allowed: {p!r}")
        candidate = candidate / p

    resolved = candidate.resolve()
    # `is_relative_to` is the whole check: after resolution (symlinks included)
    # the target must still live inside the library.
    if resolved != root_resolved and not resolved.is_relative_to(root_resolved):
        raise LibraryError("Path escapes the library root.")
    return resolved


# --------------------------------------------------------------------------- #
# Frontmatter                                                                 #
# --------------------------------------------------------------------------- #
_FM_RE = re.compile(r"\A---\n(.*?)\n---\n?", re.DOTALL)


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Split ``---`` YAML-ish frontmatter from the body.

    Intentionally a flat ``key: value`` parser rather than a YAML dependency:
    the frontmatter we write is flat, and a hand-edited file must never fail to
    open because of an exotic YAML construct. Unparseable lines are skipped.
    """
    m = _FM_RE.match(text or "")
    if not m:
        return {}, text or ""
    meta: dict = {}
    for line in m.group(1).splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, sep, value = line.partition(":")
        if not sep:
            continue
        v = value.strip().strip('"').strip("'")
        if v in ("true", "false"):
            meta[key.strip()] = v == "true"
        else:
            meta[key.strip()] = v
    return meta, text[m.end():]


def render_frontmatter(meta: dict) -> str:
    if not meta:
        return ""
    lines = ["---"]
    for k, v in meta.items():
        if isinstance(v, bool):
            lines.append(f"{k}: {'true' if v else 'false'}")
        else:
            lines.append(f"{k}: {v}")
    lines.append("---")
    return "\n".join(lines) + "\n"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------------------- #
# Models (plain dataclasses — these mirror the disk, they are not the truth)   #
# --------------------------------------------------------------------------- #
@dataclass
class DocInfo:
    path: str                 # project-relative, e.g. "ideas/kernel-ci.md"
    title: str
    updated_at: str
    size: int
    # Classification for the dashboard + hierarchy view. Derived from path and
    # frontmatter, so the frontend doesn't have to fetch and parse every doc to
    # know what kind of thing it is.
    kind: str = "doc"         # plan | consolidation | idea | research | doc
    developed: bool = False   # an idea worked by the strong model, not raw import


def _doc_kind(relpath: str, meta: dict) -> str:
    """What sort of document this is, for grouping and iconography."""
    src = str(meta.get("source") or "")
    if relpath == "project.md" or src == "gapfinder-plan":
        return "plan"
    if relpath == "critique.md" or src == "gapfinder-critique":
        return "critique"
    if relpath == "consolidation.md" or src == "gapfinder-consolidate":
        return "consolidation"
    if relpath.startswith("ideas/") or src == "gapfinder":
        return "idea"
    if relpath.startswith("research/"):
        return "research"
    return "doc"


@dataclass
class ProjectInfo:
    slug: str
    title: str
    created_at: str
    updated_at: str
    doc_count: int
    idea_count: int
    status: str = "exploring"
    # The project-level critique score, read from critique.md frontmatter if a
    # red team has been run. None = not yet validated. Lets the library grid and
    # dashboard show and compare projects by how well they survived scrutiny.
    viability: int | None = None
    confidence: str = ""
    verdict: str = ""
    # True when the plan was revised AFTER the critique was written, so the score
    # describes a plan that no longer exists — the UI invites a re-validation
    # rather than showing a stale number as current.
    validation_stale: bool = False


def _doc_title(path: Path, text: str) -> str:
    """A document's display name: frontmatter title → first `# heading` → name."""
    meta, body = parse_frontmatter(text)
    if meta.get("title"):
        return str(meta["title"])
    for line in body.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return path.stem


def _iter_docs(pdir: Path):
    for p in sorted(pdir.rglob("*")):
        if p.is_file() and p.suffix.lower() in DOC_SUFFIXES:
            yield p


# --------------------------------------------------------------------------- #
# Projects                                                                     #
# --------------------------------------------------------------------------- #
def list_projects() -> list[ProjectInfo]:
    root = ensure_root()
    out: list[ProjectInfo] = []
    for pdir in sorted(root.iterdir()):
        if not pdir.is_dir() or pdir.name.startswith("."):
            continue
        try:
            out.append(_project_info(pdir))
        except Exception:  # noqa: BLE001 - one bad dir must not sink the library.
            continue
    out.sort(key=lambda p: p.updated_at, reverse=True)
    return out


def _project_info(pdir: Path) -> ProjectInfo:
    docs = list(_iter_docs(pdir))
    title = pdir.name
    created = ""
    main = pdir / "project.md"
    if main.exists():
        meta, _ = parse_frontmatter(main.read_text(encoding="utf-8", errors="replace"))
        title = str(meta.get("title") or _doc_title(main, main.read_text(encoding="utf-8", errors="replace")))
        created = str(meta.get("created_at") or "")
        status = str(meta.get("status") or "exploring")
    else:
        status = "exploring"
    mtimes = [p.stat().st_mtime for p in docs] or [pdir.stat().st_mtime]
    updated = datetime.fromtimestamp(max(mtimes), tz=timezone.utc).isoformat(timespec="seconds")

    # Pull the critique score from critique.md's frontmatter, if it's been run.
    viability: int | None = None
    confidence = ""
    verdict = ""
    validation_stale = False
    crit = pdir / "critique.md"
    if crit.exists():
        cmeta, _ = parse_frontmatter(crit.read_text(encoding="utf-8", errors="replace"))
        try:
            viability = int(cmeta["viability"]) if cmeta.get("viability") is not None else None
        except (TypeError, ValueError):
            viability = None
        confidence = str(cmeta.get("confidence") or "")
        verdict = str(cmeta.get("verdict") or "")
        # Stale when the plan was rewritten after the critique — the score then
        # describes a plan that no longer exists.
        if main.exists() and main.stat().st_mtime > crit.stat().st_mtime + 1:
            validation_stale = True

    return ProjectInfo(
        slug=pdir.name,
        title=title,
        created_at=created or updated,
        updated_at=updated,
        doc_count=len(docs),
        idea_count=sum(1 for p in docs if p.parent.name == "ideas"),
        status=status,
        viability=viability,
        confidence=confidence,
        verdict=verdict,
        validation_stale=validation_stale,
    )


def delete_project(slug: str) -> None:
    """Remove a project directory and everything in it. Irreversible.

    Guarded by ``safe_path`` (must resolve under the library root) and a
    directory check, so a traversal or a stray file can't be used to delete
    anything outside a project folder. These are the user's files — the app can
    delete a project it can see, and nothing else.
    """
    import shutil

    pdir = safe_path(ensure_root(), slug)
    if not pdir.is_dir():
        raise LibraryError(f"No such project: {slug!r}")
    if pdir.resolve().parent != ensure_root().resolve():
        # A project is always a direct child of the root; refuse anything else.
        raise LibraryError("Refusing to delete a path that is not a project folder.")
    shutil.rmtree(pdir)


def get_project(slug: str) -> ProjectInfo:
    pdir = safe_path(ensure_root(), slug)
    if not pdir.is_dir():
        raise LibraryError(f"No such project: {slug!r}")
    return _project_info(pdir)


def create_project(title: str, thesis: str = "") -> ProjectInfo:
    """Create a project directory + its `project.md`. Idempotent on the slug."""
    root = ensure_root()
    slug = slugify(title)
    pdir = safe_path(root, slug)
    if pdir.exists():
        # Don't clobber an existing project — hand back a fresh, unique slug.
        n = 2
        while safe_path(root, f"{slug}-{n}").exists():
            n += 1
        slug = f"{slug}-{n}"
        pdir = safe_path(root, slug)

    pdir.mkdir(parents=True)
    for d in PROJECT_DIRS:
        (pdir / d).mkdir(exist_ok=True)

    meta = {"title": title.strip() or slug, "status": "exploring", "created_at": _now()}
    body = [f"# {title.strip() or slug}", ""]
    if thesis.strip():
        body += ["## Thesis", thesis.strip(), ""]
    body += [
        "## Plan",
        # `*emphasis*`, not `_emphasis_`: the app's markdown renderer supports the
        # former, and a template that renders as literal underscores looks broken.
        "*What would have to be true for this to work, and what will you do first?*",
        "",
        "## Open questions",
        "",
    ]
    (pdir / "project.md").write_text(
        render_frontmatter(meta) + "\n".join(body) + "\n", encoding="utf-8"
    )
    return _project_info(pdir)


# --------------------------------------------------------------------------- #
# Documents                                                                    #
# --------------------------------------------------------------------------- #
def list_docs(slug: str) -> list[DocInfo]:
    pdir = safe_path(ensure_root(), slug)
    if not pdir.is_dir():
        raise LibraryError(f"No such project: {slug!r}")
    out: list[DocInfo] = []
    for p in _iter_docs(pdir):
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        st = p.stat()
        rel = str(p.relative_to(pdir))
        meta, _ = parse_frontmatter(text)
        out.append(
            DocInfo(
                path=rel,
                title=_doc_title(p, text),
                updated_at=datetime.fromtimestamp(st.st_mtime, tz=timezone.utc)
                .isoformat(timespec="seconds"),
                size=st.st_size,
                kind=_doc_kind(rel, meta),
                developed=bool(meta.get("developed")),
            )
        )
    # Reading order: the plan spine, then the critique (the red team on the whole
    # bet), the consolidation, ideas, then everything else — alphabetical within.
    _kind_order = {
        "plan": 0, "critique": 1, "consolidation": 2, "idea": 3, "research": 4, "doc": 5
    }
    out.sort(key=lambda d: (_kind_order.get(d.kind, 9), d.path))
    return out


def read_doc(slug: str, relpath: str) -> tuple[str, float]:
    """Return ``(text, mtime)``. The mtime is the write precondition."""
    pdir = safe_path(ensure_root(), slug)
    p = safe_path(pdir, *Path(relpath).parts)
    if p.suffix.lower() not in DOC_SUFFIXES:
        raise LibraryError("Only markdown documents can be read.")
    if not p.is_file():
        raise LibraryError(f"No such document: {relpath!r}")
    return p.read_text(encoding="utf-8", errors="replace"), p.stat().st_mtime


def bundle_project(slug: str) -> tuple[str, int]:
    """Concatenate every doc into one portable markdown blob → (text, doc_count).

    Reading order matches ``list_docs``: ``project.md`` (the spine) first, then
    the rest alphabetically, so the blob reads top-down like the plan it is. Each
    document is separated by a ``---`` rule and its path as an H1 anchor, so a
    reader (or a chat assistant it's pasted into) can see the seams. Frontmatter
    is stripped from each doc: it's machine bookkeeping, and five copies of
    ``status: exploring`` in one paste is noise.
    """
    pinfo = get_project(slug)
    docs = list_docs(slug)
    parts = [f"# {pinfo.title}", ""]
    if pinfo.status:
        parts.append(f"_Status: {pinfo.status} · {len(docs)} documents_")
        parts.append("")
    for d in docs:
        text, _ = read_doc(slug, d.path)
        _, body = parse_frontmatter(text)
        parts.append("---")
        parts.append(f"# 📄 {d.path}")
        parts.append("")
        parts.append(body.strip())
        parts.append("")
    return "\n".join(parts).strip() + "\n", len(docs)


def write_doc(
    slug: str, relpath: str, content: str, *, base_mtime: Optional[float] = None
) -> DocInfo:
    """Write a document, refusing to clobber a concurrent external edit.

    ``base_mtime`` is the mtime the caller last read. If the file has moved on
    since, we raise :class:`ConflictError` rather than silently overwrite — the
    user may have been editing it in vim, and their work is not ours to destroy.
    """
    pdir = safe_path(ensure_root(), slug)
    if not pdir.is_dir():
        raise LibraryError(f"No such project: {slug!r}")
    p = safe_path(pdir, *Path(relpath).parts)
    if p.suffix.lower() not in DOC_SUFFIXES:
        raise LibraryError("Only markdown documents can be written.")

    if base_mtime is not None and p.exists():
        current = p.stat().st_mtime
        # Sub-second filesystem timestamps make exact equality brittle.
        if abs(current - base_mtime) > 1e-6 and current > base_mtime:
            raise ConflictError(
                f"{relpath} changed on disk since you opened it — "
                "reload to merge, or your edit would overwrite that change."
            )

    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    st = p.stat()
    return DocInfo(
        path=str(p.relative_to(pdir)),
        title=_doc_title(p, content),
        updated_at=datetime.fromtimestamp(st.st_mtime, tz=timezone.utc)
        .isoformat(timespec="seconds"),
        size=st.st_size,
    )


# A project's lifecycle. Deliberately small and linear — a project is a living
# thing that moves from a hunch to a decision, and a founder should see where it is.
PROJECT_STATUSES = ("exploring", "validating", "committed", "shelved")


def set_project_status(slug: str, status: str) -> ProjectInfo:
    """Update a project's status in its project.md frontmatter (task #10)."""
    if status not in PROJECT_STATUSES:
        raise LibraryError(
            f"Unknown status {status!r} — one of {', '.join(PROJECT_STATUSES)}."
        )
    pdir = safe_path(ensure_root(), slug)
    main = pdir / "project.md"
    if not main.is_file():
        raise LibraryError(f"No such project: {slug!r}")
    text = main.read_text(encoding="utf-8", errors="replace")
    meta, body = parse_frontmatter(text)
    meta["status"] = status
    meta.setdefault("title", pdir.name)
    main.write_text(render_frontmatter(meta) + body, encoding="utf-8")
    return _project_info(pdir)


def read_ideas(slug: str) -> list[tuple[str, str]]:
    """Every idea document in a project as ``(title, body-without-frontmatter)``.

    Backs consolidation (W-4): the model reads the ideas as the user sees them,
    not their frontmatter plumbing.
    """
    pdir = safe_path(ensure_root(), slug)
    if not pdir.is_dir():
        raise LibraryError(f"No such project: {slug!r}")
    ideas_dir = pdir / "ideas"
    out: list[tuple[str, str]] = []
    if not ideas_dir.is_dir():
        return out
    for p in sorted(ideas_dir.glob("*.md")):
        text = p.read_text(encoding="utf-8", errors="replace")
        meta, body = parse_frontmatter(text)
        title = str(meta.get("title") or _doc_title(p, text))
        out.append((title, body.strip()))
    return out


def write_idea(slug: str, title: str, markdown: str, *, developed: bool) -> DocInfo:
    """Write one imported gap into the project's ``ideas/`` folder (W-3).

    ``developed`` is recorded in frontmatter so a reader can always tell whether
    an LLM worked the idea or it is the raw export — an honest flag beats a doc
    that merely *reads* polished.
    """
    idea_slug = slugify(title)
    meta = {
        "title": title.strip() or idea_slug,
        "source": "gapfinder",
        "developed": developed,
        "imported_at": _now(),
    }
    body = markdown if markdown.endswith("\n") else markdown + "\n"
    return write_doc(slug, f"ideas/{idea_slug}.md", render_frontmatter(meta) + body)
