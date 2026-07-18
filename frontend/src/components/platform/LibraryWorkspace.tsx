import { useState } from "react";
import { Segmented } from "../ui";
import LibraryView from "./LibraryView";
import StarredView from "./StarredView";
import GraveyardView from "./GraveyardView";
import IdeasPanel from "./IdeasPanel";

type LibraryTab = "overview" | "projects";

interface Props {
  slug: string | null;
  onOpenProject: (slug: string | null) => void;
  onOpenNode: (pid: string, nodeId: string) => void;
  onImported: (slug: string) => void;
}

/**
 * Library workspace (v3 consolidation): your ideas and the graveyard side by
 * side (Overview), plus the on-disk markdown Projects. Opening a project slug
 * drills into its workspace. Old routes (#/starred, #/graveyard) resolve here.
 */
export default function LibraryWorkspace({ slug, onOpenProject, onOpenNode, onImported }: Props) {
  const [tab, setTab] = useState<LibraryTab>("overview");
  const seeDetailed = () => { window.location.hash = "#/compare"; };

  // A specific project takes over the whole area (its own filesystem-like view).
  if (slug) return <LibraryView slug={slug} onOpenProject={onOpenProject} />;

  return (
    <>
      <div className="exw-tabbar">
        <Segmented<LibraryTab>
          items={[{ id: "overview", label: "Ideas & graveyard" }, { id: "projects", label: "Projects" }]}
          value={tab}
          onChange={setTab}
          ariaLabel="Library sections"
        />
      </div>
      {tab === "projects" ? (
        <LibraryView slug={null} onOpenProject={onOpenProject} />
      ) : (
        <div className="lib-split">
          <div className="lib-split-col">
            <div className="lib-split-head">Your ideas</div>
            <IdeasPanel sections={["top"]} onOpenNode={onOpenNode} onSeeDetailed={seeDetailed} />
            <div className="lib-starred-sec">
              <div className="ideas-sec-head"><span>Starred</span></div>
              <StarredView onOpenNode={onOpenNode} onImported={onImported} />
            </div>
            <div className="lib-starred-sec">
              <IdeasPanel sections={["recent"]} onOpenNode={onOpenNode} onSeeDetailed={seeDetailed} />
            </div>
          </div>
          <div className="lib-split-col">
            <div className="lib-split-head">Graveyard</div>
            <GraveyardView onOpenNode={onOpenNode} />
          </div>
        </div>
      )}
    </>
  );
}
