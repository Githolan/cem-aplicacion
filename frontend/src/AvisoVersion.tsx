import { useRegisterSW } from "virtual:pwa-register/react";

// Documento 13, §14.4: la versión nueva se activa solo cuando la persona lo acepta.
export function AvisoVersion() {
  const {
    needRefresh: [hayVersionNueva],
    updateServiceWorker,
  } = useRegisterSW();

  if (!hayVersionNueva) return null;
  return (
    <div role="alert" className="aviso-version">
      <span>Hay una versión nueva de la aplicación.</span>
      <button type="button" onClick={() => void updateServiceWorker(true)}>
        Actualizar
      </button>
    </div>
  );
}
