import { expect, test } from "@playwright/test";

import { otroCliente, simularSesion, sinFaltasDeAccesibilidad } from "./entorno";

// Página /entrar contra el cem-backend real, a través del nginx de la imagen. El backend de la
// corrida no tiene base de datos, así que el canje feliz se prueba en el backend
// (tests/test_identidad.py); aquí, un token sin la cookie nonce de este navegador, que el
// backend rechaza antes de consultar la base.
const TOKEN = "Z".repeat(43);

test.describe("entrar con el enlace del correo", () => {
  // Un cupo propio de /api/v1/auth/: ver otroCliente en entorno.ts.
  test.use(otroCliente(10));

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

    await expect(page.getByText(/Este enlace no sirve acá/)).toBeVisible();
    expect(page.url()).not.toContain(TOKEN);
    expect(new URL(page.url()).pathname).toBe("/entrar");
    await sinFaltasDeAccesibilidad(page);
  });

  test("sin enlace ofrece pedirlo y no canjea nada", async ({ page }) => {
    await simularSesion(page, "sin_sesion");
    const pedidos: string[] = [];
    page.on("request", (r) => pedidos.push(new URL(r.url()).pathname));
    await page.goto("/entrar");
    await expect(page.getByRole("region", { name: "Pedir un código para entrar" })).toBeVisible();
    expect(pedidos).not.toContain("/api/v1/auth/canje");
    await sinFaltasDeAccesibilidad(page);
  });
});

test.describe("/entrar decide por la sesión", () => {
  test("con sesión lleva al inicio", async ({ page }) => {
    await simularSesion(page, "con_sesion");
    await page.goto("/entrar");

    await expect(page.getByRole("heading", { level: 1 })).toHaveText("Consorcio en mano");
    expect(new URL(page.url()).pathname).toBe("/");
  });

  test("control: sin sesión se queda en /entrar", async ({ page }) => {
    await simularSesion(page, "sin_sesion");
    const consulta = page.waitForResponse((r) => new URL(r.url()).pathname === "/api/v1/auth/sesion");
    await page.goto("/entrar");

    await consulta;
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("Entrar a Consorcio en Mano");
    expect(new URL(page.url()).pathname).toBe("/entrar");
  });

  test("con un enlace en el fragmento canjea aunque haya sesión: no se lo saltea yendo al inicio", async ({ page }) => {
    await simularSesion(page, "con_sesion");
    await page.route("**/api/v1/auth/canje", (ruta) =>
      ruta.fulfill({ status: 401, json: { codigo: "enlace_invalido", mensaje: "x", id_correlacion: "c" } }),
    );
    await page.goto(`/entrar#token=${TOKEN}`);

    await expect(page.getByText(/Este enlace no sirve acá/)).toBeVisible();
    expect(new URL(page.url()).pathname).toBe("/entrar");
  });
});

test.describe("/entrar: una forma por vez", () => {
  const CODIGO = { name: "Escribir el código del correo" };
  const CONTRASENA = { name: "Entrar con contraseña" };
  const ENLACE = { name: "Pedir un código para entrar" };

  test("empieza por el enlace, cambia de forma y deja el foco en el primer campo", async ({ page }) => {
    await simularSesion(page, "sin_sesion");
    await page.goto("/entrar");
    await expect(page.getByRole("region", ENLACE)).toBeVisible();
    await expect(page.getByRole("region", CODIGO)).toHaveCount(0);
    await expect(page.getByRole("region", CONTRASENA)).toHaveCount(0);

    await page.getByRole("button", { name: "Entrar con contraseña" }).click();
    await expect(page.getByRole("region", CONTRASENA).getByLabel("Correo")).toBeFocused();
    await expect(page.getByRole("region", ENLACE)).toHaveCount(0);
    await sinFaltasDeAccesibilidad(page);

    await page.getByRole("button", { name: "Ya tengo un código" }).click();
    await expect(page.getByRole("region", CODIGO).getByLabel("Correo")).toBeFocused();
    await expect(page.getByRole("region", CONTRASENA)).toHaveCount(0);
    await sinFaltasDeAccesibilidad(page);

    await page.getByRole("button", { name: "Pedir un código por correo" }).click();
    await expect(page.getByRole("region", ENLACE).getByLabel("Correo")).toBeFocused();
    await expect(page.getByRole("region", CODIGO)).toHaveCount(0);
  });
});
