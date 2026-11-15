import { useQuery } from "@tanstack/react-query";
import { useEffect, useId, useState } from "react";
import { createPortal } from "react-dom";
import { Link, Navigate, Route, Routes } from "react-router";

import { Contactos, RUTA_CONTACTOS } from "./Contactos";
import { Cuenta, RUTA_CUENTA } from "./Cuenta";
import { Documentos, RUTA_DOCUMENTOS } from "./Documentos";
import { AvisoLlegada } from "./AvisoLlegada";
import { RUTA_ENTRAR, RUTA_INICIO, RUTA_INSTALAR, RUTA_INVITACION, RUTA_INVITACION_MANDATO } from "./enlace";
import { api } from "./api/cliente";
import { cumpleEnAlguno, Exige, REQUISITOS, type Requisito, useConsultaDeAcciones } from "./permisos";
import { useSesion } from "./sesion";
import { Entrar } from "./Entrar";
import { EntrarEncima } from "./EntrarEncima";
import { PuntoServicio, useFallaDelServicio } from "./EstadoServicio";
import { ElegirConsorcio } from "./egresos/ElegirConsorcio";
import { Liquidacion, RUTA_LIQUIDACION } from "./egresos/Liquidacion";
import { PagosPorAclarar, RUTA_POR_ACLARAR } from "./egresos/PagosPorAclarar";
import { ConsultaProveedores, RUTA_CONSULTA_PROVEEDORES, TITULO_CONSULTA_PROVEEDORES } from "./egresos/ConsultaProveedores";
import { Proveedores, RUTA_PROVEEDORES } from "./egresos/Proveedores";
import { Icono, type NombreIcono } from "./Iconos";
import { EnlaceInstalar, Instalar } from "./Instalar";
import { Invitacion } from "./Invitacion";
import { InvitacionesPropias, useMisInvitaciones } from "./InvitacionesPropias";
import { InvitacionMandato } from "./InvitacionMandato";
import { Invitar, RUTA_INVITACIONES } from "./Invitar";
import { Mandatos, RUTA_MANDATOS } from "./Mandatos";
import { ModoLectura } from "./ModoLectura";
import { Navegacion, PersonaEnCabecera, useNavegacion } from "./Navegacion";
import { CircuitoPagos } from "./pagos/CircuitoPagos";
import { ConsultaPagos } from "./pagos/ConsultaPagos";
import { Cotejo } from "./pagos/Cotejo";
import { PrepararOrden } from "./pagos/PrepararOrden";
import { RUTA_CONSULTA, RUTA_COTEJO, RUTA_NUEVA, RUTA_PAGOS, RUTA_SIN_EXPLICAR } from "./pagos/rutas";
import { SinExplicar } from "./pagos/SinExplicar";
import { Padron, RUTA_PADRON } from "./Padron";
import { Panel, RUTA_PANEL, TITULO_PANEL } from "./panel/Panel";
import { TarjetaPagos, useResumenPagos } from "./ResumenPagos";
import { CONSULTA_AL_LADO, useAncho } from "./pagos/useAncho";
import { RUTA_SEGUNDO_FACTOR, SegundoFactor } from "./SegundoFactor";
import { useVersion } from "./version";
import { DETALLE_VINCULOS_SIN_LECTURA, textoVinculosPorConfirmar, useVinculosPorConfirmar } from "./VinculosPorConfirmar";

interface EnlaceDeInicio {
  ruta: string;
  texto: string;
  detalle?: string;
  icono: NombreIcono;
  requisito: Requisito;
  /** No se ofrece a quien ya cumple esto (tiene otra pantalla que lo cubre). */
  salvo?: Requisito;
}

interface GrupoDeInicio {
  titulo: string;
  enlaces: readonly EnlaceDeInicio[];
  /** Lleva «Pagos para aclarar» cuando quedó algo de quien dejó de administrar. */
  conPorAclarar?: boolean;
}

const GRUPOS_DE_INICIO: readonly GrupoDeInicio[] = [
  {
    titulo: "Personas y mandatos",
    enlaces: [
      {
        ruta: RUTA_INVITACIONES,
        texto: "Invitar a una persona",
        detalle: "Invitación de un solo uso a una unidad",
        icono: "invitar",
        requisito: REQUISITOS.invitaciones,
      },
      {
        ruta: RUTA_MANDATOS,
        texto: "Mandatos y decisiones",
        detalle: "Quién administra y qué se decidió",
        icono: "balanza",
        requisito: REQUISITOS.mandatos,
      },
      {
        ruta: RUTA_PADRON,
        texto: "Padrón del edificio",
        detalle: "Confirmar o finalizar vínculos",
        icono: "personas",
        requisito: REQUISITOS.padron,
      },
    ],
  },
  {
    titulo: "Edificio",
    enlaces: [
      {
        ruta: RUTA_CONTACTOS,
        texto: "Contactos de emergencia",
        detalle: "Lista de emergencia del edificio",
        icono: "telefono",
        requisito: REQUISITOS.contactos,
      },
      {
        ruta: RUTA_DOCUMENTOS,
        texto: "Documentos del edificio",
        detalle: "Cargar y consultar documentos",
        icono: "carpeta",
        requisito: REQUISITOS.documentos,
      },
      {
        ruta: RUTA_CONSULTA_PROVEEDORES,
        texto: TITULO_CONSULTA_PROVEEDORES,
        detalle: "Seguros, habilitaciones y lo que exige la ley",
        icono: "proveedor",
        requisito: REQUISITOS.consultaProveedores,
        salvo: REQUISITOS.proveedores,
      },
    ],
  },
  {
    titulo: "Dinero",
    conPorAclarar: true,
    enlaces: [
      { ruta: RUTA_PAGOS, texto: "Pagos", icono: "billetera", requisito: REQUISITOS.pagos },
      {
        ruta: RUTA_LIQUIDACION,
        texto: "Liquidación (borrador)",
        detalle: "Simular el reparto por período",
        icono: "calculadora",
        requisito: REQUISITOS.liquidacion,
      },
      {
        ruta: RUTA_PANEL,
        texto: TITULO_PANEL,
        detalle: "Órdenes por estado, lo pagado y las facturas que faltan",
        icono: "grafico",
        requisito: REQUISITOS.panel,
      },
    ],
  },
];

const ENLACES_DE_INICIO = GRUPOS_DE_INICIO.flatMap((g) => g.enlaces);

/** Desde 1025 px la barra lateral ya lista los destinos (D-39, decisión 3 del propietario del
 * 14-11-2026): el inicio de quien prepara órdenes muestra solo lo pendiente. En teléfono y tablet,
 * los grupos de siempre. */
const PARA_HACER: GrupoDeInicio = {
  titulo: "Para hacer",
  conPorAclarar: true,
  enlaces: ENLACES_DE_INICIO.filter((e) => e.ruta === RUTA_PAGOS),
};

// El nombre del enlace es solo su título: el detalle lo acompaña como descripción.
function FilaDeInicio({ enlace }: { enlace: Pick<EnlaceDeInicio, "ruta" | "texto" | "detalle" | "icono"> }) {
  const id = useId();
  return (
    <li>
      <Link
        to={enlace.ruta}
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
  const alLado = useAncho(CONSULTA_AL_LADO);
  if (sesion === "sin_sesion") return <Navigate to={RUTA_ENTRAR} replace />;
  if (sesion === "consultando") return <p role="status">Cargando…</p>;
  // Sin saber qué puede la persona, solo lo que le sirve igual: los contactos guardados.
  const enlaces =
    porEdificio === null
      ? ENLACES_DE_INICIO.filter((e) => e.ruta === RUTA_CONTACTOS)
      : ENLACES_DE_INICIO.filter(
          (e) => cumpleEnAlguno(porEdificio, e.requisito) && (e.salvo === undefined || !cumpleEnAlguno(porEdificio, e.salvo)),
        );
  const edificio = pagos.edificios.find((c) => c.id === pagos.consorcio);
  // En el «Para hacer» de la computadora, el Padrón entra si hay vínculos esperando (G-26) o si no
  // se pudo saber: un número desconocido no es «nada pendiente» (Codex en #245).
  const paraHacer: GrupoDeInicio =
    vinculos.falla !== null || (vinculos.cantidad !== null && vinculos.cantidad > 0)
      ? { ...PARA_HACER, enlaces: [...PARA_HACER.enlaces, ...ENLACES_DE_INICIO.filter((e) => e.ruta === RUTA_PADRON)] }
      : PARA_HACER;
  const grupos = alLado && porEdificio !== null && cumpleEnAlguno(porEdificio, REQUISITOS.ordenes) ? [paraHacer] : GRUPOS_DE_INICIO;
  const detalleDe = (e: EnlaceDeInicio): EnlaceDeInicio => {
    if (e.ruta === RUTA_PAGOS) return { ...e, detalle: pagos.detalle };
    if (e.ruta !== RUTA_PADRON) return e;
    if (vinculos.falla !== null) return { ...e, detalle: DETALLE_VINCULOS_SIN_LECTURA };
    if (vinculos.cantidad !== null && vinculos.cantidad > 0) return { ...e, detalle: textoVinculosPorConfirmar(vinculos.cantidad) };
    return e;
  };
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
      <h1>Consorcio en mano</h1>
      {porEdificio?.size === 0 && (
        <section className="resumen-inicio" aria-label="Edificio">
          <div className="resumen-inicio__arriba">
            <span>Edificio</span>
            <span className="resumen-inicio__estado resumen-inicio__estado--neutro">Sin edificio</span>
          </div>
          <p>
            Esta cuenta todavía no participa de ningún edificio.
            {invitaciones.data?.length === 0 &&
              " Para sumarse hay que abrir el enlace de la invitación que llegó por correo."}
          </p>
        </section>
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
      <ElegirConsorcio
        pantalla={{ tipo: "lista", consorcios: pagos.edificios }}
        consorcio={pagos.consorcio}
        elegir={pagos.setConsorcio}
        deshabilitado={false}
      />
      {pagos.enTarjeta && edificio !== undefined && (
        <TarjetaPagos
          cantidad={pagos.cantidad}
          falla={pagos.falla}
          reintentar={fallaDePagos && enLaTarjeta ? botonReintentar : null}
          edificio={edificio.nombre}
        />
      )}
      <div className="grupos-inicio">
        {grupos.map((g) => {
          const propios = g.enlaces.filter((e) => enlaces.includes(e) && !(pagos.enTarjeta && e.ruta === RUTA_PAGOS));
          const aclarar = g.conPorAclarar === true && porAclarar.data === true;
          if (propios.length === 0 && !aclarar) return null;
          return (
            <section key={g.titulo} aria-label={g.titulo}>
              <h2 className="titulo-seccion">{g.titulo}</h2>
              <ul className="filas-inicio">
                {propios.map((e) => (
                  <FilaDeInicio key={e.ruta} enlace={detalleDe(e)} />
                ))}
                {aclarar && (
                  <FilaDeInicio
                    enlace={{
                      ruta: RUTA_POR_ACLARAR,
                      texto: "Pagos para aclarar (de quien dejó de administrar)",
                      icono: "billetera",
                    }}
                  />
                )}
              </ul>
            </section>
          );
        })}
      </div>
      <EnlaceInstalar />
    </>
  );
}

// Una ruta que esta versión no conoce puede ser de una versión más nueva (un enlace del correo,
// abierto con la aplicación vieja en caché). Si hay una versión nueva esperando, se aplica sola en
// la misma URL completa, en vez de dejar a la persona en "No encontramos". En las rutas conocidas
// la versión nueva sigue esperando el botón del aviso (documento 13, §14.4).
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
          <Route path={RUTA_POR_ACLARAR} element={<PagosPorAclarar />} />
          <Route path="*" element={<NoEncontrada />} />
        </Routes>
      </main>
      </div>
    </div>
  );
}
