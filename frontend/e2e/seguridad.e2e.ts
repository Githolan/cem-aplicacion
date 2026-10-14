import { expect, test } from "@playwright/test";

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
  "object-src 'none'",
  "base-uri 'self'",
  "frame-ancestors 'none'",
];

test.describe("cabeceras de seguridad del nginx de la imagen", () => {
  test("están en cada tipo de respuesta y nginx no publica su versión", async ({ page, request }) => {
    await page.goto("/");
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

    await page.goto("/instalar");
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    await page.goto("/");
    await expect(page.getByText("El servicio funciona.")).toBeVisible();
    expect(errores).toEqual([]);
  });
});
