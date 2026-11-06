import { expect, test } from "@playwright/test";

import { otroCliente, simularSesion, sinFaltasDeAccesibilidad } from "./entorno";

// Entrar con el código del correo en /entrar, contra el cem-backend real a través del nginx de la
// imagen. El backend de la corrida no tiene base de datos ni CEM_CODIGO_CLAVE, así que responde
// 503 antes de consultar nada: el canje feliz se prueba en el backend
// (tests/test_identidad_codigo.py); aquí, que el formulario manda correo y código por POST, en el
// cuerpo como JSON, que nada queda en la URL y que la página avisa sin colgarse.
const CORREO = "ana@ejemplo.com";
const CODIGO = "042917";

test.describe("entrar con el código del correo", () => {
  // Un cupo propio de /api/v1/auth/: ver otroCliente en entorno.ts.
  test.use(otroCliente(11));

  test("manda correo y código por POST, en el cuerpo, y avisa si el servicio no responde", async ({ page }) => {
    await simularSesion(page, "sin_sesion");
    await page.goto("/entrar");
    await page.getByRole("button", { name: "Entrar con un código" }).click();
    await sinFaltasDeAccesibilidad(page);

    await page.getByRole("region", { name: "Entrar con un código" }).getByLabel("Correo").fill(CORREO);
    await page.getByLabel(/^Código de 6 números/).fill("042 917");
    const canje = page.waitForRequest((r) => new URL(r.url()).pathname === "/api/v1/auth/codigo");
    await page.getByRole("button", { name: "Entrar con el código" }).click();

    const pedido = await canje;
    expect(pedido.method()).toBe("POST");
    expect(new URL(pedido.url()).search).toBe("");
    expect(await pedido.headerValue("content-type")).toBe("application/json");
    expect(pedido.postDataJSON()).toEqual({ correo: CORREO, codigo: CODIGO });
    const respuesta = await pedido.response();
    expect(respuesta?.status()).toBe(503);
    expect(((await respuesta?.json()) as { codigo: string }).codigo).toBe("servicio_no_disponible");

    await expect(page.getByText(/El servicio no está disponible/)).toBeVisible();
    expect(page.url()).not.toContain(CODIGO);
    await sinFaltasDeAccesibilidad(page);
  });

  test("un código mal formado no sale y se explica junto al campo", async ({ page }) => {
    const pedidos: string[] = [];
    page.on("request", (r) => pedidos.push(new URL(r.url()).pathname));
    await simularSesion(page, "sin_sesion");
    await page.goto("/entrar");
    await page.getByRole("button", { name: "Entrar con un código" }).click();

    await page.getByRole("region", { name: "Entrar con un código" }).getByLabel("Correo").fill(CORREO);
    const campo = page.getByLabel(/^Código de 6 números/);
    await campo.fill("04291");
    await page.getByRole("button", { name: "Entrar con el código" }).click();

    await expect(page.getByText("El código tiene 6 números.")).toBeVisible();
    await expect(campo).toHaveAttribute("aria-invalid", "true");
    expect(pedidos).not.toContain("/api/v1/auth/codigo");
    await sinFaltasDeAccesibilidad(page);
  });
});
