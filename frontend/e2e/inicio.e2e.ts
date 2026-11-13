import { expect, test } from "@playwright/test";

import type { Salud } from "../src/api/cliente";
import {
  LARGO_SHA_CORTA,
  otroCliente,
  SHA_BACKEND,
  URL_SIN_BACKEND,
  simularPuntero,
  simularSesion,
  sinFaltasDeAccesibilidad,
} from "./entorno";

const TITULO_INICIO = "Consorcio en mano";
const TITULO_ENTRAR = "Entrar a Consorcio en Mano";
const ALTO_TACTIL_PX = 44;

test.describe("inicio contra el cem-backend real", () => {
  // Un cupo propio de /api/v1/auth/: ver otroCliente en entorno.ts.
  test.use(otroCliente(13));

  test("muestra el estado leído de /health a través del nginx de la imagen", async ({ page }) => {
    await simularSesion(page, "con_sesion");
    const respuestaSalud = page.waitForResponse((r) => new URL(r.url()).pathname === "/health");
    await page.goto("/");

    const salud = await respuestaSalud;
    expect(salud.status()).toBe(200);
    const cuerpo = (await salud.json()) as Salud;
    expect(cuerpo.sha_imagen).toBe(SHA_BACKEND);

    // Un punto en la barra, con nombre accesible; la versión queda al pie de Cuenta.
    await expect(page.getByRole("img", { name: "El servicio funciona" })).toBeVisible();
    await expect(page.getByRole("banner").getByRole("img", { name: "El servicio funciona" })).toBeVisible();
    await expect(page.getByText(SHA_BACKEND.slice(0, LARGO_SHA_CORTA))).toHaveCount(0);
    await expect(page.getByText("El servicio no funciona")).toHaveCount(0);
    await sinFaltasDeAccesibilidad(page);
  });

  test("sin sesión, el 401 real del backend lleva a /entrar", async ({ page }) => {
    const consulta = page.waitForResponse((r) => new URL(r.url()).pathname === "/api/v1/auth/sesion");
    await page.goto("/");

    const respuesta = await consulta;
    expect(respuesta.status()).toBe(401);
    expect(((await respuesta.json()) as { codigo: string }).codigo).toBe("no_autenticado");
    await expect(page).toHaveURL(/\/entrar$/);
    await expect(page.getByRole("heading", { level: 1 })).toHaveText(TITULO_ENTRAR);
    await sinFaltasDeAccesibilidad(page);
  });

  test("control: con sesión se queda en /", async ({ page }) => {
    await simularSesion(page, "con_sesion");
    await page.goto("/");

    await expect(page.getByRole("heading", { level: 1 })).toHaveText(TITULO_INICIO);
    await expect(page.getByRole("img", { name: "El servicio funciona" })).toBeVisible();
    expect(new URL(page.url()).pathname).toBe("/");
  });

  test("enlaza a /instalar", async ({ page }) => {
    await simularPuntero(page, false);
    await simularSesion(page, "con_sesion");
    await page.goto("/");
    await page.getByRole("link", { name: "Instalar la aplicación en este dispositivo" }).click();
    await expect(page).toHaveURL(/\/instalar$/);
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("Instalar Consorcio en Mano en este dispositivo");
  });

  test("control: en una computadora cuyo navegador no la ofrece, no enlaza a /instalar", async ({ page }) => {
    await simularPuntero(page, true);
    await simularSesion(page, "con_sesion");
    await page.goto("/");

    await expect(page.getByRole("link", { name: "Cuenta" })).toBeVisible();
    await expect(page.getByRole("link", { name: /Instalar la aplicación/ })).toHaveCount(0);
  });

  test("la marca de la cabecera lleva al inicio y se puede tocar", async ({ page }) => {
    await simularSesion(page, "con_sesion");
    await page.goto("/una/ruta/que/no/existe");
    const marca = page.getByRole("banner").getByRole("link", { name: "Consorcio en Mano" });

    expect((await marca.boundingBox())?.height).toBeGreaterThanOrEqual(ALTO_TACTIL_PX);
    await marca.click();

    await expect(page).toHaveURL(/\/$/);
    await expect(page.getByRole("heading", { level: 1 })).toHaveText(TITULO_INICIO);
  });

  test("sin edificios dice cómo sumarse, y con una invitación la ofrece", async ({ page }) => {
    let invitaciones: object[] = [];
    await simularSesion(page, "con_sesion");
    await page.context().route("**/api/v1/me/invitaciones", (ruta) => ruta.fulfill({ json: { elementos: invitaciones } }));
    await page.goto("/");

    await expect(page.getByText(/hay que abrir el enlace de la invitación que llegó por correo/)).toBeVisible();
    await expect(page.getByRole("region", { name: "Invitaciones pendientes" })).toHaveCount(0);

    invitaciones = [
      {
        id: "01a1a3a8-c5e2-7b8e-8a84-aa8992c79f3d",
        consorcio: "Edificio Azul",
        unidad: "1° A",
        tipo: "inquilino",
        nombre: "Ana Pérez",
        invitada_por: "Beto",
        vence_en: "2026-11-20T12:00:00Z",
      },
    ];
    await page.reload();

    const seccion = page.getByRole("region", { name: "Invitaciones pendientes" });
    await expect(seccion.getByRole("button", { name: "Aceptar la invitación" })).toBeVisible();
    await expect(page.getByText(/hay que abrir el enlace de la invitación que llegó por correo/)).toHaveCount(0);
    await sinFaltasDeAccesibilidad(page);
  });

  test("una ruta desconocida cae en la SPA y muestra la página de no encontrada", async ({ page }) => {
    await simularSesion(page, "con_sesion");
    const respuesta = await page.goto("/una/ruta/que/no/existe");
    expect(respuesta?.status()).toBe(200);
    await expect(page.getByRole("heading", { name: "No se encontró esa página" })).toBeVisible();
    await sinFaltasDeAccesibilidad(page);
    await page.getByRole("link", { name: "Volver al inicio" }).click();
    await expect(page).toHaveURL(/\/$/);
    await expect(page.getByRole("heading", { level: 1 })).toHaveText(TITULO_INICIO);
  });

  test("/api/ llega al backend real, que exige sesión", async ({ request }) => {
    const respuesta = await request.get("/api/v1/me/consorcios");
    expect(respuesta.status()).toBe(401);
    expect(((await respuesta.json()) as { codigo: string }).codigo).toBe("no_autenticado");
  });
});

test.describe("inicio sin backend", () => {
  test("no se sabe si hay sesión: sigue en /, avisa que no hay servicio y ofrece reintentar", async ({ page }) => {
    const consulta = page.waitForResponse((r) => new URL(r.url()).pathname === "/api/v1/auth/sesion");
    await page.goto(URL_SIN_BACKEND + "/");
    expect((await consulta).status()).toBeGreaterThanOrEqual(500);
    await expect(page.getByRole("img", { name: "El servicio no funciona" })).toBeVisible();
    await expect(page.getByRole("alert").filter({ hasText: "El servicio no funciona en este momento" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Reintentar" }).first()).toBeVisible();
    await expect(page.getByRole("heading", { level: 1 })).toHaveText(TITULO_INICIO);
    expect(new URL(page.url()).pathname).toBe("/");
    await sinFaltasDeAccesibilidad(page);
  });
});
