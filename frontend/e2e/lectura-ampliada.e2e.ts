import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

import { sinFaltasDeAccesibilidad } from "./entorno";

// Modo lectura ampliada (RF53, PA28): se mide en el navegador real, contra el nginx de la imagen.
const BOTON = "Letra grande y alto contraste";
const CLASE = "lectura-ampliada";
// Documento 08, §13.4: la base pasa de 16 a 20 píxeles (125 %).
const AUMENTO_MINIMO = 1.25;
// Áreas táctiles más grandes que las 44 px del modo normal.
const ALTO_MINIMO_BOTON_PX = 48;
// Ancho del teléfono más chico que se considera (WCAG 1.4.10 pide 320 px; 360 es el Android común).
const ANCHO_TELEFONO_PX = 360;
const ALTO_TELEFONO_PX = 780;
// Páginas que se abren sin sesión. Las que piden sesión o un enlace, más abajo (PAGINAS_CON_DATOS).
const PAGINAS = ["/", "/instalar", "/entrar", "/una/ruta/que/no/existe"];
const ESQUEMAS = ["light", "dark"] as const;

const boton = (pagina: Page) => pagina.getByRole("banner").getByRole("button", { name: BOTON });

async function tamanios(pagina: Page) {
  return pagina.evaluate(() => {
    const px = (e: Element | null) => parseFloat(getComputedStyle(e!).fontSize);
    return { raiz: px(document.documentElement), parrafo: px(document.querySelector("main p")) };
  });
}

// Contraste real medido por axe (WCAG 1.4.6, nivel AAA: 7:1 el texto normal, 4.5:1 el grande).
// Además del cero de faltas se exige que axe haya medido nodos: un análisis que no mide nada
// también da cero faltas.
async function contrasteAAA(pagina: Page) {
  const resultado = await new AxeBuilder({ page: pagina })
    .withRules(["color-contrast", "color-contrast-enhanced"])
    .analyze();
  const faltas = resultado.violations.map(
    (v) => `${v.id}: ${v.nodes.map((n) => `${n.target.join(" ")} (${n.any[0]?.message ?? ""})`).join(", ")}`,
  );
  expect(faltas).toEqual([]);
  const medidos = resultado.passes.find((p) => p.id === "color-contrast-enhanced")?.nodes.length ?? 0;
  expect(medidos).toBeGreaterThan(0);
}

async function prender(pagina: Page) {
  await boton(pagina).click();
  await expect(boton(pagina)).toHaveAttribute("aria-pressed", "true");
  await expect(pagina.locator("html")).toHaveClass(CLASE);
}

test.describe("modo lectura ampliada", () => {
  for (const esquema of ESQUEMAS) {
    test(`esquema ${esquema}: apagado AA; prendido AAA, 125 % de letra y botones grandes`, async ({ page }) => {
      await page.emulateMedia({ colorScheme: esquema });
      await page.goto("/");
      await expect(page.getByRole("status").getByText("El servicio funciona.")).toBeVisible();
      await expect(boton(page)).toHaveAttribute("aria-pressed", "false");
      await sinFaltasDeAccesibilidad(page);
      const antes = await tamanios(page);

      await prender(page);

      const despues = await tamanios(page);
      expect(despues.raiz / antes.raiz).toBeGreaterThanOrEqual(AUMENTO_MINIMO);
      expect(despues.parrafo / antes.parrafo).toBeGreaterThanOrEqual(AUMENTO_MINIMO);
      const alto = (await boton(page).boundingBox())!.height;
      expect(alto).toBeGreaterThanOrEqual(ALTO_MINIMO_BOTON_PX);
      await sinFaltasDeAccesibilidad(page);
      await contrasteAAA(page);
    });
  }

  // Una prueba por página y esquema: juntas eran 8 pasadas de axe en AAA dentro de un solo plazo
  // de 30 s, y con la PC cargada (CI con 8 navegadores) se pasaba de tiempo sin que nada
  // estuviera mal (medido el 17-10-2026: 8 de 18 corridas repetidas, también sin cambios).
  for (const ruta of PAGINAS) {
    for (const esquema of ESQUEMAS) {
      test(`prendido, ${ruta} (${esquema}) no se sale de 360 px ni baja de AAA`, async ({ page }) => {
        await page.setViewportSize({ width: ANCHO_TELEFONO_PX, height: ALTO_TELEFONO_PX });
        await page.emulateMedia({ colorScheme: esquema });
        await page.goto("/");
        await prender(page);
        await page.goto(ruta);
        await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
        await expect(page.locator("html"), ruta).toHaveClass(CLASE);
        const { ancho, visible } = await page.evaluate(() => ({
          ancho: document.documentElement.scrollWidth,
          visible: document.documentElement.clientWidth,
        }));
        expect(visible, `${ruta} (${esquema})`).toBe(ANCHO_TELEFONO_PX);
        expect(ancho, `${ruta} (${esquema}): desplazamiento horizontal`).toBeLessThanOrEqual(visible);
        await contrasteAAA(page);
      });
    }
  }

  test("la elección se recuerda al recargar y se aplica antes de pintar", async ({ page }) => {
    // Se anota si <html> ya tenía la clase cuando React puso el primer elemento en #raiz.
    await page.addInitScript((clase) => {
      const anotar = new MutationObserver(() => {
        const raiz = document.getElementById("raiz");
        if (!raiz?.firstChild) return;
        document.documentElement.dataset.claseAlPintar = String(document.documentElement.classList.contains(clase));
        anotar.disconnect();
      });
      anotar.observe(document, { childList: true, subtree: true });
    }, CLASE);
    await page.goto("/");
    await expect(page.locator("html")).toHaveAttribute("data-clase-al-pintar", "false");
    await prender(page);

    await page.reload();

    await expect(page.locator("html")).toHaveAttribute("data-clase-al-pintar", "true");
    await expect(boton(page)).toHaveAttribute("aria-pressed", "true");

    await boton(page).click();
    await page.reload();
    await expect(page.locator("html")).toHaveAttribute("data-clase-al-pintar", "false");
    await expect(boton(page)).toHaveAttribute("aria-pressed", "false");
  });

  test("sin elección guardada, prefers-contrast: more lo prende; apagarlo vale sobre el sistema", async ({ page }) => {
    await page.emulateMedia({ contrast: "more" });
    await page.goto("/");
    await expect(page.locator("html")).toHaveClass(CLASE);
    await expect(boton(page)).toHaveAttribute("aria-pressed", "true");

    await boton(page).click();
    await page.reload();
    await expect(page.locator("html")).not.toHaveClass(CLASE);
    await expect(boton(page)).toHaveAttribute("aria-pressed", "false");
  });
});

// Páginas que piden sesión o un enlace del correo (pantalla mínima, decisión del 20-10-2026). El
// backend de la corrida no tiene base de datos: la API se simula desde el navegador con los datos
// mínimos del contrato, para que la página se vea con sus datos (no el "Consultando…"); los
// textos largos (correo, descripción) son los que más empujan el ancho. Qué decide cada respuesta
// lo prueban Vitest y el backend; acá se mide el ancho a 360 px, AA y el contraste con letra grande.
const C1 = "11111111-1111-4111-8111-111111111111";
const DECISION = "77777777-7777-4777-8777-777777777777";
const TOKEN_ENLACE = "L".repeat(21) + "_-" + "m".repeat(20);
const CORREO_LARGO = "ana.perez.con.un.correo.largo@ejemplo.com";
const TOTP_ACTIVO = {
  activo: true,
  activado_en: "2026-10-15T20:00:00Z",
  codigos_recuperacion_restantes: 10,
  verificado_en: null,
};

type Respuestas = Record<string, unknown>;

interface PaginaConDatos {
  ruta: string;
  /** La URL que se abre (las invitaciones, con el token en el fragmento, como desde el correo). */
  url: string;
  /** Un texto que solo aparece con los datos ya cargados. */
  cargada: string;
  /** Respuesta por «MÉTODO camino»; lo que no está acá se corta y la prueba falla. */
  api: Respuestas;
}

const PAGINAS_CON_DATOS: PaginaConDatos[] = [
  {
    ruta: "/cuenta",
    url: "/cuenta",
    cargada: "Dónde tenés la sesión abierta",
    api: {
      "GET /api/v1/auth/sesion": {
        persona_id: "99999999-9999-4999-8999-999999999999",
        nombre: "Ana Pérez",
        correo: CORREO_LARGO,
        sesion_id: "22222222-2222-4222-8222-222222222222",
        vence_en: "2026-11-15T20:00:00Z",
      },
      "GET /api/v1/auth/sesiones": {
        elementos: [
          { id: "22222222-2222-4222-8222-222222222222", dispositivo: "Chrome en Android", actual: true },
          { id: "33333333-3333-4333-8333-333333333333", dispositivo: "Firefox en Windows", actual: false },
        ].map((s) => ({
          ...s,
          creada_en: "2026-10-01T12:00:00Z",
          ultimo_uso_en: "2026-10-16T12:00:00Z",
          vence_en: "2026-11-15T12:00:00Z",
        })),
      },
      "GET /api/v1/auth/totp": TOTP_ACTIVO,
      "GET /api/v1/auth/contrasena": { definida: true, cambiada_en: "2026-10-10T15:30:00Z" },
      "GET /api/v1/privacidad/casos": { elementos: [] },
    },
  },
  {
    ruta: "/cuenta/segundo-factor",
    url: "/cuenta/segundo-factor",
    cargada: "Está activado",
    api: { "GET /api/v1/auth/totp": TOTP_ACTIVO },
  },
  {
    ruta: "/invitaciones",
    url: "/invitaciones",
    cargada: "Beto Pérez, 1° A",
    api: {
      "GET /api/v1/me/consorcios": [{ id: C1, nombre: "Edificio Azul", vinculos: [] }],
      [`GET /api/v1/consorcios/${C1}/unidades`]: {
        elementos: [{ id: "44444444-4444-4444-8444-444444444444", designacion: "1° A" }],
      },
      [`GET /api/v1/consorcios/${C1}/invitaciones`]: {
        elementos: [
          {
            id: "55555555-5555-4555-8555-555555555555",
            unidad_id: "44444444-4444-4444-8444-444444444444",
            unidad: "1° A",
            tipo: "inquilino",
            correo: CORREO_LARGO,
            nombre: "Beto Pérez",
            estado: "pendiente",
            creada_en: "2026-10-18T12:00:00Z",
            vence_en: "2026-10-25T12:00:00Z",
            aceptada_en: null,
            revocada_en: null,
          },
        ],
      },
    },
  },
  {
    ruta: "/mandatos",
    url: "/mandatos",
    cargada: "Estudio Sur, Administración externa",
    api: {
      "GET /api/v1/me/consorcios": [{ id: C1, nombre: "Edificio Azul", vinculos: [] }],
      [`GET /api/v1/consorcios/${C1}/mandatos`]: {
        elementos: [
          {
            id: "66666666-6666-4666-8666-666666666666",
            tipo: "administrador_externo",
            nombre: "Estudio Sur",
            cuit: "20123456786",
            vigente_desde: "2026-02-01",
            vigente_hasta: null,
            estado: "vigente",
            con_cuenta: false,
          },
        ],
      },
      [`GET /api/v1/consorcios/${C1}/decisiones`]: {
        // Del largo máximo (500): la opción del selector no puede ensanchar la página.
        elementos: [{ id: DECISION, tipo: "asamblea", fecha: "2026-01-10", descripcion: "Acta muy detallada ".repeat(25).slice(0, 500) }],
      },
      [`GET /api/v1/consorcios/${C1}/mandatos/candidatos`]: {
        elementos: [{ persona_id: "88888888-8888-4888-8888-888888888888", nombre: "Beto Pérez", unidades: ["1° A"] }],
      },
      // El alta pregunta si quien mira registró la decisión elegida (A15).
      [`GET /api/v1/consorcios/${C1}/decisiones/${DECISION}`]: {
        id: DECISION,
        tipo: "asamblea",
        fecha: "2026-01-10",
        descripcion: "Acta muy detallada",
        referencia: "Acta 14",
        sha256: null,
        registrada_por_vos: false,
        del_equipo: true,
      },
    },
  },
  {
    ruta: "/invitacion",
    url: `/invitacion#token=${TOKEN_ENLACE}`,
    cargada: "Aceptar la invitación",
    api: {
      "POST /api/v1/auth/invitacion/consultar": {
        consorcio: "Edificio Azul",
        unidad: "1° A",
        tipo: "inquilino",
        correo: CORREO_LARGO,
        nombre: "Beto Pérez",
        invitada_por: "Ana",
        vence_en: "2026-10-27T12:00:00Z",
      },
    },
  },
  {
    ruta: "/invitacion-mandato",
    url: `/invitacion-mandato#token=${TOKEN_ENLACE}`,
    cargada: "Aceptar y asociar mi cuenta",
    api: {
      "POST /api/v1/auth/invitacion-mandato/consultar": {
        consorcio: "Edificio Azul",
        mandatario: "Estudio Sur",
        invitada_por: "Ana",
        vence_en: "2026-10-27T12:00:00Z",
      },
    },
  },
];

/** Responde la API desde el navegador; lo no previsto se corta y se anota (la prueba lo exige vacío). */
async function simular(pagina: Page, api: Respuestas): Promise<string[]> {
  const imprevistos: string[] = [];
  await pagina.route("**/api/v1/**", async (ruta) => {
    const pedido = ruta.request();
    const clave = `${pedido.method()} ${new URL(pedido.url()).pathname}`;
    if (clave in api) return ruta.fulfill({ json: api[clave] });
    imprevistos.push(clave);
    return ruta.abort();
  });
  return imprevistos;
}

test.describe("modo lectura ampliada en las páginas con datos", () => {
  for (const { ruta, url, cargada, api } of PAGINAS_CON_DATOS) {
    for (const esquema of ESQUEMAS) {
      test(`prendido, ${ruta} con datos (${esquema}) no se sale de 360 px y pasa AA y AAA`, async ({ page }) => {
        await page.setViewportSize({ width: ANCHO_TELEFONO_PX, height: ALTO_TELEFONO_PX });
        await page.emulateMedia({ colorScheme: esquema });
        await page.goto("/");
        await prender(page);
        const imprevistos = await simular(page, api);
        await page.goto(url);
        await expect(page.getByText(cargada, { exact: true })).toBeVisible();
        await expect(page.locator("html"), ruta).toHaveClass(CLASE);
        const { ancho, visible } = await page.evaluate(() => ({
          ancho: document.documentElement.scrollWidth,
          visible: document.documentElement.clientWidth,
        }));
        expect(visible, `${ruta} (${esquema})`).toBe(ANCHO_TELEFONO_PX);
        expect(ancho, `${ruta} (${esquema}): desplazamiento horizontal`).toBeLessThanOrEqual(visible);
        await sinFaltasDeAccesibilidad(page);
        await contrasteAAA(page);
        expect(imprevistos).toEqual([]);
      });
    }
  }
});
