import {
  SCORE_KEYS,
  SCORE_LABELS,
  SOURCE_LABEL,
} from "../../types";
import { viabilityRamp, type TreeNode } from "../../autonomous/types";
import Markdown from "./Markdown";

interface Props {
  node: TreeNode | null;
  childNodes?: TreeNode[];
  onSelectChild?: (id: string) => void;
}

const VERDICT_META: Record<string, { cls: string; word: string }> = {
  survives: { cls: "survives", word: "Survives" },
  weakens: { cls: "weakens", word: "Weakens" },
  kills: { cls: "kills", word: "Kills" },
};

// Map a 1..5 score onto the theme's grey→blue→navy ramp (denser = stronger),
// so score numbers read in the CNC palette, not a rainbow.
function scoreColor(v: number): string {
  const i = Math.min(4, Math.max(0, Math.round(v) - 1));
  return `var(--ramp-${i})`;
}
function titleCase(k: string): string {
  return k.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}
function fmtDate(d: string | null): string | null {
  if (!d) return null;
  const t = new Date(d);
  return Number.isNaN(t.getTime())
    ? d
    : t.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

/**
 * Right-rail inspector. For gap nodes it renders the full case (thesis, the
 * narrative pillars, competitors, evidence — the same structure as the
 * single-area GapDetail drawer) plus the pressure-test panel: each adversarial
 * lens with its verdict + argument, the viability score, confidence, and
 * test_rigor. For structural nodes it shows the branch rationale, keywords, and a
 * child summary (SPEC §10.2).
 */
export default function NodeInspector({ node, childNodes = [], onSelectChild }: Props) {
  if (!node) {
    return (
      <div className="inspector-empty">
        <div className="ie-ico">☞</div>
        <div className="ie-t">Select a node</div>
        <div className="ie-d">Pick any branch or gap in the tree to inspect its case and pressure test.</div>
      </div>
    );
  }

  const gapish = node.kind === "gap" || node.kind === "gap_candidate";
  const g = node.gap;

  /* ---------------------------------------------------- structural nodes -- */
  if (!gapish || !g) {
    return (
      <div className="inspector">
        <div className="insp-head">
          <div className="insp-kind">{titleCase(node.kind)}</div>
          <h3 className="insp-title">{node.title}</h3>
        </div>
        {node.rationale && <Markdown className="detail-thesis md-clamp" text={node.rationale} />}

        {node.keywords.length > 0 && (
          <div className="detail-block">
            <h4>Query keywords</h4>
            <div className="chip-wrap">
              {node.keywords.map((k) => (
                <span className="tagpill" key={k}>
                  {k}
                </span>
              ))}
            </div>
          </div>
        )}

        <div className="detail-block">
          <h4>Children ({childNodes.length})</h4>
          {childNodes.length === 0 ? (
            <div className="insp-note">
              {node.state === "expanding"
                ? "Decomposing this branch…"
                : node.state === "errored"
                ? node.error || "This branch failed to expand."
                : "No children yet."}
            </div>
          ) : (
            <div className="child-list">
              {childNodes.map((c) => (
                <button
                  key={c.id}
                  className="child-row"
                  onClick={() => onSelectChild?.(c.id)}
                >
                  <span className="cr-title">{c.title}</span>
                  {(c.kind === "gap" || c.kind === "gap_candidate") && c.viability != null && (
                    <span
                      className={`viab-chip${c.star ? " star" : ""}`}
                      style={{ background: viabilityRamp(c.viability) }}
                    >
                      {c.star && <span className="vc-star">★</span>}
                      {c.viability}
                    </span>
                  )}
                </button>
              ))}
            </div>
          )}
        </div>
      </div>
    );
  }

  /* ---------------------------------------------------------- gap nodes -- */
  const pt = node.pressure_test;
  return (
    <div className="inspector">
      <div className="insp-head">
        <div className="insp-kind">
          {node.star && <span className="ik-star">★ Starred · </span>}
          {node.kind === "gap" ? "Pressure-tested gap" : "Candidate gap"}
        </div>
        <h3 className="insp-title">{g.title}</h3>
      </div>

      {/* viability headline */}
      <div className="viab-head">
        <div
          className="viab-big"
          style={{ borderColor: viabilityRamp(node.viability), color: viabilityRamp(node.viability) }}
        >
          <span className="vb-num">{node.viability ?? "—"}</span>
          <span className="vb-lab">viability</span>
        </div>
        <div className="viab-side">
          <div className="vs-row">
            <span className="vs-k">Confidence</span>
            <span className={`conf-pill ${node.confidence ?? "low"}`}>
              {node.confidence ?? "—"}
            </span>
          </div>
          {pt && (
            <div className="vs-row">
              <span className="vs-k">Test rigor</span>
              <span className="rigor-pill">{pt.test_rigor}</span>
            </div>
          )}
          {pt && (
            <div className="vs-tally">
              <span className="tally survives">{pt.survived} survived</span>
              <span className="tally weakens">{pt.weakened} weak</span>
              <span className="tally kills">{pt.killed} killed</span>
            </div>
          )}
        </div>
      </div>

      <Markdown className="detail-thesis" text={g.thesis} />

      {/* company concept — the standalone business, not a feature */}
      {g.company && (
        <div className="detail-block company-block">
          <h4>
            The company
            <span className={`company-badge ${g.company.standalone ? "yes" : "no"}`}>
              {g.company.standalone ? "Standalone company" : "Risk: just a feature"}
            </span>
          </h4>
          {g.company.product && (
            <div className="company-product">{g.company.product}</div>
          )}
          <div className="kv-grid">
            {g.company.icp && (
              <div className="kv">
                <div className="kv-k">Who it's for</div>
                <div className="kv-v">{g.company.icp}</div>
              </div>
            )}
            {g.company.business_model && (
              <div className="kv accent">
                <div className="kv-k">Business model</div>
                <div className="kv-v">{g.company.business_model}</div>
              </div>
            )}
            {g.company.expansion_path && (
              <div className="kv">
                <div className="kv-k">Wedge → platform</div>
                <div className="kv-v">{g.company.expansion_path}</div>
              </div>
            )}
            {g.company.moat && (
              <div className="kv">
                <div className="kv-k">Moat</div>
                <div className="kv-v">{g.company.moat}</div>
              </div>
            )}
            {g.company.standalone_reason && (
              <div className={`kv ${g.company.standalone ? "" : "warn"}`}>
                <div className="kv-k">Company, not a feature?</div>
                <div className="kv-v">{g.company.standalone_reason}</div>
              </div>
            )}
          </div>
        </div>
      )}

      {/* base scores */}
      <div className="detail-scorebar">
        {SCORE_KEYS.map((k) => (
          <div className="dsb-item" key={k}>
            <div className="dsb-val" style={{ color: scoreColor(g.scores[k]) }}>
              {g.scores[k]}
            </div>
            <div className="dsb-lab">{SCORE_LABELS[k]}</div>
          </div>
        ))}
      </div>

      {g.empty_for_a_reason && (
        <div className="empty-flag">
          <span className="ef-ico">⚠︎</span>
          <div>
            <div className="ef-t">Possibly empty for a reason</div>
            <div className="ef-d">
              {g.empty_reason || "This opening may be structurally unattractive — validate before committing."}
            </div>
          </div>
        </div>
      )}

      {/* pressure-test panel */}
      {pt && pt.lenses.length > 0 && (
        <div className="detail-block">
          <h4>Pressure test — {pt.lenses.length} adversarial lenses</h4>
          {pt.summary && <div className="pt-summary">{pt.summary}</div>}
          {pt.self_critique && (
            <div className="pt-critique">
              <span className="ptc-k">Strongest reason the score is wrong</span>
              <p>{pt.self_critique}</p>
            </div>
          )}
          <div className="lens-list">
            {pt.lenses.map((l, i) => {
              const meta = VERDICT_META[l.verdict] ?? VERDICT_META.weakens;
              return (
                <div className={`lens-item ${meta.cls}`} key={l.lens + i}>
                  <div className="lens-top">
                    <span className="lens-name">{titleCase(l.lens)}</span>
                    <span className={`verdict-pill ${meta.cls}`}>{meta.word}</span>
                  </div>
                  <div className="lens-arg">{l.argument}</div>
                  {l.evidence.length > 0 && (
                    <div className="lens-ev">
                      {l.evidence.map((e, j) => (
                        <a
                          key={j}
                          className={`lens-ev-src ${e.source}`}
                          href={e.url || undefined}
                          target="_blank"
                          rel="noreferrer"
                          title={e.quote}
                        >
                          {SOURCE_LABEL[e.source]} ↗
                        </a>
                      ))}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* narrative pillars */}
      <div className="detail-block">
        <h4>The case</h4>
        <div className="kv-grid">
          {g.why_now && (
            <div className="kv accent">
              <div className="kv-k">Why now</div>
              <div className="kv-v">{g.why_now}</div>
            </div>
          )}
          <div className="kv">
            <div className="kv-k">Wedge</div>
            <div className="kv-v">{g.wedge}</div>
          </div>
          <div className="kv warn">
            <div className="kv-k">Riskiest assumption</div>
            <div className="kv-v">{g.riskiest_assumption}</div>
          </div>
          <div className="kv warn">
            <div className="kv-k">Weakest link</div>
            <div className="kv-v">{g.weakest_link}</div>
          </div>
        </div>
      </div>

      {/* competitors */}
      {g.competitors.length > 0 && (
        <div className="detail-block">
          <h4>Top competitors &amp; their blind spots</h4>
          <div className="table-scroll">
            <table className="comp-table">
              <thead>
                <tr>
                  <th>Player</th>
                  <th>Segment</th>
                  <th>Tier</th>
                  <th>Blind spot</th>
                </tr>
              </thead>
              <tbody>
                {g.competitors.map((c, i) => (
                  <tr key={c.name + i}>
                    <td>
                      {c.url ? (
                        <a className="comp-name" href={c.url} target="_blank" rel="noreferrer">
                          {c.name} <span className="ext">↗</span>
                        </a>
                      ) : (
                        <span className="comp-name">{c.name}</span>
                      )}
                      {c.positioning && (
                        <div style={{ color: "var(--text-faint)", fontSize: 11, marginTop: 3 }}>
                          {c.positioning}
                        </div>
                      )}
                    </td>
                    <td className="comp-seg">{c.segment}</td>
                    <td>
                      <span className="price-tier">{c.price_tier || "—"}</span>
                    </td>
                    <td className="comp-weak">{c.weakness}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* evidence */}
      {g.evidence.length > 0 && (
        <div className="detail-block">
          <h4>Evidence ({g.evidence.length})</h4>
          <div className="evidence-list">
            {g.evidence.map((e, i) => {
              const date = fmtDate(e.date);
              return (
                <div className="ev-item" key={i}>
                  <span className={`ev-src ${e.source}`}>{SOURCE_LABEL[e.source]}</span>
                  <div className="ev-content">
                    <div className="ev-quote">{e.quote}</div>
                    <div className="ev-foot">
                      {date && <span>{date}</span>}
                      {e.url && (
                        <a href={e.url} target="_blank" rel="noreferrer">
                          View source ↗
                        </a>
                      )}
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {g.tags.length > 0 && (
        <div className="detail-block">
          <h4>Tags</h4>
          <div className="chip-wrap">
            {g.tags.map((t) => (
              <span className="tagpill" key={t}>
                {t}
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
