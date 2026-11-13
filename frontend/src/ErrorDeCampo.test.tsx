import { describe, expect, it } from "vitest";

import { cuentaConError, cuentaValida } from "./cuentaBancaria";
import { erroresDe, marcaDe, resumenDe, REVISAR_DATO, REVISAR_FORMULARIO, sinElDe, soloDe } from "./ErrorDeCampo";

// El mecanismo común de la tanda 3: el error junto al campo. Datos ficticios.

const NOMBRES = { cuit: "el CUIT", correo: "el correo", fecha: "la fecha" };

describe("errores de campo", () => {
  it("toma el mensaje de cada campo del 422, y el de reserva si falta", () => {
    const error = {
      detalle: { campos: ["body.correo", "body.cuit", "header.Idempotency-Key"], mensajes: { "body.correo": "Escribir un correo válido." } },
    };
    expect(erroresDe(error)).toEqual({ correo: "Escribir un correo válido.", cuit: REVISAR_DATO });
    // Un error de negocio trae solo el campo: su mensaje es el del código.
    expect(erroresDe({ detalle: { campos: ["body.cuit"] } }, "Ya hay un proveedor con ese CUIT.")).toEqual({
      cuit: "Ya hay un proveedor con ese CUIT.",
    });
    // Control: sin detalle, ningún campo.
    expect(erroresDe({ codigo: "x" })).toEqual({});
    expect(erroresDe(undefined)).toEqual({});
  });

  it("un mensaje propio de la pantalla reemplaza el de la API", () => {
    const error = { detalle: { campos: ["body.titulo"], mensajes: { "body.titulo": "otro" } } };
    expect(erroresDe(error, REVISAR_DATO, { titulo: "El título no puede llevar teléfonos ni correos." })).toEqual({
      titulo: "El título no puede llevar teléfonos ni correos.",
    });
  });

  it("solo marca los campos de la pantalla y el resumen los nombra", () => {
    const errores = soloDe({ cuit: "a", fecha: "b", otro: "c" }, NOMBRES);
    expect(errores).toEqual({ cuit: "a", fecha: "b" });
    expect(resumenDe(errores, NOMBRES)).toBe("Hay que corregir el CUIT y la fecha: el motivo está junto a cada campo.");
    expect(resumenDe({ correo: "x" }, NOMBRES)).toBe("Hay que corregir el correo: el motivo está junto a cada campo.");
    expect(resumenDe({}, NOMBRES)).toBe(REVISAR_FORMULARIO);
    expect(sinElDe(errores, "cuit")).toEqual({ fecha: "b" });
  });

  it("el control queda marcado y descrito por su mensaje, sin perder su ayuda", () => {
    expect(marcaDe("c", "Revisar.", "ayuda")).toEqual({ "aria-invalid": true, "aria-describedby": "ayuda c-error" });
    expect(marcaDe("c", undefined, "ayuda")).toEqual({ "aria-invalid": undefined, "aria-describedby": "ayuda" });
    expect(marcaDe("c", undefined)).toEqual({ "aria-invalid": undefined, "aria-describedby": undefined });
  });
});

describe("cuenta destino (P-26)", () => {
  it("comprueba los dos dígitos verificadores, como el contrato", () => {
    expect(cuentaValida("0000003160000000001234")).toBe(true);
    expect(cuentaValida("0000003140000000009876")).toBe(true);
    // El segundo verificador mal, el primero mal, y el primer bloque en ceros.
    expect(cuentaValida("0000003160000000001235")).toBe(false);
    expect(cuentaValida("0000003260000000001234")).toBe(false);
    expect(cuentaValida("0000000000000000000000")).toBe(false);
  });

  it("solo avisa cuando ya están los 22 números", () => {
    expect(cuentaConError("0000003160000000001235")).toBe(true);
    expect(cuentaConError("000000316")).toBe(false);
    expect(cuentaConError("")).toBe(false);
    expect(cuentaConError("0000003160000000001234")).toBe(false);
  });
});
