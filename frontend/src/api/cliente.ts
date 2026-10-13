import createClient from "openapi-fetch";

import type { components, paths } from "./tipos";

// Cliente tipado ligero (documento 13, §11.4): las formas salen de contratos/openapi.json,
// generadas en cada construcción; si el contrato cambia de forma incompatible, tsc falla.
// La API es del mismo origen (nginx reenvía /api/ y /health), así que no hay URL de backend.
export const api = createClient<paths>({
  baseUrl: globalThis.location?.origin ?? "",
  // Se resuelve en cada pedido (no al importar) para que las pruebas puedan simular fetch.
  fetch: (pedido) => globalThis.fetch(pedido),
});

export type Salud = components["schemas"]["Salud"];
