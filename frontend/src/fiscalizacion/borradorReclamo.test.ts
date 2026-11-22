import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  type Borrador,
  borrarBorrador,
  borrarBorradores,
  borrarBorradoresAjenos,
  guardarBorrador,
  leerBorradores,
  PREFIJO_BORRADOR,
} from "./borradorReclamo";

// Borradores sin conexión de los reclamos (RF38, app 0123). Datos SINTETICOS.

const C1 = "11111111-1111-4111-8111-111111111111";
const C2 = "99999999-9999-4999-8999-999999999999";
const ANA = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const BETO = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
const borrador = (cambios: Partial<Borrador> = {}): Borrador => ({
  id: "b1", autor: ANA, consorcio_id: C1, liquidacion_id: "l1", periodo: "2026-10-01", unidad_id: "u1", designacion: "1-A",
  campo: null, texto: "Texto SINTETICO", acepta_reemplazada: false, guardado_en: "2026-11-22T10:00:00Z",
  estado: { tipo: "escribiendo" }, ...cambios,
});  // prettier-ignore
const ids = (autor: string, consorcio: string) => leerBorradores(autor, consorcio)?.map((b) => b.id);

beforeEach(() => localStorage.clear());
afterEach(() => vi.restoreAllMocks());

describe("borradores de reclamos", () => {
  it("guarda, lee por consorcio del más viejo al más nuevo y borra", () => {
    expect(guardarBorrador(borrador({ id: "b2", guardado_en: "2026-11-22T11:00:00Z" }))).toBe(true);
    expect(guardarBorrador(borrador())).toBe(true);
    expect(guardarBorrador(borrador({ id: "b3", consorcio_id: C2 }))).toBe(true);
    expect(ids(ANA, C1)).toEqual(["b1", "b2"]);
    expect(borrarBorrador(borrador())).toBe(true);
    expect(ids(ANA, C1)).toEqual(["b2"]);
    // Al salir de la cuenta no queda ninguno, de ningún edificio ni de nadie; lo demás se queda.
    expect(guardarBorrador(borrador({ id: "b4", autor: BETO }))).toBe(true);
    localStorage.setItem("otra-cosa", "1");
    borrarBorradores();
    expect(ids(ANA, C1)).toEqual([]);
    expect(ids(ANA, C2)).toEqual([]);
    expect(ids(BETO, C1)).toEqual([]);
    expect(localStorage.getItem("otra-cosa")).toBe("1");
  });

  it("los borradores de otra persona en el mismo navegador no se leen ni se tocan", () => {
    expect(guardarBorrador(borrador({ id: "de-ana" }))).toBe(true);
    expect(guardarBorrador(borrador({ id: "de-beto", autor: BETO, estado: { tipo: "por_enviar", clave: "k" } }))).toBe(true);
    expect(ids(BETO, C1)).toEqual(["de-beto"]);
    expect(ids(ANA, C1)).toEqual(["de-ana"]);
    // Leer los propios no borra los ajenos.
    expect(ids(BETO, C1)).toEqual(["de-beto"]);
  });

  it("una entrada sin autor (el formato anterior) o con la clave de otro se borra: nunca se adivina de quién es", () => {
    const viejo: Partial<Borrador> = borrador({ id: "viejo", estado: { tipo: "por_enviar", clave: "k" } });
    delete viejo.autor;
    localStorage.setItem(`${PREFIJO_BORRADOR}${C1}:viejo`, JSON.stringify(viejo));
    // Con autor Beto pero guardado bajo la clave de Ana: el contenido no coincide con la clave.
    localStorage.setItem(`${PREFIJO_BORRADOR}${ANA}:${C1}:cambiado`, JSON.stringify(borrador({ id: "cambiado", autor: BETO })));
    expect(ids(ANA, C1)).toEqual([]);
    expect(ids(BETO, C1)).toEqual([]);
    expect(Object.keys(localStorage)).toEqual([]);
  });

  it("al entrar otra persona se borran los ajenos y quedan los suyos", () => {
    expect(guardarBorrador(borrador({ id: "de-ana" }))).toBe(true);
    expect(guardarBorrador(borrador({ id: "de-beto", autor: BETO }))).toBe(true);
    localStorage.setItem(`${PREFIJO_BORRADOR}${C1}:viejo`, "{}");
    localStorage.setItem("otra-cosa", "1");
    borrarBorradoresAjenos(BETO);
    expect(Object.keys(localStorage).sort()).toEqual([`${PREFIJO_BORRADOR}${BETO}:${C1}:de-beto`, "otra-cosa"].sort());
  });

  it("una entrada que no es un borrador válido se borra y no se muestra", () => {
    localStorage.setItem(`${PREFIJO_BORRADOR}${ANA}:${C1}:x`, "no es JSON");
    localStorage.setItem(`${PREFIJO_BORRADOR}${ANA}:${C1}:y`, JSON.stringify({ ...borrador({ id: "y" }), estado: { tipo: "por_enviar" } }));
    guardarBorrador(borrador());
    expect(ids(ANA, C1)).toEqual(["b1"]);
    expect(Object.keys(localStorage)).toEqual([`${PREFIJO_BORRADOR}${ANA}:${C1}:b1`]);
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
    expect(leerBorradores(ANA, C1)).toBeNull();
  });
});
