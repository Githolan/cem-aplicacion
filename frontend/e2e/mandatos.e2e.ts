import { expect, type Page, test } from "@playwright/test";

import { otroCliente, sinFaltasDeAccesibilidad } from "./entorno";

// La página /mandatos servida por el nginx de la imagen, con su CSP real. El backend de la corrida
// no tiene base de datos: sin sesión responde el 401 de verdad y la página manda a /entrar. Con
// sesión, la API se simula desde el navegador. Quién lee y quién da altas o bajas lo deciden la
// política, las funciones de `0018` y sus pruebas (test_mandatos_0018, test_mandatos_lectura) y
// el smoke autenticado de staging. Acá se ve lo que solo un navegador real muestra: que la página
// entra en un teléfono, el contraste de WCAG AA, que la cookie CSRF y la clave de reintento viajan
// como encabezados, el aviso A15 y que no hay violaciones de la CSP.
const CSRF = "e2e-csrf-mandatos";
const C1 = "11111111-1111-4111-8111-111111111111";
const M_AE = "33333333-3333-4333-8333-333333333333";
const D_EQUIPO = "44444444-4444-4444-8444-444444444444";
const D_PROPIA = "55555555-5555-4555-8555-555555555555";
const D_LARGA = "99999999-9999-4999-8999-999999999999";
const VECINA = "66666666-6666-4666-8666-666666666666";
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const BASE = `/api/v1/consorcios/${C1}`;

type Quien = "consejo" | "propietaria";

const MANDATO_AE = {
  id: M_AE,
  tipo: "administrador_externo",
  nombre: "Estudio Sur",
  cuit: "20123456786",
  vigente_desde: "2026-02-01",
  vigente_hasta: null,
  estado: "vigente",
  con_cuenta: false,
};

const DECISIONES = [
  { id: D_EQUIPO, tipo: "asamblea", fecha: "2026-01-10", descripcion: "Designa al Consejo" },
  { id: D_PROPIA, tipo: "asamblea", fecha: "2026-03-05", descripcion: "Remueve al estudio" },
  // Una descripción del largo máximo (500): la opción del selector no puede ensanchar la página.
  { id: D_LARGA, tipo: "consejo", fecha: "2026-04-01", descripcion: "Acta muy detallada ".repeat(25).slice(0, 500) },
];

const error = (status: number, codigo: string) => ({ status, json: { codigo, mensaje: "x", id_correlacion: "c" } });

type Envio = { camino: string; csrf: string | null; clave: string | null; cuerpo: Record<string, unknown> };

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
 * Responde la API de mandatos desde el navegador. El Consejo lee todo; la propietaria recibe 403
 * en las tres listas, como `mandatos.ver`, `decisiones.ver` y `mandatos.ver_candidatos`. Sin CSRF,
 * 403; sin clave, 422; una decisión propia en la remoción, 403 (A15).
 */
async function simularApi(pagina: Page, baseURL: string, quien: Quien): Promise<Envio[]> {
  const envios: Envio[] = [];
  await pagina.route("**/api/v1/**", async (ruta) => {
    const pedido = ruta.request();
    const camino = new URL(pedido.url()).pathname;
    if (pedido.method() === "GET") {
      if (camino === "/api/v1/me/consorcios") return ruta.fulfill({ json: [{ id: C1, nombre: "Edificio Azul", vinculos: [] }] });
      if (quien === "propietaria") return ruta.fulfill(error(403, "sin_permiso"));
      if (camino === `${BASE}/mandatos`) return ruta.fulfill({ json: { elementos: [MANDATO_AE] } });
      if (camino === `${BASE}/decisiones`) return ruta.fulfill({ json: { elementos: DECISIONES } });
      if (camino === `${BASE}/mandatos/candidatos`)
        return ruta.fulfill({ json: { elementos: [{ persona_id: VECINA, nombre: "Beto Pérez", unidades: ["1° A"] }] } });
      if (camino === `${BASE}/mandatos/${M_AE}`)
        return ruta.fulfill({
          json: {
            ...MANDATO_AE,
            matricula: null,
            decision_id: D_EQUIPO,
            facultades: ["documentos.cargar"],
            causa_baja: null,
            baja_decision_id: null,
            instrumento_referencia: null,
            motivo_fin: null,
            finalizado_en: null,
          },
        });
      const decision = DECISIONES.find((d) => camino === `${BASE}/decisiones/${d.id}`);
      if (decision !== undefined)
        return ruta.fulfill({
          json: { ...decision, referencia: "Acta 14", sha256: null, registrada_por_vos: decision.id === D_PROPIA, del_equipo: decision.id === D_EQUIPO },
        });
      return ruta.abort();
    }
    const csrf = (await pedido.headerValue("x-csrf-token")) ?? null;
    const clave = (await pedido.headerValue("idempotency-key")) ?? null;
    const cuerpo = pedido.postDataJSON() as Record<string, unknown>;
    envios.push({ camino, csrf, clave, cuerpo });
    if (csrf !== CSRF) return ruta.fulfill(error(403, "csrf_invalido"));
    if (clave === null) return ruta.fulfill(error(422, "peticion_invalida"));
    if (camino === `${BASE}/mandatos`) return ruta.fulfill({ status: 201, json: { id: "77777777-7777-4777-8777-777777777777" } });
    if (camino === `${BASE}/mandatos/${M_AE}/baja`) {
      if (cuerpo.decision_id === D_PROPIA) return ruta.fulfill(error(403, "sin_permiso"));
      return ruta.fulfill({ json: { id: M_AE, causa: cuerpo.causa, vigente_hasta: "2026-10-19" } });
    }
    return ruta.abort();
  });
  await pagina.context().addCookies([
    { name: "__Host-cem_csrf", value: CSRF, domain: new URL(baseURL).hostname, path: "/", secure: true },
  ]);
  return envios;
}

test.describe("mandatos sin sesión", () => {
  test.use(otroCliente(5));

  test("el backend real responde 401 y la página manda a /entrar", async ({ page }) => {
    const errores = vigilarErrores(page);
    const consulta = page.waitForResponse((r) => new URL(r.url()).pathname === "/api/v1/me/consorcios");
    await page.goto("/mandatos");
    const respuesta = await consulta;
    expect(respuesta.status()).toBe(401);
    expect(((await respuesta.json()) as { codigo: string }).codigo).toBe("no_autenticado");
    await expect(page).toHaveURL(/\/entrar$/);
    expect(errores.filter((e) => !e.includes("401"))).toEqual([]);
  });
});

test.describe("mandatos", () => {
  test("en un teléfono, el Consejo ve las listas y da un alta con CSRF y clave de reintento", async ({ page, baseURL }) => {
    const errores = vigilarErrores(page);
    await page.setViewportSize({ width: 360, height: 740 });
    const envios = await simularApi(page, baseURL!, "consejo");
    await page.goto("/");
    await page.getByRole("link", { name: "Mandatos y decisiones" }).click();

    await expect(page.getByText("Estudio Sur, Administración externa")).toBeVisible();
    await expect(page.getByText("20123456786")).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(360);
    await sinFaltasDeAccesibilidad(page);
    // Con el detalle abierto (y su formulario de baja) tampoco hay desplazamiento horizontal.
    await page.getByRole("button", { name: "Ver detalle", exact: true }).first().click();
    await expect(page.getByRole("heading", { level: 3, name: "Dar de baja" })).toBeVisible();
    await page.getByLabel("Causa").selectOption("remocion");
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(360);

    const alta = page.getByRole("region", { name: "Dar de alta un mandato" });
    await alta.getByLabel("Decisión que lo respalda").selectOption(D_PROPIA);
    await expect(alta.getByText(/Esa decisión la registraste vos/)).toBeVisible();
    await sinFaltasDeAccesibilidad(page);
    await alta.getByLabel("Decisión que lo respalda").selectOption(D_EQUIPO);
    await expect(alta.getByText(/Esa decisión la registraste vos/)).toHaveCount(0);
    await alta.getByLabel("Desde").fill("2026-11-01");
    await alta.getByRole("button", { name: "Dar de alta" }).click();
    await expect(page.getByText("Listo: el mandato quedó dado de alta.")).toBeVisible();
    expect(envios).toHaveLength(1);
    expect(envios[0]?.csrf).toBe(CSRF);
    expect(envios[0]?.clave).toMatch(UUID);
    expect(envios[0]?.cuerpo).toMatchObject({ tipo: "miembro_consejo", persona_id: VECINA, decision_id: D_EQUIPO });
    expect(errores).toEqual([]);
  });

  test("la propietaria no lee mandatos ni decisiones y no ve el alta", async ({ page, baseURL }) => {
    const errores = vigilarErrores(page);
    await simularApi(page, baseURL!, "propietaria");
    await page.goto("/mandatos");
    await expect(page.getByText("No tenés permiso para ver los mandatos de este edificio.")).toBeVisible();
    await expect(page.getByText("No tenés permiso para ver las decisiones de este edificio.")).toBeVisible();
    await expect(page.getByRole("button", { name: "Dar de alta" })).toHaveCount(0);
    await sinFaltasDeAccesibilidad(page);
    // La consola informa los 403; violaciones de la CSP o errores, ninguno.
    expect(errores.filter((e) => !e.includes("403"))).toEqual([]);
  });

  test("la remoción con una asamblea propia la rechaza la API; con otra, se da de baja", async ({ page, baseURL }) => {
    const envios = await simularApi(page, baseURL!, "consejo");
    await page.goto("/mandatos");
    await page.getByRole("button", { name: "Ver detalle", exact: true }).first().click();
    await expect(page.getByRole("heading", { level: 2, name: "Estudio Sur, Administración externa" })).toBeFocused();
    await page.getByLabel("Causa").selectOption("remocion");
    await page.getByLabel("Por qué termina").fill("Remoción por asamblea");
    await page.getByLabel("Asamblea que lo remueve").selectOption(D_PROPIA);
    await expect(page.getByText(/no podés usarla para remover/)).toBeVisible();
    await page.getByRole("button", { name: "Dar de baja", exact: true }).click();
    await expect(page.getByRole("button", { name: "Sí, dar de baja" })).toBeFocused();
    await sinFaltasDeAccesibilidad(page);
    await page.getByRole("button", { name: "Sí, dar de baja" }).click();
    await expect(page.getByText(/No tenés permiso para dar esta baja/)).toBeFocused();
    await sinFaltasDeAccesibilidad(page);

    await page.getByLabel("Asamblea que lo remueve").selectOption(D_EQUIPO);
    await page.getByRole("button", { name: "Dar de baja", exact: true }).click();
    await page.getByRole("button", { name: "Sí, dar de baja" }).click();
    await expect(page.getByText("Listo: dimos de baja el mandato de Estudio Sur.")).toBeVisible();
    expect(envios.map((e) => e.cuerpo.decision_id)).toEqual([D_PROPIA, D_EQUIPO]);
    // Otro intento (otra asamblea): otra clave.
    expect(envios[1]?.clave).not.toBe(envios[0]?.clave);
  });

  test("control: sin la cookie CSRF, el simulador rechaza y la página lo dice", async ({ page, baseURL }) => {
    const envios = await simularApi(page, baseURL!, "consejo");
    await page.context().clearCookies();
    await page.goto("/mandatos");
    const alta = page.getByRole("region", { name: "Dar de alta un mandato" });
    await alta.getByLabel("Decisión que lo respalda").selectOption(D_EQUIPO);
    await alta.getByLabel("Desde").fill("2026-11-01");
    await alta.getByRole("button", { name: "Dar de alta" }).click();
    await expect(page.getByText("No pudimos verificar el pedido. Recargá la página.")).toBeVisible();
    expect(envios[0]?.csrf).toBeNull();
  });
});
