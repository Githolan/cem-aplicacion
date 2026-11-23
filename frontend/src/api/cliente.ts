import createClient from "openapi-fetch";

import { conAyuda, vigilarRevocacion } from "../ayuda/estadoAyuda";
import { pedirConSesion } from "../sesionEncima";
import type { components, paths } from "./tipos";

// Cliente tipado ligero (documento 13, §11.4): las formas salen de contratos/openapi.json,
// generadas en cada construcción; si el contrato cambia de forma incompatible, tsc falla.
// La API es del mismo origen (nginx reenvía /api/ y /health), así que no hay URL de backend.
export const api = createClient<paths>({
  baseUrl: globalThis.location?.origin ?? "",
  // Se resuelve en cada pedido (no al importar) para que las pruebas puedan simular fetch. Ayudando a
  // otra persona (RF53), los pedidos a su edificio llevan la autorización (`conAyuda`), y si la persona
  // asistida la revocó, la pestaña sale sola de la ayuda (`vigilarRevocacion`).
  fetch: async (pedir) => {
    const pedido = conAyuda(pedir);
    return vigilarRevocacion(pedido, await pedirConSesion(pedido));
  },
});

export type Salud = components["schemas"]["Salud"];
