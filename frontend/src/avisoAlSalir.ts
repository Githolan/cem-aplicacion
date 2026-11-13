import { useEffect } from "react";

// Mientras un alta del segundo factor está en camino o sus códigos de recuperación están a la vista,
// cerrar la pestaña o recargar los perdería: el servidor los da una sola vez. El navegador pregunta
// antes de irse (revisión de #237). Las salidas dentro de la aplicación las cierra la hoja, que deja
// inerte todo lo demás.

export function useAvisoAlSalir(activo: boolean): void {
  useEffect(() => {
    if (!activo) return undefined;
    const avisar = (evento: BeforeUnloadEvent) => {
      evento.preventDefault();
    };
    window.addEventListener("beforeunload", avisar);
    return () => window.removeEventListener("beforeunload", avisar);
  }, [activo]);
}
