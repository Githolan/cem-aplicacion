import { describe, expect, it } from "vitest";

import { mensajeDeFalla, SERVICIO_NO_DISPONIBLE, SIN_CONEXION } from "./fallas";

describe("mensajeDeFalla (E34)", () => {
  it("una respuesta de la aplicación, con su código, es del servicio", () => {
    expect(mensajeDeFalla(500, "error_interno")).toBe(SERVICIO_NO_DISPONIBLE);
    expect(mensajeDeFalla(502, "error_interno")).toBe(SERVICIO_NO_DISPONIBLE);
  });

  it("un 503 es del servicio aunque no traiga el cuerpo de la aplicación", () => {
    expect(mensajeDeFalla(503, undefined)).toBe(SERVICIO_NO_DISPONIBLE);
  });

  it("una respuesta sin código que no es 503 se explica como conexión", () => {
    expect(mensajeDeFalla(502, undefined)).toBe(SIN_CONEXION);
    expect(mensajeDeFalla(504, undefined)).toBe(SIN_CONEXION);
  });
});
