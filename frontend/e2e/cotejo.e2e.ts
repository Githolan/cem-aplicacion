import { connect } from "node:net";

import { expect, type Page, test } from "@playwright/test";

import { simularPermisos, sinFaltasDeAccesibilidad } from "./entorno";

// `/pagos/cotejo` servida por el nginx de la imagen, con su CSP real y la API simulada desde el
// navegador, en los tres proyectos (escritorio, movil, tablet). Datos SINTETICOS: el extracto lo
// arma esta prueba. Que el servidor lea, compare y separe lo prueba `backend/tests/test_cotejo_0074.py`.
const CSRF = "e2e-csrf-cotejo";
const A = "a1111111-1111-4111-8111-111111111111";
const ORDEN = "c5555555-5555-4555-8555-555555555555";
const P1 = "e7777777-7777-4777-8777-777777777777";
const P2 = "e8888888-8888-4888-8888-888888888888";
const ALTO_TOQUE_PX = 44;
const BYTES_SOBRE_EL_TOPE_DE_NGINX = 2 * 1024 * 1024;
const RAZON_LARGA = "Mantenimiento Integral de Ascensores y Equipos Electromecánicos del Río de la Plata S.R.L.";
const CSV = "Fecha valor;Detalle;Debito;Referencia\n05/11/2026;Pago;-184350,00;0048213377\n";

/** Manda los encabezados de un POST con ese Content-Length, sin el cuerpo, y lee la respuesta. */
function soloEncabezados(base: string, ruta: string, largo: number): Promise<{ estado: number; tipo: string }> {
  const url = new URL(ruta, base);
  if (url.protocol !== "http:") throw new Error(`soloEncabezados habla HTTP plano y la base es ${base}`);
  return new Promise((resolver, rechazar) => {
    const socket = connect(Number(url.port || "80"), url.hostname);
    let leido = "";
    socket.setEncoding("latin1");
    socket.on("connect", () => {
      socket.write(
        `POST ${url.pathname} HTTP/1.1\r\nHost: ${url.host}\r\nContent-Type: text/csv\r\n` +
          `Content-Length: ${largo}\r\nConnection: close\r\n\r\n`,
      );
    });
    socket.on("data", (parte: string) => {
      leido += parte;
      const fin = leido.indexOf("\r\n\r\n");
      if (fin < 0) return;
      socket.destroy();
      const [primera, ...encabezados] = leido.slice(0, fin).split("\r\n");
      const estado = Number(primera?.split(" ")[1]);
      const tipo = encabezados.find((e) => e.toLowerCase().startsWith("content-type:"))?.slice("content-type:".length).trim();
      if (!Number.isInteger(estado) || tipo === undefined) rechazar(new Error(`respuesta inesperada: ${leido.slice(0, fin)}`));
      else resolver({ estado, tipo });
    });
    socket.on("error", rechazar);
    socket.on("close", () => rechazar(new Error(`se cerró sin respuesta completa: ${leido}`)));
  });
}

const pago = (pago_id: string, numero: string, puede_cotejar = true) => ({
  pago_id, orden_id: ORDEN, proveedor: RAZON_LARGA, concepto: "SINTETICO: ascensor", numero_operacion: numero,
  fecha_operacion: "2026-11-05", importe_centavos: 18_435_000, moneda: "ARS", puede_cotejar,
});

const VISTA = {
  columnas: ["Fecha valor", "Detalle", "Debito", "Referencia"], muestra: ["05/11/2026", "Pago", "-184350,00", "0048213377"],
  signo_sugerido: "con_signo", columnas_guardadas: null, columnas_reconocidas: null,
};

const RESULTADO = {
  desde: "2026-11-01", hasta: "2026-11-30", movimientos_nuevos: 2, movimientos_repetidos: 0, cotejados: 12, ya_cotejados: 0, anulados: 0,
  esperan_conciliar: 0, para_otra_persona: 0, lineas_no_leidas: 0, filas_no_leidas: [], aca_no_banco: [pago(P2, "0048211904")],
  total_aca_no_banco: 1, banco_no_aca: [{ fecha: "2026-11-02", importe_centavos: 1_240_000, moneda: "ARS", numero_operacion: "0048213999" }],
  total_banco_no_aca: 1,
};

type Pedidos = { extracto: { cuerpo: string; parametros: URLSearchParams }[]; tildes: unknown[] };

async function simularCotejo(pagina: Page, baseURL: string): Promise<Pedidos> {
  const pedidos: Pedidos = { extracto: [], tildes: [] };
  let pendientes = [pago(P1, "0048213377"), pago(P2, "0048211904", false)];
  const c = `/api/v1/consorcios/${A}`;
  await pagina.route("**/api/v1/**", async (ruta) => {
    const pedido = ruta.request();
    const url = new URL(pedido.url());
    const camino = url.pathname;
    const permisos = simularPermisos(ruta, camino);
    if (permisos !== null) return permisos;
    if (camino === "/api/v1/me/consorcios") return ruta.fulfill({ json: [{ id: A, nombre: "Edificio Azul", vinculos: [], monedas: ["ARS"] }] });
    if (camino === `${c}/pagos/bandeja`) return ruta.fulfill({ json: { elementos: [], avisos_rechazo: [], por_cotejar: 1, sin_explicar: 0 } });
    if (camino === `${c}/pagos/cotejo` && pedido.method() === "GET")
      return ruta.fulfill({ json: { elementos: pendientes, siguiente: null, tope_bytes: 20 * 1024 * 1024, maximo_pagos: 100, moneda_extracto: "ARS" } });
    if (camino === `${c}/pagos/cotejo` && pedido.method() === "POST") {
      pedidos.tildes.push(pedido.postDataJSON());
      pendientes = pendientes.filter((p) => p.pago_id !== P1);
      return ruta.fulfill({ json: { cotejados: 1 } });
    }
    if (camino === `${c}/pagos/cotejo/columnas`) return ruta.fulfill({ json: VISTA });
    if (camino === `${c}/pagos/cotejo/extracto`) {
      pedidos.extracto.push({ cuerpo: pedido.postDataBuffer()?.toString() ?? "", parametros: url.searchParams });
      pendientes = pendientes.filter((p) => p.pago_id !== P1);
      return ruta.fulfill({ json: RESULTADO });
    }
    return ruta.abort();
  });
  await pagina.context().addCookies([
    { name: "__Host-cem_csrf", value: CSRF, domain: new URL(baseURL).hostname, path: "/", secure: true },
  ]);
  return pedidos;
}

function vigilarErrores(pagina: Page): string[] {
  const errores: string[] = [];
  pagina.on("pageerror", (e) => errores.push(e.message));
  pagina.on("console", (m) => {
    if (m.type() === "error") errores.push(m.text());
  });
  return errores;
}

async function sinDesbordar(pagina: Page) {
  const { contenido, ventana } = await pagina.evaluate(() => ({
    contenido: document.documentElement.scrollWidth,
    ventana: window.innerWidth,
  }));
  expect(contenido).toBeLessThanOrEqual(ventana);
}

test.describe("cotejar con el banco", () => {
  test("se llega desde la bandeja y se tilda a mano", async ({ page, baseURL }) => {
    const errores = vigilarErrores(page);
    const pedidos = await simularCotejo(page, baseURL!);
    await page.goto("/pagos");
    // BAN-07: el banco es una fila de la bandeja con su cuenta, no un párrafo con enlace.
    await page.getByRole("link", { name: "Para cotejar 1 pago" }).click();
    await expect(page.getByRole("heading", { level: 1, name: "Cotejar con el banco" })).toBeVisible();
    const tilde = page.getByRole("checkbox", { name: new RegExp("0048213377") });
    await expect(tilde).toBeVisible();
    const fila = page.locator("label.tilde-pago").first();
    expect((await fila.boundingBox())?.height).toBeGreaterThanOrEqual(ALTO_TOQUE_PX);
    // COT-05: lo que esta cuenta no puede cotejar no se lista; va en una línea al pie.
    await expect(page.getByText("1 pago lo coteja otra persona del Consejo: esta cuenta participó de él.")).toBeVisible();
    await expect(page.getByRole("checkbox", { name: new RegExp("0048211904") })).toHaveCount(0);
    // C8P-16: sin nada tildado no hay botón; el número del botón cuenta lo tildado.
    await expect(page.getByRole("button", { name: /^Marcar/ })).toHaveCount(0);
    await sinDesbordar(page);
    await sinFaltasDeAccesibilidad(page);

    await tilde.check();
    await page.getByRole("button", { name: "Marcar 1 como cotejado" }).click();
    // FL-CON-06: cotejado el último pago de esta cuenta, vuelve sola a Pagos con el «Listo».
    await expect(page).toHaveURL(/\/pagos$/);
    await expect(page.getByText("Listo: quedó cotejado 1 pago.")).toBeVisible();
    await expect(tilde).toHaveCount(0);
    expect(pedidos.tildes).toEqual([{ pagos: [P1] }]);
    expect(errores).toEqual([]);
  });

  test("sube el extracto en una ventana, elige las columnas y ve el resultado en una hoja", async ({ page, baseURL }) => {
    const errores = vigilarErrores(page);
    const pedidos = await simularCotejo(page, baseURL!);
    await page.goto("/pagos/cotejo");
    // COT-01: subir el extracto es un botón que abre su ventana; la lista queda de fondo.
    await page.getByRole("button", { name: "Subir el extracto" }).click();
    const subida = page.getByRole("dialog", { name: "Subir el extracto" });
    // Soltar o elegir el archivo en todos los anchos; un CSV no se saca con la cámara.
    await expect(subida.getByText("Soltar el archivo acá")).toBeVisible();
    await expect(subida.getByRole("button", { name: "Elegir un archivo" })).toBeVisible();
    await expect(subida.getByRole("button", { name: "Sacar una foto" })).toHaveCount(0);
    await subida.getByLabel("Archivo del extracto").setInputFiles({ name: "extracto.csv", mimeType: "text/csv", buffer: Buffer.from(CSV) });
    await subida.getByRole("button", { name: "Subir el extracto" }).click();

    const columnas = page.getByRole("dialog", { name: "¿Qué es cada columna?" });
    await expect(columnas.getByText("Primera fila: 05/11/2026 · -184350,00 · 0048213377")).toBeVisible();
    await sinDesbordar(page);
    await sinFaltasDeAccesibilidad(page);
    await columnas.getByRole("button", { name: "Comparar" }).click();

    // COT-04: el resultado va en una hoja que se cierra con «Listo».
    const hoja = page.getByRole("dialog", { name: /^Extracto del / });
    await expect(hoja.getByText("12 pagos coinciden con el banco y quedaron cotejados.")).toBeVisible();
    await expect(hoja.getByRole("heading", { name: /Están acá y no en el banco/ })).toBeVisible();
    // FL-CON-04: los débitos sin pago se cuentan, con el enlace a su pantalla; la lista no se repite.
    await expect(hoja.getByText(/^1 débito del banco no tiene un pago registrado acá\./)).toBeVisible();
    await expect(hoja.getByText(/0048213999/)).toHaveCount(0);
    await sinDesbordar(page);
    await sinFaltasDeAccesibilidad(page);
    await hoja.getByRole("button", { name: "Listo" }).click();
    await expect(hoja).toHaveCount(0);
    // FL-CON-06: sin nada más para esta cuenta, vuelve sola a Pagos.
    await expect(page).toHaveURL(/\/pagos$/);

    const [subido] = pedidos.extracto;
    expect(subido?.cuerpo).toBe(CSV); // el archivo tal cual, sin pasar por JSON
    expect(subido?.parametros.get("importe")).toBe("Debito");
    expect(subido?.parametros.get("moneda")).toBe("ARS"); // el extracto dice en qué moneda está (decisión 9)
    expect(errores).toEqual([]);
  });

  // Solo los encabezados: nginx decide su tope por el Content-Length, antes del cuerpo. Mandar los
  // 2 MB de verdad era una carrera: el backend contesta 401 sin leerlos (no hay sesión) y cerraba
  // mientras el cuerpo seguía saliendo («socket hang up» en cotejo.e2e.ts:152 y :166).
  test("nginx deja pasar el extracto hasta el backend, que es quien dice el tope", async ({ baseURL }) => {
    for (const final of ["columnas", "extracto"]) {
      const r = await soloEncabezados(baseURL!, `/api/v1/consorcios/${A}/pagos/cotejo/${final}`, BYTES_SOBRE_EL_TOPE_DE_NGINX);
      // Lo contesta el backend (JSON, sin sesión), no nginx (sus errores son HTML).
      expect(r.estado, final).toBe(401);
      expect(r.tipo, final).toContain("application/json");
    }
    // Control: el mismo pedido por otra ruta lo corta nginx con su tope por omisión.
    const otra = await soloEncabezados(baseURL!, `/api/v1/consorcios/${A}/pagos/cotejo`, BYTES_SOBRE_EL_TOPE_DE_NGINX);
    expect(otra.estado).toBe(413);
  });
});
