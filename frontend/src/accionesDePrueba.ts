import { readdirSync, readFileSync } from "node:fs";
import { join, resolve } from "node:path";

// Solo para las pruebas (usa Node): el catálogo de acciones sale de las políticas reales del
// contrato, para que un código que no existe no pase por uno que sí.

const CARPETA_POLITICAS = resolve(process.cwd(), "../contratos/cem_contracts/politicas_datos");

/** Las acciones declaradas en `politicas_datos/*.json`. */
export function accionesDeLasPoliticas(): string[] {
  return readdirSync(CARPETA_POLITICAS)
    .filter((archivo) => archivo.endsWith(".json") && archivo !== "esquema.json")
    .flatMap((archivo) => {
      const { politicas } = JSON.parse(readFileSync(join(CARPETA_POLITICAS, archivo), "utf8")) as {
        politicas: { accion: string }[];
      };
      return politicas.map((p) => p.accion);
    });
}

const MIS_ACCIONES = /^GET \/api\/v1\/consorcios\/[^/]+\/mis-acciones$/;

/**
 * Para las pruebas que no miran los permisos: `mis-acciones` responde todo el catálogo, así la
 * pantalla corre por el camino normal (se sabe qué alcanza la persona) y no por el de «no se sabe».
 */
export function alcanzaTodo(clave: string): (() => Response) | undefined {
  return MIS_ACCIONES.test(clave) ? () => Response.json({ acciones: accionesDeLasPoliticas() }) : undefined;
}

/** `mis-acciones` con solo esas acciones. */
export const alcanza =
  (...acciones: string[]) =>
  () =>
    Response.json({ acciones });
