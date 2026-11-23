import { Link, matchPath, useLocation } from "react-router";

import { RUTA_COMUNICADOS, TITULO_COMUNICADOS } from "./comunicados/Comunicados";
import { RUTA_CONTACTOS } from "./Contactos";
import { RUTA_CUENTA } from "./Cuenta";
import { RUTA_DOCUMENTOS } from "./Documentos";
import { RUTA_LIQUIDACION } from "./egresos/Liquidacion";
import { RUTA_POR_ACLARAR } from "./egresos/PagosPorAclarar";
import { RUTA_AUTORIZACIONES_PROVEEDORES, RUTA_CONSULTA_PROVEEDORES, RUTA_PROVEEDORES } from "./egresos/comun";
import { RUTA_INICIO } from "./enlace";
import { Icono, type NombreIcono } from "./Iconos";
import { RUTA_INVITACIONES } from "./Invitar";
import { RUTA_MANDATOS } from "./Mandatos";
import { RUTA_PADRON } from "./Padron";
import { RUTA_CONSULTA, RUTA_COTEJO, RUTA_NUEVA, RUTA_PAGOS } from "./pagos/rutas";
import { RUTA_PANEL, TITULO_PANEL } from "./panel/Panel";
import { RUTA_LIQUIDACION_ADMINISTRADOR, TITULO_LIQUIDACION_ADMINISTRADOR } from "./fiscalizacion/LiquidacionDelAdministrador";
import { RUTA_LIQUIDACION_OFICIAL, TITULO_LIQUIDACION_OFICIAL } from "./fiscalizacion/LiquidacionOficial";
import { RUTA_MIS_EXPENSAS, TITULO_MIS_EXPENSAS } from "./fiscalizacion/MisExpensas";
import { RUTA_RECLAMOS, TITULO_RECLAMOS } from "./fiscalizacion/Reclamos";
import { RUTA_REPARACIONES, TITULO_REPARACIONES } from "./reparaciones/textos";
import { RUTA_SOLICITUDES, TITULO_SOLICITUDES } from "./fiscalizacion/Solicitudes";
import { RUTA_AYUDA } from "./ayuda/estadoAyuda";
import { useSolicitudesDirigidas } from "./fiscalizacion/solicitudesDirigidas";
import { cumpleEnAlguno, ofreceEnAlguno, REQUISITOS, type Requisito, useConsultaDeAcciones } from "./permisos";
import { useSesionConNombre } from "./sesion";

// El menú (tanda 1 del informe de fricción, MEN-01 a MEN-06):
// - Una sola entrada activa por pantalla. Una entrada cuya ruta es el comienzo de otra entrada del
//   menú lleva `exacta` (se marca solo en su ruta); `Navegacion.test.tsx` falla si falta. `/pagos` no la
//   lleva: sus pantallas hijas no tienen entrada propia y la marcan a ella.
// - En el teléfono y la tablet las pantallas de Gestión no tienen pestaña: se llega desde el inicio,
//   así que «Inicio» queda marcada (`navegacion__item--desde-inicio`, solo hasta 1024 px).
// - El menú aparece entero cuando se sabe qué alcanza la persona. Si no se pudo saber, lo dice en
//   el propio menú con «Reintentar» (en el inicio ya lo dice la pantalla).
// - Proveedores es una sola entrada (MEN-05, tanda 4): lleva a la gestión si la persona gestiona
//   proveedores en algún edificio, si no a la consulta, y si no a «Quién ve los proveedores». Las
//   otras dos se abren desde la pantalla, y en las tres la entrada queda marcada.

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
  /** Una entrada para varias pantallas (MEN-05): lleva a la primera que la persona alcanza en algún
   * edificio y queda marcada en todas. Con esto, `ruta` es la primera y `requisito` no se usa. */
  opciones?: readonly { ruta: string; requisito: Requisito }[];
}

/** Todas las rutas que marcan la entrada. */
const rutasDe = (d: Destino): readonly string[] => (d.opciones === undefined ? [d.ruta] : d.opciones.map((o) => o.ruta));

const PRINCIPALES: readonly Destino[] = [
  { ruta: RUTA_INICIO, texto: "Inicio", icono: "inicio", exacta: true },
  { ruta: RUTA_PAGOS, texto: "Pagos", icono: "billetera", requisito: REQUISITOS.pagos },
  { ruta: RUTA_DOCUMENTOS, texto: "Documentos", icono: "documento", requisito: REQUISITOS.documentos },
  { ruta: RUTA_MIS_EXPENSAS, texto: TITULO_MIS_EXPENSAS, corto: "Expensas", icono: "billetera", requisito: REQUISITOS.misExpensas },
  { ruta: RUTA_COMUNICADOS, texto: TITULO_COMUNICADOS, icono: "megafono", requisito: REQUISITOS.comunicados },
  { ruta: RUTA_CONTACTOS, texto: "Contactos de emergencia", corto: "Emergencias", icono: "telefono", requisito: REQUISITOS.contactos },
  { ruta: RUTA_CUENTA, texto: "Cuenta", icono: "persona" },
];

// Solo en la barra lateral de la PC: en el teléfono y la tablet se llega desde el inicio.
export const DE_ADMINISTRACION: readonly Destino[] = [
  { ruta: RUTA_INVITACIONES, texto: "Invitar a una persona", icono: "invitar", requisito: REQUISITOS.invitaciones },
  { ruta: RUTA_MANDATOS, texto: "Mandatos y decisiones", icono: "balanza", requisito: REQUISITOS.mandatos },
  { ruta: RUTA_PADRON, texto: "Padrón del edificio", icono: "personas", requisito: REQUISITOS.padron },
  {
    ruta: RUTA_PROVEEDORES,
    texto: "Proveedores",
    icono: "proveedor",
    opciones: [
      { ruta: RUTA_PROVEEDORES, requisito: REQUISITOS.proveedores },
      { ruta: RUTA_CONSULTA_PROVEEDORES, requisito: REQUISITOS.consultaProveedores },
      { ruta: RUTA_AUTORIZACIONES_PROVEEDORES, requisito: REQUISITOS.autorizarProveedores },
    ],
  },
  { ruta: RUTA_LIQUIDACION, texto: "Liquidación (borrador)", icono: "calculadora", requisito: REQUISITOS.liquidacion },
  // Con la navegación a la vista también en el Panel (informe de fricción, AC-09).
  { ruta: RUTA_PANEL, texto: TITULO_PANEL, icono: "grafico", requisito: REQUISITOS.panel },
  { ruta: RUTA_SOLICITUDES, texto: TITULO_SOLICITUDES, icono: "sobre", requisito: REQUISITOS.panel },
  { ruta: RUTA_RECLAMOS, texto: TITULO_RECLAMOS, icono: "globo", requisito: REQUISITOS.reclamos },
  // Las reparaciones (RF19, app 0124): también para quien pide (propietario, inquilino u ocupante);
  // en el teléfono se llega desde el inicio, sin una pestaña más.
  { ruta: RUTA_REPARACIONES, texto: TITULO_REPARACIONES, icono: "herramienta", requisito: REQUISITOS.reparaciones },
  { ruta: RUTA_LIQUIDACION_ADMINISTRADOR, texto: TITULO_LIQUIDACION_ADMINISTRADOR, icono: "documento", requisito: REQUISITOS.importarLiquidacion },
  { ruta: RUTA_LIQUIDACION_OFICIAL, texto: TITULO_LIQUIDACION_OFICIAL, icono: "calculadora", requisito: REQUISITOS.liquidacionOficial },
];

/**
 * FL-VER-04: quien de toda la gestión alcanza solo la consulta de proveedores (el habitante
 * autorizado) no gestiona nada: «Proveedores» va con las principales, sin el grupo «Gestión». Recibe
 * las rutas a las que llevan las entradas de gestión que se le ofrecen.
 */
export const soloConsultaProveedores = (rutas: readonly string[]): boolean =>
  rutas.length === 1 && rutas[0] === RUTA_CONSULTA_PROVEEDORES;

/** Todas las entradas del menú, en el orden en que se dibujan. */
export const DESTINOS_DEL_MENU: readonly Destino[] = [...PRINCIPALES, ...DE_ADMINISTRACION];

/** Los pares (madre, hija) en que una ruta de una entrada es el comienzo de una de otra y la madre no es exacta. */
export function prefijosSinExacta(destinos: readonly Destino[]): [string, string][] {
  return destinos.flatMap((madre) =>
    madre.exacta === true
      ? []
      : destinos
          .filter((hija) => hija !== madre)
          .flatMap((hija) =>
            rutasDe(madre).flatMap((deMadre) =>
              rutasDe(hija)
                .filter((deHija) => matchPath({ path: deMadre, end: false }, deHija) !== null)
                .map((deHija): [string, string] => [deMadre, deHija]),
            ),
          ),
  );
}

/** Si la pantalla actual es de la entrada: una exacta, solo en su ruta. */
const marcaLa = (d: Destino, pathname: string) => rutasDe(d).some((ruta) => matchPath({ path: ruta, end: d.exacta === true }, pathname) !== null);

const enAlgunaDe = (destinos: readonly Destino[], pathname: string) => destinos.some((d) => marcaLa(d, pathname));

// Las pantallas de la aplicación. Las demás (entrar, instalar, invitaciones, el alta del segundo
// factor y una ruta desconocida) se abren solas: no le preguntan nada al servidor para navegar.
const RUTAS_CON_NAVEGACION: readonly string[] = [
  ...PRINCIPALES.flatMap(rutasDe),
  ...DE_ADMINISTRACION.flatMap(rutasDe),
  RUTA_NUEVA,
  RUTA_CONSULTA,
  RUTA_COTEJO,
  `${RUTA_PAGOS}/:orden`,
  RUTA_POR_ACLARAR,
  RUTA_AYUDA,
];

/** Adónde lleva la entrada para esta persona; null si no se le ofrece. La misma regla en el menú y
 * en la lista «Gestión» del inicio (FL-AVI-02): `conSolicitudes` es el destinatario que tiene alguna
 * solicitud dirigida a él (MEN-04). */
export function rutaDelDestino(d: Destino, porEdificio: ReadonlyMap<string, readonly string[]> | null, conSolicitudes: boolean): string | null {
  if (d.opciones !== undefined)
    return porEdificio === null ? null : (d.opciones.find((o) => cumpleEnAlguno(porEdificio, o.requisito))?.ruta ?? null);
  if (d.ruta === RUTA_SOLICITUDES && conSolicitudes) return d.ruta;
  return d.requisito === undefined || (porEdificio !== null && ofreceEnAlguno(porEdificio, d.requisito, d.salvo)) ? d.ruta : null;
}

type PropsItem = { destino: Destino; ruta: string; activa: boolean; secundario?: boolean; desdeInicio?: boolean };

function Item({ destino, ruta, activa, secundario, desdeInicio }: PropsItem) {
  const clase = [
    "navegacion__item",
    ...(secundario === true ? ["navegacion__item--secundario"] : []),
    ...(desdeInicio === true ? ["navegacion__item--desde-inicio"] : []),
    ...(activa ? ["active"] : []),
  ].join(" ");
  return (
    <Link to={ruta} className={clase} aria-current={activa ? "page" : undefined}>
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
    </Link>
  );
}

export function Navegacion() {
  const { pathname } = useLocation();
  const { porEdificio, consultando, falla, reintentar, reintentando } = useConsultaDeAcciones();
  // MEN-04: «Solicitudes formales» también para el destinatario que tiene alguna dirigida a él.
  const conSolicitudes = useSolicitudesDirigidas(porEdificio);
  const ofrecidas = (destinos: readonly Destino[]) =>
    destinos.flatMap((destino) => {
      const ruta = rutaDelDestino(destino, porEdificio, conSolicitudes);
      return ruta === null ? [] : [{ destino, ruta, activa: marcaLa(destino, pathname) }];
    });
  const administracion = ofrecidas(DE_ADMINISTRACION);
  const consultaPrincipal = soloConsultaProveedores(administracion.map((a) => a.ruta));
  const enGestion = !consultaPrincipal && enAlgunaDe(DE_ADMINISTRACION, pathname);
  const principales = ofrecidas(PRINCIPALES);
  // La consulta, entre las principales: antes de «Cuenta», que queda última.
  const deMiConsorcio = consultaPrincipal
    ? [...principales.filter((d) => d.destino.ruta !== RUTA_CUENTA), ...administracion, ...principales.filter((d) => d.destino.ruta === RUTA_CUENTA)]
    : principales;
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
      {deMiConsorcio.map((d) => (
        <Item key={d.destino.ruta} {...d} desdeInicio={d.destino.ruta === RUTA_INICIO && enGestion} />
      ))}
      {/* Neutro: el servidor dice qué alcanza la persona, no con qué papel. No se deduce acá. */}
      {!consultaPrincipal && administracion.length > 0 && <p className="navegacion__grupo">Gestión</p>}
      {!consultaPrincipal &&
        administracion.map((d) => (
          <Item key={d.destino.ruta} {...d} secundario />
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
