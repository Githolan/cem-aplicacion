import { useSyncExternalStore } from "react";

/** Desde 1025 px (documento de reglas, «Desktop»). */
export const CONSULTA_AL_LADO = "(min-width: 64.0625em)";

export function useAncho(consulta: string): boolean {
  return useSyncExternalStore(
    (avisar) => {
      const medios = window.matchMedia(consulta);
      medios.addEventListener("change", avisar);
      return () => medios.removeEventListener("change", avisar);
    },
    () => window.matchMedia(consulta).matches,
  );
}
