import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router";

import { App } from "./App";
import { AvisoVersion } from "./AvisoVersion";
import { escucharInstalacion } from "./instalacion";
import { aplicarLecturaAmpliada, preferenciaInicial } from "./lecturaAmpliada";
import { ProveedorVersion } from "./ProveedorVersion";
import "./estilos.css";
import "./lecturaAmpliada.css";

// Antes del primer render: así la página no aparece un instante con la letra normal (RF53).
aplicarLecturaAmpliada(preferenciaInicial());

escucharInstalacion();

const consultas = new QueryClient({ defaultOptions: { queries: { retry: 1 } } });

createRoot(document.getElementById("raiz")!).render(
  <StrictMode>
    <ProveedorVersion>
      <QueryClientProvider client={consultas}>
        <BrowserRouter>
          <App />
        </BrowserRouter>
        <AvisoVersion />
      </QueryClientProvider>
    </ProveedorVersion>
  </StrictMode>,
);
