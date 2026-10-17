import { defineConfig, devices } from "@playwright/test";

function requerida(nombre: string): string {
  const valor = process.env[nombre];
  if (!valor) throw new Error(`falta ${nombre}: correr con "pnpm e2e" (scripts/probar-extremo-a-extremo.sh)`);
  return valor;
}

// Navegadores en paralelo. Con el valor por defecto de Playwright (la mitad de los hilos: 8 en la
// PC de desarrollo) la máquina se satura junto con el compose y fallan pruebas por plazo vencido
// (6 de 90 el 17-10-2026); con 4 la suite completa pasó dos veces seguidas sin fallas (101 de 101).
const NAVEGADORES_EN_PARALELO = 4;

export default defineConfig({
  testDir: "e2e",
  testMatch: "**/*.e2e.ts",
  forbidOnly: true,
  workers: NAVEGADORES_EN_PARALELO,
  use: { baseURL: requerida("CEM_E2E_URL"), trace: "retain-on-failure" },
  projects: [
    { name: "escritorio", use: { ...devices["Desktop Chrome"] } },
    { name: "movil", use: { ...devices["Pixel 7"] } },
  ],
});
