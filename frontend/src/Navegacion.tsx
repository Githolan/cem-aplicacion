import { matchPath, NavLink, useLocation } from "react-router";

import { RUTA_CONTACTOS } from "./Contactos";
import { RUTA_CONTACTOS_GESTION } from "./ContactosGestion";
import { RUTA_CUENTA } from "./Cuenta";
import { RUTA_DOCUMENTOS } from "./Documentos";
import { RUTA_LIQUIDACION } from "./egresos/Liquidacion";
import { RUTA_POR_ACLARAR } from "./egresos/PagosPorAclarar";
import { RUTA_PROVEEDORES } from "./egresos/Proveedores";
import { RUTA_INICIO } from "./enlace";
import { Icono, type NombreIcono } from "./Iconos";
import { RUTA_INVITACIONES } from "./Invitar";
import { RUTA_MANDATOS } from "./Mandatos";
import { RUTA_PADRON } from "./Padron";
import { RUTA_CONSULTA, RUTA_COTEJO, RUTA_NUEVA, RUTA_PAGOS } from "./pagos/rutas";
import { cumpleEnAlguno, REQUISITOS, type Requisito, useAccionesPorEdificio } from "./permisos";
import { useSesion } from "./sesion";

interface Destino {
  ruta: string;
  texto: string;
  icono: NombreIcono;
  /** Sin requisito: lo tiene toda persona con sesión. */
  requisito?: Requisito;
}

const PRINCIPALES: readonly Destino[] = [
  { ruta: RUTA_INICIO, texto: "Inicio", icono: "inicio" },
  { ruta: RUTA_PAGOS, texto: "Pagos", icono: "billetera", requisito: REQUISITOS.pagos },
  { ruta: RUTA_DOCUMENTOS, texto: "Documentos", icono: "documento", requisito: REQUISITOS.documentos },
  { ruta: RUTA_CONTACTOS, texto: "Contactos", icono: "telefono", requisito: REQUISITOS.contactos },
  { ruta: RUTA_CUENTA, texto: "Mi cuenta", icono: "persona" },
];

// Solo en la barra lateral de la PC: en el teléfono y la tablet se llega desde el inicio.
const DE_ADMINISTRACION: readonly Destino[] = [
  { ruta: RUTA_INVITACIONES, texto: "Invitar a una persona", icono: "invitar", requisito: REQUISITOS.invitaciones },
  { ruta: RUTA_MANDATOS, texto: "Mandatos y decisiones", icono: "balanza", requisito: REQUISITOS.mandatos },
  { ruta: RUTA_PADRON, texto: "Padrón del edificio", icono: "personas", requisito: REQUISITOS.padron },
  { ruta: RUTA_PROVEEDORES, texto: "Proveedores", icono: "proveedor", requisito: REQUISITOS.proveedores },
  { ruta: RUTA_LIQUIDACION, texto: "Liquidación (borrador)", icono: "calculadora", requisito: REQUISITOS.liquidacion },
];

// Las pantallas de la aplicación. Las demás (entrar, instalar, invitaciones, el alta del segundo
// factor y una ruta desconocida) se abren solas: no le preguntan nada al servidor para navegar.
const RUTAS_CON_NAVEGACION: readonly string[] = [
  ...PRINCIPALES.map((d) => d.ruta),
  ...DE_ADMINISTRACION.map((d) => d.ruta),
  RUTA_CONTACTOS_GESTION,
  RUTA_NUEVA,
  RUTA_CONSULTA,
  RUTA_COTEJO,
  `${RUTA_PAGOS}/:orden`,
  RUTA_POR_ACLARAR,
];

function Item({ destino, secundario }: { destino: Destino; secundario?: boolean }) {
  const clase = secundario === true ? "navegacion__item navegacion__item--secundario" : "navegacion__item";
  return (
    <NavLink to={destino.ruta} end={destino.ruta === RUTA_INICIO} className={clase}>
      <Icono nombre={destino.icono} />
      <span>{destino.texto}</span>
    </NavLink>
  );
}

export function Navegacion() {
  const porEdificio = useAccionesPorEdificio();
  const ofrecido = (d: Destino) =>
    d.requisito === undefined || (porEdificio !== null && cumpleEnAlguno(porEdificio, d.requisito));
  const administracion = DE_ADMINISTRACION.filter(ofrecido);
  return (
    <nav className="navegacion" aria-label="Secciones">
      <div className="navegacion__marca" aria-hidden="true">
        <img src="/icono.svg" alt="" width="40" height="40" />
        <span>Consorcio en Mano</span>
      </div>
      <p className="navegacion__grupo">Mi consorcio</p>
      {PRINCIPALES.filter(ofrecido).map((d) => (
        <Item key={d.ruta} destino={d} />
      ))}
      {administracion.length > 0 && <p className="navegacion__grupo">Administración</p>}
      {administracion.map((d) => (
        <Item key={d.ruta} destino={d} secundario />
      ))}
    </nav>
  );
}

/** Se muestra solo con sesión: las pantallas públicas no piden ni los edificios ni las acciones. */
export function useHayNavegacion(): boolean {
  const { pathname } = useLocation();
  const privada = RUTAS_CON_NAVEGACION.some((ruta) => matchPath(ruta, pathname) !== null);
  return useSesion(privada) === "con_sesion" && privada;
}
