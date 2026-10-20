import { expect, type Page, test } from "@playwright/test";

import { otroCliente, sinFaltasDeAccesibilidad } from "./entorno";

// La página /invitaciones servida por el nginx de la imagen, con su CSP real. El backend de la
// corrida no tiene base de datos: sin sesión responde el 401 de verdad y la página manda a
// /entrar. Con sesión, la API se simula desde el navegador. Quién puede invitar a qué unidad lo
// decide la política `invitaciones.crear` del backend y lo prueban sus pruebas (test_invitaciones,
// test_contexto_mandatos) y el smoke autenticado de staging. Acá el simulador responde 403 fuera
// de la unidad propia, como el backend, para ver lo que solo un navegador real muestra: el aviso,
// que la página entra en un teléfono, el contraste de WCAG AA, que la cookie CSRF y la clave de
// reintento viajan como encabezados y que no hay violaciones de la CSP.
const CSRF = "e2e-csrf-invitar";
const C1 = "11111111-1111-4111-8111-111111111111";
const U1 = "33333333-3333-4333-8333-333333333333";
const U2 = "44444444-4444-4444-8444-444444444444";
const PENDIENTE = "55555555-5555-4555-8555-555555555555";
const ACEPTADA = "66666666-6666-4666-8666-666666666666";
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const UNIDADES = { elementos: [{ id: U1, designacion: "1° A" }, { id: U2, designacion: "2° B" }] };

type Quien = "consejo" | "propietaria";

function invitacion(id: string, unidad: string, nombre: string, correo: string | null, estado = "pendiente") {
  return {
    id,
    unidad_id: unidad,
    unidad: unidad === U1 ? "1° A" : "2° B",
    tipo: "inquilino",
    correo,
    nombre,
    estado,
    creada_en: "2026-10-18T12:00:00Z",
    vence_en: "2026-10-25T12:00:00Z",
    aceptada_en: estado === "aceptada" ? "2026-10-19T12:00:00Z" : null,
    revocada_en: null,
  };
}

const error = (status: number, codigo: string) => ({ status, json: { codigo, mensaje: "x", id_correlacion: "c" } });

type Envio = { csrf: string | null; clave: string | null; cuerpo: { unidad_id: string } };

/** Errores de la página y violaciones de la CSP, que el navegador informa como eventos. */
function vigilarErrores(pagina: Page): string[] {
  const errores: string[] = [];
  pagina.on("pageerror", (e) => errores.push(e.message));
  pagina.on("console", (m) => {
    if (m.type() === "error") errores.push(m.text());
  });
  void pagina.addInitScript(() => {
    document.addEventListener("securitypolicyviolation", (e) =>
      console.error(`CSP: ${e.violatedDirective} ${e.blockedURI}`),
    );
  });
  return errores;
}

/**
 * Responde la API de invitaciones desde el navegador. La propietaria solo invita a 1° A (403 en
 * otra, como `invitaciones.crear`); el Consejo, a cualquiera. Sin CSRF, 403; sin clave, 422.
 */
async function simularApi(pagina: Page, baseURL: string, quien: Quien): Promise<{ envios: Envio[]; retiros: (string | null)[] }> {
  const envios: Envio[] = [];
  const retiros: (string | null)[] = [];
  const lista = [
    invitacion(PENDIENTE, U1, "Beto Pérez", quien === "propietaria" ? "beto@ejemplo.com" : null),
    invitacion(ACEPTADA, U2, "Carla Gómez", null, "aceptada"),
  ];
  await pagina.route("**/api/v1/**", async (ruta) => {
    const pedido = ruta.request();
    const camino = new URL(pedido.url()).pathname;
    if (pedido.method() === "GET") {
      if (camino === "/api/v1/me/consorcios")
        return ruta.fulfill({ json: [{ id: C1, nombre: "Edificio Azul", vinculos: quien === "propietaria" ? ["propietario"] : [] }] });
      if (camino === `/api/v1/consorcios/${C1}/unidades`) return ruta.fulfill({ json: UNIDADES });
      if (camino === `/api/v1/consorcios/${C1}/invitaciones`) return ruta.fulfill({ json: { elementos: lista } });
      return ruta.abort();
    }
    const csrf = (await pedido.headerValue("x-csrf-token")) ?? null;
    if (camino === `/api/v1/consorcios/${C1}/invitaciones`) {
      const cuerpo = pedido.postDataJSON() as { unidad_id: string; nombre: string; correo: string };
      const clave = (await pedido.headerValue("idempotency-key")) ?? null;
      envios.push({ csrf, clave, cuerpo });
      if (csrf !== CSRF) return ruta.fulfill(error(403, "csrf_invalido"));
      if (clave === null) return ruta.fulfill(error(422, "peticion_invalida"));
      if (quien === "propietaria" && cuerpo.unidad_id !== U1) return ruta.fulfill(error(403, "sin_permiso"));
      const nueva = invitacion(`9999999${envios.length}-9999-4999-8999-999999999999`, cuerpo.unidad_id, cuerpo.nombre, cuerpo.correo);
      lista.unshift(nueva);
      return ruta.fulfill({ status: 201, json: nueva });
    }
    if (camino === `/api/v1/consorcios/${C1}/invitaciones/${PENDIENTE}/revocar`) {
      retiros.push(csrf);
      return csrf === CSRF ? ruta.fulfill({ status: 204 }) : ruta.fulfill(error(403, "csrf_invalido"));
    }
    return ruta.abort();
  });
  await pagina.context().addCookies([
    { name: "__Host-cem_csrf", value: CSRF, domain: new URL(baseURL).hostname, path: "/", secure: true },
  ]);
  return { envios, retiros };
}

async function invitar(pagina: Page, unidad: string, nombre: string, correo: string) {
  await pagina.getByLabel("Unidad", { exact: true }).selectOption({ label: unidad });
  await pagina.getByLabel("Nombre").fill(nombre);
  await pagina.getByLabel("Correo").fill(correo);
  await pagina.getByRole("button", { name: "Invitar" }).click();
}

test.describe("invitar sin sesión", () => {
  test.use(otroCliente(3));

  test("el backend real responde 401 y la página manda a /entrar", async ({ page }) => {
    const errores = vigilarErrores(page);
    const consulta = page.waitForResponse((r) => new URL(r.url()).pathname === "/api/v1/me/consorcios");
    await page.goto("/invitaciones");
    const respuesta = await consulta;
    expect(respuesta.status()).toBe(401);
    expect(((await respuesta.json()) as { codigo: string }).codigo).toBe("no_autenticado");
    await expect(page).toHaveURL(/\/entrar$/);
    expect(errores.filter((e) => !e.includes("401"))).toEqual([]);
  });
});

test.describe("invitar", () => {
  test("en un teléfono, el Consejo invita a otra unidad con CSRF y clave de reintento", async ({ page, baseURL }) => {
    const errores = vigilarErrores(page);
    await page.setViewportSize({ width: 360, height: 740 });
    const { envios } = await simularApi(page, baseURL!, "consejo");
    await page.goto("/");
    await page.getByRole("link", { name: "Invitar a una persona" }).click();

    await expect(page.getByText(/Invitar no acredita el carácter/)).toBeVisible();
    await expect(page.getByText("Beto Pérez, 1° A")).toBeVisible();
    await expect(page.getByText("beto@ejemplo.com")).toHaveCount(0); // no la creó: P6
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(360);
    await sinFaltasDeAccesibilidad(page);

    // Solo las pendientes a la vista; la aceptada, en el historial plegado (UX móvil, CEM).
    await expect(page.getByText("Carla Gómez, 2° B")).toBeHidden();
    const resumen = page.getByText("Historial: aceptadas, retiradas y vencidas (1)");
    expect((await resumen.boundingBox())?.height).toBeGreaterThanOrEqual(44);
    await resumen.click();
    await expect(page.getByText("Carla Gómez, 2° B")).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(360);
    await sinFaltasDeAccesibilidad(page);
    // El botón de envío ocupa todo el ancho del formulario en el teléfono, con 44 px de alto.
    const boton = await page.getByRole("button", { name: "Invitar" }).boundingBox();
    const campo = await page.getByLabel("Correo").boundingBox();
    expect(boton?.height).toBeGreaterThanOrEqual(44);
    expect(Math.abs((boton?.width ?? 0) - (campo?.width ?? -1))).toBeLessThanOrEqual(1);

    await invitar(page, "2° B", "Dora Ruiz", "dora@ejemplo.com");
    await expect(page.getByText("Listo: le mandamos la invitación a Dora Ruiz.")).toBeVisible();
    await expect(page.getByText("Dora Ruiz, 2° B")).toBeVisible();
    expect(envios).toHaveLength(1);
    expect(envios[0]?.csrf).toBe(CSRF);
    expect(envios[0]?.clave).toMatch(UUID);
    expect(envios[0]?.cuerpo.unidad_id).toBe(U2);
    await sinFaltasDeAccesibilidad(page);
    expect(errores).toEqual([]);
  });

  test("la propietaria no puede invitar a otra unidad, pero sí a la suya", async ({ page, baseURL }) => {
    const errores = vigilarErrores(page);
    const { envios } = await simularApi(page, baseURL!, "propietaria");
    await page.goto("/invitaciones");
    await expect(page.getByText("beto@ejemplo.com")).toBeVisible(); // la creó ella: P6

    await invitar(page, "2° B", "Eva Sosa", "eva@ejemplo.com");
    await expect(page.getByText("No tenés permiso para invitar a esa unidad.")).toBeVisible();
    await expect(page.getByText("Eva Sosa, 2° B")).toHaveCount(0);

    await invitar(page, "1° A", "Eva Sosa", "eva@ejemplo.com");
    await expect(page.getByText("Listo: le mandamos la invitación a Eva Sosa.")).toBeVisible();
    await expect(page.getByText("Eva Sosa, 1° A")).toBeVisible();
    expect(envios.map((e) => e.cuerpo.unidad_id)).toEqual([U2, U1]);
    // Otro intento (otra unidad): otra clave.
    expect(envios[1]?.clave).not.toBe(envios[0]?.clave);
    // La consola informa el 403 del recurso; violaciones de la CSP o errores, ninguno.
    expect(errores.filter((e) => !e.includes("403"))).toEqual([]);
  });

  test("con Enter en el correo, al terminar el envío el foco vuelve a ese campo", async ({ page, baseURL }) => {
    await simularApi(page, baseURL!, "consejo");
    await page.goto("/invitaciones");
    await page.getByLabel("Unidad", { exact: true }).selectOption({ label: "2° B" });
    await page.getByLabel("Nombre").fill("Elsa Paz");
    await page.getByLabel("Correo").fill("elsa@ejemplo.com");
    await page.getByLabel("Correo").press("Enter");
    await expect(page.getByText("Listo: le mandamos la invitación a Elsa Paz.")).toBeVisible();
    await expect(page.getByLabel("Correo")).toBeFocused();
  });

  test("si durante el envío la persona lleva el foco a otro lado, no se lo saca", async ({ page, baseURL }) => {
    await simularApi(page, baseURL!, "consejo");
    let soltar: () => void = () => undefined;
    const retenido = new Promise<void>((r) => {
      soltar = r;
    });
    await page.route(`**/api/v1/consorcios/${C1}/invitaciones`, async (ruta) => {
      if (ruta.request().method() !== "POST") return ruta.fallback();
      await retenido;
      return ruta.fallback();
    });
    await page.goto("/invitaciones");
    await page.getByLabel("Nombre").fill("Fito Sosa");
    await page.getByLabel("Correo").fill("fito@ejemplo.com");
    await page.getByLabel("Correo").press("Enter");
    await expect(page.getByLabel("Correo")).toBeDisabled();
    await expect(page.getByLabel("Correo")).not.toBeFocused();
    // La persona va a otra parte (acá, el título; en la app real, la barra o el lector).
    const elegido = await page.evaluate(() => {
      const titulo = document.querySelector("h1") as HTMLElement;
      titulo.tabIndex = -1;
      titulo.focus();
      return document.activeElement?.textContent ?? "";
    });
    expect(elegido).toBe("Invitar a una persona");
    soltar();
    await expect(page.getByText("Listo: le mandamos la invitación a Fito Sosa.")).toBeVisible();
    expect(await page.evaluate(() => document.activeElement?.textContent ?? "")).toBe(elegido);
    await expect(page.getByLabel("Correo")).not.toBeFocused();
  });

  test("retirar pide confirmación y manda el CSRF", async ({ page, baseURL }) => {
    const { retiros } = await simularApi(page, baseURL!, "propietaria");
    await page.goto("/invitaciones");
    const fila = page.locator(".sesiones li", { hasText: "Beto Pérez, 1° A" });
    await fila.getByRole("button", { name: "Retirar" }).click();
    await expect(fila.getByText("¿Retirar la invitación de Beto Pérez a la unidad 1° A?")).toBeVisible();
    await sinFaltasDeAccesibilidad(page);
    await fila.getByRole("button", { name: "No" }).click();
    expect(retiros).toEqual([]);

    await fila.getByRole("button", { name: "Retirar" }).click();
    await fila.getByRole("button", { name: "Sí, retirarla" }).click();
    await expect(page.getByText("Listo: retiramos la invitación de Beto Pérez.")).toBeVisible();
    // Retirada, deja la vista principal y pasa al historial plegado (sigue en la base y la auditoría).
    await expect(page.getByText("No hay invitaciones pendientes.")).toBeVisible();
    await expect(fila.getByText("Retirada", { exact: true })).toHaveCount(1);
    await expect(fila.getByText("Retirada", { exact: true })).toBeHidden();
    await page.getByText("Historial: aceptadas, retiradas y vencidas (2)").click();
    await expect(fila.getByText("Retirada", { exact: true })).toBeVisible();
    expect(retiros).toEqual([CSRF]);
  });

  test("control: sin la cookie CSRF, el simulador rechaza y la página lo dice", async ({ page, baseURL }) => {
    const { envios } = await simularApi(page, baseURL!, "consejo");
    await page.context().clearCookies();
    await page.goto("/invitaciones");
    await invitar(page, "2° B", "Dora Ruiz", "dora@ejemplo.com");
    await expect(page.getByText("No pudimos verificar el pedido. Recargá la página.")).toBeVisible();
    expect(envios[0]?.csrf).toBeNull();
  });
});
