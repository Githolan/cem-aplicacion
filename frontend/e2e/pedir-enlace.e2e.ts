import { expect, test } from "@playwright/test";

import { otroCliente, simularSesion, sinFaltasDeAccesibilidad } from "./entorno";

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
  // Un cupo propio de /api/v1/auth/: ver otroCliente en entorno.ts.
  test.use(otroCliente(12));

  test("manda el correo por POST, en el cuerpo, y avisa si el servicio no responde", async ({ page }) => {
    await simularSesion(page, "sin_sesion");
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

    await expect(seccion.getByText(/El servicio no está disponible/)).toBeVisible();
    expect(page.url()).not.toContain(CORREO);
    await sinFaltasDeAccesibilidad(page);
  });

  test("un correo que el contrato rechaza (422) se explica junto al campo", async ({ page }) => {
    await simularSesion(page, "sin_sesion");
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

  test("con el pedido hecho (202 simulado) pasa al código, con el correo escrito y el mensaje", async ({ page }) => {
    const mensaje = "Si el correo corresponde a una cuenta, en unos minutos te llega un enlace para entrar.";
    await simularSesion(page, "sin_sesion");
    await page.route(`**${RUTA}`, (ruta) => ruta.fulfill({ status: 202, json: { estado: "pedido", mensaje } }));
    await page.goto("/entrar");
    const seccion = page.getByRole("region", { name: "Pedir el enlace para entrar" });
    await seccion.getByLabel("Correo").fill(`  ${CORREO} `);
    await seccion.getByRole("button", { name: "Pedir el enlace" }).click();

    const codigo = page.getByRole("region", { name: "Entrar con un código" });
    await expect(codigo.getByText(mensaje)).toBeVisible();
    await expect(codigo.getByLabel("Correo")).toHaveValue(CORREO);
    // El correo ya está: el foco va a lo que falta, y el código se envía recién con el botón.
    await expect(codigo.getByLabel(/^Código de 6 números/)).toBeFocused();
    await expect(seccion).toHaveCount(0);
    expect(page.url()).not.toContain(CORREO);
    await sinFaltasDeAccesibilidad(page);
  });
});
