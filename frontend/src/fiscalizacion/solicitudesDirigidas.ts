import { useEffect, useState } from "react";

import { api } from "../api/cliente";
import { cumple, REQUISITOS } from "../permisos";

// MEN-04 (informe de fricción del 17-11-2026, tanda 8): la pantalla de solicitudes formales la abre
// quien ve el panel (todas) y el destinatario (las suyas), pero el menú se la ofrecía solo a quien ve
// el panel. `solicitud_formal.responder` lo alcanza todo el que está en el edificio (`permisos.tsx`),
// así que no alcanza para ofrecerla: se ofrece a quien tiene al menos una solicitud dirigida a él.
// Sin el panel, la lista que devuelve la API es solo la de las suyas: con una, hay algo para ver.

/** Los edificios donde la persona abre la pantalla sin ver el panel: ahí la lista son las suyas. */
export function edificiosDeDestinatario(porEdificio: ReadonlyMap<string, readonly string[]> | null): string[] {
  if (porEdificio === null) return [];
  return [...porEdificio.entries()]
    .filter(([, alcanzadas]) => cumple(alcanzadas, REQUISITOS.solicitudes) && !cumple(alcanzadas, REQUISITOS.panel))
    .map(([id]) => id)
    .sort();
}

async function algunaDirigida(edificios: readonly string[]): Promise<boolean> {
  const hay = await Promise.all(
    edificios.map(async (consorcio) => {
      const { data } = await api.GET("/api/v1/consorcios/{consorcio_id}/solicitudes-formales", { params: { path: { consorcio_id: consorcio } } });
      // Un número que no se pudo saber no es «ninguna»: se ofrece, y la pantalla dice qué pasó.
      return data === undefined || data.elementos.length > 0;
    }),
  );
  return hay.some(Boolean);
}

/** Si el destinatario tiene alguna solicitud dirigida a él en algún edificio donde no ve el panel. */
export function useSolicitudesDirigidas(porEdificio: ReadonlyMap<string, readonly string[]> | null): boolean {
  const edificios = edificiosDeDestinatario(porEdificio);
  const clave = edificios.join(",");
  const [leido, setLeido] = useState<{ clave: string; hay: boolean } | null>(null);

  useEffect(() => {
    if (clave === "") return;
    let vigente = true;
    void algunaDirigida(clave.split(",")).then(
      (hay) => {
        if (vigente) setLeido({ clave, hay });
      },
      // Sin conexión tampoco se sabe: se ofrece, como con un número desconocido.
      () => {
        if (vigente) setLeido({ clave, hay: true });
      },
    );
    return () => {
      vigente = false;
    };
  }, [clave]);

  return clave !== "" && leido?.clave === clave && leido.hay;
}
