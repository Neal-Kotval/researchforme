import type { Project } from "../../autonomous/types";
import { Segmented } from "../ui";
import ExploreView from "./ExploreView";
import AutonomousView from "./AutonomousView";
import PressureTestView from "./PressureTestView";
import CompareView from "./CompareView";

export type ExploreTab = "live" | "autonomous" | "pressure" | "compare";

interface Props {
  projects: Project[];
  tab: ExploreTab;
  onTab: (t: ExploreTab) => void;
  onOpenProject: (pid: string) => void;
  onOpenNode: (pid: string, nodeId: string) => void;
  onNewExploration: () => void;
}

/**
 * Explore workspace (v3 consolidation): one screen holding the whole hunt — the
 * live run, autonomous mode, the red team, and the comparison — switched by a
 * segmented tag row. Each tag deep-links (the hash updates), so every prior
 * route (#/autonomous, #/pressure-test, #/compare) still resolves here.
 */
export default function ExploreWorkspace({ projects, tab, onTab, onOpenProject, onOpenNode, onNewExploration }: Props) {
  return (
    <>
      <div className="exw-tabbar">
        <Segmented<ExploreTab>
          items={[
            { id: "live", label: "Live" },
            { id: "autonomous", label: "Autonomous" },
            { id: "pressure", label: "Pressure-test" },
            { id: "compare", label: "Compare" },
          ]}
          value={tab}
          onChange={onTab}
          ariaLabel="Explore tools"
        />
      </div>
      {tab === "live" && <ExploreView projects={projects} onOpenProject={onOpenProject} onOpenNode={onOpenNode} onNewExploration={onNewExploration} />}
      {tab === "autonomous" && <AutonomousView projects={projects} onOpenProject={onOpenProject} onNewExploration={onNewExploration} />}
      {tab === "pressure" && <PressureTestView onOpenNode={onOpenNode} onNewExploration={onNewExploration} />}
      {tab === "compare" && <CompareView onOpenNode={onOpenNode} onNewExploration={onNewExploration} />}
    </>
  );
}
