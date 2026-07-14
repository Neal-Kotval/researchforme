"""Project-scoped assistant (W-6): doc.* tools are wired when a project is given.

We can't run the live agent loop in a unit test (no LLM), but we can assert the
tool layer: the doc tools exist, are scoped to the project, and actually read and
write real files through the same library guard.
"""
import pytest

from app import library as lib
from app.routers.assistant import _build_project_tools, ChatAction


@pytest.fixture()
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("RESEARCHFORME_DIR", str(tmp_path / "lib"))
    lib.ensure_root()
    lib.create_project("Kernel CI", thesis="Verify fused kernels.")
    return "kernel-ci"


def _tool(tools, display):
    return next(t for t in tools if t.name == display)


async def test_doc_tools_are_scoped_and_functional(project):
    actions: list[ChatAction] = []
    tools = _build_project_tools(project, actions)
    names = {t.name for t in tools}
    assert names == {"doc_list", "doc_read", "doc_write", "doc_append"}

    # write a new doc
    w = await _tool(tools, "doc_write").handler({"path": "research/notes.md", "content": "# Notes\nfound X"})
    assert "written" in w

    # it shows up in the list
    listed = await _tool(tools, "doc_list").handler({})
    assert "research/notes.md" in listed

    # append to the plan
    await _tool(tools, "doc_append").handler({"path": "project.md", "content": "## Next\ncall 10 leads"})
    plan = await _tool(tools, "doc_read").handler({"path": "project.md"})
    assert "call 10 leads" in plan
    assert "Verify fused kernels" in plan   # append kept the original

    # every call was recorded for the UI
    assert len(actions) == 4


async def test_doc_tools_cannot_escape_the_project(project):
    actions: list[ChatAction] = []
    tools = _build_project_tools(project, actions)
    out = await _tool(tools, "doc_write").handler({"path": "../../../etc/pwned.md", "content": "x"})
    assert "error" in out.lower()
