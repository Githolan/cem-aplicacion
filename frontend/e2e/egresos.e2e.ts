import { expect, type Page, test } from "@playwright/test";

import { simularPermisos, sinFaltasDeAccesibilidad } from "./entorno";

// Las páginas de egresos servidas por el nginx de la imagen, con su CSP real y la API simulada
// desde el navegador, como en padron.e2e.ts. Mensaje del propietario del 30-10-2026, puntos 31 a
// 33: que entren en un teléfono de 360 px, WCAG AA, que pasar del edificio A al B y volver al A
// nunca muestre datos de otro edificio, y que la cuenta completa no esté en el documento salvo en
// la revisión del Consejo (la privacidad la hace cumplir el backend; acá se ve que la página no
// agrega nada).
const CSRF = "e2e-csrf-egresos";
const A = "a1111111-1111-4111-8111-111111111111";
const B = "b2222222-2222-4222-8222-222222222222";
const PROV_A = "a3333333-3333-4333-8333-333333333333";
const PROV_B = "b4444444-4444-4444-8444-444444444444";
const ORDEN = "c5555555-5555-4555-8555-555555555555";
const ADM = "d6666666-6666-4666-8666-666666666666";
const CUENTA = "2850590940090418135201"; // sintética, de formato válido
const ANCHO_TELEFONO_PX = 360;
// Una razón social del largo que admite la base: la tarjeta no puede ensanchar la página.
const RAZON_LARGA = "Mantenimiento Integral de Ascensores y Equipos Electromecánicos del Río de la Plata S.R.L.";

const proveedor = (id: string, razon: string, ultimos: string) => ({
  id, cuit: "30712345679", version: 1, razon_social: razon, rubro: "Ascensores", contacto: null,
  estado: "activo", documento_id: null, cuenta_ultimos_digitos: ultimos, huella: "ab".repeat(32),
  alta_por: ADM, creado_en: "2026-10-30T12:00:00Z", actualizado_en: "2026-10-30T12:00:00Z",
});

const ORDEN_A = {
  id: ORDEN, estado: "en_revision", version_vigente: 1, preparada_por: ADM, creada_en: "2026-10-30T12:00:00Z",
  actualizada_en: "2026-10-30T12:00:00Z", motivo_rechazo: null, importe_centavos: 12_000_000, moneda: "ARS",
  concepto: "SINTETICO: reparación del ascensor", proveedor_id: PROV_A, razon_social: RAZON_LARGA,
  condicion_beneficiario: "conocido", usa_fondo_reserva: false, aprobaciones_requeridas: 1, aprobaciones: 0,
  franja: "sobre_umbral", alerta_fraccionamiento: true, aprobada_en: null, aprobacion_vence_en: null, aprobada_por_vos: false,
};

const version = (cuentaDestino: string | null) => ({
  numero: 1, importe_centavos: 12_000_000, moneda: "ARS", concepto: ORDEN_A.concepto, comprobante: "Factura A 0001-00000042",
  usa_fondo_reserva: false, huella: "cd".repeat(32), preparada_por: ADM, creada_en: "2026-10-30T12:00:00Z",
  aprobaciones_requeridas: 1, proveedor_id: PROV_A, proveedor_version: 1, condicion_beneficiario: "conocido",
  razon_social: RAZON_LARGA, cuit: "30712345679", cuenta_ultimos_digitos: CUENTA.slice(-4), aprobada_por: [],
  franja: "sobre_umbral", alerta_fraccionamiento: true, umbral_importe_centavos: 10_000_000, umbral_moneda: "ARS",
  contrato_id: null, cuenta_destino: cuentaDestino,
});

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

/** La API de egresos de dos edificios. `revisa` decide qué devuelve la revisión: la cuenta
 * completa y las dos decisiones (un consejero que no preparó) o nada de eso (quien preparó). Anota
 * cada lectura. */
async function simularApi(pagina: Page, baseURL: string, revisa: "consejero" | "quien_preparo"): Promise<string[]> {
  const lecturas: string[] = [];
  await pagina.route("**/api/v1/**", async (ruta) => {
    const pedido = ruta.request();
    const camino = new URL(pedido.url()).pathname;
    if (pedido.method() !== "GET") return ruta.abort();
    const permisos = simularPermisos(ruta, camino);
    if (permisos !== null) return permisos;
    lecturas.push(camino);
    if (camino === "/api/v1/me/consorcios")
      return ruta.fulfill({ json: [{ id: A, nombre: "Edificio Azul", vinculos: [] }, { id: B, nombre: "Edificio Bordó", vinculos: [] }] });
    if (camino === `/api/v1/consorcios/${A}/proveedores`)
      return ruta.fulfill({ json: { elementos: [proveedor(PROV_A, RAZON_LARGA, CUENTA.slice(-4))] } });
    if (camino === `/api/v1/consorcios/${B}/proveedores`)
      return ruta.fulfill({ json: { elementos: [proveedor(PROV_B, "Limpieza Bordó SINTETICO", "9876")] } });
    if (camino === `/api/v1/consorcios/${A}/ordenes-pago/revision`) return ruta.fulfill({ json: { elementos: [ORDEN_A] } });
    if (camino === `/api/v1/consorcios/${A}/ordenes-pago/${ORDEN}/revision`)
      return ruta.fulfill({
        json: {
          orden: ORDEN_A,
          versiones: [version(revisa === "consejero" ? CUENTA : null)],
          puede_aprobar: revisa === "consejero",
          puede_rechazar: revisa === "consejero",
        },
      });
    return ruta.abort();
  });
  await pagina.context().addCookies([
    { name: "__Host-cem_csrf", value: CSRF, domain: new URL(baseURL).hostname, path: "/", secure: true },
  ]);
  return lecturas;
}

async function entraEnElTelefono(pagina: Page) {
  expect(await pagina.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(ANCHO_TELEFONO_PX);
}

test.describe("egresos en un teléfono", () => {
  test.beforeEach(async ({ page }) => {
    await page.setViewportSize({ width: ANCHO_TELEFONO_PX, height: 740 });
  });

  test("proveedores: A, B y de nuevo A, cada uno con sus datos y la cuenta enmascarada", async ({ page, baseURL }) => {
    const errores = vigilarErrores(page);
    const lecturas = await simularApi(page, baseURL!, "consejero");
    await page.goto("/egresos/proveedores");
    const edificio = page.getByLabel("Edificio");

    await expect(page.getByRole("heading", { name: RAZON_LARGA })).toBeVisible();
    await expect(page.getByText(`…${CUENTA.slice(-4)}`)).toBeVisible();
    await entraEnElTelefono(page);
    await sinFaltasDeAccesibilidad(page);

    await edificio.selectOption(B);
    await expect(page.getByRole("heading", { name: "Limpieza Bordó SINTETICO" })).toBeVisible();
    await expect(page.getByRole("heading", { name: RAZON_LARGA })).toHaveCount(0);
    await entraEnElTelefono(page);

    await edificio.selectOption(A);
    await expect(page.getByRole("heading", { name: RAZON_LARGA })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Limpieza Bordó SINTETICO" })).toHaveCount(0);

    const deProveedores = lecturas.filter((l) => l.endsWith("/proveedores"));
    expect(deProveedores).toEqual([A, B, A].map((c) => `/api/v1/consorcios/${c}/proveedores`));
    expect(await page.content()).not.toContain(CUENTA);
    expect(errores).toEqual([]);
  });
});

// El circuito de pagos: una bandeja y una ficha, con la API simulada desde el navegador. Cada prueba
// corre en los tres proyectos (escritorio, movil, tablet) con su ancho, sin fijarlo: en PC la ficha
// se abre al lado y más angosto va aparte. La cuenta completa no está en la página hasta el acto de
// ejecución (un POST que la API responde con todas las barreras).
const EJECUCION = "e7777777-7777-4777-8777-777777777777";
const PAGO = "f8888888-8888-4888-8888-888888888888";
const COMPROBANTE = "f9999999-9999-4999-8999-999999999999";
const ARCHIVO = "fa000000-0000-4000-8000-000000000000";
const ANCHO_PC_PX = 1025;
const ANCHO_PESTANAS_PX = 768;
const ACCIONES_EN_FALSO = {
  puede_enviar: false, puede_aprobar: false, puede_rechazar: false, puede_renovar: false, puede_pagar: false,
  puede_reabrir_cuenta: false, puede_modificar: false, puede_subir_comprobante: false, puede_registrar: false,
  puede_declarar: false, puede_corregir: false, puede_conciliar: false, puede_confirmar: false,
};
const ORDEN_APROBADA = { ...ORDEN_A, estado: "aprobada", aprobaciones: 1, aprobada_en: "2026-10-31T12:00:00Z",
  aprobacion_vence_en: "2099-11-07T12:00:00Z" };
const EJECUCION_VACIA = { id: EJECUCION, version: 1, creada_en: "2026-10-31T12:30:00Z", resultado: null, referencia: null,
  declaraciones: [], pagos: [] };

const fichaDe = (extra: object) => ({
  ...ACCIONES_EN_FALSO, orden: ORDEN_APROBADA, versiones: [version(null)], situacion: "para_pagar", paso: null, le_toca_a: null,
  ejecucion: null, comprobante_actual: null, observacion_vigente: null, declaracion_vigente: null, ...extra,
});
const PARA_PAGAR = fichaDe({ paso: "pagar", le_toca_a: "vos", puede_pagar: true });
const SUBIENDO = fichaDe({
  orden: { ...ORDEN_APROBADA, estado: "en_ejecucion" }, situacion: "pagando", paso: "subir_comprobante", le_toca_a: "vos",
  puede_subir_comprobante: true, puede_registrar: true, puede_declarar: true, puede_reabrir_cuenta: true, ejecucion: EJECUCION_VACIA,
});
const PARA_APROBAR = fichaDe({
  orden: ORDEN_A, situacion: "en_revision", paso: "aprobar", le_toca_a: "vos", puede_aprobar: true, puede_rechazar: true,
});
const PARA_CONCILIAR = fichaDe({
  orden: { ...ORDEN_APROBADA, estado: "en_verificacion" }, situacion: "falta_conciliar", paso: "conciliar", le_toca_a: "vos", puede_conciliar: true,
  comprobante_actual: { documento_id: COMPROBANTE, version_documento_id: ARCHIVO },
  ejecucion: { ...EJECUCION_VACIA, pagos: [{ id: PAGO, corrige_a: null, numero_operacion: "0001234567", fecha_operacion: "2026-10-31",
    importe_centavos: 12_000_000, moneda: "ARS", documento_id: COMPROBANTE, version_documento_id: ARCHIVO, registrado_por: ADM,
    registrado_en: "2026-10-31T13:00:00Z", conciliacion: null, motivo_conciliacion: null, conciliado_por: null, conciliado_en: null,
    fuera_de_termino: false }] },
});

const GRUPO_DE: Record<string, string> = { pagar: "para_pagar", aprobar: "para_aprobar", conciliar: "para_conciliar" };

/** La API del circuito con una orden. La ficha cambia con el acto, como en el servidor. */
async function simularPagos(pagina: Page, baseURL: string, inicial: { paso: string | null }): Promise<string[]> {
  const pedidosHechos: string[] = [];
  let actual: object = inicial;
  await pagina.route("**/api/v1/**", async (ruta) => {
    const pedido = ruta.request();
    const camino = new URL(pedido.url()).pathname;
    const permisos = simularPermisos(ruta, camino);
    if (permisos !== null) return permisos;
    pedidosHechos.push(`${pedido.method()} ${camino}`);
    const c = `/api/v1/consorcios/${A}`;
    if (camino === "/api/v1/me/consorcios") return ruta.fulfill({ json: [{ id: A, nombre: "Edificio Azul", vinculos: [] }] });
    if (camino === `${c}/pagos/bandeja`)
      return ruta.fulfill({ json: { elementos: [{ orden_id: ORDEN, proveedor: RAZON_LARGA, concepto: ORDEN_A.concepto,
        importe_centavos: 12_000_000, moneda: "ARS", paso: inicial.paso, grupo: GRUPO_DE[inicial.paso ?? ""], le_toca_a: "vos" }] } });
    if (camino === `${c}/pagos/${ORDEN}`) return ruta.fulfill({ json: actual });
    if (camino === `${c}/ordenes-pago/${ORDEN}/revision`)
      return ruta.fulfill({ json: { orden: ORDEN_A, versiones: [version(CUENTA)], puede_aprobar: true, puede_rechazar: true } });
    if (camino === `${c}/documentos`)
      return ruta.fulfill({ json: { documentos: [{ id: COMPROBANTE, categoria: "facturas_y_comprobantes",
        titulo: "Transferencia SINTETICA", fecha: "2026-10-31", cargado_en: "2026-10-31T13:00:00Z", orden_pago_id: ORDEN,
        versiones: [{ id: ARCHIVO, estado: "disponible" }] }] } });
    if (pedido.method() === "POST" && camino === `${c}/ordenes-pago/${ORDEN}/emitir`)
      return ruta.fulfill({ json: { id: ORDEN, version: 1 } });
    if (pedido.method() === "POST" && camino === `${c}/ordenes-pago/${ORDEN}/pago-manual/cuenta`) {
      actual = SUBIENDO;
      return ruta.fulfill({ json: { ejecucion_id: EJECUCION, version: 1, huella: "cd".repeat(32), proveedor_id: PROV_A,
        proveedor_version: 1, razon_social: RAZON_LARGA, cuit: "30712345679", cuenta_destino: CUENTA,
        importe_centavos: 12_000_000, moneda: "ARS", concepto: ORDEN_A.concepto, comprobante: "Factura A 0001-00000042",
        aprobada_en: "2026-10-31T12:00:00Z", aprobacion_vence_en: "2026-11-07T12:00:00Z" } });
    }
    return ruta.abort();
  });
  await pagina.context().addCookies([
    { name: "__Host-cem_csrf", value: CSRF, domain: new URL(baseURL).hostname, path: "/", secure: true },
  ]);
  return pedidosHechos;
}

const anchoDe = (pagina: Page) => pagina.viewportSize()?.width ?? 0;

async function sinDesbordar(pagina: Page) {
  const { contenido, ventana } = await pagina.evaluate(() => ({
    contenido: document.documentElement.scrollWidth,
    ventana: window.innerWidth,
  }));
  expect(contenido).toBeLessThanOrEqual(ventana);
}

/** Entra a la bandeja y llega a la ficha: en PC ya está abierta al lado; más angosto, se toca. */
async function abrirLaFicha(pagina: Page) {
  await pagina.goto("/pagos");
  const fila = pagina.getByRole("link", { name: new RegExp(RAZON_LARGA) });
  await expect(fila).toBeVisible();
  expect((await fila.boundingBox())?.height).toBeGreaterThanOrEqual(44);
  await sinDesbordar(pagina);
  if (anchoDe(pagina) < ANCHO_PC_PX) await fila.click();
}

test.describe("circuito de pagos", () => {
  test("en PC la ficha va al lado de la bandeja; más angosto, aparte", async ({ page, baseURL }) => {
    const errores = vigilarErrores(page);
    await simularPagos(page, baseURL!, PARA_PAGAR);
    await page.goto("/pagos");
    const pagar = page.getByRole("button", { name: "Ver la cuenta para pagar" });
    const fila = page.getByRole("link", { name: new RegExp(RAZON_LARGA) });
    await expect(fila).toBeVisible();
    if (anchoDe(page) >= ANCHO_PC_PX) {
      await expect(pagar).toBeVisible();
      const [lista, ficha] = [await fila.boundingBox(), await pagar.boundingBox()];
      expect(ficha?.x).toBeGreaterThan((lista?.x ?? 0) + (lista?.width ?? 0));
    } else {
      await expect(pagar).toHaveCount(0);
      await fila.click();
      await expect(pagar).toBeVisible();
      await expect(fila).toHaveCount(0);
    }
    await sinDesbordar(page);
    await sinFaltasDeAccesibilidad(page);
    expect(errores).toEqual([]);
  });

  test("las pestañas se ven solo en un ancho de teléfono", async ({ page, baseURL }) => {
    await simularPagos(page, baseURL!, PARA_PAGAR);
    await page.goto("/pagos");
    const pestana = page.getByRole("navigation", { name: "Secciones" }).getByRole("link", { name: "Pagos" });
    await expect(page.getByRole("heading", { level: 1, name: "Pagos" })).toBeVisible();
    if (anchoDe(page) <= ANCHO_PESTANAS_PX) {
      await expect(pestana).toBeVisible();
      expect((await pestana.boundingBox())?.height).toBeGreaterThanOrEqual(44);
    } else await expect(pestana).toBeHidden();
  });

  test("la cuenta completa aparece solo en el acto de ejecución, con emitir y ver en un toque", async ({ page, baseURL }) => {
    const errores = vigilarErrores(page);
    const hechos = await simularPagos(page, baseURL!, PARA_PAGAR);
    await abrirLaFicha(page);
    const pagar = page.getByRole("button", { name: "Ver la cuenta para pagar" });
    await expect(pagar).toBeVisible();
    expect((await pagar.boundingBox())?.height).toBeGreaterThanOrEqual(44);
    expect(await page.content()).not.toContain(CUENTA);
    expect(hechos.filter((h) => h.startsWith("POST"))).toEqual([]);

    await pagar.click();
    await expect(page.getByText(CUENTA)).toBeVisible();
    await expect(page.getByText("Verificá que la cuenta mostrada coincida con la cuenta autorizada para esta versión.")).toBeVisible();
    await expect(page.getByRole("button", { name: "Registrar el pago" })).toBeVisible();
    expect(hechos.filter((h) => h.startsWith("POST")).map((h) => h.split("/").at(-1))).toEqual(["emitir", "cuenta"]);
    await sinDesbordar(page);
    await sinFaltasDeAccesibilidad(page);
    expect(errores).toEqual([]);
  });

  test("el importe abre el teclado numérico y al salir se ve con centavos", async ({ page, baseURL }) => {
    await simularPagos(page, baseURL!, SUBIENDO);
    await page.route(`**/api/v1/consorcios/${A}/pagos/bandeja`, (ruta) => ruta.fulfill({ json: { elementos: [] } }));
    await page.goto(`/pagos/${ORDEN}`);
    const campo = page.getByLabel(/^Importe transferido/);
    await expect(campo).toHaveAttribute("inputmode", "decimal");
    await campo.fill("120000");
    await expect(campo).toHaveValue("120000");
    await campo.blur();
    await expect(campo).toHaveValue("120.000,00");
    await campo.focus();
    await expect(campo).toHaveValue("120000");
    // Control: lo que no es un importe queda como se escribió.
    await campo.fill("12,345,6");
    await campo.blur();
    await expect(campo).toHaveValue("12,345,6");
  });

  test("aprobar: la alerta de fraccionamiento a la vista y la cuenta completa", async ({ page, baseURL }) => {
    const errores = vigilarErrores(page);
    await simularPagos(page, baseURL!, PARA_APROBAR);
    await abrirLaFicha(page);
    await expect(page.getByText("Posible fraccionamiento: existen órdenes recientes relacionadas")).toBeVisible();
    await expect(page.getByText(CUENTA)).toBeVisible();
    await expect(page.getByRole("button", { name: "Aprobar esta versión" })).toBeEnabled();
    await expect(page.getByRole("button", { name: "Rechazar", exact: true })).toBeVisible();
    await sinDesbordar(page);
    await sinFaltasDeAccesibilidad(page);
    expect(errores).toEqual([]);
  });

  test("conciliar muestra la evidencia sin la cuenta", async ({ page, baseURL }) => {
    const errores = vigilarErrores(page);
    await simularPagos(page, baseURL!, PARA_CONCILIAR);
    await abrirLaFicha(page);
    await expect(page.getByText("0001234567")).toBeVisible();
    await expect(page.getByRole("link", { name: "Ver el comprobante" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Conforme" })).toBeVisible();
    expect(await page.content()).not.toContain(CUENTA);
    await sinDesbordar(page);
    await sinFaltasDeAccesibilidad(page);
    expect(errores).toEqual([]);
  });

  test("las direcciones viejas llevan a la bandeja", async ({ page, baseURL }) => {
    await simularPagos(page, baseURL!, PARA_PAGAR);
    for (const vieja of ["/egresos/ordenes", "/egresos/revision", "/egresos/pagos", "/egresos/conciliacion"]) {
      await page.goto(vieja);
      await expect(page.getByRole("heading", { level: 1, name: "Pagos" })).toBeVisible();
      await expect(page).toHaveURL(/\/pagos$/);
    }
  });
});
