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
// Páginas que se abren sin sesión. /segundo-factor pide sesión: se prueba cuando haya cuenta de prueba.
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
