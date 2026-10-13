import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { App } from "../App";
import { ContextoClienteApi, crearClienteApi } from "../api/cliente";

/** Monta la aplicación en una ruta, con un fetch de prueba que reemplaza a la red. */
export function renderizarEn(ruta: string, fetchDePrueba: typeof globalThis.fetch) {
  const clienteConsultas = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const clienteApi = crearClienteApi({ baseUrl: "http://cem.prueba", fetch: fetchDePrueba });
  return render(
    <ContextoClienteApi.Provider value={clienteApi}>
      <QueryClientProvider client={clienteConsultas}>
        <MemoryRouter initialEntries={[ruta]}>
          <App />
        </MemoryRouter>
      </QueryClientProvider>
    </ContextoClienteApi.Provider>,
  );
}
