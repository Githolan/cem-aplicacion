import { useQuery } from "@tanstack/react-query";
import { obtenerSalud, useClienteApi } from "../api/cliente";
import { INTERVALO_CONSULTA_SALUD_MS } from "../config";

/** Muestra si el backend responde, leyendo /health. */
export function EstadoServicio() {
  const cliente = useClienteApi();
  const consulta = useQuery({
    queryKey: ["salud"],
    queryFn: () => obtenerSalud(cliente),
    refetchInterval: INTERVALO_CONSULTA_SALUD_MS,
  });

  if (consulta.isPending) {
    return (
      <p className="estado estado--consultando" role="status">
        Consultando el servicio…
      </p>
    );
  }
  if (consulta.isError) {
    return (
      <p className="estado estado--caido" role="status">
        El servicio no responde. {consulta.error.message}
      </p>
    );
  }
  const salud = consulta.data;
  return (
    <p className="estado estado--ok" role="status">
      Servicio en línea · versión {salud.version} · contrato {salud.version_contrato} · imagen{" "}
      <code>{salud.sha_imagen.slice(0, 7)}</code>
    </p>
  );
}
