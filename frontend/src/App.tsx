import { useQuery } from "@tanstack/react-query";
import { useEffect, useId, useState } from "react";
import { createPortal } from "react-dom";
import { Link, Navigate, Route, Routes } from "react-router";

import { Contactos, RUTA_CONTACTOS } from "./Contactos";
import { Cuenta, RUTA_CUENTA } from "./Cuenta";
import { Documentos, RUTA_DOCUMENTOS } from "./Documentos";
import { AvisoLlegada } from "./AvisoLlegada";
import { VersionAlCambiarDePantalla } from "./AvisoVersion";
import { RUTA_ENTRAR, RUTA_INICIO, RUTA_INSTALAR, RUTA_INVITACION, RUTA_INVITACION_MANDATO } from "./enlace";
import { api } from "./api/cliente";
import { Exige, ofreceEnAlguno, REQUISITOS, useConsultaDeAcciones } from "./permisos";
import { useSesion } from "./sesion";
import { Entrar } from "./Entrar";
import { EntrarEncima } from "./EntrarEncima";
import { PuntoServicio, useFallaDelServicio } from "./EstadoServicio";
import { ElegirConsorcio } from "./egresos/ElegirConsorcio";
import { Liquidacion, RUTA_LIQUIDACION } from "./egresos/Liquidacion";
import { PagosPorAclarar, RUTA_POR_ACLARAR } from "./egresos/PagosPorAclarar";
import { AutorizacionesProveedores, RUTA_AUTORIZACIONES_PROVEEDORES } from "./egresos/AutorizacionesProveedores";
import { ConsultaProveedores, RUTA_CONSULTA_PROVEEDORES } from "./egresos/ConsultaProveedores";
import { Proveedores, RUTA_PROVEEDORES } from "./egresos/Proveedores";
import { Icono, type NombreIcono } from "./Iconos";
import { EnlaceInstalar, Instalar } from "./Instalar";
import { Invitacion } from "./Invitacion";
import { InvitacionesPropias, useMisInvitaciones } from "./InvitacionesPropias";
import { InvitacionMandato } from "./InvitacionMandato";
import { Invitar, RUTA_INVITACIONES } from "./Invitar";
import { Mandatos, RUTA_MANDATOS } from "./Mandatos";
import { ModoLectura } from "./ModoLectura";
import { DE_ADMINISTRACION, Navegacion, PersonaEnCabecera, useNavegacion } from "./Navegacion";
import { CircuitoPagos } from "./pagos/CircuitoPagos";
import { ConsultaPagos } from "./pagos/ConsultaPagos";
import { Cotejo } from "./pagos/Cotejo";
import { PrepararOrden } from "./pagos/PrepararOrden";
import { RUTA_CONSULTA, RUTA_COTEJO, RUTA_NUEVA, RUTA_PAGOS, RUTA_SIN_EXPLICAR } from "./pagos/rutas";
import { SinExplicar } from "./pagos/SinExplicar";
import { Padron, RUTA_PADRON } from "./Padron";
import { Panel, RUTA_PANEL } from "./panel/Panel";
import { RUTA_SOLICITUDES, Solicitudes } from "./fiscalizacion/Solicitudes";
import { TarjetaPagos, useResumenPagos } from "./ResumenPagos";
import { CONSULTA_AL_LADO, useAncho } from "./pagos/useAncho";
import { RUTA_SEGUNDO_FACTOR, SegundoFactor } from "./SegundoFactor";
import { useVersion } from "./version";
import { DETALLE_VINCULOS_SIN_LECTURA, textoVinculosPorConfirmar, useVinculosPorConfirmar } from "./VinculosPorConfirmar";

interface EnlaceDeInicio {
  ruta: string;
  texto: string;
  /** Solo lo pendiente («3 vínculos esperan confirmación»): nada fijo (INI-05, C8G-16). */
  detalle?: string;
  icono: NombreIcono;
  /** El edificio con el que se abre la pantalla (`state.consorcio`). */
  consorcio?: string;
}

// El inicio muestra solo lo pendiente, para todos los roles (decisión 5 del informe de fricción del
// 17-11-2026, que extiende D-39): Pagos con lo que hay para hacer, los vínculos que esperan
// confirmación y los pagos para aclarar. Lo que ya está en el menú no se repite (INI-01, INI-02). En
// el teléfono y la tablet las pantallas de Gestión no tienen pestaña: el inicio las lista, sin
// descripción fija (INI-05, C8G-16).

// El nombre del enlace es solo su título: el detalle lo acompaña como descripción.
function FilaDeInicio({ enlace }: { enlace: EnlaceDeInicio }) {
  const id = useId();
  return (
    <li>
      <Link
        to={enlace.ruta}
        state={enlace.consorcio === undefined ? undefined : { consorcio: enlace.consorcio }}
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

/** Las pantallas que reemplazó otra (el circuito de pagos; los contactos en una sola, G-52 y G-53):
 * un enlace viejo o un marcador siguen llegando. */
const RUTAS_VIEJAS: readonly (readonly [string, string])[] = [
  ["/egresos/ordenes", RUTA_PAGOS],
  ["/egresos/revision", RUTA_PAGOS],
  ["/egresos/pagos", RUTA_PAGOS],
  ["/egresos/conciliacion", RUTA_PAGOS],
  ["/egresos/por-aclarar", RUTA_POR_ACLARAR],
  ["/contactos/gestion", RUTA_CONTACTOS],
];

async function hayPagosPorAclarar(): Promise<boolean> {
  const { data } = await api.GET("/api/v1/pagos-por-aclarar");
  if (data === undefined) throw new Error("no se pudo leer si hay pagos para aclarar");
  return data.elementos.length > 0;
}

function Inicio({ barra }: { barra: HTMLElement | null }) {
  const sesion = useSesion();
  const { porEdificio, falla, reintentar, reintentando } = useConsultaDeAcciones();
  const invitaciones = useMisInvitaciones(sesion === "con_sesion");
  // No depende de una acción: es lo que quedó pendiente de quien dejó de administrar.
  const porAclarar = useQuery({
    queryKey: ["hay-pagos-por-aclarar"],
    queryFn: hayPagosPorAclarar,
    enabled: sesion === "con_sesion",
    gcTime: 0,
    retry: false,
  });
  const pagos = useResumenPagos(porEdificio);
  const vinculos = useVinculosPorConfirmar(porEdificio);
  const servicio = useFallaDelServicio(sesion === "con_sesion");
  // Desde 1025 px la barra lateral ya lista las pantallas: el inicio no las repite.
  const conBarra = useAncho(CONSULTA_AL_LADO);
  if (sesion === "sin_sesion") return <Navigate to={RUTA_ENTRAR} replace />;
  if (sesion === "consultando") return <p role="status">Cargando…</p>;
  const edificio = pagos.edificios.find((c) => c.id === pagos.consorcio);

  // Pagos: la tarjeta de quien revisa (con algo para hacer, o mientras se sabe), o la fila de quien
  // prepara cuando hay algo. Con más de un edificio, el selector va junto a Pagos, que es lo único
  // que cambia (INI-07), y Pagos queda aunque ese edificio no tenga nada: es la forma de ver otro.
  const varios = pagos.edificios.length > 1;
  const tarjetaPagos = pagos.enTarjeta && edificio !== undefined;
  const filaPagos =
    !pagos.enTarjeta && edificio !== undefined && (pagos.falla !== null || (pagos.cantidad ?? 0) > 0 || varios);
  // El Padrón entra si hay vínculos esperando (G-26) o si no se pudo saber: un número desconocido no
  // es «nada pendiente» (Codex en #245).
  const padron =
    porEdificio !== null &&
    ofreceEnAlguno(porEdificio, REQUISITOS.padron) &&
    (vinculos.falla !== null || (vinculos.cantidad ?? 0) > 0);
  const pendientes: EnlaceDeInicio[] = [
    ...(filaPagos
      ? [{ ruta: RUTA_PAGOS, texto: "Pagos", icono: "billetera" as const, detalle: pagos.detalle, consorcio: pagos.consorcio }]
      : []),
    ...(padron
      ? [
          {
            ruta: RUTA_PADRON,
            texto: "Padrón del edificio",
            icono: "personas" as const,
            detalle: vinculos.falla !== null ? DETALLE_VINCULOS_SIN_LECTURA : textoVinculosPorConfirmar(vinculos.cantidad ?? 0),
          },
        ]
      : []),
    ...(porAclarar.data === true
      ? [{ ruta: RUTA_POR_ACLARAR, texto: "Pagos para aclarar (de quien dejó de administrar)", icono: "billetera" as const }]
      : []),
  ];
  const hayParaHacer = tarjetaPagos || pendientes.length > 0;

  // En el teléfono y la tablet, las pantallas de Gestión (sin pestaña) que no estén ya en «Para
  // hacer». Sin saber qué alcanza la persona el menú queda corto: los contactos guardados le sirven.
  const gestion: EnlaceDeInicio[] =
    porEdificio === null
      ? falla === null
        ? []
        : [{ ruta: RUTA_CONTACTOS, texto: "Contactos de emergencia", icono: "telefono" }]
      : conBarra
        ? []
        : DE_ADMINISTRACION.filter(
            (d) =>
              d.requisito !== undefined &&
              ofreceEnAlguno(porEdificio, d.requisito, d.salvo) &&
              !pendientes.some((p) => p.ruta === d.ruta),
          ).map((d) => ({ ruta: d.ruta, texto: d.texto, icono: d.icono }));

  // Un solo «Reintentar» para todo lo que no se pudo leer (AC-08): el servicio, las secciones y la
  // tarjeta de Pagos. Vuelve a pedir solo lo que falló.
  const fallaDePagos = pagos.enTarjeta && pagos.falla !== null;
  const reintentables = [
    ...(servicio.falla === null ? [] : [servicio]),
    ...(falla === null ? [] : [{ reintentar, reintentando }]),
    ...(fallaDePagos ? [pagos] : []),
    ...(vinculos.falla === null ? [] : [vinculos]),
  ];
  const reintentarTodo = () => reintentables.forEach((r) => r.reintentar());
  const botonReintentar = (
    <button type="button" className="secundario" disabled={reintentables.some((r) => r.reintentando)} onClick={reintentarTodo}>
      Reintentar
    </button>
  );
  const enLaTarjeta = servicio.falla === null && falla === null && vinculos.falla === null;
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
      {!enLaTarjeta && (
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
          {vinculos.falla !== null && (
            <p className="aviso-error" role="alert">
              {DETALLE_VINCULOS_SIN_LECTURA}. {vinculos.falla}
            </p>
          )}
          {botonReintentar}
        </div>
      )}
      <InvitacionesPropias consulta={invitaciones} />
      <div className="grupos-inicio">
        {hayParaHacer && (
          <section aria-label="Para hacer">
            <h2 className="titulo-seccion">Para hacer</h2>
            {(tarjetaPagos || filaPagos) && (
              <ElegirConsorcio
                pantalla={{ tipo: "lista", consorcios: pagos.edificios }}
                consorcio={pagos.consorcio}
                elegir={pagos.setConsorcio}
                deshabilitado={false}
              />
            )}
            {tarjetaPagos && (
              <TarjetaPagos
                cantidad={pagos.cantidad}
                falla={pagos.falla}
                reintentar={fallaDePagos && enLaTarjeta ? botonReintentar : null}
                edificio={edificio.nombre}
              />
            )}
            {pendientes.length > 0 && (
              <ul className="filas-inicio">
                {pendientes.map((e) => (
                  <FilaDeInicio key={e.ruta} enlace={e} />
                ))}
              </ul>
            )}
          </section>
        )}
        {gestion.length > 0 && (
          <section aria-label="Gestión">
            {porEdificio !== null && <h2 className="titulo-seccion">Gestión</h2>}
            <ul className="filas-inicio">
              {gestion.map((e) => (
                <FilaDeInicio key={e.ruta} enlace={e} />
              ))}
            </ul>
          </section>
        )}
      </div>
      <EnlaceInstalar />
    </>
  );
}

// Una ruta que esta versión no conoce puede ser de una versión más nueva (un enlace del correo,
// abierto con la aplicación vieja en caché). Si hay una versión nueva esperando, se aplica sola en
// la misma URL completa, en vez de dejar a la persona en "No encontramos". En las rutas conocidas
// la versión nueva espera el botón del aviso o el próximo cambio de pantalla (MEN-07).
function NoEncontrada() {
  const { hayVersionNueva, actualizar } = useVersion();
  useEffect(() => {
    if (hayVersionNueva) actualizar();
  }, [hayVersionNueva, actualizar]);

  if (hayVersionNueva) {
    return (
      <>
        <h1>Actualizando la aplicación</h1>
        <p role="status" className="tarjeta">
          Cargando la versión nueva…
        </p>
      </>
    );
  }
  return (
    <>
      <h1>No se encontró esa página</h1>
      <p>
        <Link to="/" className="enlace-suelto">Volver al inicio</Link>
      </p>
    </>
  );
}

export function App() {
  const [barra, setBarra] = useState<HTMLElement | null>(null);
  const { hay: hayNavegacion, nombre } = useNavegacion();
  return (
    <div className={hayNavegacion ? "aplicacion aplicacion--con-navegacion" : "aplicacion"}>
      {hayNavegacion && <Navegacion />}
      <div className="aplicacion__columna">
      <header className="cabecera">
        <Link to={RUTA_INICIO} className="marca">
          <img src="/icono.svg" alt="" width="40" height="40" />
          <span>Consorcio en Mano</span>
        </Link>
        <div className="cabecera-fin">
          <div ref={setBarra} className="cabecera-punto" />
          <ModoLectura />
          {nombre && <PersonaEnCabecera nombre={nombre} />}
        </div>
      </header>
      <main id="contenido">
        <AvisoLlegada />
        <VersionAlCambiarDePantalla />
        <EntrarEncima />
        <Routes>
          <Route path="/" element={<Inicio barra={barra} />} />
          <Route path={RUTA_INSTALAR} element={<Instalar />} />
          <Route path={RUTA_ENTRAR} element={<Entrar />} />
          <Route path={RUTA_INVITACION} element={<Invitacion />} />
          <Route path={RUTA_INVITACION_MANDATO} element={<InvitacionMandato />} />
          <Route path={RUTA_CUENTA} element={<Cuenta />} />
          <Route path={RUTA_INVITACIONES} element={<Exige requisito={REQUISITOS.invitaciones}><Invitar /></Exige>} />
          <Route path={RUTA_MANDATOS} element={<Exige requisito={REQUISITOS.mandatos}><Mandatos /></Exige>} />
          <Route path={RUTA_PADRON} element={<Exige requisito={REQUISITOS.padron}><Padron /></Exige>} />
          <Route path={RUTA_CONTACTOS} element={<Exige requisito={REQUISITOS.contactos}><Contactos /></Exige>} />
          <Route path={RUTA_DOCUMENTOS} element={<Exige requisito={REQUISITOS.documentos}><Documentos /></Exige>} />
          <Route path={RUTA_SEGUNDO_FACTOR} element={<SegundoFactor />} />
          <Route path={RUTA_PROVEEDORES} element={<Exige requisito={REQUISITOS.proveedores}><Proveedores /></Exige>} />
          <Route
            path={RUTA_CONSULTA_PROVEEDORES}
            element={<Exige requisito={REQUISITOS.consultaProveedores}><ConsultaProveedores /></Exige>}
          />
          <Route
            path={RUTA_AUTORIZACIONES_PROVEEDORES}
            element={<Exige requisito={REQUISITOS.autorizarProveedores}><AutorizacionesProveedores /></Exige>}
          />
          <Route path={RUTA_NUEVA} element={<Exige requisito={REQUISITOS.ordenes}><PrepararOrden /></Exige>} />
          <Route path={RUTA_CONSULTA} element={<Exige requisito={REQUISITOS.pagos}><ConsultaPagos /></Exige>} />
          <Route path={RUTA_COTEJO} element={<Exige requisito={REQUISITOS.cotejo}><Cotejo /></Exige>} />
          <Route path={RUTA_SIN_EXPLICAR} element={<Exige requisito={REQUISITOS.sinExplicar}><SinExplicar /></Exige>} />
          {/* Una sola ruta madre: la bandeja sigue montada al abrir la ficha al lado. */}
          <Route path={RUTA_PAGOS} element={<Exige requisito={REQUISITOS.pagos}><CircuitoPagos /></Exige>}>
            <Route index element={null} />
            <Route path=":orden" element={null} />
          </Route>
          {RUTAS_VIEJAS.map(([vieja, nueva]) => (
            <Route key={vieja} path={vieja} element={<Navigate to={nueva} replace />} />
          ))}
          <Route path={RUTA_LIQUIDACION} element={<Exige requisito={REQUISITOS.liquidacion}><Liquidacion /></Exige>} />
          <Route path={RUTA_PANEL} element={<Exige requisito={REQUISITOS.panel}><Panel /></Exige>} />
          <Route path={RUTA_SOLICITUDES} element={<Exige requisito={REQUISITOS.solicitudes}><Solicitudes /></Exige>} />
          <Route path={RUTA_POR_ACLARAR} element={<PagosPorAclarar />} />
          <Route path="*" element={<NoEncontrada />} />
        </Routes>
      </main>
      </div>
    </div>
  );
}
