import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";

import { api } from "./api/cliente";
import type { components } from "./api/tipos";
import { borrarCopias } from "./copiaContactos";
import { encabezadoCsrf } from "./csrf";
import { codigoDe, mensajeDeFalla, SIN_CONEXION } from "./fallas";
import { TIPOS } from "./Invitacion";
import { FallaDeLectura, mensajeDeLectura, releerAcciones, SIN_CACHE } from "./permisos";
import { useIrAlInicio } from "./sesion";
import { useAEntrar } from "./sesionEncima";

// Las invitaciones dirigidas al correo de la persona, en el inicio (RF02): quien ya tiene cuenta
// acepta con su sesión, sin volver al correo, y es lo único que puede hacer un importado pendiente.
// Aceptar no pide más datos: la base usa el mismo camino que el enlace y el vínculo sigue las reglas
// normales. Sin invitaciones, la sección no aparece.

type Invitacion = components["schemas"]["InvitacionPropia"];

const ESTADO_SIN_SESION = 401;
const ESTADO_NO_SIRVE = 404;
const ESTADO_CONFLICTO = 409;

async function misInvitaciones(): Promise<readonly Invitacion[]> {
  const { data, error, response } = await api.GET("/api/v1/me/invitaciones");
  if (data === undefined) throw new FallaDeLectura(mensajeDeFalla(response.status, codigoDe(error)));
  return data.elementos;
}

export function useMisInvitaciones(activa: boolean) {
  return useQuery({ queryKey: ["mis-invitaciones"], queryFn: misInvitaciones, enabled: activa, ...SIN_CACHE });
}

export function InvitacionesPropias({ consulta }: { consulta: ReturnType<typeof useMisInvitaciones> }) {
  const id = useId();
  const cliente = useQueryClient();
  // Toda salida a /entrar borra las copias de los contactos (H2b_7 §5).
  const aEntrar = useAEntrar(borrarCopias);
  const irAlInicio = useIrAlInicio();
  const [aceptando, setAceptando] = useState<string | null>(null);
  const [falla, setFalla] = useState<string | null>(null);

  function aceptar(invitacion: Invitacion) {
    setAceptando(invitacion.id);
    setFalla(null);
    void api
      .POST("/api/v1/me/invitaciones/{invitacion_id}/aceptar", {
        params: { path: { invitacion_id: invitacion.id } },
        headers: encabezadoCsrf(),
      })
      .then(({ data, error, response }) => {
        if (data !== undefined) {
          releerAcciones(cliente);
          void consulta.refetch();
          irAlInicio(`Listo: la cuenta ya es parte de ${invitacion.consorcio}.`);
        } else if (response.status === ESTADO_SIN_SESION) {
          aEntrar();
        } else if ((response.status === ESTADO_NO_SIRVE || response.status === ESTADO_CONFLICTO) && error && "mensaje" in error) {
          // El servidor explica por qué esa invitación ya no se puede aceptar; la lista se actualiza.
          setFalla(error.mensaje);
          void consulta.refetch();
        } else setFalla(mensajeDeFalla(response.status, codigoDe(error)));
      })
      .catch(() => setFalla(SIN_CONEXION))
      .finally(() => setAceptando(null));
  }

  const elementos = consulta.data ?? [];
  const fallaDeLectura = consulta.isError && consulta.data === undefined;
  if (elementos.length === 0 && falla === null && !fallaDeLectura) return null;

  return (
    <section aria-labelledby={`${id}-titulo`} className="tarjeta">
      <h2 id={`${id}-titulo`}>Invitaciones pendientes</h2>
      {fallaDeLectura && (
        <>
          <p className="aviso-error" role="alert">
            {mensajeDeLectura(consulta.error)}
          </p>
          <button className="secundario" type="button" disabled={consulta.isFetching} onClick={() => void consulta.refetch()}>
            Reintentar
          </button>
        </>
      )}
      {elementos.length > 0 && (
        <ul className="sesiones">
          {elementos.map((invitacion) => (
            <li key={invitacion.id}>
              <h3 id={`${id}-${invitacion.id}`}>{invitacion.consorcio}</h3>
              <dl className="detalles">
                <dt>Unidad</dt>
                <dd>{invitacion.unidad}</dd>
                <dt>Como</dt>
                <dd>{TIPOS[invitacion.tipo]}</dd>
                <dt>Invitó</dt>
                <dd>{invitacion.invitada_por ?? "El equipo de Consorcio en Mano"}</dd>
              </dl>
              <button
                type="button"
                disabled={aceptando !== null}
                aria-describedby={`${id}-${invitacion.id}`}
                onClick={() => aceptar(invitacion)}
              >
                Aceptar la invitación
              </button>
            </li>
          ))}
        </ul>
      )}
      <div role="status" aria-live="polite">
        {aceptando !== null && <p>Aceptando…</p>}
        {falla !== null && <p className="aviso-error">{falla}</p>}
      </div>
    </section>
  );
}
