import { expect, test } from "@playwright/test";

import { sinFaltasDeAccesibilidad } from "./entorno";

// Página /entrar contra el cem-backend real, a través del nginx de la imagen. El backend de la
// corrida no tiene base de datos, así que el canje feliz se prueba en el backend
// (tests/test_identidad.py); aquí, un token sin la cookie nonce de este navegador, que el
// backend rechaza antes de consultar la base.
const TOKEN = "Z".repeat(43);

test.describe("entrar con el enlace del correo", () => {
  test("canjea el token del fragmento por POST, en el cuerpo, y lo saca de la URL", async ({ page }) => {
    const canje = page.waitForRequest((r) => new URL(r.url()).pathname === "/api/v1/auth/canje");
    await page.goto(`/entrar#token=${TOKEN}`);

    const pedido = await canje;
    expect(pedido.method()).toBe("POST");
    expect(new URL(pedido.url()).search).toBe("");
    expect(pedido.postDataJSON()).toEqual({ token: TOKEN });
    const respuesta = await pedido.response();
    expect(respuesta?.status()).toBe(401);
    expect(((await respuesta?.json()) as { codigo: string }).codigo).toBe("enlace_invalido");

    await expect(page.getByText(/No pudimos hacerte entrar con este enlace/)).toBeVisible();
    expect(page.url()).not.toContain(TOKEN);
    expect(new URL(page.url()).pathname).toBe("/entrar");
    await sinFaltasDeAccesibilidad(page);
  });

  test("sin enlace, explica qué hacer y no consulta al backend", async ({ page }) => {
    const pedidos: string[] = [];
    page.on("request", (r) => pedidos.push(new URL(r.url()).pathname));
    await page.goto("/entrar");
    await expect(page.getByText(/abrí el enlace que te mandamos por correo/)).toBeVisible();
    expect(pedidos).not.toContain("/api/v1/auth/canje");
    await sinFaltasDeAccesibilidad(page);
  });
});
