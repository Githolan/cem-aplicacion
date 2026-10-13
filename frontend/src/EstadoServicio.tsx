import { useQuery } from "@tanstack/react-query";

import { api, type Salud } from "./api/cliente";

async function consultarSalud(): Promise<Salud> {
  const { data, error } = await api.GET("/health");
  if (error !== undefined || data === undefined) throw new Error("sin respuesta");
  return data;
}

export function EstadoServicio() {
  const salud = useQuery({ queryKey: ["salud"], queryFn: consultarSalud });

  return (
    <section aria-labelledby="titulo-estado" className="tarjeta">
      <h2 id="titulo-estado">Estado del servicio</h2>
      <div role="status" aria-live="polite">
        {salud.isPending && <p>Consultando…</p>}
        {salud.isError && (
          <>
            <p className="aviso-error">No pudimos contactar al servicio. Probá de nuevo en unos minutos.</p>
            <button type="button" onClick={() => void salud.refetch()}>
              Reintentar
            </button>
          </>
        )}
        {salud.isSuccess && (
          <>
            <p className="aviso-ok">El servicio funciona.</p>
            <dl className="detalles">
              <dt>Versión</dt>
              <dd>{salud.data.version}</dd>
              <dt>Contrato</dt>
              <dd>{salud.data.version_contrato}</dd>
              <dt>Compilación</dt>
              <dd>
                <code>{salud.data.sha_imagen.slice(0, 7)}</code>
              </dd>
            </dl>
          </>
        )}
      </div>
    </section>
  );
}
