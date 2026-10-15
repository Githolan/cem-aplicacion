import { useCallback, useMemo, useRef, useState, type ReactNode } from "react";
import { useRegisterSW } from "virtual:pwa-register/react";

import { ContextoVersion } from "./version";

// Registra el service worker (una sola vez para toda la aplicación) y decide cuándo recargar.
//
// Documento 13, §14.4: la versión nueva se activa solo cuando la persona lo acepta. Cuando se
// activa, vite-plugin-pwa avisa en TODAS las pestañas que habían visto la versión en espera; por
// defecto todas recargan, y una pestaña que nunca apretó "Actualizar" perdería un formulario a
// medio completar. Por eso recarga solo la pestaña que la pidió; las demás siguen como están, con
// el aviso, y su botón solo recarga. La recarga conserva la URL completa: location.reload()
// vuelve a abrir la misma ruta, consulta y fragmento.
export function ProveedorVersion({ children }: { children: ReactNode }) {
  const pedidaAca = useRef(false);
  const [yaActiva, setYaActiva] = useState(false);
  const {
    needRefresh: [hayVersionNueva],
    updateServiceWorker,
  } = useRegisterSW({
    onNeedReload() {
      if (pedidaAca.current) window.location.reload();
      else setYaActiva(true);
    },
  });

  const actualizar = useCallback(() => {
    if (yaActiva) {
      window.location.reload();
      return;
    }
    // StrictMode y un doble clic: un solo pedido de activación.
    if (pedidaAca.current) return;
    pedidaAca.current = true;
    void updateServiceWorker(true);
  }, [yaActiva, updateServiceWorker]);

  const version = useMemo(() => ({ hayVersionNueva, actualizar }), [hayVersionNueva, actualizar]);
  return <ContextoVersion.Provider value={version}>{children}</ContextoVersion.Provider>;
}
