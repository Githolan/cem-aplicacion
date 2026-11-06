import { expect, type Page, type Request, test } from "@playwright/test";

import { simularPermisos, sinFaltasDeAccesibilidad } from "./entorno";

// Lo transversal del rediseño de pagos (§12), servido por el nginx de la imagen con la API simulada
// desde el navegador. Corre en los tres proyectos (escritorio, movil, tablet) con su ancho.
const CSRF = "e2e-csrf-transversales";
const EDIFICIO = "a1111111-1111-4111-8111-111111111111";
const PROVEEDOR = "b2222222-2222-4222-8222-222222222222";
const PERSONA = "99999999-9999-4999-8999-999999999999";
const RUTA_PROVEEDORES = `/api/v1/consorcios/${EDIFICIO}/proveedores`;
const CUIT = "30712345679";
const RAZON = "Plomería SINTETICA SRL (NO REAL)";
const ANCHO_HOJA_CENTRADA_PX = 769;

const sesion = (id: string) => ({
  persona_id: PERSONA,
  nombre: "Ana Pérez",
  correo: "ana@ejemplo.com",
  sesion_id: id,
  vence_en: "2026-11-30T20:00:00Z",
});

const proveedor = {
  id: PROVEEDOR, cuit: CUIT, version: 1, razon_social: RAZON, rubro: "Plomería", contacto: "Marta, de la guardia",
  telefono: "11 5555 0142", whatsapp: "11 5555 0143", correo: "guardia@ejemplo.com", domicilio: "Calle Ejemplo 1234",
  atiende_emergencias: true, estado: "activo", documento_id: null, cuenta_ultimos_digitos: "1234", huella: "ab".repeat(32),
  alta_por: PERSONA, creado_en: "2026-10-30T12:00:00Z", actualizado_en: "2026-10-30T12:00:00Z",
};

type Alta = (pedido: Request, intento: number) => { status: number; json: object };

/** La API de proveedores con la sesión de `sesionId`; el alta responde lo que diga `alta`. */
async function simularApi(pagina: Page, baseURL: string, alta: Alta) {
  const estado = { sesionId: "sesion-vieja", altas: [] as Request[] };
  await pagina.route("**/api/v1/**", async (ruta) => {
    const pedido = ruta.request();
    const camino = new URL(pedido.url()).pathname;
    if (camino === "/api/v1/auth/sesion") return ruta.fulfill({ json: sesion(estado.sesionId) });
    if (camino === "/api/v1/auth/totp")
      return ruta.fulfill({ json: { activo: true, llaves: 0, codigos_recuperacion_restantes: 5, verificado_en: null } });
    if (camino === "/api/v1/auth/totp/verificar") return ruta.fulfill({ json: { vale_hasta: "2026-11-30T20:00:00Z" } });
    if (camino === "/api/v1/auth/codigo") {
      estado.sesionId = "sesion-nueva";
      return ruta.fulfill({ json: {} });
    }
    const permisos = simularPermisos(ruta, camino);
    if (permisos !== null) return permisos;
    if (camino === "/api/v1/me/consorcios")
      return ruta.fulfill({ json: [{ id: EDIFICIO, nombre: "Edificio Azul", vinculos: [] }] });
    if (camino === RUTA_PROVEEDORES && pedido.method() === "GET") return ruta.fulfill({ json: { elementos: [proveedor] } });
    if (camino === RUTA_PROVEEDORES) {
      estado.altas.push(pedido);
      return ruta.fulfill(alta(pedido, estado.altas.length));
    }
    if (camino === `${RUTA_PROVEEDORES}/${PROVEEDOR}`)
      return ruta.fulfill({
        json: {
          proveedor,
          versiones: [{ ...proveedor, preparada_por: PERSONA, creada_en: proveedor.creado_en }],
        },
      });
    return ruta.abort();
  });
  await pagina.context().addCookies([
    { name: "__Host-cem_csrf", value: CSRF, domain: new URL(baseURL).hostname, path: "/", secure: true },
  ]);
  return estado;
}

async function completarAlta(pagina: Page) {
  await pagina.goto("/egresos/proveedores");
  await pagina.getByRole("button", { name: "Dar de alta un proveedor" }).click();
  await pagina.getByLabel(/^CUIT/).fill(CUIT);
  await pagina.getByLabel("Razón social").fill(RAZON);
}

const guardar = (pagina: Page) => pagina.getByRole("button", { name: "Guardar" }).click();

async function sinDesborde(pagina: Page) {
  const { scrollWidth, clientWidth } = await pagina.evaluate(() => {
    const { scrollWidth, clientWidth } = document.documentElement;
    return { scrollWidth, clientWidth };
  });
  expect(scrollWidth).toBeLessThanOrEqual(clientWidth);
}

const creado = { status: 201, json: { id: PROVEEDOR, version: 1 } };

test.describe("entrar encima", () => {
  test("con la sesión vencida entra sobre la misma pantalla, conserva lo escrito y no reenvía", async ({ page, baseURL }) => {
    const vencida = { status: 401, json: { codigo: "no_autenticado", mensaje: "x", id_correlacion: "c" } };
    const estado = await simularApi(page, baseURL!, (_pedido, intento) => (intento === 1 ? vencida : creado));
    await completarAlta(page);
    await guardar(page);

    const hoja = page.getByRole("dialog", { name: "Tu sesión venció" });
    await expect(hoja).toBeVisible();
    expect(new URL(page.url()).pathname).toBe("/egresos/proveedores");
    await sinFaltasDeAccesibilidad(page);

    // Desde abajo en un teléfono, al centro con más ancho: y siempre dentro de la pantalla.
    const caja = await hoja.boundingBox();
    const ventana = page.viewportSize()!;
    expect(caja).not.toBeNull();
    expect(caja!.x).toBeGreaterThanOrEqual(0);
    expect(caja!.x + caja!.width).toBeLessThanOrEqual(ventana.width);
    if (ventana.width < ANCHO_HOJA_CENTRADA_PX) expect(caja!.y + caja!.height).toBeCloseTo(ventana.height, 0);
    await sinDesborde(page);

    await hoja.getByRole("button", { name: "Entrar con un código" }).click();
    await expect(hoja.getByLabel("Correo")).toHaveValue("ana@ejemplo.com");
    await hoja.getByLabel(/Código de 6 números/).fill("042917");
    await hoja.getByRole("button", { name: "Entrar con el código" }).click();

    await expect(page.getByText("Seguís donde estabas.")).toBeVisible();
    await expect(hoja).toHaveCount(0);
    await expect(page.getByLabel("Razón social")).toHaveValue(RAZON);
    expect(estado.altas).toHaveLength(1); // control: el pedido no salió solo

    await guardar(page);
    await expect(page.getByText("Listo: dimos de alta el proveedor.")).toBeVisible();
    expect(estado.altas).toHaveLength(2);
    const [primera, segunda] = await Promise.all(estado.altas.map((a) => a.headerValue("idempotency-key")));
    expect(primera).toBeTruthy();
    expect(primera).toBe(segunda);
  });

  test("con más de 12 horas también, sin aviso viejo al volver", async ({ page, baseURL }) => {
    const vieja = { status: 403, json: { codigo: "sesion_reciente_requerida", mensaje: "x", id_correlacion: "c" } };
    await simularApi(page, baseURL!, (_pedido, intento) => (intento === 1 ? vieja : creado));
    await completarAlta(page);
    await guardar(page);

    const hoja = page.getByRole("dialog", { name: "Tu sesión venció" });
    await hoja.getByRole("button", { name: "Entrar con un código" }).click();
    await hoja.getByLabel(/Código de 6 números/).fill("042917");
    await hoja.getByRole("button", { name: "Entrar con el código" }).click();

    await expect(page.getByText("Seguís donde estabas.")).toBeVisible();
    await expect(page.getByText(/más de 12 horas/)).toHaveCount(0);
    await expect(page.getByLabel("Razón social")).toHaveValue(RAZON);
  });
});

test.describe("confirmar directo", () => {
  test("va directo al último método, sin «Confirmá que sos vos», y sigue con el mismo pedido", async ({ page, baseURL }) => {
    const falta = { status: 403, json: { codigo: "segundo_factor_requerido", mensaje: "x", id_correlacion: "c" } };
    const estado = await simularApi(page, baseURL!, (_pedido, intento) => (intento === 1 ? falta : creado));
    await completarAlta(page);
    await guardar(page);

    const hoja = page.getByRole("dialog", { name: "Escribí el código de tu aplicación" });
    await expect(hoja).toBeVisible();
    await expect(page.getByText("Confirmá que sos vos")).toHaveCount(0);
    await sinFaltasDeAccesibilidad(page);
    await sinDesborde(page);

    await hoja.getByLabel("Código", { exact: true }).fill("042917");

    await expect(page.getByText("Listo: dimos de alta el proveedor.")).toBeVisible();
    expect(estado.altas).toHaveLength(2);
    const [primera, segunda] = await Promise.all(estado.altas.map((a) => a.headerValue("idempotency-key")));
    expect(primera).toBe(segunda);
    expect(await page.evaluate(() => window.localStorage.getItem("cem.confirmar.ultimo-metodo"))).toBe("aplicacion");
  });
});

test.describe("datos del proveedor", () => {
  test("el CUIT es obligatorio: sin él el formulario no sale", async ({ page, baseURL }) => {
    const estado = await simularApi(page, baseURL!, () => creado);
    await page.goto("/egresos/proveedores");
    await page.getByRole("button", { name: "Dar de alta un proveedor" }).click();
    await page.getByLabel("Razón social").fill(RAZON);
    await guardar(page);

    expect(estado.altas).toHaveLength(0);
    await expect(page.getByLabel(/^CUIT/)).toHaveJSProperty("validity.valueMissing", true);
    await sinDesborde(page);

    await page.getByLabel(/^CUIT/).fill(CUIT);
    await page.getByLabel("Teléfono (opcional)").fill("11 5555 0142");
    await page.getByLabel("Atiende emergencias").check();
    await guardar(page);
    await expect(page.getByText("Listo: dimos de alta el proveedor.")).toBeVisible();
    expect(estado.altas[0]?.postDataJSON()).toMatchObject({ cuit: CUIT, telefono: "11 5555 0142", atiende_emergencias: true });
  });

  test("la ficha muestra el contacto", async ({ page, baseURL }) => {
    await simularApi(page, baseURL!, () => creado);
    await page.goto("/egresos/proveedores");
    await page.getByRole("button", { name: `Ver la ficha de ${RAZON}` }).click();

    for (const dato of [proveedor.contacto, proveedor.telefono, proveedor.whatsapp, proveedor.correo, proveedor.domicilio])
      await expect(page.getByText(dato, { exact: true })).toBeVisible();
    await expect(page.getByText("Atiende emergencias")).toBeVisible();
    await sinDesborde(page);
    await sinFaltasDeAccesibilidad(page);
  });
});
