import { expect, test } from "@playwright/test";

import { sinFaltasDeAccesibilidad } from "./entorno";

// Pedir el enlace para entrar en /entrar, contra el cem-backend real a través del nginx de la
// imagen. El backend de la corrida no tiene base de datos ni CEM_ORIGEN_PUBLICO, así que responde
// 503 antes de registrar nada: el 202 y la cookie nonce se prueban en el backend
// (tests/test_identidad*.py); aquí, que el formulario manda el correo por POST, en el cuerpo como
// JSON, que nada queda en la URL, que la página avisa sin colgarse y que un 422 de verdad (la
// validación del contrato corre antes que el servicio) marca el campo.
const CORREO = "ana@ejemplo.com";
// Válido para el navegador (type="email" no exige punto en el dominio), inválido para el contrato.
const CORREO_SIN_DOMINIO = "ana@ejemplo";
const RUTA = "/api/v1/auth/enlace";

test.describe("pedir el enlace para entrar", () => {
  test("manda el correo por POST, en el cuerpo, y avisa si el servicio no responde", async ({ page }) => {
    await page.goto("/entrar");
    await sinFaltasDeAccesibilidad(page);

    const seccion = page.getByRole("region", { name: "Pedir el enlace para entrar" });
    await expect(seccion.getByText(/en otro dispositivo, usá el código de 6 números/)).toBeVisible();
    await seccion.getByLabel("Correo").fill(CORREO);
    const pedido = page.waitForRequest((r) => new URL(r.url()).pathname === RUTA);
    await seccion.getByRole("button", { name: "Pedir el enlace" }).click();

    const enviado = await pedido;
    expect(enviado.method()).toBe("POST");
    expect(new URL(enviado.url()).search).toBe("");
    expect(await enviado.headerValue("content-type")).toBe("application/json");
    expect(enviado.postDataJSON()).toEqual({ correo: CORREO });
    const respuesta = await enviado.response();
    expect(respuesta?.status()).toBe(503);
    expect(((await respuesta?.json()) as { codigo: string }).codigo).toBe("servicio_no_disponible");

    await expect(seccion.getByText(/No pudimos completar el pedido/)).toBeVisible();
    expect(page.url()).not.toContain(CORREO);
    await sinFaltasDeAccesibilidad(page);
  });

  test("un correo que el contrato rechaza (422) se explica junto al campo", async ({ page }) => {
    await page.goto("/entrar");
    const seccion = page.getByRole("region", { name: "Pedir el enlace para entrar" });
    const campo = seccion.getByLabel("Correo");
    await campo.fill(CORREO_SIN_DOMINIO);
    const pedido = page.waitForRequest((r) => new URL(r.url()).pathname === RUTA);
    await seccion.getByRole("button", { name: "Pedir el enlace" }).click();

    const respuesta = await (await pedido).response();
    expect(respuesta?.status()).toBe(422);
    await expect(seccion.getByText("Revisá el correo: no parece una dirección válida.")).toBeVisible();
    await expect(campo).toHaveAttribute("aria-invalid", "true");
    await sinFaltasDeAccesibilidad(page);
  });
});
