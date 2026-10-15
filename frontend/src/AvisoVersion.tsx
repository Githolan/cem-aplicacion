import { useVersion } from "./version";

// Documento 13, §14.4: la versión nueva se activa solo cuando la persona lo acepta.
export function AvisoVersion() {
  const { hayVersionNueva, actualizar } = useVersion();

  if (!hayVersionNueva) return null;
  return (
    <div role="alert" className="aviso-version">
      <span>Hay una versión nueva de la aplicación.</span>
      <button type="button" onClick={actualizar}>
        Actualizar
      </button>
    </div>
  );
}
