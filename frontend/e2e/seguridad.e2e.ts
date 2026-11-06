import { expect, test } from "@playwright/test";

import { simularSesion } from "./entorno";

// Cabeceras de nginx/cabeceras.conf (documento 13, §14.3 y §14.5). Se exigen en la página, en
// las rutas de la SPA, en los activos, en el manifiesto y en lo que se reenvía al backend.
const CABECERAS: Record<string, string> = {
  "x-frame-options": "DENY",
  "x-content-type-options": "nosniff",
  "referrer-policy": "strict-origin-when-cross-origin",
  "permissions-policy": "camera=(self), geolocation=(), microphone=(), payment=()",
};
const DIRECTIVAS_CSP = [
  "default-src 'self'",
  "script-src 'self'",
  "style-src 'self'",
  "img-src 'self' data: blob:",
  "object-src 'none'",
  "base-uri 'self'",
  "frame-ancestors 'none'",
];

test.describe("cabeceras de seguridad del nginx de la imagen", () => {
  test("están en cada tipo de respuesta y nginx no publica su versión", async ({ page, request }) => {
    await page.goto("/instalar");
    const activo = await page.locator('script[type="module"][src^="/assets/"]').getAttribute("src");
    expect(activo).toBeTruthy();

    for (const ruta of ["/", "/instalar", activo!, "/manifest.webmanifest", "/sw.js", "/health"]) {
      const respuesta = await request.get(ruta);
      expect(respuesta.status(), ruta).toBe(200);
      const cabeceras = respuesta.headers();
      for (const [nombre, valor] of Object.entries(CABECERAS)) {
        expect(cabeceras[nombre], `${nombre} en ${ruta}`).toBe(valor);
      }
      const csp = (cabeceras["content-security-policy"] ?? "").split(";").map((d) => d.trim());
      for (const directiva of DIRECTIVAS_CSP) expect(csp, `CSP en ${ruta}`).toContain(directiva);
      expect(cabeceras["server"], ruta).toBe("nginx");
    }
  });

  test("el contenido de un archivo no suma las cabeceras de la aplicación a las del backend", async ({ request }) => {
    const id = "00000000-0000-4000-8000-000000000000";
    const respuesta = await request.get(`/api/v1/consorcios/${id}/documentos/${id}/versiones/${id}/contenido`);
    expect(respuesta.status()).toBe(401);
    const cabeceras = respuesta.headersArray().map((c) => c.name.toLowerCase());
    expect(cabeceras.filter((c) => c === "x-content-type-options")).toHaveLength(1);
    expect(cabeceras.filter((c) => c === "x-frame-options")).toHaveLength(1);
    expect(cabeceras).not.toContain("content-security-policy");
    // Control: el resto de la API sí lleva la CSP de la aplicación.
    const otra = await request.get(`/api/v1/consorcios/${id}/documentos`);
    expect(otra.headers()["content-security-policy"]).toContain("default-src 'self'");
  });

  test("la página carga sin errores ni violaciones de la CSP", async ({ page }) => {
    const errores: string[] = [];
    page.on("pageerror", (e) => errores.push(e.message));
    page.on("console", (m) => {
      if (m.type() === "error") errores.push(m.text());
    });
    await page.addInitScript(() => {
      document.addEventListener("securitypolicyviolation", (e) =>
        console.error(`CSP: ${e.violatedDirective} ${e.blockedURI}`),
      );
    });

    await simularSesion(page, "con_sesion");
    await page.goto("/instalar");
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    await page.goto("/");
    await expect(page.getByText("El servicio funciona.")).toBeVisible();
    expect(errores).toEqual([]);
  });
});
