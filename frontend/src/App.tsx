import { useQuery } from "@tanstack/react-query";
import { useEffect } from "react";
import { Link, Navigate, Route, Routes } from "react-router";

import { Contactos, RUTA_CONTACTOS } from "./Contactos";
import { ContactosGestion, RUTA_CONTACTOS_GESTION } from "./ContactosGestion";
import { Cuenta, RUTA_CUENTA } from "./Cuenta";
import { Documentos, RUTA_DOCUMENTOS } from "./Documentos";
import { AvisoLlegada } from "./AvisoLlegada";
import { RUTA_ENTRAR, RUTA_INICIO, RUTA_INVITACION, RUTA_INVITACION_MANDATO } from "./enlace";
import { api } from "./api/cliente";
import { cumpleEnAlguno, Exige, REQUISITOS, type Requisito, useConsultaDeAcciones } from "./permisos";
import { useSesion } from "./sesion";
import { Entrar } from "./Entrar";
import { EstadoServicio } from "./EstadoServicio";
import { Liquidacion, RUTA_LIQUIDACION } from "./egresos/Liquidacion";
import { PagosPorAclarar, RUTA_POR_ACLARAR } from "./egresos/PagosPorAclarar";
import { Proveedores, RUTA_PROVEEDORES } from "./egresos/Proveedores";
import { EnlaceInstalar, Instalar } from "./Instalar";
import { Invitacion } from "./Invitacion";
import { InvitacionesPropias, useMisInvitaciones } from "./InvitacionesPropias";
import { InvitacionMandato } from "./InvitacionMandato";
import { Invitar, RUTA_INVITACIONES } from "./Invitar";
import { Mandatos, RUTA_MANDATOS } from "./Mandatos";
import { ModoLectura } from "./ModoLectura";
import { CircuitoPagos } from "./pagos/CircuitoPagos";
import { ConsultaPagos } from "./pagos/ConsultaPagos";
import { Pestanas } from "./pagos/Pestanas";
import { PrepararOrden } from "./pagos/PrepararOrden";
import { RUTA_CONSULTA, RUTA_NUEVA, RUTA_PAGOS } from "./pagos/rutas";
import { Padron, RUTA_PADRON } from "./Padron";
import { RUTA_SEGUNDO_FACTOR, SegundoFactor } from "./SegundoFactor";
import { useVersion } from "./version";

const ENLACES_DE_INICIO: readonly { ruta: string; texto: string; requisito: Requisito }[] = [
  { ruta: RUTA_INVITACIONES, texto: "Invitar a una persona", requisito: REQUISITOS.invitaciones },
  { ruta: RUTA_MANDATOS, texto: "Mandatos y decisiones", requisito: REQUISITOS.mandatos },
  { ruta: RUTA_PADRON, texto: "Padrón del edificio", requisito: REQUISITOS.padron },
  { ruta: RUTA_CONTACTOS, texto: "Contactos de emergencia", requisito: REQUISITOS.contactos },
  { ruta: RUTA_DOCUMENTOS, texto: "Documentos del edificio", requisito: REQUISITOS.documentos },
  { ruta: RUTA_PAGOS, texto: "Pagos", requisito: REQUISITOS.pagos },
  { ruta: RUTA_LIQUIDACION, texto: "Liquidación (borrador)", requisito: REQUISITOS.liquidacion },
];

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

function Inicio() {
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
  if (sesion === "sin_sesion") return <Navigate to={RUTA_ENTRAR} replace />;
  if (sesion === "consultando") return <p role="status">Cargando…</p>;
  // Sin saber qué puede la persona, solo lo que le sirve igual: los contactos guardados.
  const enlaces =
    porEdificio === null
      ? ENLACES_DE_INICIO.filter((e) => e.ruta === RUTA_CONTACTOS)
      : ENLACES_DE_INICIO.filter((e) => cumpleEnAlguno(porEdificio, e.requisito));
  return (
    <>
      <h1>Tu consorcio, en tu mano</h1>
      <p>
        Estamos construyendo la aplicación. Pronto vas a poder ver tus expensas, documentos y
        avisos desde acá.
      </p>
      {porEdificio?.size === 0 && (
        <p>
          Todavía no participás de ningún edificio.
          {invitaciones.data?.length === 0 &&
            " Para sumarte, abrí el enlace de la invitación que te llegó por correo."}
        </p>
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
      <p>
        <Link to={RUTA_CUENTA}>Mi cuenta</Link>
      </p>
      {enlaces.map((e) => (
        <p key={e.ruta}>
          <Link to={e.ruta}>{e.texto}</Link>
        </p>
      ))}
      {porAclarar.data === true && (
        <p>
          <Link to={RUTA_POR_ACLARAR}>Pagos para aclarar (si dejaste de administrar)</Link>
        </p>
      )}
      <EnlaceInstalar />
      <EstadoServicio />
      <Pestanas />
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
        <Link to="/">Volver al inicio</Link>
      </p>
    </>
  );
}

export function App() {
  return (
    <>
      <header className="cabecera">
        <Link to={RUTA_INICIO} className="marca">
          <img src="/icono.svg" alt="" width="40" height="40" />
          <span>Consorcio en Mano</span>
        </Link>
        <ModoLectura />
      </header>
      <main id="contenido">
        <AvisoLlegada />
        <Routes>
          <Route path="/" element={<Inicio />} />
          <Route path="/instalar" element={<Instalar />} />
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
    </>
  );
}
