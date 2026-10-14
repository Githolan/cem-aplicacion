import { expect, test } from "@playwright/test";

import type { Salud } from "../src/api/cliente";
import { LARGO_SHA_CORTA, SHA_BACKEND, URL_SIN_BACKEND, sinFaltasDeAccesibilidad } from "./entorno";

test.describe("inicio contra el cem-backend real", () => {
  test("muestra el estado leído de /health a través del nginx de la imagen", async ({ page }) => {
    const respuestaSalud = page.waitForResponse((r) => new URL(r.url()).pathname === "/health");
    await page.goto("/");

    const salud = await respuestaSalud;
    expect(salud.status()).toBe(200);
    const cuerpo = (await salud.json()) as Salud;
    expect(cuerpo.sha_imagen).toBe(SHA_BACKEND);

    const estado = page.getByRole("status");
    await expect(estado.getByText("El servicio funciona.")).toBeVisible();
    await expect(estado.locator("code")).toHaveText(SHA_BACKEND.slice(0, LARGO_SHA_CORTA));
    await expect(estado.locator("dd").first()).toHaveText(cuerpo.version);
    await sinFaltasDeAccesibilidad(page);
  });

  test("enlaza a /instalar", async ({ page }) => {
    await page.goto("/");
    await page.getByRole("link", { name: "Instalá la aplicación en tu teléfono" }).click();
    await expect(page).toHaveURL(/\/instalar$/);
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("Instalá Consorcio en Mano en tu teléfono");
  });

  test("una ruta desconocida cae en la SPA y muestra la página de no encontrada", async ({ page }) => {
    const respuesta = await page.goto("/una/ruta/que/no/existe");
    expect(respuesta?.status()).toBe(200);
    await expect(page.getByRole("heading", { name: "No encontramos esa página" })).toBeVisible();
    await sinFaltasDeAccesibilidad(page);
    await page.getByRole("link", { name: "Volver al inicio" }).click();
    await expect(page).toHaveURL(/\/$/);
  });

  test("/api/ llega al backend real, que exige sesión", async ({ request }) => {
    const respuesta = await request.get("/api/v1/me/consorcios");
    expect(respuesta.status()).toBe(401);
    expect(((await respuesta.json()) as { codigo: string }).codigo).toBe("no_autenticado");
  });
});

test.describe("inicio sin backend", () => {
  test("avisa que no hay servicio y ofrece reintentar", async ({ page }) => {
    await page.goto(URL_SIN_BACKEND + "/");
    const estado = page.getByRole("status");
    await expect(estado.getByText(/No pudimos contactar al servicio/)).toBeVisible();
    await expect(estado.getByRole("button", { name: "Reintentar" })).toBeVisible();
    await sinFaltasDeAccesibilidad(page);
  });
});
