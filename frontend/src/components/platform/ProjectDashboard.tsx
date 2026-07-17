import { useMemo } from "react";
import Markdown from "../autonomous/Markdown";
import type { LibraryDoc } from "../../autonomous/api";

interface Props {
  title: string;
  status: string;
  planBody: string | null;           // project.md, frontmatter already stripped
  docs: LibraryDoc[];
  onOpenDoc: (path: string) => void;
  onConsolidate: () => void;
  consolidating: boolean;
}

/** Split a markdown body into its `## Section` blocks, keyed by heading. */
function sections(body: string): Record<string, string> {
  const out: Record<string, string> = {};
  if (!body) return out;
  // Everything before the first ## is the lede (rare in synthesized plans).
  const parts = body.split(/\n(?=## )/);
  for (const part of parts) {
    const m = /^## +(.+)\n?([\s\S]*)$/.exec(part.trim());
    if (m) out[m[1].trim().toLowerCase()] = m[2].trim();
  }
  return out;
}

/** First heading whose name contains any of the needles (case-insensitive). */
function pick(secs: Record<string, string>, needles: string[]): string | null {
  for (const key of Object.keys(secs)) {
    if (needles.some((n) => key.includes(n))) return secs[key];
  }
  return null;
}

const KIND_LABEL: Record<string, string> = {
  plan: "Plan",
  consolidation: "Consolidation",
  idea: "Ideas",
  research: "Research",
  doc: "Notes",
};

const KIND_ICON: Record<string, string> = {
  plan: "◆",
  consolidation: "⋈",
  idea: "◇",
  research: "❑",
  doc: "▪",
};

/**
 * The project home: the synthesized vision up top, the numbers that describe the
 * project's shape, and the document hierarchy — so opening a project lands on
 * "here is the bet and where it stands", not on a raw markdown file.
 *
 * Everything here is derived from documents already on disk; the dashboard
 * invents nothing. When the plan hasn't been synthesized yet, sections simply
 * don't render rather than showing empty scaffolding.
 */
export default function ProjectDashboard({
  title,
  status,
  planBody,
  docs,
  onOpenDoc,
  onConsolidate,
  consolidating,
}: Props) {
  const secs = useMemo(() => sections(planBody ?? ""), [planBody]);

  const ideas = docs.filter((d) => d.kind === "idea");
  const developed = ideas.filter((d) => d.developed).length;
  const consolidation = docs.find((d) => d.kind === "consolidation");

  const vision = pick(secs, ["what this project is", "vision", "thesis", "overview"]);
  const wedge = pick(secs, ["the wedge", "wedge", "where to start"]);
  const kills = pick(secs, ["what could kill", "kill", "risks", "risk"]);
  const first = pick(secs, ["first 90", "first moves", "next", "90 days"]);

  // Group docs by kind, preserving the backend's reading order. Default a
  // missing kind to "doc" so an older backend (or a hand-added file) can never
  // produce an undefined React key.
  const groups: { kind: string; docs: LibraryDoc[] }[] = [];
  for (const d of docs) {
    const kind = d.kind || "doc";
    let g = groups.find((x) => x.kind === kind);
    if (!g) groups.push((g = { kind, docs: [] }));
    g.docs.push(d);
  }

  return (
    <div className="pd">
      <header className="pd-head">
        <div className="pd-titlewrap">
          <h1 className="pd-title">{title}</h1>
          <span className={`lc-status st-${status}`}>{status}</span>
        </div>
      </header>

      {/* The shape of the project, in numbers. */}
      <div className="pd-stats">
        <div className="pd-stat">
          <span className="pd-stat-n">{ideas.length}</span>
          <span className="pd-stat-l">idea{ideas.length === 1 ? "" : "s"}</span>
        </div>
        <div className="pd-stat">
          <span className="pd-stat-n">{developed}</span>
          <span className="pd-stat-l">developed</span>
        </div>
        <div className="pd-stat">
          <span className="pd-stat-n">{consolidation ? "✓" : "—"}</span>
          <span className="pd-stat-l">consolidated</span>
        </div>
        <div className="pd-stat">
          <span className="pd-stat-n">{docs.length}</span>
          <span className="pd-stat-l">document{docs.length === 1 ? "" : "s"}</span>
        </div>
      </div>

      {vision && (
        <section className="pd-section">
          <h4 className="pd-cap">The vision</h4>
          <Markdown className="pd-prose" text={vision} />
        </section>
      )}

      {wedge && (
        <section className="pd-section pd-wedge">
          <h4 className="pd-cap">The wedge — where to start</h4>
          <Markdown className="pd-prose" text={wedge} />
        </section>
      )}

      <div className="pd-two">
        {first && (
          <section className="pd-section">
            <h4 className="pd-cap">First moves</h4>
            <Markdown className="pd-prose" text={first} />
          </section>
        )}
        {kills && (
          <section className="pd-section pd-kills">
            <h4 className="pd-cap">What could kill this</h4>
            <Markdown className="pd-prose" text={kills} />
          </section>
        )}
      </div>

      {ideas.length >= 2 && !consolidation && (
        <div className="pd-cta">
          <div className="pd-cta-text">
            <strong>{ideas.length} ideas, no synthesis yet.</strong> Consolidate
            reads them together and writes one thesis — naming where they conflict
            and what doesn't belong.
          </div>
          <button
            className="btn btn-primary"
            disabled={consolidating}
            onClick={onConsolidate}
          >
            {consolidating ? "Consolidating…" : "⋈ Consolidate ideas"}
          </button>
        </div>
      )}

      {/* The document hierarchy — every file, grouped by what it is. */}
      <section className="pd-section">
        <h4 className="pd-cap">Documents</h4>
        <div className="pd-tree">
          {groups.map((g) => (
            <div className="pd-branch" key={g.kind}>
              <div className="pd-branch-head">
                <span className="pd-branch-icon">{KIND_ICON[g.kind] ?? "▪"}</span>
                {KIND_LABEL[g.kind] ?? g.kind}
                <span className="pd-branch-count">{g.docs.length}</span>
              </div>
              {g.docs.map((d) => (
                <button
                  key={d.path}
                  className="pd-leaf"
                  onClick={() => onOpenDoc(d.path)}
                  title={d.title}
                >
                  <span className="pd-leaf-title">{d.title}</span>
                  {d.kind === "idea" && (
                    <span className={`pd-leaf-tag${d.developed ? " dev" : ""}`}>
                      {d.developed ? "developed" : "raw"}
                    </span>
                  )}
                </button>
              ))}
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}
