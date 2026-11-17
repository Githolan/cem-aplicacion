import { matchPath, NavLink, useLocation } from "react-router";

import { RUTA_CONTACTOS } from "./Contactos";
import { RUTA_CUENTA } from "./Cuenta";
import { RUTA_DOCUMENTOS } from "./Documentos";
import { RUTA_LIQUIDACION } from "./egresos/Liquidacion";
import { RUTA_POR_ACLARAR } from "./egresos/PagosPorAclarar";
import { RUTA_AUTORIZACIONES_PROVEEDORES, TITULO_AUTORIZACIONES_PROVEEDORES } from "./egresos/AutorizacionesProveedores";
import { RUTA_CONSULTA_PROVEEDORES, TITULO_CONSULTA_PROVEEDORES } from "./egresos/ConsultaProveedores";
import { RUTA_PROVEEDORES } from "./egresos/Proveedores";
import { RUTA_INICIO } from "./enlace";
import { Icono, type NombreIcono } from "./Iconos";
import { RUTA_INVITACIONES } from "./Invitar";
import { RUTA_MANDATOS } from "./Mandatos";
import { RUTA_PADRON } from "./Padron";
import { RUTA_CONSULTA, RUTA_COTEJO, RUTA_NUEVA, RUTA_PAGOS } from "./pagos/rutas";
import { RUTA_PANEL, TITULO_PANEL } from "./panel/Panel";
import { RUTA_SOLICITUDES, TITULO_SOLICITUDES } from "./fiscalizacion/Solicitudes";
import { ofreceEnAlguno, REQUISITOS, type Requisito, useConsultaDeAcciones } from "./permisos";
import { useSesionConNombre } from "./sesion";

// El menú (tanda 1 del informe de fricción, MEN-01 a MEN-06):
// - Una sola entrada activa por pantalla. Una entrada cuya ruta es el comienzo de otra entrada del
//   menú lleva `exacta` (`end` de NavLink); `Navegacion.test.tsx` falla si falta. `/pagos` no la
//   lleva: sus pantallas hijas no tienen entrada propia y la marcan a ella.
// - En el teléfono y la tablet las pantallas de Gestión no tienen pestaña: se llega desde el inicio,
//   así que «Inicio» queda marcada (`navegacion__item--desde-inicio`, solo hasta 1024 px).
// - El menú aparece entero cuando se sabe qué alcanza la persona. Si no se pudo saber, lo dice en
//   el propio menú con «Reintentar» (en el inicio ya lo dice la pantalla).

export interface Destino {
  ruta: string;
  texto: string;
  /** Lo que entra en las pestañas del teléfono. Sin él va `texto`: la función completa, y se
   * abrevia solo cuando falta lugar (comentario 24). */
  corto?: string;
  icono: NombreIcono;
  /** Sin requisito: lo tiene toda persona con sesión. */
  requisito?: Requisito;
  /** No se ofrece a quien ya cumple esto (tiene otra pantalla que lo cubre). */
  salvo?: Requisito;
  /** Activa solo en su ruta exacta: su ruta es el comienzo de la de otra entrada (MEN-01). */
  exacta?: boolean;
}

const PRINCIPALES: readonly Destino[] = [
  { ruta: RUTA_INICIO, texto: "Inicio", icono: "inicio", exacta: true },
  { ruta: RUTA_PAGOS, texto: "Pagos", icono: "billetera", requisito: REQUISITOS.pagos },
  { ruta: RUTA_DOCUMENTOS, texto: "Documentos", icono: "documento", requisito: REQUISITOS.documentos },
  { ruta: RUTA_CONTACTOS, texto: "Contactos de emergencia", corto: "Emergencias", icono: "telefono", requisito: REQUISITOS.contactos },
  { ruta: RUTA_CUENTA, texto: "Cuenta", icono: "persona" },
];

// Solo en la barra lateral de la PC: en el teléfono y la tablet se llega desde el inicio.
export const DE_ADMINISTRACION: readonly Destino[] = [
  { ruta: RUTA_INVITACIONES, texto: "Invitar a una persona", icono: "invitar", requisito: REQUISITOS.invitaciones },
  { ruta: RUTA_MANDATOS, texto: "Mandatos y decisiones", icono: "balanza", requisito: REQUISITOS.mandatos },
  { ruta: RUTA_PADRON, texto: "Padrón del edificio", icono: "personas", requisito: REQUISITOS.padron },
  {
    ruta: RUTA_AUTORIZACIONES_PROVEEDORES,
    texto: TITULO_AUTORIZACIONES_PROVEEDORES,
    icono: "ojo",
    requisito: REQUISITOS.autorizarProveedores,
  },
  { ruta: RUTA_PROVEEDORES, texto: "Proveedores", icono: "proveedor", requisito: REQUISITOS.proveedores },
  {
    ruta: RUTA_CONSULTA_PROVEEDORES,
    texto: TITULO_CONSULTA_PROVEEDORES,
    icono: "proveedor",
    requisito: REQUISITOS.consultaProveedores,
    salvo: REQUISITOS.proveedores,
    // Es el comienzo de «Quién ve los proveedores» (`/proveedores/quien-los-ve`): defecto 37.
    exacta: true,
  },
  { ruta: RUTA_LIQUIDACION, texto: "Liquidación (borrador)", icono: "calculadora", requisito: REQUISITOS.liquidacion },
  // Con la navegación a la vista también en el Panel (informe de fricción, AC-09).
  { ruta: RUTA_PANEL, texto: TITULO_PANEL, icono: "grafico", requisito: REQUISITOS.panel },
  { ruta: RUTA_SOLICITUDES, texto: TITULO_SOLICITUDES, icono: "sobre", requisito: REQUISITOS.panel },
];

/** Todas las entradas del menú, en el orden en que se dibujan. */
export const DESTINOS_DEL_MENU: readonly Destino[] = [...PRINCIPALES, ...DE_ADMINISTRACION];

/** Los pares (madre, hija) en que la ruta de una entrada es el comienzo de otra y la madre no es exacta. */
export function prefijosSinExacta(destinos: readonly Destino[]): [string, string][] {
  return destinos.flatMap((madre) =>
    destinos
      .filter((hija) => hija !== madre && madre.exacta !== true && matchPath({ path: madre.ruta, end: false }, hija.ruta) !== null)
      .map((hija): [string, string] => [madre.ruta, hija.ruta]),
  );
}

const enAlgunaDe = (destinos: readonly Destino[], pathname: string) =>
  destinos.some((d) => matchPath({ path: d.ruta, end: d.exacta === true }, pathname) !== null);

// Las pantallas de la aplicación. Las demás (entrar, instalar, invitaciones, el alta del segundo
// factor y una ruta desconocida) se abren solas: no le preguntan nada al servidor para navegar.
const RUTAS_CON_NAVEGACION: readonly string[] = [
  ...PRINCIPALES.map((d) => d.ruta),
  ...DE_ADMINISTRACION.map((d) => d.ruta),
  RUTA_NUEVA,
  RUTA_CONSULTA,
  RUTA_COTEJO,
  `${RUTA_PAGOS}/:orden`,
  RUTA_POR_ACLARAR,
];

function Item({ destino, secundario, desdeInicio }: { destino: Destino; secundario?: boolean; desdeInicio?: boolean }) {
  const clase = [
    "navegacion__item",
    ...(secundario === true ? ["navegacion__item--secundario"] : []),
    ...(desdeInicio === true ? ["navegacion__item--desde-inicio"] : []),
  ].join(" ");
  return (
    <NavLink to={destino.ruta} end={destino.exacta === true} className={clase}>
      <Icono nombre={destino.icono} />
      {destino.corto === undefined ? (
        <span>{destino.texto}</span>
      ) : (
        // Las dos están en la página y el ancho deja ver una: el nombre del enlace es el que se ve.
        <>
          <span className="navegacion__largo">{destino.texto}</span>
          <span className="navegacion__corto">{destino.corto}</span>
        </>
      )}
    </NavLink>
  );
}

export function Navegacion() {
  const { pathname } = useLocation();
  const { porEdificio, consultando, falla, reintentar, reintentando } = useConsultaDeAcciones();
  const ofrecido = (d: Destino) => d.requisito === undefined || (porEdificio !== null && ofreceEnAlguno(porEdificio, d.requisito, d.salvo));
  const administracion = DE_ADMINISTRACION.filter(ofrecido);
  const enGestion = enAlgunaDe(DE_ADMINISTRACION, pathname);
  const marca = (
    <div className="navegacion__marca" aria-hidden="true">
      <img src="/icono.svg" alt="" width="40" height="40" />
      <span>Consorcio en Mano</span>
    </div>
  );
  // Mientras se consulta, el lugar del menú queda reservado y vacío: aparece entero de una vez.
  if (consultando)
    return (
      <nav className="navegacion" aria-label="Secciones" aria-busy="true">
        {marca}
      </nav>
    );
  return (
    <nav className="navegacion" aria-label="Secciones">
      {marca}
      <p className="navegacion__grupo">Mi consorcio</p>
      {PRINCIPALES.filter(ofrecido).map((d) => (
        <Item key={d.ruta} destino={d} desdeInicio={d.ruta === RUTA_INICIO && enGestion} />
      ))}
      {/* Neutro: el servidor dice qué alcanza la persona, no con qué papel. No se deduce acá. */}
      {administracion.length > 0 && <p className="navegacion__grupo">Gestión</p>}
      {administracion.map((d) => (
        <Item key={d.ruta} destino={d} secundario />
      ))}
      {/* En el inicio la falla ya la dice la pantalla, con su «Reintentar» (C1). */}
      {falla !== null && pathname !== RUTA_INICIO && (
        <p className="navegacion__falla" role="alert">
          No se pudo leer el menú: faltan secciones.{" "}
          <button type="button" className="secundario" onClick={reintentar} disabled={reintentando}>
            Reintentar
          </button>
        </p>
      )}
    </nav>
  );
}

/** Se muestra solo con sesión: las pantallas públicas no piden ni los edificios ni las acciones. */
export function useNavegacion(): { hay: boolean; nombre: string | null } {
  const { pathname } = useLocation();
  const privada = RUTAS_CON_NAVEGACION.some((ruta) => matchPath(ruta, pathname) !== null);
  const { estado, nombre } = useSesionConNombre(privada);
  const hay = estado === "con_sesion" && privada;
  return { hay, nombre: hay ? nombre : null };
}

const iniciales = (nombre: string) =>
  nombre
    .split(/\s+/)
    .slice(0, 2)
    .map((palabra) => palabra.charAt(0).toUpperCase())
    .join("");

/** Arriba, en tablet y PC: quién tiene la sesión. Es solo el nombre: a la cuenta se llega por la
 * entrada «Cuenta» del menú, un solo camino (MEN-03). En el teléfono está en «Cuenta». */
export function PersonaEnCabecera({ nombre }: { nombre: string }) {
  return (
    <p className="persona-cabecera">
      <span className="persona-cabecera__avatar" aria-hidden="true">
        {iniciales(nombre)}
      </span>
      <span className="persona-cabecera__nombre">{nombre}</span>
    </p>
  );
}
