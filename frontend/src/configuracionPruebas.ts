import { configure } from "@testing-library/react";
import { afterEach, beforeEach } from "vitest";

import { faltasVistas, vigilarBotones } from "./pesoDeBotones";

// Solo para vitest. La suite completa satura el procesador por sí sola (un proceso por núcleo, 80 a
// 100 % medido el 07-11-2026), y el segundo que da `findBy*` por omisión a veces no alcanza aunque la
// pantalla esté bien. Es un tope: una prueba que encuentra lo que busca sigue terminando enseguida.
const ESPERA_DE_LO_ASINCRONO_MS = 10_000;

configure({ asyncUtilTimeout: ESPERA_DE_LO_ASINCRONO_MS });

// Colores y botón principal (pregunta 13, tanda 5): cada pantalla que una prueba dibuja se revisa
// mientras se dibuja; una falta hace fallar esa prueba (`pesoDeBotones.ts`).
beforeEach(() => vigilarBotones());
afterEach(() => {
  const faltas = faltasVistas();
  if (faltas.length > 0) throw new Error(`Colores y botón principal (pregunta 13):\n${faltas.join("\n")}`);
});
