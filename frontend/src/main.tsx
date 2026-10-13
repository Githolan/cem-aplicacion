import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router";
import { App } from "./App";
import { ContextoClienteApi, crearClienteApi } from "./api/cliente";
import { REINTENTOS_CONSULTA } from "./config";
import "./estilos.css";

const raiz = document.getElementById("raiz");
if (raiz === null) {
  throw new Error("Falta el elemento #raiz en index.html");
}

const clienteConsultas = new QueryClient({ defaultOptions: { queries: { retry: REINTENTOS_CONSULTA } } });
const clienteApi = crearClienteApi({ baseUrl: window.location.origin });

createRoot(raiz).render(
  <StrictMode>
    <ContextoClienteApi.Provider value={clienteApi}>
      <QueryClientProvider client={clienteConsultas}>
        <BrowserRouter>
          <App />
        </BrowserRouter>
      </QueryClientProvider>
    </ContextoClienteApi.Provider>
  </StrictMode>,
);
