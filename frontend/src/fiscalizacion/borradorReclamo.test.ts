import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { type Borrador, borrarBorrador, borrarBorradores, guardarBorrador, leerBorradores, PREFIJO_BORRADOR } from "./borradorReclamo";

// Borradores sin conexión de los reclamos (RF38, app 0123). Datos SINTETICOS.

const C1 = "11111111-1111-4111-8111-111111111111";
const C2 = "99999999-9999-4999-8999-999999999999";
const borrador = (cambios: Partial<Borrador> = {}): Borrador => ({
  id: "b1", consorcio_id: C1, liquidacion_id: "l1", periodo: "2026-10-01", unidad_id: "u1", designacion: "1-A",
  campo: null, texto: "Texto SINTETICO", acepta_reemplazada: false, guardado_en: "2026-11-22T10:00:00Z",
  estado: { tipo: "escribiendo" }, ...cambios,
});  // prettier-ignore

beforeEach(() => localStorage.clear());
afterEach(() => vi.restoreAllMocks());

describe("borradores de reclamos", () => {
  it("guarda, lee por consorcio del más viejo al más nuevo y borra", () => {
    expect(guardarBorrador(borrador({ id: "b2", guardado_en: "2026-11-22T11:00:00Z" }))).toBe(true);
    expect(guardarBorrador(borrador())).toBe(true);
    expect(guardarBorrador(borrador({ id: "b3", consorcio_id: C2 }))).toBe(true);
    expect(leerBorradores(C1)?.map((b) => b.id)).toEqual(["b1", "b2"]);
    expect(borrarBorrador(borrador())).toBe(true);
    expect(leerBorradores(C1)?.map((b) => b.id)).toEqual(["b2"]);
    // Al salir de la cuenta no queda ninguno, de ningún edificio; lo demás se queda.
    localStorage.setItem("otra-cosa", "1");
    borrarBorradores();
    expect(leerBorradores(C1)).toEqual([]);
    expect(leerBorradores(C2)).toEqual([]);
    expect(localStorage.getItem("otra-cosa")).toBe("1");
  });

  it("una entrada que no es un borrador válido se borra y no se muestra", () => {
    localStorage.setItem(`${PREFIJO_BORRADOR}${C1}:x`, "no es JSON");
    localStorage.setItem(`${PREFIJO_BORRADOR}${C1}:y`, JSON.stringify({ ...borrador({ id: "y" }), estado: { tipo: "por_enviar" } }));
    guardarBorrador(borrador());
    expect(leerBorradores(C1)?.map((b) => b.id)).toEqual(["b1"]);
    expect(Object.keys(localStorage)).toEqual([`${PREFIJO_BORRADOR}${C1}:b1`]);
  });

  it("sin almacenamiento lo dice: guardar devuelve false y leer null, nunca un borrador inventado", () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("bloqueado", "SecurityError");
    });
    expect(guardarBorrador(borrador())).toBe(false);
    vi.spyOn(Storage.prototype, "key").mockImplementation(() => {
      throw new DOMException("bloqueado", "SecurityError");
    });
    vi.spyOn(Storage.prototype, "length", "get").mockReturnValue(1);
    expect(leerBorradores(C1)).toBeNull();
  });
});
