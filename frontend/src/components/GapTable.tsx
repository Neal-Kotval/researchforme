import { useMemo, useState } from "react";
import {
  SCORE_KEYS,
  SCORE_LABELS,
  type RankedGap,
  type ScoreKey,
  type SourceName,
  type SourceStatusKind,
} from "../types";

interface Props {
  gaps: RankedGap[];
  selectedTitle: string | null;
  onSelect: (title: string) => void;
  sourceStatus: Record<SourceName, SourceStatusKind | undefined>;
}

type SortKey = "rank" | "composite" | ScoreKey;
type Dir = "asc" | "desc";

/** Best evidence provenance for a gap, derived from live source reports. */
function evidenceStatus(
  rg: RankedGap,
  sourceStatus: Record<SourceName, SourceStatusKind | undefined>
): "live" | "mock" | "none" {
  const srcs = new Set(rg.gap.evidence.map((e) => e.source));
  if (srcs.size === 0) return "none";
  let sawMock = false;
  for (const s of srcs) {
    const st = sourceStatus[s];
    if (st === "live") return "live";
    if (st === "mock") sawMock = true;
  }
  return sawMock ? "mock" : "none";
}

export default function GapTable({ gaps, selectedTitle, onSelect, sourceStatus }: Props) {
  const [sortKey, setSortKey] = useState<SortKey>("rank");
  const [dir, setDir] = useState<Dir>("asc");

  function toggleSort(key: SortKey) {
    if (key === sortKey) {
      setDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(key);
      // rank defaults ascending (1 first); scores/composite default descending (best first)
      setDir(key === "rank" ? "asc" : "desc");
    }
  }

  const sorted = useMemo(() => {
    const arr = [...gaps];
    arr.sort((a, b) => {
      let av: number, bv: number;
      if (sortKey === "rank") {
        av = a.rank;
        bv = b.rank;
      } else if (sortKey === "composite") {
        av = a.composite;
        bv = b.composite;
      } else {
        av = a.gap.scores[sortKey];
        bv = b.gap.scores[sortKey];
      }
      return dir === "asc" ? av - bv : bv - av;
    });
    return arr;
  }, [gaps, sortKey, dir]);

  const indicator = (key: SortKey) =>
    sortKey === key ? <span className="sort-ind">{dir === "asc" ? "▲" : "▼"}</span> : null;

  const maxComposite = 5;

  return (
    <div className="card table-card">
      <div className="table-scroll">
        <table className="gaps">
          <thead>
            <tr>
              <th className="sortable num" style={{ width: 60 }} onClick={() => toggleSort("rank")}>
                # {indicator("rank")}
              </th>
              <th style={{ minWidth: 240 }}>Gap</th>
              {SCORE_KEYS.map((k) => (
                <th key={k} className="sortable num" onClick={() => toggleSort(k)} title={`Sort by ${SCORE_LABELS[k]}`}>
                  {SCORE_LABELS[k]} {indicator(k)}
                </th>
              ))}
              <th className="sortable" style={{ width: 140 }} onClick={() => toggleSort("composite")}>
                Composite {indicator("composite")}
              </th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((rg) => {
              const selected = rg.gap.title === selectedTitle;
              const ev = evidenceStatus(rg, sourceStatus);
              const novelty = Math.round(rg.gap.novelty);
              return (
                <tr
                  key={rg.gap.title}
                  className={selected ? "selected" : ""}
                  onClick={() => onSelect(rg.gap.title)}
                  tabIndex={0}
                  role="button"
                  aria-pressed={selected}
                  aria-label={`Open details for ${rg.gap.title}`}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      onSelect(rg.gap.title);
                    }
                  }}
                >
                  <td className="num">
                    <span className={`rank-badge ${rg.rank === 1 ? "top" : ""}`}>{rg.rank}</span>
                  </td>
                  <td className="gap-cell">
                    <div className="gc-title">
                      {rg.rank === 1 && <span className="gc-star">★</span>}
                      {rg.gap.title}
                    </div>
                    <div className="gc-sub">
                      {rg.gap.sub_segment && <span className="tagpill">{rg.gap.sub_segment}</span>}
                      {rg.gap.tags.slice(0, 2).map((t) => (
                        <span className="tagpill" key={t}>
                          {t}
                        </span>
                      ))}
                      <span className="novelty-stars" title={`Novelty ${novelty}/5`}>
                        {"◆".repeat(novelty)}
                        <span className="off">{"◆".repeat(Math.max(0, 5 - novelty))}</span>
                      </span>
                      <span className={`ev-dot ${ev}`} title={`${ev} evidence`}>
                        <span className="d" />
                        {ev === "none" ? "no evidence" : ev}
                      </span>
                    </div>
                  </td>
                  {SCORE_KEYS.map((k) => {
                    const v = rg.gap.scores[k];
                    return (
                      <td key={k} className="num">
                        <div className="score-bar">
                          <span className="val">{v}</span>
                          <span className="track">
                            <span className="fill" style={{ width: `${(v / 5) * 100}%` }} />
                          </span>
                        </div>
                      </td>
                    );
                  })}
                  <td>
                    <div className="composite-cell">
                      <span className="cnum">{rg.composite.toFixed(2)}</span>
                      <span className="cbar">
                        <span className="cfill" style={{ width: `${(rg.composite / maxComposite) * 100}%` }} />
                      </span>
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
