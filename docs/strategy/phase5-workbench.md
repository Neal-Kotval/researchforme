# Phase 5 — From Finder to Workbench

_2026-07-14. Extends [STRATEGY.md](STRATEGY.md), whose Phases 0–4 have shipped.
Owner's purpose, verbatim: **"a projects feature where you can import multiple ideas…
functions more like a document with a plan… a chat interface with the context of the
project… maybe it could create a project in a researchforme folder. like a library
folder?"**_

## 1. Thesis

Phases 0–4 made the engine a **finder**: it proposes spaces, red-teams them, scores
them for viability and founder fit, and hands you a ranked shortlist. It is good at
that now.

But a found idea has **nowhere to go**. You star it, and then… you copy it into a chat
somewhere, or you lose it. The engine's output is a dead end — a report, not a
workspace. Phase 5 builds the other half: the place a starred idea becomes a **project
you actually work**, with documents, a plan, and a conversation that knows what you're
working on.

The unit of value shifts from *the gap* to *the project*.

## 2. Principles

| # | Principle | Why |
|---|-----------|-----|
| W1 | **The library is plain files on disk, not rows in a database.** | The engine now has a CLI and an MCP server. If projects are markdown in a real directory, then Claude Code, Cursor, git, any editor, and your own agents can all read and write them. A document store only this app can open would be a silo bolted onto a system we deliberately made open. |
| W2 | **The app is a view onto files the user owns, never their jailer.** | Files edited outside the app are first-class. No lockfiles, no proprietary format, no "import to continue". If the app is deleted, the work survives. |
| W3 | **Consolidation is the AI's job; judgment is the user's.** | Merging N ideas into one thesis is genuine synthesis work worth spending a strong model on. Deciding which ideas belong together is not — that is taste, and the user has it. |
| W4 | **Never launder criticism out of an idea.** | An idea imported into a project carries its red team, its riskiest assumption, and its unmeasured-demand caveats. A project doc that quietly drops the kills would turn a hypothesis into a plan. (Same rule already enforced by the copy-for-chat export.) |
| W5 | **The chat is context-aware, not a second chat.** | Extend the existing tool-using Assistant rather than growing a parallel one. Inside a project, its documents and imported ideas are in context and it gains tools to write them. |

## 3. The shape

```
~/researchforme/                 # the library (configurable; RESEARCHFORME_DIR)
  kernel-ci/                     # a project = a directory
    project.md                   # frontmatter (status, created) + thesis + plan
    ideas/
      serving-config-compiler.md # one imported gap, with its red team intact
      verified-kernel-porting.md
    research/
      red-team.md
      interviews.md
    notes.md
```

A **project** is a directory. A **document** is a markdown file with YAML frontmatter.
That is the whole data model. It is greppable, git-able, and diffable.

## 4. The plan

Ordered by leverage. Each step is usable on its own.

### W-1 — Starred ideas tab (S) ✅ _first slice_
A cross-project `#/starred` view of every `user_star`'d gap. This is the shortlist —
and, critically, the **entry point for import**: multi-select rows → "Create project
from N ideas". Depends on `user_star` (shipped).

### W-2 — The library + project CRUD (M)
`RESEARCHFORME_DIR` (default `~/researchforme`). Backend owns reads/writes; the
frontend never touches the filesystem. Endpoints: list projects, create project, read
document, write document. Path traversal is the security surface — every path resolves
under the library root or is rejected.

### W-3 — Export / import ideas into a project (S+)
A project holds **many** ideas, so export is the join: selected gaps are written as
`ideas/<slug>.md` under a project. Two modes:

- **Raw export** (default, free, instant): the deterministic `gapToMarkdown` serializer
  already built for copy-for-chat — one formatter, two destinations (clipboard, file).
- **Developed export** (opt-in, one LLM call): a *development pass* that takes the gap
  further than the engine's own output — sharpens the thesis, works the wedge into
  concrete first steps, turns the riskiest assumption into a falsification plan, and
  names the open questions. This is what makes an exported idea a working document
  rather than a transcript.

The development pass **must not launder the criticism** (W4): the red team's verdicts,
the unmeasured-demand caveats, and the weakest link survive into the developed doc,
under their own heading. An idea that reads better after export but hides what would
kill it is a downgrade disguised as an upgrade.

It also **cannot fail**: if no LLM backend can produce a real development pass, it
falls back to the raw export with an honest `developed: false` in the frontmatter —
never canned prose, never a lost idea.

### W-4 — AI consolidation (M) — _the interesting part_
One strong-model pass over N imported ideas → a `project.md` that finds the **common
thesis**, names where the ideas **conflict**, and proposes a plan. It must be honest
about what it cannot reconcile: "these three share a wedge; this fourth one is a
different company and should be its own project." Degrades honestly (503, never canned)
like every other LLM surface here.

### W-5 — Document workspace UI (M)
Tabs across a project's documents; the existing safe `Markdown.tsx` renders them; edit
in place. This is the "functions more like a document with a plan" surface.

### W-6 — Project-aware Assistant (M)
The existing Assistant gains project context + document tools (`doc.read`, `doc.write`,
`doc.list`). Inside a project, "add what we learned from the interview to the plan"
works, and the diff lands in a file you own.

### W-7 — Round-trip with the outside world (S)
The CLI and MCP server gain the same document tools, so an agent in a terminal and the
web UI are editing one library. `gapfinder project new`, `gapfinder doc write`, and the
MCP equivalents. This is the payoff for W1.

## 5. What NOT to build

- **Two-way filesystem sync with a database mirror.** The file is the truth. A cache
  that can disagree with the disk is a bug factory.
- **A proprietary document format**, rich-text editor state, or anything that makes the
  files unreadable outside the app (violates W2).
- **Auto-consolidation on import.** Merging ideas costs a strong-model call and is a
  judgment call; it happens when the user asks, never implicitly.
- **Dropping the red team on import** (W4). The most dangerous thing this feature could
  do is turn a well-criticized hypothesis into a confident-looking plan.

## 6. Open risks

- **External edits vs. open editors.** If the user edits `project.md` in vim while the
  web UI has it open, last-write-wins silently clobbers. Mitigation: mtime check on
  write, and surface a conflict rather than overwrite.
- **Path traversal** is the one real security surface a local file API introduces.
  Every path must resolve under the library root; symlinks resolved before the check.
- **Consolidation is where mode collapse would hurt most** — a merge prompt will happily
  invent a unifying thesis for ideas that have nothing in common. The prompt must be
  allowed to say "these do not belong together."
