import { execFileSync } from "node:child_process";

// Contenedores de cem-frontend de distintas versiones en un mismo origen (127.0.0.1:puerto), para
// probar qué pasa cuando el navegador tiene en caché una versión y el servidor ya sirve otra. El
// origen es lo que comparte el service worker, así que la versión nueva se levanta en el MISMO
// puerto que la anterior. Las imágenes y la red las deja scripts/probar-extremo-a-extremo.sh.

function requerida(nombre: string): string {
  const valor = process.env[nombre];
  if (!valor) throw new Error(`falta ${nombre}: correr con "pnpm e2e" (scripts/probar-extremo-a-extremo.sh)`);
  return valor;
}

/** La versión que estaba desplegada antes de que existiera /entrar (38aca25). */
export const IMAGEN_ANTERIOR = requerida("CEM_E2E_IMAGEN_ANTERIOR");
/** La de esta corrida. */
export const IMAGEN_ACTUAL = requerida("CEM_E2E_IMAGEN_ACTUAL");
/** La de esta corrida con otros bytes en sw.js: el navegador la toma como versión nueva. */
export const IMAGEN_SIGUIENTE = requerida("CEM_E2E_IMAGEN_SIGUIENTE");

const RED = requerida("CEM_E2E_RED");
const BACKEND_DESTINO = requerida("CEM_E2E_BACKEND_DESTINO");
const SUFIJO = requerida("CEM_E2E_SUFIJO");
const PREFIJO = requerida("CEM_E2E_PREFIJO_VERSIONES");

/** Lo que se espera a que nginx del contenedor nuevo responda. */
const ESPERA_ARRANQUE_MS = 30_000;
const PAUSA_SONDEO_MS = 250;

export interface Servidor {
  nombre: string;
  puerto: number;
  origen: string;
}

function docker(...argumentos: string[]): string {
  return execFileSync("docker", argumentos, { encoding: "utf8" }).trim();
}

async function esperarArranque(origen: string): Promise<void> {
  const limite = Date.now() + ESPERA_ARRANQUE_MS;
  for (;;) {
    const respuesta = await fetch(`${origen}/frontend-salud`).catch(() => null);
    if (respuesta?.status === 200) return;
    if (Date.now() > limite) throw new Error(`${origen} no respondió /frontend-salud en ${ESPERA_ARRANQUE_MS} ms`);
    await new Promise((listo) => setTimeout(listo, PAUSA_SONDEO_MS));
  }
}

/**
 * Levanta `imagen` con un nombre único de la prueba (`etiqueta`). Sin `puerto`, Docker elige uno
 * libre; con `puerto`, ocupa ese, que es como se reemplaza una versión por otra en el mismo origen.
 */
export async function levantar(imagen: string, etiqueta: string, puerto?: number): Promise<Servidor> {
  const nombre = `${PREFIJO}-${etiqueta}-${SUFIJO}`;
  const publicacion = puerto === undefined ? "127.0.0.1::8080" : `127.0.0.1:${puerto}:8080`;
  docker(
    "run", "-d", "--name", nombre, "--network", RED, "-p", publicacion,
    "-e", `CEM_BACKEND_DESTINO=${BACKEND_DESTINO}`, imagen,
  );
  const asignado = Number(docker("port", nombre, "8080/tcp").split("\n")[0]?.split(":").pop());
  if (!Number.isInteger(asignado)) throw new Error(`no pude leer el puerto de ${nombre}`);
  const origen = `http://127.0.0.1:${asignado}`;
  await esperarArranque(origen);
  return { nombre, puerto: asignado, origen };
}

/** Reemplaza el contenedor por otra imagen en el mismo puerto (mismo origen). */
export async function reemplazar(actual: Servidor, imagen: string, etiqueta: string): Promise<Servidor> {
  bajar(actual);
  return levantar(imagen, etiqueta, actual.puerto);
}

export function bajar(servidor: Servidor): void {
  docker("rm", "-f", servidor.nombre);
}

/** Bajar al terminar la prueba, pase lo que pase; los que ya no existen no cuentan. */
export function bajarTodos(etiquetas: string[]): void {
  for (const etiqueta of etiquetas) {
    const nombre = `${PREFIJO}-${etiqueta}-${SUFIJO}`;
    if (docker("ps", "-aq", "--filter", `name=^${nombre}$`) !== "") docker("rm", "-f", nombre);
  }
}
