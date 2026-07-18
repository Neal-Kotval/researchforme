import { useEffect, useState } from "react";
import { getPortfolio, ApiError } from "../../autonomous/api";
import type { PortfolioItem } from "../../autonomous/types";
import { confidenceTrust } from "./portfolioPlot";
import { Button, Card, ScoreBadge } from "../ui";

interface Props {
  onOpenNode: (pid: string, nodeId: string) => void;
  /** Opens the full portfolio (the "wiki/tree" of every idea). */
  onSeeDetailed: () => void;
  /** Which sections to render — lets Starred sit between top-rated and recent. */
  sections?: ("top" | "recent")[];
}

const Chevron = () => (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor"
    strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round"><path d="m9 6 6 6-6 6" /></svg>
);

function IdeaRow({ it, onOpen }: { it: PortfolioItem; onOpen: () => void }) {
  const verified = confidenceTrust(it.confidence) === "earned";
  return (
    <button className="ui-row" onClick={onOpen}>
      <ScoreBadge value={it.viability} verified={verified} />
      <div className="ui-row-main">
        <div className="ui-row-title">{it.title}</div>
        <div className="ui-row-cap">
          {it.domain ?? "unknown run"}{it.fit != null ? ` · fit ${it.fit}` : ""}
        </div>
      </div>
      <span className="ui-row-chev"><Chevron /></span>
    </button>
  );
}

/**
 * The ideas side of Library: the engine's top-rated gaps and the most recent
 * ones, across every run. Starred ideas + export live below (StarredView).
 * "See detailed" opens the full portfolio — the wiki/2×2 of every idea.
 */
export default function IdeasPanel({ onOpenNode, onSeeDetailed, sections = ["top", "recent"] }: Props) {
  const [items, setItems] = useState<PortfolioItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getPortfolio()
      .then(setItems)
      .catch((e) => setError(e instanceof ApiError ? e.message : "Could not load the portfolio."));
  }, []);

  if (error) return <Card pad><div className="ui-empty-body" role="alert" style={{ textAlign: "center", maxWidth: "none" }}>{error}</div></Card>;
  if (items === null) return <Card pad><div className="ui-empty-body" style={{ textAlign: "center", maxWidth: "none" }}>Loading ideas…</div></Card>;

  const scored = items.filter((i) => i.viability != null);
  const topRated = [...scored].sort((a, b) => (b.viability ?? 0) - (a.viability ?? 0)).slice(0, 10);
  const recent = [...items].sort((a, b) => (b.updated_at || "").localeCompare(a.updated_at || "")).slice(0, 8);

  return (
    <div className="ideas-panel">
      {sections.includes("top") && (
        <>
          <div className="ideas-sec-head">
            <span>Top rated</span>
            <Button variant="quiet" size="sm" onClick={onSeeDetailed}>See detailed →</Button>
          </div>
          {topRated.length > 0 ? (
            <div className="ui-rowlist">
              {topRated.map((it) => <IdeaRow key={it.node_id} it={it} onOpen={() => onOpenNode(it.project_id, it.node_id)} />)}
            </div>
          ) : (
            <Card pad><div className="ui-empty"><div className="ui-empty-title">No rated ideas yet</div><div className="ui-empty-body">Ideas appear here as runs score their candidate gaps.</div></div></Card>
          )}
        </>
      )}

      {sections.includes("recent") && recent.length > 0 && (
        <>
          <div className="ideas-sec-head"><span>Recent</span></div>
          <div className="ui-rowlist">
            {recent.map((it) => <IdeaRow key={`r-${it.node_id}`} it={it} onOpen={() => onOpenNode(it.project_id, it.node_id)} />)}
          </div>
        </>
      )}
    </div>
  );
}
