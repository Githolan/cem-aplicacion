import { createContext, useContext } from "react";

// Estado de la versión de la aplicación (documento 13, §14.4). Lo provee ProveedorVersion, que
// registra el service worker una sola vez; lo leen el aviso y la página de "no encontrada".
export interface Version {
  /** Hay un service worker nuevo instalado que espera para activarse. */
  hayVersionNueva: boolean;
  /** Activa la versión nueva y recarga esta pestaña en su misma URL (ruta, consulta y fragmento). */
  actualizar: () => void;
}

export const ContextoVersion = createContext<Version | null>(null);

export function useVersion(): Version {
  const version = useContext(ContextoVersion);
  if (version === null) throw new Error("useVersion se usa fuera de ProveedorVersion");
  return version;
}
