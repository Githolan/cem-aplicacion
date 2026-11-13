import { type QueryClient, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { type ReactNode, useCallback, useEffect, useRef, useState } from "react";
import { Navigate } from "react-router";

import { api } from "./api/cliente";
import { RUTA_INICIO } from "./enlace";
import { codigoDe, mensajeDeFalla, SIN_CONEXION } from "./fallas";

// Qué le mostramos a cada persona (regla de fricción, decisión del propietario del 05-11-2026): lo
// que no puede hacer no aparece. `GET /consorcios/{id}/mis-acciones` trae lo que alcanza en cada
// edificio. Es solo para mostrar: el servidor decide cada pedido igual, así que mientras no se sabe
// (cargando, sin conexión) se muestra y la pantalla responde lo que el servidor diga. `gcTime: 0`:
// al salir, la lista de una persona no queda para la siguiente que entre en este navegador.
// `networkMode: "always"`: sin red la consulta falla enseguida en vez de quedar en pausa, y la
// pantalla sigue (la copia de los contactos es justo para ese momento). `retryOnMount: false`: una
// pantalla que se monta no repite lo que acaba de fallar. `refetchOnReconnect: true`: con
// `networkMode: "always"` TanStack lo apaga, y lo que falló sin red no volvería al reconectar.

export const SIN_CACHE = {
  gcTime: 0,
  retry: false,
  retryOnMount: false,
  networkMode: "always",
  refetchOnReconnect: true,
} as const;

/** Una lectura que respondió mal: el mensaje ya dice de quién es la falla (E34). */
export class FallaDeLectura extends Error {}

/** Lo que hay que decir de una consulta fallida; sin respuesta del servidor, es la conexión. */
export function mensajeDeLectura(error: unknown): string {
  return error instanceof FallaDeLectura ? error.message : SIN_CONEXION;
}

async function misConsorcios() {
  const { data, error, response } = await api.GET("/api/v1/me/consorcios");
  if (data === undefined) throw new FallaDeLectura(mensajeDeFalla(response.status, codigoDe(error)));
  return data;
}

async function misAcciones(consorcio: string): Promise<readonly string[]> {
  const { data, error, response } = await api.GET("/api/v1/consorcios/{consorcio_id}/mis-acciones", {
    params: { path: { consorcio_id: consorcio } },
  });
  if (data === undefined) throw new FallaDeLectura(mensajeDeFalla(response.status, codigoDe(error)));
  return data.acciones;
}

const SIN_EDIFICIOS: Awaited<ReturnType<typeof misConsorcios>> = [];

/** Los edificios de la persona; vacío mientras no se sabe. */
export function useMisConsorcios() {
  const consulta = useQuery({ queryKey: ["mis-consorcios"], queryFn: misConsorcios, ...SIN_CACHE });
  return consulta.data ?? SIN_EDIFICIOS;
}

/** Lo que alcanza la persona cambió (aceptó una invitación): se vuelve a leer. */
export function releerAcciones(cliente: QueryClient) {
  void cliente.invalidateQueries({ queryKey: ["mis-consorcios"] });
  void cliente.invalidateQueries({ queryKey: ["mis-acciones"] });
}

/** Lo que alcanza en un edificio, o null mientras no se sabe. */
export function useAcciones(consorcio: string): readonly string[] | null {
  const consulta = useQuery({
    queryKey: ["mis-acciones", consorcio],
    queryFn: () => misAcciones(consorcio),
    enabled: consorcio !== "",
    ...SIN_CACHE,
  });
  return consulta.data ?? null;
}

/** `puede(accion)` en un edificio. Mientras no se sabe, sí: lo decide el servidor. */
export function usePuede(consorcio: string): (...acciones: string[]) => boolean {
  const alcanzadas = useAcciones(consorcio);
  return (...acciones) => alcanzadas === null || acciones.some((a) => alcanzadas.includes(a));
}

type AccionesPorEdificio = {
  porEdificio: ReadonlyMap<string, readonly string[]> | null;
  /** Falta alguna respuesta: si ya se sabe que no va a llegar (sin red, error), es false. */
  consultando: boolean;
  /** Qué decirle a la persona si no se pudo saber lo que alcanza; null si no hubo falla. */
  falla: string | null;
  /** Vuelve a pedir lo que falló. */
  reintentar: () => void;
  reintentando: boolean;
};

export function useConsultaDeAcciones(): AccionesPorEdificio {
  const consorcios = useQuery({ queryKey: ["mis-consorcios"], queryFn: misConsorcios, ...SIN_CACHE });
  const ids = (consorcios.data ?? []).map((c) => c.id);
  const acciones = useQueries({
    queries: ids.map((id) => ({
      queryKey: ["mis-acciones", id],
      queryFn: () => misAcciones(id),
      ...SIN_CACHE,
    })),
  });
  const reintentar = () => {
    void consorcios.refetch();
    for (const a of acciones) if (a.isError) void a.refetch();
  };
  const reintentando = consorcios.isFetching || acciones.some((a) => a.isFetching);
  if (consorcios.data === undefined || acciones.some((a) => a.data === undefined)) {
    const error = consorcios.error ?? acciones.find((a) => a.isError)?.error ?? null;
    return {
      porEdificio: null,
      consultando: error === null,
      falla: error === null ? null : mensajeDeLectura(error),
      reintentar,
      reintentando,
    };
  }
  return {
    porEdificio: new Map(ids.map((id, i) => [id, acciones[i]?.data ?? []])),
    consultando: false,
    falla: null,
    reintentar,
    reintentando,
  };
}

/** Por cada edificio de la persona, lo que alcanza; null mientras no se sepa de todos. */
export function useAccionesPorEdificio(): ReadonlyMap<string, readonly string[]> | null {
  return useConsultaDeAcciones().porEdificio;
}

/** Una pantalla se ofrece si alguna de sus condiciones se cumple; cada una, con todas sus acciones. */
export type Requisito = readonly (readonly string[])[];

export function cumple(alcanzadas: readonly string[], requisito: Requisito): boolean {
  return requisito.some((juntas) => juntas.every((a) => alcanzadas.includes(a)));
}

export function cumpleEnAlguno(porEdificio: ReadonlyMap<string, readonly string[]>, requisito: Requisito): boolean {
  return [...porEdificio.values()].some((alcanzadas) => cumple(alcanzadas, requisito));
}

/** Los edificios donde la pantalla sirve: el selector no ofrece otros. Mientras no se sabe, todos. */
export function useEdificiosCon<T extends { id: string }>(consorcios: readonly T[], requisito: Requisito): readonly T[] {
  const porEdificio = useAccionesPorEdificio();
  if (porEdificio === null) return consorcios;
  return consorcios.filter((c) => cumple(porEdificio.get(c.id) ?? [], requisito));
}

// El edificio elegido, entre pantallas. Solo en memoria, nunca en el dispositivo: se guarda por
// cliente de consultas, que hay uno por carga de la página, así que muere con ella.
const recordados = new WeakMap<QueryClient, string>();

/** Al salir de la cuenta: el edificio de una persona no queda para la siguiente. */
export function olvidarEdificio(cliente: QueryClient) {
  recordados.delete(cliente);
}

/**
 * El edificio de una pantalla: el que eligió la persona en ella, si no el `preferido` (el que trae
 * la navegación), si no el que eligió en otra pantalla y si no el primero, siempre entre los que la
 * pantalla sirve. Hasta tener la respuesta no hay edificio ni selector: la pantalla no le pide
 * datos a uno que no va a ofrecer, y un 403 de ese edificio no la manda al inicio. Si la respuesta
 * no llega (sin red), siguen todos.
 */
export function useEdificioElegido<T extends { id: string }>(
  consorcios: readonly T[],
  requisito: Requisito,
  preferido: string | null = null,
) {
  const cliente = useQueryClient();
  const { consultando } = useConsultaDeAcciones();
  const ofrecidos = useEdificiosCon(consorcios, requisito);
  const [elegido, setElegido] = useState("");
  const setConsorcio = useCallback(
    (id: string) => {
      recordados.set(cliente, id);
      setElegido(id);
    },
    [cliente],
  );
  // Una vez resuelto no se vuelve atrás: otra consulta que falle después (al volver a la ventana) no
  // le saca el edificio a la pantalla, que lo perdería todo.
  const resuelto = useRef(false);
  resuelto.current ||= !consultando;
  const recordado = recordados.get(cliente);
  const actual =
    ofrecidos.find((c) => c.id === elegido) ??
    ofrecidos.find((c) => c.id === preferido) ??
    ofrecidos.find((c) => c.id === recordado) ??
    ofrecidos[0];
  // El que trajo la navegación también queda: al volver de otra pantalla (dar de alta la verificación,
  // por ejemplo) la dirección llega sin él, y la persona no tiene que encontrarse en otro edificio.
  const llegoCon = preferido !== null && actual?.id === preferido;
  useEffect(() => {
    if (llegoCon) recordados.set(cliente, preferido);
  }, [cliente, llegoCon, preferido]);
  return {
    edificios: resuelto.current ? ofrecidos : [],
    consorcio: resuelto.current ? (actual?.id ?? "") : "",
    setConsorcio,
  };
}

/** Una dirección abierta a mano sin permiso lleva al inicio, sin pantalla de «sin permiso». */
export function Exige({ requisito, children }: { requisito: Requisito; children: ReactNode }) {
  const porEdificio = useAccionesPorEdificio();
  if (porEdificio !== null && !cumpleEnAlguno(porEdificio, requisito)) return <Navigate to={RUTA_INICIO} replace />;
  return <>{children}</>;
}

/**
 * El inicio es la única pantalla de quien no participa de ningún edificio: dice cómo sumarse. Se
 * vuelve a leer lo que alcanza, porque una falla anterior no se repite sola y taparía ese aviso.
 */
export function AlInicioSinEdificios() {
  const cliente = useQueryClient();
  useEffect(() => releerAcciones(cliente), [cliente]);
  return <Navigate to={RUTA_INICIO} replace />;
}

export const REQUISITOS = {
  invitaciones: [["invitaciones.crear"]],
  mandatos: [["mandatos.ver"], ["decisiones.ver"], ["decisiones.registrar"]],
  padron: [["padron.ver"]],
  contactos: [["contactos.ver"]],
  contactosGestion: [["contactos.gestionar"]],
  documentos: [["documentos.ver"], ["documentos.cargar"], ["documentos.cargar_operativos"]],
  proveedores: [["egresos.preparar_orden"]],
  ordenes: [["egresos.preparar_orden"]],
  /** Las lecturas del circuito (`_lector`): quien prepara, y si no, quien revisa. */
  pagos: [["egresos.preparar_orden"], ["egresos.revisar_orden"]],
  /** Las reglas de conciliar: la base separa pago por pago. */
  cotejo: [["egresos.conciliar_pago"]],
  /** Lo que el extracto no explica (pregunta 8, opción c): quien concilia y la administración, que
   * solo lee. Las mismas lecturas que la bandeja. */
  sinExplicar: [["egresos.preparar_orden"], ["egresos.revisar_orden"]],
  liquidacion: [["liquidacion.preparar"]],
  /** El panel completo del Consejo y la administración (RF48). */
  panel: [["fiscalizacion.ver_panel"]],
} as const satisfies Record<string, Requisito>;
