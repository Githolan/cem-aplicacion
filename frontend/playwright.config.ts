import { defineConfig, devices } from "@playwright/test";

function requerida(nombre: string): string {
  const valor = process.env[nombre];
  if (!valor) throw new Error(`falta ${nombre}: correr con "pnpm e2e" (scripts/probar-extremo-a-extremo.sh)`);
  return valor;
}

export default defineConfig({
  testDir: "e2e",
  testMatch: "**/*.e2e.ts",
  forbidOnly: true,
  use: { baseURL: requerida("CEM_E2E_URL"), trace: "retain-on-failure" },
  projects: [
    { name: "escritorio", use: { ...devices["Desktop Chrome"] } },
    { name: "movil", use: { ...devices["Pixel 7"] } },
  ],
});
