"""The library: projects as folders, documents as markdown (Phase 5 W-2/W-3).

The security-critical surface is path traversal — a local file API must never be
talked into reading or writing outside the library root. Those tests come first
and are the ones that matter.
"""
import os

import pytest

from app import library as lib


@pytest.fixture()
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("RESEARCHFORME_DIR", str(tmp_path / "researchforme"))
    return lib.ensure_root()


# --------------------------------------------------------------------------- #
# Path traversal — the security boundary                                       #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "evil",
    [
        "..",
        "../etc",
        "../../etc/passwd",
        "/etc/passwd",
        "~/.ssh/id_rsa",
        "foo/../../../etc",
    ],
)
def test_safe_path_rejects_traversal(root, evil):
    with pytest.raises(lib.LibraryError):
        lib.safe_path(root, *evil.split("/"))


def test_safe_path_rejects_a_symlink_escaping_the_root(root, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "escape").symlink_to(outside)
    # Resolution must see through the symlink, not just the literal path.
    with pytest.raises(lib.LibraryError):
        lib.safe_path(root, "escape", "secret.md")


def test_safe_path_allows_a_normal_nested_path(root):
    p = lib.safe_path(root, "proj", "ideas", "x.md")
    assert str(p).startswith(str(root.resolve()))


def test_read_doc_cannot_escape_via_relpath(root):
    lib.create_project("Proj")
    with pytest.raises(lib.LibraryError):
        lib.read_doc("proj", "../../../etc/passwd")


def test_write_doc_cannot_escape_via_relpath(root):
    lib.create_project("Proj")
    with pytest.raises(lib.LibraryError):
        lib.write_doc("proj", "../outside.md", "pwned")


def test_only_markdown_can_be_written(root):
    lib.create_project("Proj")
    with pytest.raises(lib.LibraryError):
        lib.write_doc("proj", "notes.sh", "#!/bin/sh\nrm -rf /")


# --------------------------------------------------------------------------- #
# Projects                                                                     #
# --------------------------------------------------------------------------- #
def test_create_project_makes_a_real_folder_with_project_md(root):
    p = lib.create_project("Kernel CI", thesis="Verify fused kernels.")
    assert p.slug == "kernel-ci"
    pdir = root / "kernel-ci"
    assert (pdir / "project.md").is_file()
    assert (pdir / "ideas").is_dir()
    text = (pdir / "project.md").read_text()
    assert "Verify fused kernels." in text
    meta, _ = lib.parse_frontmatter(text)
    assert meta["title"] == "Kernel CI"


def test_create_project_never_clobbers_an_existing_one(root):
    a = lib.create_project("Kernel CI")
    b = lib.create_project("Kernel CI")
    assert a.slug == "kernel-ci" and b.slug == "kernel-ci-2"


def test_list_projects_sees_a_folder_created_outside_the_app(root):
    """Files the user made by hand are first-class — the app is a view, not a jailer."""
    (root / "hand-made").mkdir()
    (root / "hand-made" / "project.md").write_text("# Made in vim\n")
    slugs = [p.slug for p in lib.list_projects()]
    assert "hand-made" in slugs


# --------------------------------------------------------------------------- #
# Documents                                                                    #
# --------------------------------------------------------------------------- #
def test_write_then_read_roundtrip(root):
    lib.create_project("Proj")
    lib.write_doc("proj", "notes.md", "# Notes\nhello")
    text, mtime = lib.read_doc("proj", "notes.md")
    assert "hello" in text and mtime > 0


def test_write_refuses_to_clobber_a_concurrent_external_edit(root):
    lib.create_project("Proj")
    lib.write_doc("proj", "notes.md", "v1")
    _, mtime = lib.read_doc("proj", "notes.md")

    # Someone edits it in vim while our editor had it open.
    path = root / "proj" / "notes.md"
    path.write_text("edited in vim")
    os.utime(path, (mtime + 10, mtime + 10))

    with pytest.raises(lib.ConflictError):
        lib.write_doc("proj", "notes.md", "v2 from the app", base_mtime=mtime)
    # The external edit survived.
    assert path.read_text() == "edited in vim"


def test_docs_list_puts_project_md_first(root):
    lib.create_project("Proj")
    lib.write_doc("proj", "zzz.md", "z")
    lib.write_doc("proj", "ideas/a.md", "a")
    assert lib.list_docs("proj")[0].path == "project.md"


def test_write_idea_lands_in_ideas_and_records_developed_flag(root):
    lib.create_project("Proj")
    doc = lib.write_idea("proj", "Kernel Equivalence Harness", "## Thesis\nx", developed=False)
    assert doc.path == "ideas/kernel-equivalence-harness.md"
    text, _ = lib.read_doc("proj", doc.path)
    meta, _ = lib.parse_frontmatter(text)
    # An honest flag beats a doc that merely reads polished.
    assert meta["developed"] is False
    assert meta["title"] == "Kernel Equivalence Harness"


def test_frontmatter_roundtrip(root):
    text = lib.render_frontmatter({"title": "T", "developed": True}) + "# Body\n"
    meta, body = lib.parse_frontmatter(text)
    assert meta == {"title": "T", "developed": True}
    assert body.strip() == "# Body"


def test_slugify_is_filesystem_safe():
    assert lib.slugify("The Kernel CI: correctness & perf!") == "the-kernel-ci-correctness-perf"
    assert lib.slugify("") == "untitled"
    assert "/" not in lib.slugify("a/b/../c")


# --------------------------------------------------------------------------- #
# The library must never sit on top of a code repository                       #
# --------------------------------------------------------------------------- #
def test_refuses_to_use_a_git_repo_as_the_library(tmp_path, monkeypatch):
    """Pointing the library at a checkout would list `backend/` as a "project"
    and write markdown into someone's source tree. Refuse, loudly."""
    repo = tmp_path / "some-repo"
    (repo / ".git").mkdir(parents=True)
    monkeypatch.setenv("RESEARCHFORME_DIR", str(repo))

    with pytest.raises(lib.LibraryError, match="code repository"):
        lib.ensure_root()


def test_default_root_is_not_the_apps_own_repo(monkeypatch):
    monkeypatch.delenv("RESEARCHFORME_DIR", raising=False)
    assert lib.library_root().name != "researchforme"


def test_bundle_concatenates_all_docs_frontmatter_stripped(tmp_path, monkeypatch):
    """The copy-whole-project blob: every doc, reading order, no frontmatter noise."""
    import app.library as lib

    monkeypatch.setattr(lib, "library_root", lambda: tmp_path)
    p = lib.create_project("Platform Bet")
    lib.write_idea(p.slug, "Idea Alpha", "## Alpha\nthe first bet", developed=True)
    lib.write_idea(p.slug, "Idea Beta", "## Beta\nthe second bet", developed=False)

    md, count = lib.bundle_project(p.slug)

    assert count >= 3                       # project.md + two ideas
    assert "Platform Bet" in md
    assert "the first bet" in md and "the second bet" in md
    assert "# 📄 project.md" in md          # seam markers present
    assert "# 📄 ideas/" in md
    # project.md is the spine and must come first
    assert md.index("project.md") < md.index("ideas/")
    # frontmatter is stripped — no raw 'source: gapfinder' / 'developed:' lines
    assert "\nsource:" not in md
    assert "\ndeveloped:" not in md
