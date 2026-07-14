import { useCallback, useEffect, useState } from "react";

export type Route =
  | { view: "home" }
  | { view: "exploration"; projectId: string; nodeId: string | null }
  | { view: "explore" }
  | { view: "pressure" }
  | { view: "compare" }
  | { view: "assistant" }
  | { view: "graveyard" }
  | { view: "starred" }
  | { view: "library"; slug: string | null };

/** Flat-view hash segments (platform screens without params). */
const FLAT_VIEWS: Record<string, Route> = {
  explore: { view: "explore" },
  "pressure-test": { view: "pressure" },
  compare: { view: "compare" },
  assistant: { view: "assistant" },
  graveyard: { view: "graveyard" },
  starred: { view: "starred" },
  library: { view: "library", slug: null },
};

/** Parse `window.location.hash` into a Route. Unknown shapes fall back to home. */
export function parseHash(hash: string): Route {
  const raw = hash.replace(/^#/, "").replace(/^\/+/, "");
  const parts = raw.split("/").filter(Boolean);
  if (parts[0] === "library" && parts[1]) {
    return { view: "library", slug: decodeURIComponent(parts[1]) };
  }
  // ["e", projectId] or ["e", projectId, "n", nodeId]
  if (parts[0] === "e" && parts[1]) {
    const projectId = decodeURIComponent(parts[1]);
    if (parts[2] === "n" && parts[3]) {
      return { view: "exploration", projectId, nodeId: decodeURIComponent(parts[3]) };
    }
    if (parts.length === 2) {
      return { view: "exploration", projectId, nodeId: null };
    }
  }
  if (parts.length === 1 && FLAT_VIEWS[parts[0]]) return FLAT_VIEWS[parts[0]];
  return { view: "home" };
}

/** Format a Route back into a hash string. */
export function routeToHash(route: Route): string {
  if (route.view === "home") return "#/";
  if (route.view === "explore") return "#/explore";
  if (route.view === "pressure") return "#/pressure-test";
  if (route.view === "compare") return "#/compare";
  if (route.view === "assistant") return "#/assistant";
  if (route.view === "graveyard") return "#/graveyard";
  if (route.view === "starred") return "#/starred";
  if (route.view === "library") return route.slug ? `#/library/${encodeURIComponent(route.slug)}` : "#/library";
  const base = `#/e/${encodeURIComponent(route.projectId)}`;
  return route.nodeId ? `${base}/n/${encodeURIComponent(route.nodeId)}` : base;
}

/**
 * The single source of navigation truth. Reads the hash on mount and on every
 * `hashchange`; the nav helpers write the hash (which fires `hashchange`), so
 * there is exactly one place "where am I" is decided.
 */
export function useHashRoute() {
  const [route, setRoute] = useState<Route>(() => parseHash(window.location.hash));

  useEffect(() => {
    const onHash = () => setRoute(parseHash(window.location.hash));
    window.addEventListener("hashchange", onHash);
    onHash(); // sync in case the hash changed before the listener attached
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const navHome = useCallback(() => {
    window.location.hash = routeToHash({ view: "home" });
  }, []);
  const navProject = useCallback((projectId: string) => {
    window.location.hash = routeToHash({ view: "exploration", projectId, nodeId: null });
  }, []);
  const navNode = useCallback((projectId: string, nodeId: string) => {
    window.location.hash = routeToHash({ view: "exploration", projectId, nodeId });
  }, []);
  const navView = useCallback(
    (view: "home" | "explore" | "pressure" | "compare" | "assistant" | "graveyard" | "starred" | "library") => {
      window.location.hash = routeToHash({ view } as Route);
    },
    []
  );

  return { route, navHome, navProject, navNode, navView };
}
