import AxeBuilder from "@axe-core/playwright";
import { expect, type Page } from "@playwright/test";

// Datos de la corrida que deja scripts/probar-extremo-a-extremo.sh. Sin ellos no hay contra qué
// probar: se falla en vez de suponer una URL.
function requerida(nombre: string): string {
  const valor = process.env[nombre];
  if (!valor) throw new Error(`falta ${nombre}: correr con "pnpm e2e" (scripts/probar-extremo-a-extremo.sh)`);
  return valor;
}

/** El mismo cem-frontend apuntando a un backend que no existe: /health da 502. */
export const URL_SIN_BACKEND = requerida("CEM_E2E_URL_SIN_BACKEND");
/** SHA que el cem-backend de la corrida informa en /health (CEM_SHA_IMAGEN). */
export const SHA_BACKEND = requerida("CEM_E2E_SHA");

// La página muestra los primeros 7 caracteres de la SHA, como git.
export const LARGO_SHA_CORTA = 7;

export const AGENTE_IPHONE =
  "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1";

// Documento 13, §14 y RNF07: WCAG 2.2 AA. axe solo evalúa las reglas con estas etiquetas.
const ETIQUETAS_WCAG = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"];

export async function sinFaltasDeAccesibilidad(pagina: Page) {
  const resultado = await new AxeBuilder({ page: pagina }).withTags(ETIQUETAS_WCAG).analyze();
  const faltas = resultado.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`);
  expect(faltas).toEqual([]);
}

// Chromium no emula `display-mode` (ni con Emulation.setEmulatedMedia de CDP: medido con Chromium
// 143, la consulta sigue en false), y una instalación de verdad (CDP PWA.install) crea accesos
// directos en el sistema. Así que solo esa consulta se responde desde un script de inicio; todas
// las demás siguen yendo al matchMedia real del navegador.
export async function simularStandalone(pagina: Page) {
  await pagina.addInitScript(() => {
    const original = window.matchMedia.bind(window);
    window.matchMedia = (consulta: string) => {
      const real = original(consulta);
      if (consulta.replace(/\s/g, "") !== "(display-mode:standalone)") return real;
      return Object.create(real, { matches: { value: true } }) as MediaQueryList;
    };
  });
}
