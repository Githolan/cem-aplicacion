import { describe, expect, it } from "vitest";

import {
  aBytes,
  aTexto,
  llaveDisponible,
  nombreDelDispositivo,
  opcionesDeAlta,
  opcionesDeVerificacion,
} from "./llaveAcceso";

describe("llave de acceso", () => {
  it("ida y vuelta de base64url, con los caracteres que cambian", () => {
    const datos = Uint8Array.from([251, 255, 254, 0, 62, 63]).buffer;
    const texto = aTexto(datos);
    expect(texto).toBe("-__-AD4_");
    expect(new Uint8Array(aBytes(texto))).toEqual(new Uint8Array(datos));
  });

  it("las opciones de alta llevan binarios donde WebAuthn los pide", () => {
    const opciones = opcionesDeAlta({
      challenge: "AAEC",
      user: { id: "AwQF", name: "a", displayName: "a" },
      excludeCredentials: [{ id: "BgcI", type: "public-key" }],
    });
    expect(new Uint8Array(opciones.challenge as ArrayBuffer)).toEqual(Uint8Array.from([0, 1, 2]));
    expect(new Uint8Array(opciones.user.id as ArrayBuffer)).toEqual(Uint8Array.from([3, 4, 5]));
    const excluida = opciones.excludeCredentials?.[0];
    expect(new Uint8Array(excluida?.id as ArrayBuffer)).toEqual(Uint8Array.from([6, 7, 8]));
  });

  it("las de verificación también, y sin lista quedan vacías", () => {
    const opciones = opcionesDeVerificacion({ challenge: "AAEC" });
    expect(new Uint8Array(opciones.challenge as ArrayBuffer)).toEqual(Uint8Array.from([0, 1, 2]));
    expect(opciones.allowCredentials).toEqual([]);
  });

  it("nombra el dispositivo sin pedirle nada a la persona", () => {
    expect(nombreDelDispositivo("Mozilla/5.0 (Windows NT 10.0; Win64; x64)")).toBe("Computadora con Windows");
    expect(nombreDelDispositivo("Mozilla/5.0 (Linux; Android 14)")).toBe("Teléfono Android");
    expect(nombreDelDispositivo("Mozilla/5.0 (iPhone; CPU iPhone OS 18_0)")).toBe("iPhone o iPad");
    expect(nombreDelDispositivo("otro")).toBe("Este dispositivo");
  });

  it("sin soporte del navegador no se ofrece", () => {
    expect(llaveDisponible()).toBe(false);
  });
});
