import { useEffect, useState } from "react";
import { Link, Navigate, Route, Routes } from "react-router";

import { Contactos, RUTA_CONTACTOS } from "./Contactos";
import { Cuenta, RUTA_CUENTA } from "./Cuenta";
import { Documentos, RUTA_DOCUMENTOS } from "./Documentos";
import { AvisoLlegada } from "./AvisoLlegada";
import { RUTA_ENTRAR, RUTA_INICIO, RUTA_INSTALAR, RUTA_INVITACION, RUTA_INVITACION_MANDATO } from "./enlace";
import { Exige, REQUISITOS } from "./permisos";
import { Entrar } from "./Entrar";
import { EntrarEncima } from "./EntrarEncima";
import { Liquidacion, RUTA_LIQUIDACION } from "./egresos/Liquidacion";
import { PagosPorAclarar, RUTA_POR_ACLARAR } from "./egresos/PagosPorAclarar";
import { AutorizacionesProveedores, RUTA_AUTORIZACIONES_PROVEEDORES } from "./egresos/AutorizacionesProveedores";
import { ConsultaProveedores, RUTA_CONSULTA_PROVEEDORES } from "./egresos/ConsultaProveedores";
import { Proveedores, RUTA_PROVEEDORES } from "./egresos/Proveedores";
import { Instalar } from "./Instalar";
import { Inicio } from "./inicio/Inicio";
import { Invitacion } from "./Invitacion";
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
import { Panel, RUTA_PANEL } from "./panel/Panel";
import { LiquidacionDelAdministrador, RUTA_LIQUIDACION_ADMINISTRADOR } from "./fiscalizacion/LiquidacionDelAdministrador";
import { LiquidacionOficial, RUTA_LIQUIDACION_OFICIAL } from "./fiscalizacion/LiquidacionOficial";
import { MisExpensas, RUTA_MIS_EXPENSAS } from "./fiscalizacion/MisExpensas";
import { RUTA_SOLICITUDES, Solicitudes } from "./fiscalizacion/Solicitudes";
import { RUTA_SEGUNDO_FACTOR, SegundoFactor } from "./SegundoFactor";
import { useVersion } from "./version";

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
          <Route
            path={RUTA_LIQUIDACION_ADMINISTRADOR}
            element={<Exige requisito={REQUISITOS.importarLiquidacion}><LiquidacionDelAdministrador /></Exige>}
          />
          <Route path={RUTA_MIS_EXPENSAS} element={<Exige requisito={REQUISITOS.misExpensas}><MisExpensas /></Exige>} />
          <Route
            path={RUTA_LIQUIDACION_OFICIAL}
            element={<Exige requisito={REQUISITOS.liquidacionOficial}><LiquidacionOficial /></Exige>}
          />
          <Route path={RUTA_SOLICITUDES} element={<Exige requisito={REQUISITOS.solicitudes}><Solicitudes /></Exige>} />
          <Route path={RUTA_POR_ACLARAR} element={<PagosPorAclarar />} />
          <Route path="*" element={<NoEncontrada />} />
        </Routes>
      </main>
      </div>
    </div>
  );
}
