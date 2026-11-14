import { useQuery } from "@tanstack/react-query";

import { api } from "./api/cliente";
import { codigoDe, mensajeDeFalla } from "./fallas";
import { cumple, FallaDeLectura, mensajeDeLectura, REQUISITOS, SIN_CACHE } from "./permisos";

// Lo que el inicio dice del Padrón a quien confirma vínculos (G-26, sí del propietario del
// 14-11-2026): cuántos vínculos esperan confirmación, sumados en los edificios donde la persona
// los confirma. Cuenta las mismas filas que el Padrón muestra «Sin confirmar».

async function cuantosEsperan(edificios: readonly string[]): Promise<number> {
  const cantidades = await Promise.all(
    edificios.map(async (consorcio) => {
      const { data, error, response } = await api.GET("/api/v1/consorcios/{consorcio_id}/padron", {
        params: { path: { consorcio_id: consorcio } },
      });
      if (data === undefined) throw new FallaDeLectura(mensajeDeFalla(response.status, codigoDe(error)));
      return data.elementos.filter((f) => !f.confirmado).length;
    }),
  );
  return cantidades.reduce((a, b) => a + b, 0);
}

export const DETALLE_VINCULOS_SIN_LECTURA = "No se pudieron leer los vínculos sin confirmar";

export function textoVinculosPorConfirmar(cantidad: number): string {
  return cantidad === 1 ? "1 vínculo espera confirmación" : `${cantidad} vínculos esperan confirmación`;
}

/** Cuántos vínculos esperan confirmación donde la persona los confirma; null si no confirma en
 * ningún edificio o todavía no se sabe. */
export function useVinculosPorConfirmar(porEdificio: ReadonlyMap<string, readonly string[]> | null) {
  const edificios =
    porEdificio === null
      ? []
      : [...porEdificio.entries()].filter(([, alcanzadas]) => cumple(alcanzadas, REQUISITOS.confirmarVinculos)).map(([id]) => id).sort();
  const consulta = useQuery({
    queryKey: ["vinculos-por-confirmar", edificios],
    queryFn: () => cuantosEsperan(edificios),
    enabled: edificios.length > 0,
    ...SIN_CACHE,
  });
  return {
    cantidad: consulta.data ?? null,
    falla: consulta.isError ? mensajeDeLectura(consulta.error) : null,
    reintentar: () => void consulta.refetch(),
    reintentando: consulta.isFetching,
  };
}
