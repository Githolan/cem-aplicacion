// Token CSRF de token doble (documento 13, §10.4): el backend lo deja en una cookie legible y
// toda petición con sesión que cambia estado lo devuelve en el encabezado X-CSRF-Token.

export const COOKIE_CSRF = "__Host-cem_csrf";
export const ENCABEZADO_CSRF = "X-CSRF-Token";

/** El encabezado CSRF si hay sesión en este navegador; vacío si no. */
export function encabezadoCsrf(cookies: string = document.cookie): Record<string, string> {
  for (const parte of cookies.split(";")) {
    const [nombre, ...valor] = parte.trim().split("=");
    if (nombre === COOKIE_CSRF && valor.length > 0) return { [ENCABEZADO_CSRF]: valor.join("=") };
  }
  return {};
}
