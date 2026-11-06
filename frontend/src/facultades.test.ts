import { readdirSync, readFileSync } from "node:fs";
import { join, resolve } from "node:path";

import { describe, expect, it } from "vitest";

import { CODIGOS_CON_NOMBRE, nombreDeFacultad } from "./facultades";

const CARPETA_POLITICAS = resolve(process.cwd(), "../contratos/cem_contracts/politicas_datos");

function facultadesDeLasPoliticas(): string[] {
  const todas = readdirSync(CARPETA_POLITICAS)
    .filter((archivo) => archivo.endsWith(".json") && archivo !== "esquema.json")
    .flatMap((archivo) => {
      const { politicas } = JSON.parse(readFileSync(join(CARPETA_POLITICAS, archivo), "utf8")) as {
        politicas: { facultad: string | null }[];
      };
      return politicas.map((p) => p.facultad);
    });
  return [...new Set(todas.filter((f) => f !== null))].sort();
}

describe("nombres de las facultades", () => {
  it("toda facultad de las políticas tiene su nombre, y ninguno sobra", () => {
    const facultades = facultadesDeLasPoliticas();
    expect(facultades.length).toBeGreaterThan(0); // control: la lectura encontró algo
    expect([...CODIGOS_CON_NOMBRE].sort()).toEqual(facultades);
  });

  it("ningún nombre es el código", () => {
    for (const codigo of CODIGOS_CON_NOMBRE) expect(nombreDeFacultad(codigo)).not.toContain(".");
  });

  it("una facultad más nueva que la aplicación tampoco muestra su código", () => {
    expect(nombreDeFacultad("algo.nuevo")).toBe("Otra facultad");
  });
});
