import AxeBuilder from "@axe-core/playwright";
import { expect, type Page, test } from "@playwright/test";

import { otroCliente, sinFaltasDeAccesibilidad } from "./entorno";

// La contraseña en /entrar y en /cuenta, servidas por el nginx de la imagen con su CSP real.
// El backend de la corrida no tiene base de datos: lo que responde de verdad se mide y se afirma
// en el primer bloque. El flujo con cuenta (contraseña correcta, segundo factor, cambio) se
// simula desde el navegador, como en segundo-factor.e2e.ts; la lógica está en Vitest y el flujo
// contra PostgreSQL en el backend (tests/test_contrasena.py). Aquí se comprueba lo que solo un
// navegador de verdad muestra: la CSP, que todo entra en un teléfono, el contraste (también en el
// modo lectura ampliada) y que la cookie CSRF viaja como encabezado.
const CORREO = "ana@ejemplo.com";
const CONTRASENA = "una frase larga de verdad";
const NUEVA = "otra frase larga de verdad";
const CSRF = "e2e-csrf-contrasena";
const ENTRAR = "/api/v1/auth/contrasena/entrar";
const PASO = "/api/v1/auth/contrasena/segundo-factor";
const ESTADO = "/api/v1/auth/contrasena";
const ANCHO_TELEFONO_PX = 360;

const YO = {
  persona_id: "99999999-9999-4999-8999-999999999999",
  nombre: "Ana Pérez",
  correo: CORREO,
  sesion_id: "11111111-1111-4111-8111-111111111111",
  vence_en: "2026-11-15T20:00:00Z",
};
const SESIONES = [
  {
    id: YO.sesion_id,
    dispositivo: "Chrome en Android",
    actual: true,
    creada_en: "2026-10-01T12:00:00Z",
    ultimo_uso_en: "2026-10-16T12:00:00Z",
    vence_en: "2026-11-15T12:00:00Z",
  },
];
const TOTP = { activo: true, activado_en: "2026-10-15T20:00:00Z", codigos_recuperacion_restantes: 10, verificado_en: null };

const seccionEntrar = (pagina: Page) => pagina.getByRole("region", { name: "Entrar con contraseña" });
const seccionCuenta = (pagina: Page) => pagina.getByRole("region", { name: "Contraseña" });

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

const fallo = (codigo: string) => ({ codigo, mensaje: "x", id_correlacion: "c" });

async function anchoSinDesborde(pagina: Page) {
  expect(await pagina.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(ANCHO_TELEFONO_PX);
}

// Medido el 16-10-2026 contra la imagen de esta rama, sin CEM_DATABASE_URL ni CEM_CODIGO_CLAVE:
// - POST /contrasena/entrar → 503 servicio_no_disponible: sin motor, `transaccion()` lanza
//   SinBase al consultar la cuenta (antes era un 500 error_interno; con la base caída, 503 por
//   OperationalError). Lo prueba backend/tests/test_contrasena_errores.py.
// - POST /contrasena/segundo-factor → 503 servicio_no_disponible: ClavesDep (sin
//   CEM_CODIGO_CLAVE) responde antes de mirar la cookie del desafío.
// - GET y PUT /contrasena sin sesión → 401 no_autenticado (el GET se afirma en cuenta.e2e.ts, que
//   ya abre /cuenta sin sesión: cada pedido de más gasta el cupo de 20 por minuto de /api/v1/auth/).
test.describe("ver la contraseña mientras se escribe", () => {
  test("el botón Mostrar la deja ver y la vuelve a ocultar, sin desbordar ni faltas de axe", async ({ page }) => {
    const errores = vigilarErrores(page);
    await page.setViewportSize({ width: ANCHO_TELEFONO_PX, height: 740 });
    await page.goto("/entrar");
    const seccion = seccionEntrar(page);
    const campo = seccion.getByLabel("Contraseña", { exact: true });
    await campo.fill(CONTRASENA);
    await expect(campo).toHaveAttribute("type", "password");

    await seccion.getByRole("button", { name: "Mostrar lo escrito" }).click();
    await expect(campo).toHaveAttribute("type", "text");
    await expect(campo).toHaveValue(CONTRASENA);
    await anchoSinDesborde(page);
    await sinFaltasDeAccesibilidad(page);

    await seccion.getByRole("button", { name: "Ocultar lo escrito" }).click();
    await expect(campo).toHaveAttribute("type", "password");
    expect(errores).toEqual([]);
  });
});

test.describe("contraseña contra el backend real sin base", () => {
  // Un cupo propio de /api/v1/auth/: ver otroCliente en entorno.ts.
  test.use(otroCliente(1));

  test("entrar: manda correo y contraseña por POST, en el cuerpo; el 503 real se avisa sin colgarse", async ({ page }) => {
    const errores = vigilarErrores(page);
    await page.goto("/entrar");
    const seccion = seccionEntrar(page);
    await seccion.getByLabel("Correo").fill(CORREO);
    await seccion.getByLabel("Contraseña").fill(CONTRASENA);
    const entrada = page.waitForRequest((r) => new URL(r.url()).pathname === ENTRAR);
    await seccion.getByRole("button", { name: "Entrar con la contraseña" }).click();

    const pedido = await entrada;
    expect(pedido.method()).toBe("POST");
    expect(new URL(pedido.url()).search).toBe("");
    expect(await pedido.headerValue("content-type")).toBe("application/json");
    expect(pedido.postDataJSON()).toEqual({ correo: CORREO, contrasena: CONTRASENA });
    const respuesta = await pedido.response();
    expect(respuesta?.status()).toBe(503);
    expect(((await respuesta?.json()) as { codigo: string }).codigo).toBe("servicio_no_disponible");
    expect(respuesta?.headers()["cache-control"]).toBe("no-store");

    await expect(seccion.getByRole("status")).toContainText("No pudimos completar el pedido.");
    await expect(seccion.getByRole("button", { name: "Entrar con la contraseña" })).toBeEnabled();
    expect(page.url()).not.toContain("frase");
    await sinFaltasDeAccesibilidad(page);
    // La consola informa el 503 del recurso; violaciones de la CSP o errores de la página, ninguno.
    expect(errores.filter((e) => !e.includes("503"))).toEqual([]);
  });

  test("paso del segundo factor: con la contraseña simulada, el 503 real del código se avisa y deja reintentar", async ({
    page,
  }) => {
    await page.route(`**${ENTRAR}`, (ruta) =>
      ruta.fulfill({ json: { estado: "falta_segundo_factor", vence_en: "2026-10-16T21:05:00Z" } }),
    );
    await page.goto("/entrar");
    const seccion = seccionEntrar(page);
    await seccion.getByLabel("Correo").fill(CORREO);
    await seccion.getByLabel("Contraseña").fill(CONTRASENA);
    await seccion.getByRole("button", { name: "Entrar con la contraseña" }).click();
    await seccion.getByLabel("Código de la aplicación o de recuperación").fill("042 917");
    const paso = page.waitForRequest((r) => new URL(r.url()).pathname === PASO);
    await seccion.getByRole("button", { name: "Entrar con el código" }).click();

    const pedido = await paso;
    expect(pedido.method()).toBe("POST");
    expect(pedido.postDataJSON()).toEqual({ codigo: "042917" });
    const respuesta = await pedido.response();
    expect(respuesta?.status()).toBe(503);
    expect(((await respuesta?.json()) as { codigo: string }).codigo).toBe("servicio_no_disponible");

    await expect(seccion.getByRole("status")).toContainText("No pudimos completar el pedido.");
    await expect(seccion.getByLabel("Código de la aplicación o de recuperación")).toBeVisible();
    await sinFaltasDeAccesibilidad(page);
  });

  test("cambiar la contraseña sin sesión: el backend real responde 401", async ({ page }) => {
    await page.goto("/entrar");
    const medido = await page.evaluate(async (camino) => {
      const r = await fetch(camino, {
        method: "PUT",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ contrasena_nueva: "una frase larga de verdad" }),
      });
      return { status: r.status, codigo: ((await r.json()) as { codigo: string }).codigo };
    }, ESTADO);
    expect(medido).toEqual({ status: 401, codigo: "no_autenticado" });
  });
});

test.describe("entrar con contraseña y segundo factor (API simulada)", () => {
  test("en un teléfono: contraseña, código y adentro, sin violar la CSP", async ({ page }) => {
    const errores = vigilarErrores(page);
    await page.setViewportSize({ width: ANCHO_TELEFONO_PX, height: 740 });
    const cuerpos: Record<string, unknown> = {};
    await page.route(`**${ENTRAR}`, async (ruta) => {
      cuerpos[ENTRAR] = ruta.request().postDataJSON();
      await ruta.fulfill({ json: { estado: "falta_segundo_factor", vence_en: "2026-10-16T21:05:00Z" } });
    });
    await page.route(`**${PASO}`, async (ruta) => {
      cuerpos[PASO] = ruta.request().postDataJSON();
      await ruta.fulfill({ json: YO });
    });
    await page.goto("/entrar");

    const seccion = seccionEntrar(page);
    await seccion.getByLabel("Correo").fill(CORREO);
    await seccion.getByLabel("Contraseña").fill(CONTRASENA);
    await anchoSinDesborde(page);
    await sinFaltasDeAccesibilidad(page);
    await seccion.getByRole("button", { name: "Entrar con la contraseña" }).click();

    const campo = seccion.getByLabel("Código de la aplicación o de recuperación");
    await expect(campo).toBeFocused();
    await expect(seccion.getByText(/Tenés hasta las 18:05/)).toBeVisible();
    await anchoSinDesborde(page);
    await sinFaltasDeAccesibilidad(page);
    await campo.fill("k7m2q-x9p4t");
    await seccion.getByRole("button", { name: "Entrar con el código" }).click();

    await expect(seccion.getByText("Entraste.")).toBeVisible();
    expect(cuerpos).toEqual({ [ENTRAR]: { correo: CORREO, contrasena: CONTRASENA }, [PASO]: { codigo: "k7m2q-x9p4t" } });
    expect(page.url()).not.toContain("frase");
    await sinFaltasDeAccesibilidad(page);
    expect(errores).toEqual([]);
  });

  test("credenciales inválidas: mensaje genérico, accesible y sin colgarse", async ({ page }) => {
    await page.route(`**${ENTRAR}`, (ruta) => ruta.fulfill({ status: 401, json: fallo("credenciales_invalidas") }));
    await page.goto("/entrar");

    const seccion = seccionEntrar(page);
    await seccion.getByLabel("Correo").fill(CORREO);
    await seccion.getByLabel("Contraseña").fill(CONTRASENA);
    await seccion.getByRole("button", { name: "Entrar con la contraseña" }).click();

    await expect(seccion.getByRole("status")).toContainText("El correo o la contraseña no son correctos.");
    await sinFaltasDeAccesibilidad(page);
  });
});

/**
 * Responde la API de /cuenta desde el navegador. El PUT sin el encabezado CSRF recibe 403, como
 * en el backend; devuelve el encabezado y el cuerpo que trajo cada PUT.
 */
async function simularCuenta(pagina: Page, baseURL: string, respuestaPut: { status: number; json: object }) {
  const puts: { csrf: string | null; cuerpo: unknown }[] = [];
  await pagina.route("**/api/v1/auth/**", async (ruta) => {
    const pedido = ruta.request();
    const camino = new URL(pedido.url()).pathname;
    if (pedido.method() === "GET") {
      if (camino === "/api/v1/auth/sesion") return ruta.fulfill({ json: YO });
      if (camino === "/api/v1/auth/sesiones") return ruta.fulfill({ json: { elementos: SESIONES } });
      if (camino === "/api/v1/auth/totp") return ruta.fulfill({ json: TOTP });
      if (camino === ESTADO) return ruta.fulfill({ json: { definida: true, cambiada_en: "2026-10-10T15:30:00Z" } });
      return ruta.abort();
    }
    if (pedido.method() === "PUT" && camino === ESTADO) {
      const csrf = (await pedido.headerValue("x-csrf-token")) ?? null;
      puts.push({ csrf, cuerpo: pedido.postDataJSON() });
      if (csrf !== CSRF) return ruta.fulfill({ status: 403, json: fallo("csrf_invalido") });
      return ruta.fulfill(respuestaPut);
    }
    return ruta.abort();
  });
  // Como en cuenta.e2e.ts: la cookie __Host- se pasa con domain y path, no con url.
  await pagina.context().addCookies([
    { name: "__Host-cem_csrf", value: CSRF, domain: new URL(baseURL).hostname, path: "/", secure: true },
  ]);
  return puts;
}

/** El formulario empieza plegado: el botón lo abre y deja el foco en el primer campo. */
async function desplegar(pagina: Page) {
  const seccion = seccionCuenta(pagina);
  const boton = seccion.getByRole("button", { name: "Cambiar la contraseña" });
  await expect(boton).toHaveAttribute("aria-expanded", "false");
  await expect(seccion.getByLabel("Contraseña nueva")).toHaveCount(0);
  await boton.click();
  await expect(seccion.getByLabel("Contraseña nueva")).toBeFocused();
}

async function cambiar(pagina: Page) {
  const seccion = seccionCuenta(pagina);
  if ((await seccion.getByLabel("Contraseña nueva").count()) === 0) await desplegar(pagina);
  await seccion.getByLabel("Contraseña nueva").fill(NUEVA);
  await seccion.getByLabel("Repetí la contraseña").fill(NUEVA);
  await seccion.getByRole("button", { name: "Guardar la contraseña nueva" }).click();
}

test.describe("contraseña en /cuenta (API simulada)", () => {
  test("en un teléfono: la cambia con CSRF, entra entera y no viola la CSP", async ({ page, baseURL }) => {
    const errores = vigilarErrores(page);
    await page.setViewportSize({ width: ANCHO_TELEFONO_PX, height: 740 });
    const puts = await simularCuenta(page, baseURL!, {
      status: 200,
      json: { cambiada_en: "2026-10-16T18:00:00Z", sesiones_cerradas: 0 },
    });
    await page.goto("/cuenta");

    const seccion = seccionCuenta(page);
    await expect(seccion.getByText(/La cambiaste por última vez el 10 de octubre de 2026/)).toBeVisible();
    await desplegar(page);
    await expect(seccion.getByLabel("Contraseña nueva")).toHaveAttribute("autocomplete", "new-password");
    await anchoSinDesborde(page);
    await sinFaltasDeAccesibilidad(page);
    await cambiar(page);

    await expect(seccion.getByRole("status")).toContainText("Listo: cambiaste tu contraseña.");
    await expect(seccion.getByRole("button", { name: "Cambiar la contraseña" })).toBeFocused();
    expect(puts).toEqual([{ csrf: CSRF, cuerpo: { contrasena_nueva: NUEVA, contrasena_actual: null } }]);
    await anchoSinDesborde(page);
    await sinFaltasDeAccesibilidad(page);
    expect(errores).toEqual([]);
  });

  test("sin la verificación reciente (403) lo explica con el enlace al segundo factor", async ({ page, baseURL }) => {
    await simularCuenta(page, baseURL!, { status: 403, json: fallo("segundo_factor_requerido") });
    await page.goto("/cuenta");
    await cambiar(page);

    const estado = seccionCuenta(page).getByRole("status");
    await expect(estado).toContainText("Para esto tenés que confirmar");
    await estado.getByRole("link", { name: /Verificá un código/ }).click();
    await expect(page).toHaveURL(/\/cuenta\/segundo-factor$/);
  });

  test("modo lectura ampliada: la página con la sección y su aviso pasa el contraste AAA", async ({ page, baseURL }) => {
    await simularCuenta(page, baseURL!, { status: 403, json: fallo("segundo_factor_requerido") });
    await page.goto("/cuenta");
    await page.getByRole("banner").getByRole("button", { name: "Letra grande y alto contraste" }).click();
    await expect(page.locator("html")).toHaveClass("lectura-ampliada");
    await cambiar(page);
    await expect(seccionCuenta(page).getByRole("status")).toContainText("Para esto tenés que confirmar");

    const resultado = await new AxeBuilder({ page })
      .withRules(["color-contrast", "color-contrast-enhanced"])
      .analyze();
    expect(resultado.violations.map((v) => v.id)).toEqual([]);
    // Un análisis que no mide nada también da cero faltas.
    expect(resultado.passes.find((p) => p.id === "color-contrast-enhanced")?.nodes.length ?? 0).toBeGreaterThan(0);
  });

  test("control: sin la cookie CSRF, el backend simulado rechaza y la página lo dice", async ({ page, baseURL }) => {
    const puts = await simularCuenta(page, baseURL!, {
      status: 200,
      json: { cambiada_en: "2026-10-16T18:00:00Z", sesiones_cerradas: 0 },
    });
    await page.context().clearCookies();
    await page.goto("/cuenta");
    await cambiar(page);

    await expect(seccionCuenta(page).getByRole("status")).toContainText("No pudimos verificar el pedido");
    expect(puts.map((p) => p.csrf)).toEqual([null]);
  });
});
