import { useQueries } from "@tanstack/react-query";

import { api } from "../api/cliente";
import { RUTA_CONTACTOS } from "../Contactos";
import { ESTADO, fechaLegible, importe, momento, RUTA_CONSULTA_PROVEEDORES, RUTA_PROVEEDORES } from "../egresos/comun";
import { codigoDe, mensajeDeFalla } from "../fallas";
import { RUTA_RECLAMOS, TITULO_RECLAMOS } from "../fiscalizacion/Reclamos";
import { RUTA_SOLICITUDES, TITULO_SOLICITUDES } from "../fiscalizacion/Solicitudes";
import type { NombreIcono } from "../Iconos";
import { RUTA_MANDATOS } from "../Mandatos";
import { RUTA_PADRON } from "../Padron";
import { RUTA_NUEVA, RUTA_PAGOS } from "../pagos/rutas";
import { cumple, FallaDeLectura, mensajeDeLectura, REQUISITOS, type Requisito, SIN_CACHE } from "../permisos";

// El inicio es la única bandeja (decisión 9 del 18-11-2026, FL-AVI-01 a FL-AVI-05): una fila por cada
// pendiente que le toca a quien mira, y por edificio, sin selector. Cada fuente lee lo que la API ya
// informa, un pedido por edificio donde la persona puede hacer algo con eso; no hay todavía un
// `GET /me/pendientes` que lo cuente de una vez. Cada fila abre la pantalla en el edificio de la
// fila (`state.consorcio`, FL-AVI-03).

export interface EnlaceDeInicio {
  /** Única en el inicio: la pantalla, el edificio y, si hace falta, el registro. */
  clave: string;
  ruta: string;
  texto: string;
  /** Solo lo pendiente («3 vínculos esperan confirmación»): nada fijo (INI-05, C8G-16). */
  detalle?: string;
  icono: NombreIcono;
  /** Con qué se abre la pantalla: el edificio de la fila y, si hace falta, el registro. */
  estado?: Readonly<Record<string, string>>;
}

/** Lo que aporta una fuente de pendientes al inicio. */
export interface Fuente {
  filas: EnlaceDeInicio[];
  /** Todavía falta alguna respuesta: no se puede decir «No hay nada pendiente.». */
  cargando: boolean;
  /** Qué decir si algún edificio no se pudo leer; null si no hubo falla. */
  falla: string | null;
  reintentar: () => void;
  reintentando: boolean;
}

export interface Edificio {
  id: string;
  nombre: string;
}

export const DETALLE_SIN_LECTURA = "No se pudieron leer los pendientes";

const plural = (n: number, uno: string, varios: string) => `${n} ${n === 1 ? uno : varios}`;

/**
 * Una fuente: un pedido por cada edificio donde la persona cumple `requisito`, y las filas que salen
 * de cada respuesta. Un edificio que no se pudo leer deja la fila de su pantalla con «No se pudieron
 * leer los pendientes»: un número desconocido no es «nada pendiente» (Codex en #245).
 */
function usePorEdificio<T>({
  clave,
  porEdificio,
  edificios,
  requisito,
  leer,
  filas,
  siFalla,
}: {
  clave: string;
  porEdificio: ReadonlyMap<string, readonly string[]> | null;
  edificios: readonly Edificio[];
  requisito: Requisito;
  leer: (consorcio: string, alcanzadas: readonly string[]) => Promise<T>;
  filas: (dato: T, edificio: Edificio, alcanzadas: readonly string[]) => EnlaceDeInicio[];
  siFalla: (edificio: Edificio) => EnlaceDeInicio;
}): Fuente {
  const alcance = porEdificio === null ? [] : edificios.filter((e) => cumple(porEdificio.get(e.id) ?? [], requisito));
  const consultas = useQueries({
    queries: alcance.map((e) => {
      const alcanzadas = porEdificio?.get(e.id) ?? [];
      return {
        queryKey: ["pendientes", clave, e.id, alcanzadas],
        queryFn: () => leer(e.id, alcanzadas),
        ...SIN_CACHE,
      };
    }),
  });
  // Un «sin permiso» es la respuesta del servidor, no una falla: ahí no hay nada que le toque.
  const fallo = (c: (typeof consultas)[number]) => c.isError && !(c.error instanceof SinPermisoEnElEdificio);
  const conFalla = consultas.find(fallo);
  return {
    filas: alcance.flatMap((e, i) => {
      const consulta = consultas[i];
      if (consulta === undefined || consulta.isError) return consulta !== undefined && fallo(consulta) ? [siFalla(e)] : [];
      return consulta.data === undefined ? [] : filas(consulta.data, e, porEdificio?.get(e.id) ?? []);
    }),
    cargando: porEdificio === null || consultas.some((c) => c.data === undefined && !c.isError),
    falla: conFalla === undefined ? null : mensajeDeLectura(conFalla.error),
    reintentar: () => {
      for (const c of consultas) if (fallo(c)) void c.refetch();
    },
    reintentando: consultas.some((c) => c.isFetching),
  };
}

/** Con más de un edificio, la fila dice de cuál es; con uno, no hace falta (C1). */
export function conEdificio(texto: string, edificio: Edificio, varios: boolean): string {
  return varios ? `${texto} · ${edificio.nombre}` : texto;
}

type Comunes = { porEdificio: ReadonlyMap<string, readonly string[]> | null; edificios: readonly Edificio[] };

function fila(base: Omit<EnlaceDeInicio, "clave" | "estado" | "texto">, texto: string, edificio: Edificio, varios: boolean, extra: Record<string, string> = {}): EnlaceDeInicio {
  return {
    ...base,
    clave: [base.ruta, edificio.id, ...Object.values(extra)].join(" "),
    texto: conEdificio(texto, edificio, varios),
    estado: { consorcio: edificio.id, ...extra },
  };
}

/** El servidor dice que esa lectura no le corresponde a la persona en ese edificio (403). */
class SinPermisoEnElEdificio extends Error {}

function exigir<T>(respuesta: { data?: T; error?: unknown; response: Response }): T {
  if (respuesta.response.status === ESTADO.sinPermiso) throw new SinPermisoEnElEdificio();
  if (respuesta.data === undefined) throw new FallaDeLectura(mensajeDeFalla(respuesta.response.status, codigoDe(respuesta.error)));
  return respuesta.data;
}

// --- Pagos: lo que el servidor marca pendiente y las órdenes rechazadas sin ver (FL-AVI-04) ---------

/** Lo que el servidor marca como pendiente (la tabla de la pantalla), los pagos por cotejar y, para
 * quien coteja, lo que el extracto no explica (pregunta 8). Las rechazadas que quien las preparó
 * todavía no vio van aparte: no son «cosas para hacer» de la bandeja, pero hay que enterarse. */
export function textoPagos(paraHacer: number, rechazadas: number): string | null {
  const partes = [
    ...(paraHacer > 0 ? [plural(paraHacer, "cosa para hacer", "cosas para hacer")] : []),
    ...(rechazadas > 0 ? [plural(rechazadas, "orden rechazada", "órdenes rechazadas")] : []),
  ];
  return partes.length === 0 ? null : partes.join(" · ");
}

export function usePendientesPagos({ porEdificio, edificios }: Comunes): Fuente {
  const varios = edificios.length > 1;
  const base = { ruta: RUTA_PAGOS, icono: "billetera" } as const;
  return usePorEdificio({
    clave: "pagos",
    porEdificio,
    edificios,
    requisito: REQUISITOS.pagos,
    leer: async (consorcio, alcanzadas) => {
      const bandeja = exigir(
        await api.GET("/api/v1/consorcios/{consorcio_id}/pagos/bandeja", { params: { path: { consorcio_id: consorcio } } }),
      );
      const coteja = cumple(alcanzadas, REQUISITOS.cotejo);
      return {
        paraHacer: bandeja.elementos.filter((e) => e.pendiente).length + bandeja.por_cotejar + (coteja ? bandeja.sin_explicar : 0),
        rechazadas: bandeja.avisos_rechazo.length,
      };
    },
    filas: ({ paraHacer, rechazadas }, edificio) => {
      const detalle = textoPagos(paraHacer, rechazadas);
      return detalle === null ? [] : [fila({ ...base, detalle }, "Pagos", edificio, varios)];
    },
    siFalla: (edificio) => fila({ ...base, detalle: DETALLE_SIN_LECTURA }, "Pagos", edificio, varios),
  });
}

// --- Padrón: los vínculos que esa cuenta puede confirmar (FL-PAD-01, G-26) ---------------------------

export function textoVinculosPorConfirmar(cantidad: number): string {
  return cantidad === 1 ? "1 vínculo espera confirmación" : `${cantidad} vínculos esperan confirmación`;
}

export function usePendientesPadron({ porEdificio, edificios }: Comunes): Fuente {
  const varios = edificios.length > 1;
  const base = { ruta: RUTA_PADRON, icono: "personas" } as const;
  return usePorEdificio({
    clave: "padron",
    porEdificio,
    edificios,
    requisito: REQUISITOS.confirmarVinculos,
    // Solo los que esa cuenta puede confirmar: nadie confirma su propio vínculo ni el que registró, y
    // esos no esperan a quien mira (FL-PAD-01).
    leer: async (consorcio) =>
      exigir(await api.GET("/api/v1/consorcios/{consorcio_id}/padron", { params: { path: { consorcio_id: consorcio } } })).elementos.filter(
        (f) => f.confirmable_por_vos,
      ).length,
    filas: (cantidad, edificio) =>
      cantidad === 0 ? [] : [fila({ ...base, detalle: textoVinculosPorConfirmar(cantidad) }, "Padrón del edificio", edificio, varios)],
    siFalla: (edificio) => fila({ ...base, detalle: DETALLE_SIN_LECTURA }, "Padrón del edificio", edificio, varios),
  });
}

// --- Solicitudes formales: lo que la lista ofrece hacer a quien mira (FL-AVI-01, FL-AVI-02) ----------

type Solicitud = { acciones: readonly string[]; estado: string; envio: { vence_el: string } | null };

/** Lo que le toca a quien mira, según las `acciones` que la API ofrece en cada solicitud; las
 * vencidas, para quien ve el panel (el Consejo). `null` si no hay nada. */
export function textoSolicitudes(solicitudes: readonly Solicitud[], vePanel: boolean): string | null {
  const con = (accion: string) => solicitudes.filter((s) => s.acciones.includes(accion));
  const porResponder = con("responder");
  const vence = porResponder
    .map((s) => s.envio?.vence_el)
    .filter((v): v is string => v !== undefined)
    .sort()[0];
  const firmar = con("firmar").length;
  const envio = con("registrar_envio").length;
  const respuesta = con("registrar_respuesta").length;
  const vencidas = vePanel ? solicitudes.filter((s) => s.estado === "vencida").length : 0;
  const partes = [
    ...(porResponder.length > 0
      ? [`${plural(porResponder.length, "espera respuesta", "esperan respuesta")}${vence === undefined ? "" : `, vence el ${fechaLegible(vence)}`}`]
      : []),
    ...(firmar > 0 ? [plural(firmar, "espera la firma", "esperan la firma")] : []),
    ...(envio > 0 ? [plural(envio, "con el envío sin registrar", "con el envío sin registrar")] : []),
    ...(respuesta > 0 ? [plural(respuesta, "con la respuesta por registrar", "con la respuesta por registrar")] : []),
    ...(vencidas > 0 ? [plural(vencidas, "vencida", "vencidas")] : []),
  ];
  return partes.length === 0 ? null : partes.join(" · ");
}

export function usePendientesSolicitudes({ porEdificio, edificios }: Comunes): Fuente {
  const varios = edificios.length > 1;
  const base = { ruta: RUTA_SOLICITUDES, icono: "sobre" } as const;
  return usePorEdificio({
    clave: "solicitudes",
    porEdificio,
    edificios,
    requisito: REQUISITOS.solicitudes,
    leer: async (consorcio) =>
      exigir(await api.GET("/api/v1/consorcios/{consorcio_id}/solicitudes-formales", { params: { path: { consorcio_id: consorcio } } }))
        .elementos,
    filas: (solicitudes, edificio, alcanzadas) => {
      const detalle = textoSolicitudes(solicitudes, cumple(alcanzadas, REQUISITOS.panel));
      return detalle === null ? [] : [fila({ ...base, detalle }, TITULO_SOLICITUDES, edificio, varios)];
    },
    siFalla: (edificio) => fila({ ...base, detalle: DETALLE_SIN_LECTURA }, TITULO_SOLICITUDES, edificio, varios),
  });
}

// --- Reclamos sobre la liquidación que esperan respuesta (RF34, app 0123), para quien los atiende

export function textoReclamos(presentados: number): string | null {
  return presentados === 0 ? null : plural(presentados, "reclamo espera respuesta", "reclamos esperan respuesta");
}

export function usePendientesReclamos({ porEdificio, edificios }: Comunes): Fuente {
  const varios = edificios.length > 1;
  const base = { ruta: RUTA_RECLAMOS, icono: "globo" } as const;
  return usePorEdificio({
    clave: "reclamos",
    porEdificio,
    edificios,
    requisito: REQUISITOS.reclamos,
    leer: async (consorcio) =>
      exigir(await api.GET("/api/v1/consorcios/{consorcio_id}/reclamos-liquidacion", { params: { path: { consorcio_id: consorcio } } })).elementos,
    filas: (reclamos, edificio) => {
      const detalle = textoReclamos(reclamos.filter((r) => r.estado === "presentado").length);
      return detalle === null ? [] : [fila({ ...base, detalle }, TITULO_RECLAMOS, edificio, varios)];
    },
    siFalla: (edificio) => fila({ ...base, detalle: DETALLE_SIN_LECTURA }, TITULO_RECLAMOS, edificio, varios),
  });
}

// --- Proveedores de quien prepara: el salteo que espera su orden y la documentación (FL-SAL-01, FL-AVI-05)

export function textoDocumentacion(cantidad: number): string {
  return `${plural(cantidad, "proveedor", "proveedores")} con documentación vencida o faltante`;
}

/** FL-SAL-01 (tanda 9): hasta cuándo sirve el salteo que espera su orden (`vence_salteo`, app 0110). */
export const textoSalteoHasta = (vence: string) => `salteo por urgencia hasta el ${momento(vence)}`;
/** El paso que sigue para quien prepara: con el salteo (pedido y atendido, o no), la orden. */
export const PREPARAR_LA_ORDEN = "Preparar la orden";

export function usePendientesProveedores({ porEdificio, edificios }: Comunes): Fuente {
  const varios = edificios.length > 1;
  const base = { ruta: RUTA_PROVEEDORES, icono: "proveedor" } as const;
  return usePorEdificio({
    clave: "proveedores",
    porEdificio,
    edificios,
    requisito: REQUISITOS.ordenes,
    leer: async (consorcio) =>
      exigir(await api.GET("/api/v1/consorcios/{consorcio_id}/proveedores", { params: { path: { consorcio_id: consorcio } } })),
    filas: (lista, edificio) => {
      const activos = lista.elementos.filter((p) => p.estado === "activo");
      // El salteo vigente que sirve para una orden de quien mira (también el que atendió su pedido al
      // Consejo): la fila abre «Preparar la orden» con ese proveedor y dice hasta cuándo sirve.
      const vence = new Map(lista.vence_salteo.map((v) => [v.proveedor_id, v.vence_en]));
      const salteos = activos
        .filter((p) => lista.con_salteo.includes(p.id))
        .map((p) => {
          const hasta = vence.get(p.id);
          return fila(
            { ruta: RUTA_NUEVA, icono: "billetera", detalle: hasta === undefined ? p.razon_social : `${p.razon_social}: ${textoSalteoHasta(hasta)}` },
            PREPARAR_LA_ORDEN,
            edificio,
            varios,
            { proveedor: p.id },
          );
        });
      const conFaltas = activos.filter((p) => lista.documentacion_vencida.includes(p.id) || lista.documentacion_faltante.includes(p.id)).length;
      return [...salteos, ...(conFaltas === 0 ? [] : [fila({ ...base, detalle: textoDocumentacion(conFaltas) }, "Proveedores", edificio, varios)])];
    },
    siFalla: (edificio) => fila({ ...base, detalle: DETALLE_SIN_LECTURA }, "Proveedores", edificio, varios),
  });
}

// --- Pedidos de salteo, para el Consejo que saltea (pregunta 3 del propietario, tanda 9) ----------

const SALTEA: Requisito = [["egresos.saltear_documentacion"]];
export const TITULO_PEDIDO_SALTEO = "Pedido de salteo";

export function usePendientesPedidosSalteo({ porEdificio, edificios }: Comunes): Fuente {
  const varios = edificios.length > 1;
  const base = { ruta: RUTA_CONSULTA_PROVEEDORES, icono: "proveedor" } as const;
  return usePorEdificio({
    clave: "pedidos-salteo",
    porEdificio,
    edificios,
    requisito: SALTEA,
    leer: async (consorcio) =>
      exigir(await api.GET("/api/v1/consorcios/{consorcio_id}/pedidos-salteo", { params: { path: { consorcio_id: consorcio } } })).elementos.filter(
        // Los que esperan y no pidió quien mira: nadie saltea su propio pedido.
        (p) => p.estado === "pendiente" && !p.propio,
      ),
    // Cada pedido abre la ficha de su proveedor, donde se registra el salteo: la de gestión si quien mira
    // gestiona proveedores; si no, la de consulta.
    filas: (pedidos, edificio, alcanzadas) =>
      pedidos.map((p) =>
        fila(
          {
            ...base,
            ruta: cumple(alcanzadas, REQUISITOS.proveedores) ? RUTA_PROVEEDORES : RUTA_CONSULTA_PROVEEDORES,
            detalle: `${p.razon_social}: ${importe(p.importe_centavos, p.moneda)} · ${p.pedido_por_nombre}`,
          },
          TITULO_PEDIDO_SALTEO,
          edificio,
          varios,
          { abrir: p.proveedor_id, pedido: String(p.numero) },
        ),
      ),
    siFalla: (edificio) => fila({ ...base, detalle: DETALLE_SIN_LECTURA }, TITULO_PEDIDO_SALTEO, edificio, varios),
  });
}

// --- Mandatos en revisión, para quien puede resolverlos (FL-AVI-05) ---------------------------------

const RESUELVE_REVISION: Requisito = [["mandatos.ver", "mandatos.resolver_revision"]];
/** FL-MAN-04 (tanda 9): quien aprueba la invitación de la administración, que la ve en la lista. */
const APRUEBA_INVITACION: Requisito = [["mandatos.ver", "mandatos.ver_asociaciones", "mandatos.aprobar_asociacion"]];

/** Lo que espera a quien mira en Mandatos: los mandatos en revisión y las invitaciones por aprobar. */
export function textoMandatos(enRevision: number, invitaciones: number): string | null {
  const partes = [
    ...(enRevision > 0 ? [plural(enRevision, "mandato en revisión", "mandatos en revisión")] : []),
    ...(invitaciones > 0 ? [plural(invitaciones, "invitación espera aprobación", "invitaciones esperan aprobación")] : []),
  ];
  return partes.length === 0 ? null : partes.join(" · ");
}

export function usePendientesMandatos({ porEdificio, edificios }: Comunes): Fuente {
  const varios = edificios.length > 1;
  const base = { ruta: RUTA_MANDATOS, icono: "balanza" } as const;
  return usePorEdificio({
    clave: "mandatos",
    porEdificio,
    edificios,
    requisito: [...RESUELVE_REVISION, ...APRUEBA_INVITACION],
    leer: async (consorcio) =>
      exigir(await api.GET("/api/v1/consorcios/{consorcio_id}/mandatos", { params: { path: { consorcio_id: consorcio } } })).elementos,
    filas: (mandatos, edificio, alcanzadas) => {
      const enRevision = cumple(alcanzadas, RESUELVE_REVISION) ? mandatos.filter((m) => m.estado === "pendiente_revision").length : 0;
      // Cuatro ojos: la que emitió quien mira la aprueba otra persona.
      const invitaciones = cumple(alcanzadas, APRUEBA_INVITACION)
        ? mandatos.filter((m) => m.invitacion === "por_aprobar" && !m.invitacion_emitida_por_mi).length
        : 0;
      const detalle = textoMandatos(enRevision, invitaciones);
      return detalle === null ? [] : [fila({ ...base, detalle }, "Mandatos y decisiones", edificio, varios)];
    },
    siFalla: (edificio) => fila({ ...base, detalle: DETALLE_SIN_LECTURA }, "Mandatos y decisiones", edificio, varios),
  });
}

// --- Contactos con un dato señalado, para quien los gestiona (FL-AVI-05) ---------------------------

export function usePendientesContactos({ porEdificio, edificios }: Comunes): Fuente {
  const varios = edificios.length > 1;
  const base = { ruta: RUTA_CONTACTOS, icono: "telefono" } as const;
  return usePorEdificio({
    clave: "contactos",
    porEdificio,
    edificios,
    requisito: REQUISITOS.contactosGestion,
    leer: async (consorcio) =>
      exigir(await api.GET("/api/v1/consorcios/{consorcio_id}/contactos/gestion", { params: { path: { consorcio_id: consorcio } } }))
        .elementos.filter((c) => c.observado).length,
    filas: (cantidad, edificio) =>
      cantidad === 0
        ? []
        : [
            fila(
              { ...base, detalle: plural(cantidad, "contacto con un dato señalado como incorrecto", "contactos con un dato señalado como incorrecto") },
              "Contactos de emergencia",
              edificio,
              varios,
            ),
          ],
    siFalla: (edificio) => fila({ ...base, detalle: DETALLE_SIN_LECTURA }, "Contactos de emergencia", edificio, varios),
  });
}
