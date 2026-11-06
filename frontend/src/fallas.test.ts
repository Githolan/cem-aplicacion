import { describe, expect, it } from "vitest";

import { ENTRAR_DE_NUEVO, esDeConexion, LIMITE_DE_PEDIDOS, mensajeDeFalla, RECARGAR, SERVICIO_NO_DISPONIBLE, SIN_CONEXION } from "./fallas";

describe("mensajeDeFalla (E34)", () => {
  it("un 5xx de la aplicación, con su código, es del servicio", () => {
    expect(mensajeDeFalla(500, "error_interno")).toBe(SERVICIO_NO_DISPONIBLE);
    expect(mensajeDeFalla(502, "error_interno")).toBe(SERVICIO_NO_DISPONIBLE);
  });

  it("un 503 es del servicio aunque no traiga el cuerpo de la aplicación", () => {
    expect(mensajeDeFalla(503, undefined)).toBe(SERVICIO_NO_DISPONIBLE);
  });

  it("un 5xx sin cuerpo de la aplicación (el borde) también es del servicio", () => {
    expect(mensajeDeFalla(502, undefined)).toBe(SERVICIO_NO_DISPONIBLE);
    expect(mensajeDeFalla(504, undefined)).toBe(SERVICIO_NO_DISPONIBLE);
  });

  it("la conexión se nombra solo si no hubo respuesta", () => {
    expect(mensajeDeFalla(0, undefined)).toBe(SIN_CONEXION);
    expect(mensajeDeFalla(404, undefined)).toBe(RECARGAR); // control: respondió alguien
  });

  it("un 429 de la aplicación es el límite de pedidos, no el servicio", () => {
    expect(mensajeDeFalla(429, "demasiadas_peticiones")).toBe(LIMITE_DE_PEDIDOS);
  });

  it("un 4xx de la aplicación sin mensaje propio pide recargar", () => {
    expect(mensajeDeFalla(404, "no_encontrado")).toBe(RECARGAR);
    expect(mensajeDeFalla(422, "peticion_invalida")).toBe(RECARGAR);
  });

  it("una acción que pide una entrada reciente dice que hay que volver a entrar", () => {
    expect(mensajeDeFalla(403, "reautenticacion_requerida")).toBe(ENTRAR_DE_NUEVO);
    expect(mensajeDeFalla(403, "sin_permiso")).toBe(RECARGAR); // control
  });

  it("esDeConexion solo cuando no hubo respuesta", () => {
    expect(esDeConexion(0, undefined)).toBe(true);
    expect(esDeConexion(502, undefined)).toBe(false);
    expect(esDeConexion(503, "servicio_no_disponible")).toBe(false);
    expect(esDeConexion(503, undefined)).toBe(false);
  });
});
