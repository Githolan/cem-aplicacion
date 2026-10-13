import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./App";
import { AvisoVersion } from "./AvisoVersion";
import "./estilos.css";

const consultas = new QueryClient({ defaultOptions: { queries: { retry: 1 } } });

createRoot(document.getElementById("raiz")!).render(
  <StrictMode>
    <QueryClientProvider client={consultas}>
      <App />
      <AvisoVersion />
    </QueryClientProvider>
  </StrictMode>,
);
