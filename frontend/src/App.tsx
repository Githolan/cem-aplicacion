import { useQuery } from "@tanstack/react-query";
import { useEffect, useId, useState } from "react";
import { createPortal } from "react-dom";
import { Link, Navigate, Route, Routes } from "react-router";

import { Contactos, RUTA_CONTACTOS } from "./Contactos";
import { ContactosGestion, RUTA_CONTACTOS_GESTION } from "./ContactosGestion";
import { Cuenta, RUTA_CUENTA } from "./Cuenta";
import { Documentos, RUTA_DOCUMENTOS } from "./Documentos";
import { AvisoLlegada } from "./AvisoLlegada";
import { RUTA_ENTRAR, RUTA_INICIO, RUTA_INSTALAR, RUTA_INVITACION, RUTA_INVITACION_MANDATO } from "./enlace";
import { api } from "./api/cliente";
import { cumpleEnAlguno, Exige, REQUISITOS, type Requisito, useConsultaDeAcciones } from "./permisos";
import { useSesion } from "./sesion";
import { Entrar } from "./Entrar";
import { EntrarEncima } from "./EntrarEncima";
import { AvisoServicio, PuntoServicio } from "./EstadoServicio";
import { ElegirConsorcio } from "./egresos/ElegirConsorcio";
import { Liquidacion, RUTA_LIQUIDACION } from "./egresos/Liquidacion";
import { PagosPorAclarar, RUTA_POR_ACLARAR } from "./egresos/PagosPorAclarar";
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
import { RUTA_CONSULTA, RUTA_COTEJO, RUTA_NUEVA, RUTA_PAGOS } from "./pagos/rutas";
import { Padron, RUTA_PADRON } from "./Padron";
import { TarjetaPagos, useResumenPagos } from "./ResumenPagos";
import { RUTA_SEGUNDO_FACTOR, SegundoFactor } from "./SegundoFactor";
import { useVersion } from "./version";

interface EnlaceDeInicio {
  ruta: string;
  texto: string;
  detalle?: string;
  icono: NombreIcono;
  requisito: Requisito;
}

const GRUPOS_DE_INICIO: readonly { titulo: string; enlaces: readonly EnlaceDeInicio[] }[] = [
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
    ],
  },
  {
    titulo: "Dinero",
    enlaces: [
      { ruta: RUTA_PAGOS, texto: "Pagos", icono: "billetera", requisito: REQUISITOS.pagos },
      {
        ruta: RUTA_LIQUIDACION,
        texto: "Liquidación (borrador)",
        detalle: "Simular el reparto por período",
        icono: "calculadora",
        requisito: REQUISITOS.liquidacion,
      },
    ],
  },
];

const ENLACES_DE_INICIO = GRUPOS_DE_INICIO.flatMap((g) => g.enlaces);

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

/** Las pantallas que reemplazó el circuito de pagos: un enlace viejo sigue llegando. */
const RUTAS_VIEJAS: readonly (readonly [string, string])[] = [
  ["/egresos/ordenes", RUTA_PAGOS],
  ["/egresos/revision", RUTA_PAGOS],
  ["/egresos/pagos", RUTA_PAGOS],
  ["/egresos/conciliacion", RUTA_PAGOS],
  ["/egresos/por-aclarar", RUTA_POR_ACLARAR],
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
  if (sesion === "sin_sesion") return <Navigate to={RUTA_ENTRAR} replace />;
  if (sesion === "consultando") return <p role="status">Cargando…</p>;
  // Sin saber qué puede la persona, solo lo que le sirve igual: los contactos guardados.
  const enlaces =
    porEdificio === null
      ? ENLACES_DE_INICIO.filter((e) => e.ruta === RUTA_CONTACTOS)
      : ENLACES_DE_INICIO.filter((e) => cumpleEnAlguno(porEdificio, e.requisito));
  const edificio = pagos.edificios.find((c) => c.id === pagos.consorcio);
  return (
    <>
      {barra !== null && createPortal(<PuntoServicio />, barra)}
      <h1>Tu consorcio, en tu mano</h1>
      <AvisoServicio />
      <p>
        Estamos construyendo la aplicación. Pronto vas a poder ver tus expensas, documentos y
        avisos desde acá.
      </p>
      {porEdificio?.size === 0 && (
        <section className="resumen-inicio" aria-label="Tu edificio">
          <div className="resumen-inicio__arriba">
            <span>Tu edificio</span>
            <span className="resumen-inicio__estado resumen-inicio__estado--neutro">Sin edificio</span>
          </div>
          <p>
            Todavía no participás de ningún edificio.
            {invitaciones.data?.length === 0 &&
              " Para sumarte, abrí el enlace de la invitación que te llegó por correo."}
          </p>
        </section>
      )}
      {falla !== null && (
        <div className="tarjeta">
          <p className="aviso-error" role="alert">
            Puede que falten secciones. {falla}
          </p>
          <button type="button" disabled={reintentando} onClick={reintentar}>
            Reintentar
          </button>
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
          reintentar={pagos.reintentar}
          reintentando={pagos.reintentando}
          edificio={edificio.nombre}
        />
      )}
      <div className="grupos-inicio">
        {GRUPOS_DE_INICIO.map((g) => {
          const propios = g.enlaces.filter((e) => enlaces.includes(e) && !(pagos.enTarjeta && e.ruta === RUTA_PAGOS));
          const aclarar = g.titulo === "Dinero" && porAclarar.data === true;
          if (propios.length === 0 && !aclarar) return null;
          return (
            <section key={g.titulo} aria-label={g.titulo}>
              <h2 className="titulo-seccion">{g.titulo}</h2>
              <ul className="filas-inicio">
                {propios.map((e) => (
                  <FilaDeInicio key={e.ruta} enlace={e.ruta === RUTA_PAGOS ? { ...e, detalle: pagos.detalle } : e} />
                ))}
                {aclarar && (
                  <FilaDeInicio
                    enlace={{
                      ruta: RUTA_POR_ACLARAR,
                      texto: "Pagos para aclarar (si dejaste de administrar)",
                      icono: "billetera",
                    }}
                  />
                )}
              </ul>
            </section>
          );
        })}
      </div>
      {pagos.prepara && (
        <p className="aviso-info">La aplicación no mueve dinero: el pago se hace por home banking y acá registrás la evidencia.</p>
      )}
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
      <h1>No encontramos esa página</h1>
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
          <Route path={RUTA_CONTACTOS_GESTION} element={<Exige requisito={REQUISITOS.contactosGestion}><ContactosGestion /></Exige>} />
          <Route path={RUTA_DOCUMENTOS} element={<Exige requisito={REQUISITOS.documentos}><Documentos /></Exige>} />
          <Route path={RUTA_SEGUNDO_FACTOR} element={<SegundoFactor />} />
          <Route path={RUTA_PROVEEDORES} element={<Exige requisito={REQUISITOS.proveedores}><Proveedores /></Exige>} />
          <Route path={RUTA_NUEVA} element={<Exige requisito={REQUISITOS.ordenes}><PrepararOrden /></Exige>} />
          <Route path={RUTA_CONSULTA} element={<Exige requisito={REQUISITOS.pagos}><ConsultaPagos /></Exige>} />
          <Route path={RUTA_COTEJO} element={<Exige requisito={REQUISITOS.cotejo}><Cotejo /></Exige>} />
          {/* Una sola ruta madre: la bandeja sigue montada al abrir la ficha al lado. */}
          <Route path={RUTA_PAGOS} element={<Exige requisito={REQUISITOS.pagos}><CircuitoPagos /></Exige>}>
            <Route index element={null} />
            <Route path=":orden" element={null} />
          </Route>
          {RUTAS_VIEJAS.map(([vieja, nueva]) => (
            <Route key={vieja} path={vieja} element={<Navigate to={nueva} replace />} />
          ))}
          <Route path={RUTA_LIQUIDACION} element={<Exige requisito={REQUISITOS.liquidacion}><Liquidacion /></Exige>} />
          <Route path={RUTA_POR_ACLARAR} element={<PagosPorAclarar />} />
          <Route path="*" element={<NoEncontrada />} />
        </Routes>
      </main>
      </div>
    </div>
  );
}
