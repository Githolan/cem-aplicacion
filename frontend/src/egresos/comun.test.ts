import { describe, expect, it } from "vitest";

import { fechaLegible, momento, momentoAlFinal } from "./comun";

describe("textos de fecha y hora", () => {
  it("una hora que ya trae su punto no suma otro al cerrar la frase", () => {
    const iso = "2026-11-05T12:00:00Z";
    expect(momento(iso).endsWith(".")).toBe(true);
    expect(momentoAlFinal(iso)).toBe(momento(iso));
    expect(momentoAlFinal(iso).endsWith("..")).toBe(false);
  });

  it("la fecha se lee día, mes y año", () => {
    expect(fechaLegible("2026-10-31")).toBe("31/10/2026");
  });
});
