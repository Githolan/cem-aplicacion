import { useQuery } from "@tanstack/react-query";
import { useId } from "react";
import { createPortal } from "react-dom";
import { Link, Navigate } from "react-router";

import { api } from "../api/cliente";
import { RUTA_CONTACTOS } from "../Contactos";
import { RUTA_POR_ACLARAR } from "../egresos/PagosPorAclarar";
import { RUTA_ENTRAR } from "../enlace";
import { PuntoServicio, useFallaDelServicio } from "../EstadoServicio";
import { useSolicitudesDirigidas } from "../fiscalizacion/solicitudesDirigidas";
import { Icono } from "../Iconos";
import { EnlaceInstalar } from "../Instalar";
import { InvitacionesPropias, useMisInvitaciones } from "../InvitacionesPropias";
import { DE_ADMINISTRACION, rutaDelDestino } from "../Navegacion";
import { CONSULTA_AL_LADO, useAncho } from "../pagos/useAncho";
import { useConsultaDeAcciones, useMisConsorcios } from "../permisos";
import { useSesion } from "../sesion";
import {
  type EnlaceDeInicio,
  type Fuente,
  usePendientesContactos,
  usePendientesMandatos,
  usePendientesPadron,
  usePendientesPagos,
  usePendientesProveedores,
  usePendientesSolicitudes,
} from "./pendientes";

// El inicio es la única bandeja (decisión 9 del 18-11-2026): una fila por cada pendiente que le toca
// a quien mira, y por edificio, sin selector de edificio. Sin nada pendiente, una línea: «No hay nada
// pendiente.» (FL-INI-01). Lo que ya está en el menú no se repite (INI-01, INI-02). En el teléfono y
// la tablet las pantallas de Gestión no tienen pestaña: el inicio las lista debajo, sin descripción
// fija (INI-05, C8G-16).

export const NADA_PENDIENTE = "No hay nada pendiente.";

// El nombre del enlace es solo su título: el detalle lo acompaña como descripción.
function FilaDeInicio({ enlace }: { enlace: EnlaceDeInicio }) {
  const id = useId();
  return (
    <li>
      <Link
        to={enlace.ruta}
        state={enlace.estado}
        className="fila-inicio"
        aria-labelledby={`${id}-titulo`}
        aria-describedby={enlace.detalle === undefined ? undefined : `${id}-detalle`}
      >
        <span className="fila-inicio__icono">
          <Icono nombre={enlace.icono} />
        </span>
        <span className="fila-inicio__texto">
          <span id={`${id}-titulo`} className="fila-inicio__titulo">
            {enlace.texto}
          </span>
          {enlace.detalle !== undefined && (
            <span id={`${id}-detalle`} className="fila-inicio__detalle">
              {enlace.detalle}
            </span>
          )}
        </span>
        <Icono nombre="flecha" className="fila-inicio__flecha" />
      </Link>
    </li>
  );
}

async function hayPagosPorAclarar(): Promise<boolean> {
  const { data } = await api.GET("/api/v1/pagos-por-aclarar");
  if (data === undefined) throw new Error("no se pudo leer si hay pagos para aclarar");
  return data.elementos.length > 0;
}

/** Lo que quedó pendiente de quien dejó de administrar: no depende de una acción ni de un edificio. */
function usePendientesPorAclarar(activa: boolean): Fuente {
  const consulta = useQuery({ queryKey: ["hay-pagos-por-aclarar"], queryFn: hayPagosPorAclarar, enabled: activa, gcTime: 0, retry: false });
  return {
    filas:
      consulta.data === true
        ? [{ clave: RUTA_POR_ACLARAR, ruta: RUTA_POR_ACLARAR, texto: "Pagos para aclarar (de quien dejó de administrar)", icono: "billetera" }]
        : [],
    // Si no se pudo leer, no hay fila: es una consulta aparte, no un número de la bandeja.
    cargando: activa && consulta.isPending && !consulta.isError,
    falla: null,
    reintentar: () => undefined,
    reintentando: false,
  };
}

export function Inicio({ barra }: { barra: HTMLElement | null }) {
  const sesion = useSesion();
  const { porEdificio, falla, reintentar, reintentando } = useConsultaDeAcciones();
  const invitaciones = useMisInvitaciones(sesion === "con_sesion");
  const edificios = useMisConsorcios();
  const comunes = { porEdificio, edificios };
  // Cada fuente pone sus filas en «Para hacer», en este orden. Una fila nueva del inicio (por ejemplo,
  // activar la confirmación con código, decisión 10) entra como una fuente más en esta lista.
  const fuentes: Fuente[] = [
    usePendientesPagos(comunes),
    usePendientesProveedores(comunes),
    usePendientesSolicitudes(comunes),
    usePendientesPadron(comunes),
    usePendientesMandatos(comunes),
    usePendientesContactos(comunes),
    usePendientesPorAclarar(sesion === "con_sesion"),
  ];
  // FL-AVI-02: «Solicitudes formales» también para el destinatario que no ve el panel, como en el menú.
  const conSolicitudes = useSolicitudesDirigidas(porEdificio);
  const servicio = useFallaDelServicio(sesion === "con_sesion");
  // Desde 1025 px la barra lateral ya lista las pantallas: el inicio no las repite.
  const conBarra = useAncho(CONSULTA_AL_LADO);
  if (sesion === "sin_sesion") return <Navigate to={RUTA_ENTRAR} replace />;
  if (sesion === "consultando") return <p role="status">Cargando…</p>;

  const pendientes = fuentes.flatMap((f) => f.filas);
  const conFalla = fuentes.filter((f) => f.falla !== null);
  const sabido = porEdificio !== null && fuentes.every((f) => !f.cargando) && invitaciones.data !== undefined;
  const nadaPendiente = sabido && porEdificio.size > 0 && pendientes.length === 0 && conFalla.length === 0 && invitaciones.data.length === 0;

  // En el teléfono y la tablet, las pantallas de Gestión (sin pestaña) que no estén ya en «Para
  // hacer». Sin saber qué alcanza la persona el menú queda corto: los contactos guardados le sirven.
  const gestion: EnlaceDeInicio[] =
    porEdificio === null
      ? falla === null
        ? []
        : [{ clave: RUTA_CONTACTOS, ruta: RUTA_CONTACTOS, texto: "Contactos de emergencia", icono: "telefono" }]
      : conBarra
        ? []
        : DE_ADMINISTRACION.flatMap((d) => {
            const ruta = rutaDelDestino(d, porEdificio, conSolicitudes);
            return ruta === null || pendientes.some((p) => p.ruta === ruta) ? [] : [{ clave: ruta, ruta, texto: d.texto, icono: d.icono }];
          });

  // Un solo «Reintentar» para todo lo que no se pudo leer (AC-08): el servicio, las secciones y los
  // pendientes. Vuelve a pedir solo lo que falló.
  const reintentables = [...(servicio.falla === null ? [] : [servicio]), ...(falla === null ? [] : [{ reintentar, reintentando }]), ...conFalla];
  const reintentarTodo = () => reintentables.forEach((r) => r.reintentar());
  return (
    <>
      {barra !== null && createPortal(<PuntoServicio />, barra)}
      {/* La marca ya está en la cabecera o en la barra lateral (INI-04). */}
      <h1 className="solo-lector">Consorcio en mano</h1>
      {porEdificio?.size === 0 && (
        <p className="tarjeta">
          Esta cuenta todavía no participa de ningún edificio.
          {invitaciones.data?.length === 0 && " Para sumarse hay que abrir el enlace de la invitación que llegó por correo."}
        </p>
      )}
      {reintentables.length > 0 && (
        <div className="tarjeta">
          {servicio.falla !== null && (
            <p className="aviso-error" role="alert">
              {servicio.falla}
            </p>
          )}
          {falla !== null && (
            <p className="aviso-error" role="alert">
              Puede que falten secciones. {falla}
            </p>
          )}
          {conFalla[0] !== undefined && (
            <p className="aviso-error" role="alert">
              Puede que falten pendientes. {conFalla[0].falla}
            </p>
          )}
          <button type="button" className="secundario" disabled={reintentables.some((r) => r.reintentando)} onClick={reintentarTodo}>
            Reintentar
          </button>
        </div>
      )}
      <InvitacionesPropias consulta={invitaciones} />
      {nadaPendiente && <p className="inicio-nada">{NADA_PENDIENTE}</p>}
      <div className="grupos-inicio">
        {pendientes.length > 0 && (
          <section aria-label="Para hacer">
            <h2 className="titulo-seccion">Para hacer</h2>
            <ul className="filas-inicio">
              {pendientes.map((e) => (
                <FilaDeInicio key={e.clave} enlace={e} />
              ))}
            </ul>
          </section>
        )}
        {gestion.length > 0 && (
          <section aria-label="Gestión">
            {porEdificio !== null && <h2 className="titulo-seccion">Gestión</h2>}
            <ul className="filas-inicio">
              {gestion.map((e) => (
                <FilaDeInicio key={e.clave} enlace={e} />
              ))}
            </ul>
          </section>
        )}
      </div>
      <EnlaceInstalar />
    </>
  );
}
