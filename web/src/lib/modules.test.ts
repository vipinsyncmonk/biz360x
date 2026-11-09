import { describe, expect, it } from "vitest";
import { NAV } from "../components/nav";
import { LAYERS, RAILS, allModules, countLine, coverage, coverageLine, hasScreen, railCounts, switchedOn } from "./modules";

describe("module map", () => {
  it("has four layers and every module belongs to at least one track", () => {
    expect(LAYERS.map((l) => l.title)).toEqual(["Grow", "Govern", "Run", "Foundation"]);
    expect(allModules()).toHaveLength(27);
    expect(allModules().every((x) => x.tracks.length > 0 && x.about.length > 0)).toBe(true);
    expect(new Set(allModules().map((x) => x.name)).size).toBe(27);
  });

  it("only links to screens that exist in the navigation", () => {
    const real = new Set(NAV.flatMap((g) => g.items).filter((i) => i.built).map((i) => i.href));
    for (const mod of allModules().filter(hasScreen)) expect(real.has(mod.href!), `${mod.name} -> ${mod.href}`).toBe(true);
  });

  it("counts what each track switches on", () => {
    expect(countLine("All")).toBe("27 modules across 4 layers");
    expect(countLine("Startup")).toMatch(/^\d+ of 27 modules switch on for Startup$/);
    const mods = allModules();
    expect(mods.filter((x) => switchedOn(x, "Startup")).length).toBe(Number(countLine("Startup").split(" ")[0]));
    expect(switchedOn(mods.find((x) => x.name === "ESOP Manager")!, "MSME")).toBe(false);
    expect(switchedOn(mods.find((x) => x.name === "Compliance Engine")!, "MSME")).toBe(true);
    expect(switchedOn(mods[0]!, "All")).toBe(true);
  });

  it("says how much has a screen, without rounding it up", () => {
    const all = coverage("All");
    expect(all.total).toBe(27);
    expect(all.withScreen).toBe(allModules().filter(hasScreen).length);
    expect(all.withScreen).toBeLessThan(all.total);                                  // some modules are still planned
    expect(coverageLine("All")).toBe(`${all.withScreen} of 27 have a screen`);
    expect(coverage("MSME").total).toBe(Number(countLine("MSME").split(" ")[0]));
  });

  it("is honest that no portal has been tried for real", () => {
    expect(RAILS.some((r) => r.state === ("live" as never))).toBe(false);
    const c = railCounts();
    expect(c.sandbox + c.planned).toBe(RAILS.length);
    expect(RAILS.find((r) => r.name === "Udyam portal")?.state).toBe("planned");
    expect(RAILS.find((r) => r.name === "GSTN via GSP")?.state).toBe("sandbox");
  });
});
