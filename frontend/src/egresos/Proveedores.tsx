import { useEffect, useId, useRef, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router";

import { CabeceraInterna } from "../CabeceraInterna";
import { CampoCuit, cuitConError } from "../CampoCuit";
import { FilaDeRegistro, useFichas } from "../FichaDeRegistro";
import { type FinDeEnvio, VentanaDeFormulario } from "../VentanaDeFormulario";
import { type Cargado, CargaConLectura, type LecturaCarga, MarcaDeLectura, TEXTO_ESPERA_PARA_GUARDAR } from "../CargaConLectura";
import { api } from "../api/cliente";
import type { components } from "../api/tipos";
import { encabezadoCsrf } from "../csrf";
import { CUENTA_INVALIDA, cuentaConError } from "../cuentaBancaria";
import {
  ErrorDeCampo,
  type ErroresDeCampo,
  erroresDe,
  faltaCompletar,
  hayErrores,
  marcaDe,
  resumenDe,
  SIN_ERRORES,
  sinElDe,
  soloDe,
} from "../ErrorDeCampo";
import { codigoDe, SIN_CONEXION } from "../fallas";
import {
  type Aviso,
  cambioLaOrden,
  cambioLoVisto,
  cuentaEnmascarada,
  ENCABEZADO_IDEMPOTENCIA,
  ESTADO,
  estadoSiNoEsActivo,
  type EstadoProveedor,
  mensajeDeError,
  momento,
  renovarClave,
  RUTA_AUTORIZACIONES_PROVEEDORES,
  RUTA_CONSULTA_PROVEEDORES,
  RUTA_PROVEEDORES,
  SEGUNDO_FACTOR_REQUERIDO,
  SESION_RECIENTE_REQUERIDA,
  sinLaFalla,
  sinPisarLoHecho,
  sobreLoNuevo,
  TEXTO_ESTADO_PROVEEDOR,
  TITULO_AUTORIZACIONES_PROVEEDORES,
} from "./comun";
import { ElegirConsorcio } from "./ElegirConsorcio";
import { AvisoPantalla, ConsorcioSinServicio, FallaDeLectura } from "./EstadoPantalla";
import { datosDelProveedor, FichaDelProveedor } from "./FichaDelProveedor";
import { ofreceEnAlguno, REQUISITOS, useAccionesPorEdificio, usePuede } from "../permisos";
import { RUTA_NUEVA, RUTA_PAGOS } from "../pagos/rutas";
import { PedirCodigo } from "./PedirCodigo";
import { exigirSesion, SinSesion, useConsorcio, useMontado } from "./useConsorcio";

// Página /egresos/proveedores (pantalla 1, aprobada el 30-10-2026; app 0048 y 0049): la lista con
// la cuenta enmascarada, la ficha con la historia de versiones, el alta y el cambio. Cambiar la
// cuenta pide confirmación explícita. Lo decide la API con `egresos.preparar_orden`.
//
// El alta empieza por el documento que respalda los datos (0088): la IA lee CUIT, razón social,
// domicilio, teléfono y correo, y los marca para revisar. La cuenta se escribe siempre a mano: la
// lectura nunca la trae. Desde una orden, llega con lo leído de la factura y vuelve a la orden.
//
// Informe de fricción, tanda 4: la ficha es la común, en una hoja sobre la lista, con las acciones
// debajo de los datos (PRV-01); el alta, el cambio y el CUIT, en su ventana (PRV-02); la historia
// dice solo qué cambió (PRV-03); la fila y la ficha no dicen lo que es por defecto (PRV-04); cambiar
// la cuenta es una pregunta corta (PRV-05); y guardar sin cambios no llega al servidor (C8P-07).
// «Quién ve los proveedores» se abre desde acá (MEN-05).

type Proveedor = components["schemas"]["Proveedor"];
type FichaProveedor = components["schemas"]["ProveedorConVersiones"];
type Version = components["schemas"]["VersionProveedor"];

/** La marca de la lista (RF50, app 0103): vencido marca, no excluye. */
export const MARCA_VENCIDA = "Documentación vencida";
export const MARCA_POR_VENCER = "Documentación por vencer";
export const MARCA_FALTANTE = "Le falta documentación legal";

export { RUTA_PROVEEDORES };
const CBU = "[0-9]{22}";
export const FALTA_CUIT = "Falta escribir el CUIT.";
export const FALTA_RAZON = "Falta escribir la razón social.";
/** Guardar un cambio sin cambiar nada: se dice acá y no se le pregunta al servidor (C8P-07). */
export const SIN_CAMBIOS_PARA_GUARDAR = "Todavía no hay cambios para guardar.";
export const OTROS_EDIFICIOS = "Proveedores de los otros edificios";

type Lectura =
  | { tipo: "cargando" }
  | { tipo: "sin_servicio" }
  | { tipo: "ok"; proveedores: Proveedor[]; vencida: ReadonlySet<string>; porVencer: ReadonlySet<string>; faltante: ReadonlySet<string> };

type Datos = {
  cuit: string;
  razon: string;
  rubro: string;
  contacto: string;
  telefono: string;
  whatsapp: string;
  correo: string;
  domicilio: string;
  emergencias: boolean;
  cuenta: string;
  estado: EstadoProveedor;
};
const VACIO: Datos = {
  cuit: "",
  razon: "",
  rubro: "",
  contacto: "",
  telefono: "",
  whatsapp: "",
  correo: "",
  domicilio: "",
  emergencias: false,
  cuenta: "",
  estado: "activo",
};

type Cambio = { modo: "cambio"; proveedor: Proveedor };
type CargaCuit = { modo: "cuit"; proveedor: Proveedor };
type Formulario = { modo: "alta" } | Cambio | CargaCuit;
type Pendiente = { acto: string; reintentar: () => void };

const opcional = (v: string) => (v.trim() === "" ? null : v.trim());

/** Un proveedor en el formulario. La cuenta completa solo llega a la revisión del Consejo: acá el
 * campo empieza vacío y vacío quiere decir «conservar la que tiene». */
const datosDe = (p: Proveedor): Datos => ({
  cuit: p.cuit ?? "",
  razon: p.razon_social,
  rubro: p.rubro ?? "",
  contacto: p.contacto ?? "",
  telefono: p.telefono ?? "",
  whatsapp: p.whatsapp ?? "",
  correo: p.correo ?? "",
  domicilio: p.domicilio ?? "",
  emergencias: p.atiende_emergencias,
  cuenta: "",
  estado: p.estado,
});

type CampoLeido = "cuit" | "razon" | "domicilio" | "telefono" | "correo";

/** El campo de la API de cada dato del formulario: un 422 o un error de negocio lo nombra así. */
const CAMPO_API: Record<keyof Datos, string> = {
  cuit: "cuit",
  razon: "razon_social",
  rubro: "rubro",
  contacto: "contacto",
  telefono: "telefono",
  whatsapp: "whatsapp",
  correo: "correo",
  domicilio: "domicilio",
  emergencias: "atiende_emergencias",
  cuenta: "cuenta_destino",
  estado: "estado",
};

/** Cómo se nombra cada campo en el resumen del error («Hay que corregir el CUIT: …»). */
const NOMBRES: Record<string, string> = {
  cuit: "el CUIT",
  razon_social: "la razón social",
  rubro: "el rubro",
  contacto: "el contacto",
  telefono: "el teléfono",
  whatsapp: "el WhatsApp",
  correo: "el correo",
  domicilio: "el domicilio",
  cuenta_destino: "la cuenta destino",
  estado: "el estado",
};

/** Los campos que muestra cada formulario: solo esos se pueden marcar. */
function nombresDe(f: Formulario): Record<string, string> {
  if (f.modo === "cuit") return { cuit: "el CUIT" };
  const { cuit, estado, ...comunes } = NOMBRES;
  return f.modo === "alta" ? { ...comunes, cuit: cuit ?? "" } : { ...comunes, estado: estado ?? "" };
}

/** Lo que trae la orden al dar de alta el proveedor de su factura (`AltaDesdeLaFactura`), con el
 * edificio de esa factura: en otro edificio no se usa. */
type DesdeLaFactura = { consorcio: string; datos: Partial<Datos>; documento: Cargado | null; orden: Record<string, unknown> | null };

/** Los datos del proveedor que leyó la IA; nunca la cuenta. */
function datosLeidos(l: Pick<LecturaCarga, "cuit" | "razon_social" | "domicilio" | "telefono" | "correo">): Partial<Datos> {
  return {
    ...(l.cuit === null ? {} : { cuit: l.cuit }),
    ...(l.razon_social === null ? {} : { razon: l.razon_social }),
    ...(l.domicilio === null ? {} : { domicilio: l.domicilio }),
    ...(l.telefono === null ? {} : { telefono: l.telefono }),
    ...(l.correo === null ? {} : { correo: l.correo }),
  };
}

const textoONulo = (v: unknown) => (typeof v === "string" ? v : null);

function desdeLaFactura(estado: unknown): DesdeLaFactura | null {
  if (typeof estado !== "object" || estado === null || !("alta" in estado)) return null;
  const { alta, documento, orden, consorcio } = estado as Record<string, unknown>;
  if (typeof alta !== "object" || alta === null || typeof consorcio !== "string") return null;
  const a = alta as Record<string, unknown>;
  const d = typeof documento === "object" && documento !== null ? (documento as Record<string, unknown>) : null;
  const cargado =
    d !== null && typeof d.documento === "string" && typeof d.version === "string" && typeof d.nombre === "string"
      ? { documento: d.documento, version: d.version, nombre: d.nombre }
      : null;
  return {
    consorcio,
    datos: datosLeidos({ cuit: textoONulo(a.cuit), razon_social: textoONulo(a.razon_social), domicilio: textoONulo(a.domicilio),
      telefono: textoONulo(a.telefono), correo: textoONulo(a.correo) }),
    documento: cargado,
    orden: typeof orden === "object" && orden !== null ? (orden as Record<string, unknown>) : null,
  };
}

/** Si se llegó desde Órdenes de pago porque faltaba un proveedor (`state.paraPreparar`). */
function vieneAPreparar(estado: unknown): boolean {
  return typeof estado === "object" && estado !== null && "paraPreparar" in estado && estado.paraPreparar === true;
}

/** En un cambio, el campo vacío conserva la cuenta del proveedor. La pantalla nunca la tiene
 * completa: lo pide con `conservar_cuenta` y el backend la toma de la versión esperada. */
function conservaCuenta(f: Formulario, d: Datos): boolean {
  return f.modo === "cambio" && d.cuenta.trim() === "";
}

/** La pregunta antes de cambiar la cuenta (decisión del 30-10, punto 21): corta y con el color de lo
 * pendiente, no de un error (PRV-05). */
export const AVISO_CUENTA = "¿Cambiar la cuenta? Las órdenes ya preparadas siguen con la anterior.";
export const SI_CAMBIAR_CUENTA = "Sí, cambiar la cuenta";
export const NO_CAMBIAR_CUENTA = "No, conservar la que tiene";

/** Los datos que trae cada versión y se pueden comparar, en el orden en que se dicen. */
const CAMBIOS_DE_VERSION: readonly ((v: Version) => string)[] = [
  (v) => v.razon_social,
  (v) => TEXTO_ESTADO_PROVEEDOR[v.estado],
  (v) => (v.rubro === null ? "sin rubro" : `rubro ${v.rubro}`),
  (v) => (v.contacto === null ? "sin contacto" : `contacto ${v.contacto}`),
  (v) => `cuenta ${cuentaEnmascarada(v.cuenta_ultimos_digitos)}`,
];

/**
 * La historia del proveedor (PRV-03): cada versión anterior, con su fecha y solo lo que era distinto
 * de la versión que la siguió. Sin el número de versión (es interno). Una versión que no difiere en
 * nada de lo que trae la API no se muestra.
 */
export function historiaDeVersiones(versiones: readonly Version[], vigente: number): string[] {
  const ordenadas = [...versiones].sort((a, b) => b.version - a.version);
  return ordenadas.flatMap((v, i) => {
    const siguiente = ordenadas[i - 1];
    if (v.version === vigente || siguiente === undefined) return [];
    const despues = CAMBIOS_DE_VERSION.map((dato) => dato(siguiente));
    const distintos = CAMBIOS_DE_VERSION.map((dato) => dato(v)).filter((valor, j) => valor !== despues[j]);
    return distintos.length === 0 ? [] : [`${momento(v.creada_en)}: ${distintos.join(", ")}`];
  });
}

/** Lo escrito no cambia nada del proveedor: la cuenta vacía la conserva (C8P-07). */
function sinCambios(escrito: Datos, actual: Datos): boolean {
  return (Object.keys(escrito) as (keyof Datos)[]).every((campo) => {
    const a = escrito[campo];
    const b = actual[campo];
    return typeof a === "string" && typeof b === "string" ? a.trim() === b.trim() : a === b;
  });
}

export const AVISO_PROVEEDOR_CAMBIO = "El proveedor cambió mientras estaba abierto. Hay que revisar los datos y guardar de nuevo.";

type PropsDelEdificio = ReturnType<typeof useConsorcio> & { vuelveAPreparar: boolean; factura: DesdeLaFactura | null };

// Cada edificio monta la pantalla de nuevo: nada de un edificio (estado, respuestas tardías,
// el reintento pendiente después del código) llega a la pantalla de otro.
export function Proveedores() {
  const elegido = useConsorcio(REQUISITOS.proveedores);
  const estado = useLocation().state;
  const vuelveAPreparar = useRef(vieneAPreparar(estado));
  const factura = useRef(desdeLaFactura(estado));
  // Lo leído de la factura es del edificio de la orden: al elegir otro edificio no se usa.
  const deEsteEdificio = factura.current?.consorcio === elegido.consorcio ? factura.current : null;
  return <ProveedoresDelEdificio key={elegido.consorcio} {...elegido} vuelveAPreparar={vuelveAPreparar.current} factura={deEsteEdificio} />;
}

/** La fila del proveedor (PRV-04): el estado solo si no es «Activo», y la cuenta que falta sin la
 * palabra «Cuenta» delante. «Sin CUIT» queda: es un pendiente con su botón en la ficha. */
export function resumenDeFila(p: Proveedor, marcas: readonly (string | null)[]): string {
  return [
    p.cuit ?? "Sin CUIT",
    estadoSiNoEsActivo(p.estado),
    p.cuenta_ultimos_digitos === null ? "Sin cuenta" : `Cuenta ${cuentaEnmascarada(p.cuenta_ultimos_digitos)}`,
    ...marcas,
  ]
    .filter((dato): dato is string => dato !== null)
    .join(" · ");
}

function ProveedoresDelEdificio({ pantalla, consorcio, setConsorcio, aEntrar, aInicio, reintentar, vuelveAPreparar, factura }: PropsDelEdificio) {
  const id = useId();
  const navegar = useNavigate();
  const [lectura, setLectura] = useState<Lectura>({ tipo: "cargando" });
  const [vuelta, setVuelta] = useState(0);
  const [ficha, setFicha] = useState<FichaProveedor | null>(null);
  /** La fila que abrió la ficha (comentario 21): al cerrarla, el foco vuelve a ella. */
  const fichas = useFichas<string>();
  const [formulario, setFormulario] = useState<Formulario | null>(factura === null ? null : { modo: "alta" });
  const [datos, setDatos] = useState<Datos>(factura === null ? VACIO : { ...VACIO, ...factura.datos });
  /** El documento que respalda el alta (0088), ya aprobado por el antivirus. */
  const [respaldo, setRespaldo] = useState<Cargado | null>(factura?.documento ?? null);
  /** El documento del alta subiéndose, en el antivirus o leyéndose: hasta que termine no se guarda. */
  const [respaldoEnEspera, setRespaldoEnEspera] = useState(false);
  /** Los campos que completó la lectura y la persona todavía no revisó. */
  const [deLaLectura, setDeLaLectura] = useState<ReadonlySet<CampoLeido>>(
    new Set(factura === null ? [] : (Object.keys(factura.datos) as CampoLeido[])),
  );
  const [confirmarCuenta, setConfirmarCuenta] = useState(false);
  const [enviando, setEnviando] = useState(false);
  const [aviso, setAviso] = useState<Aviso | null>(null);
  /** El rechazo del reintento que sigue al código (R10): sale fuera del envío de la ventana. */
  const [rechazoDelReintento, setRechazoDelReintento] = useState<string | null>(null);
  /** Los salteos que registró esta persona desde que abrió la pantalla (comentario 40). */
  const [salteosPropios, setSalteosPropios] = useState<ReadonlySet<number>>(new Set());
  const puede = usePuede(consorcio);
  const porEdificio = useAccionesPorEdificio();
  /** Los campos que la API rechazó, con qué hacer en cada uno (tanda 3, P-26). */
  const [errores, setErrores] = useState<ErroresDeCampo>(SIN_ERRORES);
  const [pendiente, setPendiente] = useState<Pendiente | null>(null);
  const clave = useRef<string | null>(null);
  const montado = useMontado();

  useEffect(() => {
    if (consorcio === "") return;
    let vigente = true;
    void api
      .GET("/api/v1/consorcios/{consorcio_id}/proveedores", { params: { path: { consorcio_id: consorcio } } })
      .then(({ data, error, response }) => {
        exigirSesion(response);
        if (!vigente) return;
        const codigo = codigoDe(error);
        if (data !== undefined)
          setLectura({
            tipo: "ok",
            proveedores: data.elementos,
            vencida: new Set(data.documentacion_vencida),
            porVencer: new Set(data.documentacion_por_vencer),
            faltante: new Set(data.documentacion_faltante),
          });
        else if (codigo === SEGUNDO_FACTOR_REQUERIDO)
          setPendiente({ acto: "ver los proveedores", reintentar: () => setVuelta((v) => v + 1) });
        else if (response.status === ESTADO.sinPermiso && codigo === "sin_permiso") aInicio();
        else {
          setLectura({ tipo: "sin_servicio" });
          setAviso(sinPisarLoHecho(mensajeDeError(response.status, codigo)));
        }
      })
      .catch((e: unknown) => {
        if (!vigente) return;
        if (e instanceof SinSesion) aEntrar();
        else {
          setLectura({ tipo: "sin_servicio" });
          setAviso(sinPisarLoHecho(SIN_CONEXION));
        }
      });
    return () => {
      vigente = false;
    };
  }, [consorcio, vuelta, aEntrar, aInicio]);

  function cerrarFicha(devolverFoco = true) {
    setFicha(null);
    fichas.cerrar(devolverFoco);
  }

  function volverAPedir() {
    setLectura({ tipo: "cargando" });
    cerrarFicha(false);
    setVuelta((v) => v + 1);
  }

  function reintentarLectura() {
    setAviso(sinLaFalla);
    volverAPedir();
  }

  /** Abre la ficha, o la vuelve a leer después de un cambio en una de sus secciones. */
  function verFicha(p: { id: string }) {
    setAviso(null);
    void api
      .GET("/api/v1/consorcios/{consorcio_id}/proveedores/{proveedor_id}", {
        params: { path: { consorcio_id: consorcio, proveedor_id: p.id } },
      })
      .then(({ data, error, response }) => {
        exigirSesion(response);
        if (!montado.current) return;
        if (data !== undefined) {
          setFicha(data);
          fichas.abrir(p.id);
        } else if (codigoDe(error) === SEGUNDO_FACTOR_REQUERIDO)
          setPendiente({ acto: "ver el proveedor", reintentar: () => verFicha(p) });
        else setAviso({ texto: mensajeDeError(response.status, codigoDe(error)), ok: false });
      })
      .catch((e: unknown) => (e instanceof SinSesion ? aEntrar() : setAviso({ texto: SIN_CONEXION, ok: false })));
  }

  function abrir(f: Formulario) {
    clave.current = null;
    setConfirmarCuenta(false);
    setAviso(null);
    setErrores(SIN_ERRORES);
    setRechazoDelReintento(null);
    setDatos(f.modo === "alta" ? VACIO : datosDe(f.proveedor));
    setRespaldo(null);
    setDeLaLectura(new Set());
    setFormulario(f);
  }

  function revisado(campo: keyof Datos) {
    setDeLaLectura((marcados) => {
      if (!marcados.has(campo as CampoLeido)) return marcados;
      const otros = new Set(marcados);
      otros.delete(campo as CampoLeido);
      return otros;
    });
  }

  function leerDocumento(l: LecturaCarga) {
    const leidos = datosLeidos(l);
    clave.current = null;
    setDatos((d) => ({ ...d, ...leidos }));
    setDeLaLectura(new Set(Object.keys(leidos) as CampoLeido[]));
  }

  function cerrar() {
    clave.current = null;
    setConfirmarCuenta(false);
    setErrores(SIN_ERRORES);
    setRechazoDelReintento(null);
    setFormulario(null);
  }

  function cambiar<K extends keyof Datos>(campo: K, valor: Datos[K]) {
    revisado(campo);
    clave.current = null;
    setConfirmarCuenta(false);
    // Lo que se corrige deja de estar marcado; los otros campos siguen con su aviso.
    setErrores((e) => sinElDe(e, CAMPO_API[campo]));
    setDatos((d) => ({ ...d, [campo]: valor }));
  }

  /** El botón principal de la ventana, siempre activo (decisión 4): lo que falta se marca en su campo. */
  function enviar(f: Formulario): FinDeEnvio | Promise<FinDeEnvio> {
    setRechazoDelReintento(null);
    // Dar de alta ahora mandaría el proveedor sin el documento que todavía no terminó: la línea de
    // arriba del botón dice por qué no sale.
    if (f.modo === "alta" && respaldoEnEspera) return "seguir";
    const faltan = faltaCompletar({
      ...(f.modo === "cambio" ? {} : { cuit: [datos.cuit, FALTA_CUIT] as const }),
      ...(f.modo === "cuit" ? {} : { razon_social: [datos.razon, FALTA_RAZON] as const }),
    });
    if (hayErrores(faltan)) {
      setErrores((actuales) => ({ ...actuales, ...faltan }));
      return "seguir";
    }
    // El CUIT con el verificador mal ya se ve marcado en el campo: no se manda.
    if (f.modo !== "cambio" && cuitConError(datos.cuit.trim())) return "seguir";
    // Lo mismo con la cuenta (P-26): el verificador mal ya se ve junto al campo.
    if (f.modo !== "cuit" && cuentaConError(datos.cuenta.trim())) return "seguir";
    // Sin cambios, el servidor diría que no hay nada para guardar: se dice acá (C8P-07).
    if (f.modo === "cambio" && sinCambios(datos, datosDe(f.proveedor))) return { rechazo: SIN_CAMBIOS_PARA_GUARDAR };
    const cambiaCuenta = f.modo === "cambio" && !conservaCuenta(f, datos);
    if (cambiaCuenta && !confirmarCuenta) {
      setConfirmarCuenta(true);
      return "seguir";
    }
    return pedir(f);
  }

  /** El proveedor cambió mientras se editaba: el formulario sigue con lo escrito sobre la versión
   * nueva, y cambiar la cuenta se vuelve a confirmar. Otra falla lo deja como está: reintentar
   * vuelve a pasar por acá. */
  function seguirSobreLaVersionNueva(f: Cambio) {
    void api
      .GET("/api/v1/consorcios/{consorcio_id}/proveedores/{proveedor_id}", {
        params: { path: { consorcio_id: consorcio, proveedor_id: f.proveedor.id } },
      })
      .then(({ data, response }) => {
        exigirSesion(response);
        if (!montado.current) return;
        if (data !== undefined) {
          const { proveedor } = data;
          setDatos((d) => sobreLoNuevo(d, datosDe(f.proveedor), datosDe(proveedor)));
          setFormulario({ modo: "cambio", proveedor });
          setFicha(data);
          setConfirmarCuenta(false);
        } else if (response.status === ESTADO.noEncontrado) {
          cerrar();
          volverAPedir();
        }
      })
      .catch((e: unknown) => (e instanceof SinSesion ? aEntrar() : setRechazoDelReintento(SIN_CONEXION)));
  }

  /**
   * Manda el formulario y dice cómo terminó, para la ventana. Lo que sale bien cierra la ventana y la
   * ficha, y el resultado queda arriba de la lista recargada; un rechazo queda en la ventana.
   */
  async function pedir(f: Formulario): Promise<FinDeEnvio> {
    if (!montado.current) return "cerrar";
    clave.current ??= crypto.randomUUID();
    const encabezado = { [ENCABEZADO_IDEMPOTENCIA]: clave.current };
    const comunes = {
      razon_social: datos.razon.trim(),
      rubro: opcional(datos.rubro),
      contacto: opcional(datos.contacto),
      telefono: opcional(datos.telefono),
      whatsapp: opcional(datos.whatsapp),
      correo: opcional(datos.correo),
      domicilio: opcional(datos.domicilio),
      atiende_emergencias: datos.emergencias,
      cuenta_destino: conservaCuenta(f, datos) ? null : opcional(datos.cuenta),
    };
    setEnviando(true);
    setAviso(null);
    try {
      const { data, error, response } = await (f.modo === "cuit"
        ? api.POST("/api/v1/consorcios/{consorcio_id}/proveedores/{proveedor_id}/cuit", {
            headers: encabezadoCsrf(),
            params: { path: { consorcio_id: consorcio, proveedor_id: f.proveedor.id }, header: encabezado },
            body: { cuit: datos.cuit.trim(), version_esperada: f.proveedor.version },
          })
        : f.modo === "alta"
          ? api.POST("/api/v1/consorcios/{consorcio_id}/proveedores", {
              headers: encabezadoCsrf(),
              params: { path: { consorcio_id: consorcio }, header: encabezado },
              body: { ...comunes, cuit: datos.cuit.trim(), documento_id: respaldo?.documento ?? null },
            })
          : api.POST("/api/v1/consorcios/{consorcio_id}/proveedores/{proveedor_id}/versiones", {
              headers: encabezadoCsrf(),
              params: { path: { consorcio_id: consorcio, proveedor_id: f.proveedor.id }, header: encabezado },
              body: {
                ...comunes,
                documento_id: f.proveedor.documento_id,
                estado: datos.estado,
                version_esperada: f.proveedor.version,
                conservar_cuenta: conservaCuenta(f, datos),
              },
            }));
      exigirSesion(response);
      if (data !== undefined) {
        cerrar();
        // Solo lo que se hizo (PRV-06): si una orden aprobada necesita el CUIT, lo dice su ficha.
        const hecho =
          f.modo === "alta"
            ? "Listo: el proveedor quedó dado de alta."
            : f.modo === "cuit"
              ? `Listo: el CUIT de ${f.proveedor.razon_social} quedó cargado.`
              : `Listo: los cambios de ${comunes.razon_social} quedaron guardados.`;
        if (f.modo === "alta" && vuelveAPreparar) {
          navegar(RUTA_NUEVA, { state: { ...(factura?.orden ?? {}), consorcio, proveedor: data.id, aviso: hecho } });
          return "cerrar";
        }
        setAviso({ texto: hecho, ok: true });
        volverAPedir();
        return "cerrar";
      }
      const codigo = codigoDe(error);
      if (codigo === SEGUNDO_FACTOR_REQUERIDO) {
        setPendiente({ acto: "guardar el proveedor", reintentar: () => reintentarDespuesDelCodigo(f) });
        return "seguir";
      }
      // La sesión vieja la pide la hoja que se abre encima: lo escrito queda para repetir el paso.
      if (codigo === SESION_RECIENTE_REQUERIDA) return "seguir";
      if (renovarClave(response.status, codigo)) clave.current = null;
      if (f.modo === "cambio" && cambioLaOrden(codigo)) {
        seguirSobreLaVersionNueva(f);
        // La lista de atrás también quedó vieja; sin «Cargando», para no sacar el formulario.
        setVuelta((v) => v + 1);
        return { rechazo: AVISO_PROVEEDOR_CAMBIO };
      }
      // Lo que se veía cambió (p. ej. `cuit_ya_cargado`: otra sesión ya cargó el CUIT): el formulario
      // viejo se cierra y la lista se vuelve a pedir, aunque la API nombre un campo (Codex en #236).
      const cambioLoQueSeVe = cambioLoVisto(response.status, codigo) && codigo !== "sin_cambios" && codigo !== "cuit_repetido";
      if (cambioLoQueSeVe) {
        cerrar();
        setAviso({ texto: mensajeDeError(response.status, codigo), ok: false });
        volverAPedir();
        return "cerrar";
      }
      // Si no, el rechazo que nombra campos del formulario se dice junto a cada uno (tanda 3, P-26).
      const nombres = nombresDe(f);
      const marcados = soloDe(erroresDe(error, mensajeDeError(response.status, codigo)), nombres);
      if (Object.keys(marcados).length > 0) {
        setErrores(marcados);
        return { rechazo: resumenDe(marcados, nombres) };
      }
      return { rechazo: mensajeDeError(response.status, codigo) };
    } catch (e: unknown) {
      if (e instanceof SinSesion) {
        // La sesión se pide encima: lo escrito queda en la ventana para repetir el paso.
        aEntrar();
        return "seguir";
      }
      return { rechazo: SIN_CONEXION };
    } finally {
      setEnviando(false);
    }
  }

  /** Verificado el código, el mismo pedido sale solo; si no sale bien, el motivo queda en la ventana. */
  function reintentarDespuesDelCodigo(f: Formulario) {
    void pedir(f).then((fin) => {
      if (typeof fin === "object" && montado.current) setRechazoDelReintento(fin.rechazo);
    });
  }

  /** La cuenta con el verificador mal se marca apenas tiene los 22 números; si no, lo que dijo la API. */
  const errorDeCuenta = cuentaConError(datos.cuenta.trim()) ? CUENTA_INVALIDA : errores.cuenta_destino;

  const marca = (campo: CampoLeido) => (deLaLectura.has(campo) ? <MarcaDeLectura alRevisar={() => revisado(campo)} /> : null);
  // La ficha muestra el proveedor de la fila abierta: si una recarga la cierra, se va.
  const enFicha = ficha !== null && fichas.abierta === ficha.proveedor.id ? ficha : null;
  const deOtrosEdificios = porEdificio !== null && ofreceEnAlguno(porEdificio, REQUISITOS.consultaProveedores, REQUISITOS.proveedores);

  return (
    <>
      <CabeceraInterna titulo="Proveedores" volver={RUTA_PAGOS} destino="Pagos" />
      <ElegirConsorcio pantalla={pantalla} consorcio={consorcio} deshabilitado={enviando}
        elegir={setConsorcio} />

      {pantalla.tipo === "cargando" && <p role="status">Cargando…</p>}
      {pantalla.tipo === "sin_consorcios" && <p className="tarjeta">Esta cuenta no tiene edificios en Consorcio en Mano.</p>}
      <ConsorcioSinServicio pantalla={pantalla} reintentar={reintentar} />

      <AvisoPantalla aviso={aviso} />

      {pendiente !== null && (
        <PedirCodigo
          acto={pendiente.acto}
          alVerificar={() => {
            const { reintentar } = pendiente;
            setPendiente(null);
            reintentar();
          }}
          alCancelar={() => {
            setPendiente(null);
            setEnviando(false);
          }}
        />
      )}

      {pantalla.tipo === "lista" && (
        <>
          {lectura.tipo === "cargando" && <p role="status">Cargando los proveedores…</p>}
          {lectura.tipo === "sin_servicio" && <FallaDeLectura alReintentar={reintentarLectura} avisar={setAviso} />}
          {lectura.tipo === "ok" && (
            <>
              <p className="acciones-de-proveedores">
                <button type="button" onClick={() => abrir({ modo: "alta" })}>
                  Dar de alta un proveedor
                </button>
                {/* MEN-05: quién ve los proveedores se decide desde acá, no desde otra entrada del menú. */}
                {puede("proveedores.autorizar") && (
                  <Link to={RUTA_AUTORIZACIONES_PROVEEDORES} state={{ consorcio }} className="enlace-suelto">
                    {TITULO_AUTORIZACIONES_PROVEEDORES}
                  </Link>
                )}
                {/* Quien gestiona en un edificio y solo consulta en otro: la consulta, desde acá. */}
                {deOtrosEdificios && (
                  <Link to={RUTA_CONSULTA_PROVEEDORES} className="enlace-suelto">
                    {OTROS_EDIFICIOS}
                  </Link>
                )}
              </p>
              {lectura.proveedores.length === 0 ? (
                <p className="tarjeta">El edificio no tiene proveedores cargados.</p>
              ) : (
                // La fila entera abre la ficha del proveedor; las acciones están en la ficha.
                <ul className="filas-registro" aria-label="Proveedores">
                  {lectura.proveedores.map((p) => (
                    <li key={p.id}>
                      <FilaDeRegistro
                        titulo={p.razon_social}
                        refFila={fichas.refFila(p.id)}
                        abierta={fichas.abierta === p.id}
                        deshabilitada={enviando}
                        alAbrir={() => verFicha(p)}
                        resumen={resumenDeFila(p, [
                          lectura.vencida.has(p.id) ? MARCA_VENCIDA : lectura.porVencer.has(p.id) ? MARCA_POR_VENCER : null,
                          lectura.faltante.has(p.id) ? MARCA_FALTANTE : null,
                        ])}
                      />
                    </li>
                  ))}
                </ul>
              )}
            </>
          )}

          {enFicha !== null && fichaDe(enFicha)}
          {formulario?.modo === "alta" && ventanaDeDatos(formulario)}
        </>
      )}
    </>
  );

  /** La ficha común (PRV-01): datos, enseguida las acciones, y las secciones. */
  function fichaDe(f: FichaProveedor) {
    const p = f.proveedor;
    const historia = historiaDeVersiones(f.versiones, p.version);
    return (
      <FichaDelProveedor
        consorcio={consorcio}
        ficha={f}
        datos={datosDelProveedor(p, { sinCuit: "Sin CUIT", cuenta: cuentaEnmascarada(p.cuenta_ultimos_digitos) })}
        puedeCambiar
        puedeCargar={f.puede_cargar_documentacion}
        puedeSaltear={puede("egresos.saltear_documentacion")}
        salteosPropios={salteosPropios}
        alRegistrarSalteo={(numero) => setSalteosPropios((propios) => new Set([...propios, numero]))}
        ocupada={enviando}
        aEntrar={aEntrar}
        alCambiar={() => {
          verFicha(p);
          // Las marcas de la lista pueden haber cambiado: se vuelve a leer sin sacar la ficha.
          setVuelta((v) => v + 1);
        }}
        alCerrar={() => cerrarFicha()}
        acciones={
          <p className="ficha-registro__acciones">
            {/* Un solo botón principal (P-27): cargar el CUIT mientras falte; si no, cambiar los datos. */}
            {p.cuit === null && (
              <button type="button" onClick={() => abrir({ modo: "cuit", proveedor: p })}>
                Cargar el CUIT
              </button>
            )}
            <button type="button" className={p.cuit === null ? "secundario" : undefined} onClick={() => abrir({ modo: "cambio", proveedor: p })}>
              Cambiar los datos
            </button>
          </p>
        }
        historia={
          historia.length > 0 && (
            <details className="historial">
              <summary>Versiones anteriores</summary>
              <ol className="versiones">
                {historia.map((renglon) => (
                  <li key={renglon}>{renglon}</li>
                ))}
              </ol>
            </details>
          )
        }
        ventana={formulario !== null && formulario.modo !== "alta" && formulario.proveedor.id === p.id && ventanaDeDatos(formulario)}
      />
    );
  }

  /** El alta, el cambio y la carga del CUIT, cada uno en su ventana (PRV-02). */
  function ventanaDeDatos(f: Formulario) {
    const reintento = rechazoDelReintento !== null && (
      <p role="alert" className="aviso-error">
        {rechazoDelReintento}
      </p>
    );
    if (f.modo === "cuit")
      return (
        <VentanaDeFormulario titulo={`Cargar el CUIT de ${f.proveedor.razon_social}`} textoEnviar="Guardar el CUIT"
          alEnviar={() => enviar(f)} alCerrar={cerrar}>
          <p>Se carga una sola vez y después no cambia: conviene revisarlo antes de guardar.</p>
          <CampoCuit id={`${id}-cuit`} valor={datos.cuit} deshabilitado={false} foco error={errores.cuit} alCambiar={(v) => cambiar("cuit", v)} />
          {reintento}
        </VentanaDeFormulario>
      );
    const alta = f.modo === "alta";
    return (
      <VentanaDeFormulario
        titulo={alta ? "Alta de un proveedor" : `Cambiar ${f.proveedor.razon_social}`}
        textoEnviar={confirmarCuenta ? SI_CAMBIAR_CUENTA : alta ? "Dar de alta el proveedor" : "Guardar los cambios"}
        ancha
        alEnviar={() => enviar(f)}
        alCerrar={cerrar}
      >
        {alta && (
          <>
            <CargaConLectura consorcio={consorcio} tipo="proveedor" titulo="Documento del proveedor" de="del proveedor"
              textoVer="Ver el documento" deshabilitada={enviando} inicial={factura?.documento ?? null} aEntrar={aEntrar}
              alCargar={(c) => {
                clave.current = null;
                setRespaldo(c);
              }}
              alLeer={leerDocumento} alEsperar={setRespaldoEnEspera} />
            <CampoCuit id={`${id}-cuit`} valor={datos.cuit} deshabilitado={false} foco error={errores.cuit} alCambiar={(v) => cambiar("cuit", v)} />
            {marca("cuit")}
          </>
        )}
        <label htmlFor={`${id}-razon`}>Razón social</label>
        <input {...marcaDe(`${id}-razon`, errores.razon_social)} id={`${id}-razon`} data-foco={alta ? undefined : true} required maxLength={200}
          value={datos.razon} onChange={(e) => cambiar("razon", e.target.value)} />
        <ErrorDeCampo idCampo={`${id}-razon`} mensaje={errores.razon_social} />
        {marca("razon")}
        <label htmlFor={`${id}-rubro`}>Rubro (opcional)</label>
        <input {...marcaDe(`${id}-rubro`, errores.rubro)} id={`${id}-rubro`} maxLength={100} value={datos.rubro}
          onChange={(e) => cambiar("rubro", e.target.value)} />
        <ErrorDeCampo idCampo={`${id}-rubro`} mensaje={errores.rubro} />
        <label htmlFor={`${id}-contacto`}>Contacto (opcional)</label>
        <input {...marcaDe(`${id}-contacto`, errores.contacto)} id={`${id}-contacto`} maxLength={200} value={datos.contacto}
          onChange={(e) => cambiar("contacto", e.target.value)} />
        <ErrorDeCampo idCampo={`${id}-contacto`} mensaje={errores.contacto} />
        <label htmlFor={`${id}-telefono`}>Teléfono (opcional)</label>
        <input {...marcaDe(`${id}-telefono`, errores.telefono)} id={`${id}-telefono`} type="tel" autoComplete="off" maxLength={50} value={datos.telefono}
          onChange={(e) => cambiar("telefono", e.target.value)} />
        <ErrorDeCampo idCampo={`${id}-telefono`} mensaje={errores.telefono} />
        {marca("telefono")}
        <label htmlFor={`${id}-whatsapp`}>WhatsApp (opcional)</label>
        <input {...marcaDe(`${id}-whatsapp`, errores.whatsapp)} id={`${id}-whatsapp`} type="tel" autoComplete="off" maxLength={50} value={datos.whatsapp}
          onChange={(e) => cambiar("whatsapp", e.target.value)} />
        <ErrorDeCampo idCampo={`${id}-whatsapp`} mensaje={errores.whatsapp} />
        <label htmlFor={`${id}-correo`}>Correo (opcional)</label>
        <input {...marcaDe(`${id}-correo`, errores.correo)} id={`${id}-correo`} type="email" autoComplete="off" maxLength={254} value={datos.correo}
          onChange={(e) => cambiar("correo", e.target.value)} />
        <ErrorDeCampo idCampo={`${id}-correo`} mensaje={errores.correo} />
        {marca("correo")}
        <label htmlFor={`${id}-domicilio`}>Domicilio (opcional)</label>
        <input {...marcaDe(`${id}-domicilio`, errores.domicilio)} id={`${id}-domicilio`} autoComplete="off" maxLength={200} value={datos.domicilio}
          onChange={(e) => cambiar("domicilio", e.target.value)} />
        <ErrorDeCampo idCampo={`${id}-domicilio`} mensaje={errores.domicilio} />
        {marca("domicilio")}
        <label className="opcion">
          <input type="checkbox" checked={datos.emergencias} onChange={(e) => cambiar("emergencias", e.target.checked)} />
          Atiende emergencias
        </label>
        <label htmlFor={`${id}-cuenta`}>
          {alta ? "Cuenta destino: CBU o CVU de 22 números (opcional)" : "Cuenta destino: CBU o CVU de 22 números. Cambia solo si se escribe otra"}
        </label>
        {/* La cuenta que tiene va dentro del campo (comentario 27): vacío la conserva. */}
        <input id={`${id}-cuenta`} inputMode="numeric" pattern={CBU} value={datos.cuenta}
          {...marcaDe(`${id}-cuenta`, errorDeCuenta)}
          placeholder={alta ? undefined : cuentaEnmascarada(f.proveedor.cuenta_ultimos_digitos)}
          onChange={(e) => cambiar("cuenta", e.target.value)} />
        <ErrorDeCampo idCampo={`${id}-cuenta`} mensaje={errorDeCuenta} />
        {!alta && (
          <>
            <label htmlFor={`${id}-estado`}>Estado</label>
            <select id={`${id}-estado`} value={datos.estado} {...marcaDe(`${id}-estado`, errores.estado)}
              onChange={(e) => cambiar("estado", e.target.value as EstadoProveedor)}>
              {(Object.keys(TEXTO_ESTADO_PROVEEDOR) as EstadoProveedor[]).map((e) => (
                <option key={e} value={e}>{TEXTO_ESTADO_PROVEEDOR[e]}</option>
              ))}
            </select>
            <ErrorDeCampo idCampo={`${id}-estado`} mensaje={errores.estado} />
          </>
        )}
        {/* Una pregunta, no un error (PRV-05): «Sí» es el botón principal; «No» deja la cuenta que tiene. */}
        {confirmarCuenta && (
          <p className="aviso-pendiente pregunta-cuenta" role="status">
            {AVISO_CUENTA}{" "}
            <button type="button" className="secundario" onClick={() => cambiar("cuenta", "")}>
              {NO_CAMBIAR_CUENTA}
            </button>
          </p>
        )}
        {alta && respaldoEnEspera && (
          <p id={`${id}-espera`} className="aviso-info">
            {TEXTO_ESPERA_PARA_GUARDAR}
          </p>
        )}
        {reintento}
      </VentanaDeFormulario>
    );
  }
}
