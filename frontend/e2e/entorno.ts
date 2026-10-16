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

// El backend limita /api/v1/auth/ a 20 pedidos por minuto por IP (backend/app/limite.py), y toda
// la corrida sale de la misma IP: la suite de main ya pasa cerca de ese cupo (medido el
// 16-10-2026: 69 pruebas en 1,0 min, y con 8 pedidos de más fallaron 7 por 429). Las pruebas que
// necesitan más pedidos reales se presentan como otro cliente, como en producción: nginx toma la IP
// de CF-Connecting-IP (nginx/cem-frontend.conf.template). Direcciones de documentación (RFC 5737).
export function otroCliente(n: number): { extraHTTPHeaders: Record<string, string> } {
  return { extraHTTPHeaders: { "CF-Connecting-IP": `198.51.100.${n}` } };
}

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
