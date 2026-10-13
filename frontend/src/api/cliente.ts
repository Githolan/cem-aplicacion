// Cliente tipado de la API. Los tipos salen de contratos/openapi.json (AD-06): nunca se escriben a mano.
import createClient from "openapi-fetch";
import { createContext, useContext } from "react";
import type { components, paths } from "./esquema";

export type ClienteApi = ReturnType<typeof createClient<paths>>;
export type Salud = components["schemas"]["Salud"];

/** La API comparte origen con la aplicación: el proxy enruta /api y /health al backend. */
export function crearClienteApi(opciones: { baseUrl: string; fetch?: typeof globalThis.fetch }): ClienteApi {
  return createClient<paths>(opciones);
}

export const ContextoClienteApi = createContext<ClienteApi | null>(null);

export function useClienteApi(): ClienteApi {
  const cliente = useContext(ContextoClienteApi);
  if (cliente === null) {
    throw new Error("useClienteApi se usó fuera de ContextoClienteApi");
  }
  return cliente;
}

export class ErrorApi extends Error {
  constructor(
    readonly estadoHttp: number,
    mensaje: string,
  ) {
    super(mensaje);
    this.name = "ErrorApi";
  }
}

export async function obtenerSalud(cliente: ClienteApi): Promise<Salud> {
  const { data, response } = await cliente.GET("/health");
  if (data === undefined) {
    throw new ErrorApi(response.status, `GET /health respondió ${response.status}`);
  }
  return data;
}
