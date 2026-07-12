import { describe, it, expect } from "vitest";
import { parseHash, routeToHash, type Route } from "./useHashRoute";

describe("parseHash", () => {
  it("treats an empty hash as home", () => {
    expect(parseHash("")).toEqual({ view: "home" });
  });
  it("treats '#/' as home", () => {
    expect(parseHash("#/")).toEqual({ view: "home" });
  });
  it("parses a project route", () => {
    expect(parseHash("#/e/abc")).toEqual({
      view: "exploration",
      projectId: "abc",
      nodeId: null,
    });
  });
  it("parses a node route", () => {
    expect(parseHash("#/e/abc/n/xyz")).toEqual({
      view: "exploration",
      projectId: "abc",
      nodeId: "xyz",
    });
  });
  it("falls back to home on a malformed hash", () => {
    expect(parseHash("#/garbage/here")).toEqual({ view: "home" });
  });
  it("parses the flat platform views", () => {
    expect(parseHash("#/explore")).toEqual({ view: "explore" });
    expect(parseHash("#/pressure-test")).toEqual({ view: "pressure" });
    expect(parseHash("#/compare")).toEqual({ view: "compare" });
    expect(parseHash("#/assistant")).toEqual({ view: "assistant" });
  });
  it("parses the graveyard route", () => {
    expect(parseHash("#/graveyard")).toEqual({ view: "graveyard" });
  });
  it("round-trips the flat platform views", () => {
    for (const r of [{ view: "explore" }, { view: "pressure" }, { view: "compare" }, { view: "assistant" }, { view: "graveyard" }] as Route[]) {
      expect(parseHash(routeToHash(r))).toEqual(r);
    }
  });
  it("decodes percent-encoded ids", () => {
    expect(parseHash("#/e/a%20b/n/n%2F1")).toEqual({
      view: "exploration",
      projectId: "a b",
      nodeId: "n/1",
    });
  });
});

describe("routeToHash + parseHash round-trip", () => {
  it("round-trips a node route with awkward characters", () => {
    const r: Route = { view: "exploration", projectId: "a b", nodeId: "n 1" };
    expect(parseHash(routeToHash(r))).toEqual(r);
  });
  it("formats home as '#/'", () => {
    expect(routeToHash({ view: "home" })).toBe("#/");
  });
});
