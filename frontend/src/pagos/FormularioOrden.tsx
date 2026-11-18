import { type FormEvent, useEffect, useId, useRef, useState } from "react";
import { Link } from "react-router";

import { api } from "../api/cliente";
import type { components } from "../api/tipos";
import {
  type Cargado,
  CargaConLectura,
  type LecturaCarga,
  MarcaDeDiferencia,
  MarcaDeFalta,
  TEXTO_ESPERA_PARA_GUARDAR,
  useVersionVisible,
} from "../CargaConLectura";
import { encabezadoCsrf } from "../csrf";
import { CampoImporte } from "../egresos/CampoImporte";
import {
  aCentavos,
  aTexto,
  cambioLaOrden,
  cambioLoVisto,
  ENCABEZADO_IDEMPOTENCIA,
  ESTADO,
  importe,
  mensajeDeActo,
  mensajeDeError,
  momentoAlFinal,
  sinLaFalla,
  sinPisarLoHecho,
  sobreLoNuevo,
} from "../egresos/comun";
import { ComandoNoDisponible } from "../ComandoNoDisponible";
import { FallaDeLectura } from "../egresos/EstadoPantalla";
import { PedidoDeSalteo } from "../egresos/PedidoSalteo";
import { RUTA_PROVEEDORES } from "../egresos/Proveedores";
import { exigirSesion, type MonedasDelEdificio, SinSesion } from "../egresos/useConsorcio";
import { ErrorDeCampo, type ErroresDeCampo, faltaCompletar, hayErrores, marcaDe, SIN_ERRORES, sinElDe } from "../ErrorDeCampo";
import { codigoDe, SIN_CONEXION } from "../fallas";
import { VentanaDeFormulario } from "../VentanaDeFormulario";
import { leerComoQuedo, TEXTO_ENVIADA } from "./comoQuedo";
import { rutaFicha } from "./rutas";
import {
  AVISO_MODIFICAR,
  CON_SALTEO,
  FALTA_CONCEPTO,
  FALTA_IMPORTE,
  FALTA_PROVEEDOR,
  fechaCorta,
  GRUPO_SIN_ORDEN,
  GUARDAR_IGUAL,
  MARCA_CON_SALTEO,
  NOMBRE_ESTADO,
  PROVEEDOR_INACTIVO,
  SIN_CAMBIOS,
  SIN_CUENTA_DESTINO,
  SIN_DOCUMENTACION,
  SALTEO_PROPIO,
  textoAbrirFicha,
  textoContratoExcedido,
  textoLeidoInactivo,
  textoLeidoSinOrden,
  textoParecidaAntes,
  TITULO_PARECIDA,
  VER_LA_OTRA,
} from "./textos";
import type { ActoEnCurso } from "./useActo";

// Preparar una orden o guardar una versión nueva: el proveedor se elige de la lista, nunca se
// escribe a mano. Con la orden abierta (`modificar`), el formulario sigue a la versión que muestra
// la ficha: si otra persona la cambió, lo que se escribió queda sobre la versión nueva.
//
// Arriba, la factura (0088): elegir el archivo la sube, el antivirus la revisa y la IA lee importe,
// moneda, número, concepto y el CUIT del proveedor. Lo leído llena el formulario y se avisa solo lo
// que no coincide o falta (comentario 32 del propietario, 12-11-2026; como D-25 en el registro del
// pago): un campo que ya decía otra cosa queda marcado con lo que decía, hasta que la persona lo
// cambia o lo da por revisado, y un obligatorio que la lectura no completó, hasta que se escribe. Lo
// que coincide o llena un campo vacío no se marca: la IA propone y guardar es confirmar. Guardar
// manda la versión exacta del archivo que se vio
// (`factura_version_id`): otro archivo subido después al mismo documento no cambia lo aprobado.
//
// Una orden nueva se guarda y se envía a aprobar en un solo acto (decisión del propietario del
// 12-11-2026, P-10): son dos pedidos seguidos, y si el envío pide el segundo factor se pide ahí
// mismo. «Guardar como borrador» queda como salida secundaria. Si el envío no sale, la orden ya
// quedó guardada: se va a su ficha, que lo dice y tiene «Enviar a aprobar».
//
// Comentario 41 del propietario (flujos 18-11, tanda 1, criterio C9): elegido un proveedor al que no
// se le puede preparar la orden, quedan el desplegable, el aviso con el enlace a SU ficha y
// «Cancelar» (FL-ORD-01); en el desplegable van aparte (FL-ORD-03); la lectura de la factura no los
// elige sola (FL-ORD-02); la ficha vuelve a la orden con lo escrito y la factura, y la lista se vuelve
// a leer al volver a la pestaña (FL-ORD-04).

type Proveedor = components["schemas"]["Proveedor"];
type Contrato = components["schemas"]["Contrato"];
type Version = components["schemas"]["VersionOrdenPago"];
type OrdenParecida = components["schemas"]["OrdenParecida"];
type ModoExigencia = components["schemas"]["ModoExigencia"];

export const MONEDAS = ["ARS", "USD"] as const;
/** Lo que ve la persona al elegir la moneda de una orden (P-14): el código queda para el servidor. */
const NOMBRE_MONEDA: Record<(typeof MONEDAS)[number], string> = { ARS: "Pesos", USD: "Dólares" };
/** Rechazos que se arreglan cambiando lo escrito: el formulario sigue abierto con sus datos. */
const SE_CORRIGE_EN_EL_FORMULARIO = new Set(["sin_cambios", "proveedor_inactivo"]);

type Lectura =
  | { tipo: "cargando" }
  | { tipo: "sin_servicio" }
  | {
      tipo: "ok";
      proveedores: Proveedor[];
      contratos: Contrato[];
      /** Los proveedores a los que les falta documentación legal (app 0104) y si eso bloquea (0106). */
      sinDocumentacion: ReadonlySet<string>;
      modo: ModoExigencia;
      /** Los bloqueados con un salteo por urgencia vigente que le sirve a quien prepara (app 0109). */
      conSalteo: ReadonlySet<string>;
      /** Los bloqueados cuyo salteo vigente lo registró quien prepara: la base no lo toma (app 0109). */
      salteoPropio: ReadonlySet<string>;
      /** Hasta cuándo sirve el salteo de cada uno de `conSalteo` (FL-SAL-01, app 0110). */
      venceSalteo: ReadonlyMap<string, string>;
      /** Los bloqueados cuyo último salteo venció sin usarse (FL-SAL-04, app 0110). */
      vencidoSinUsar: ReadonlySet<string>;
    };

/** Lo que el formulario toma de la lista de proveedores: la lista y sus bloqueos. */
function deLaLista(l: components["schemas"]["ListaProveedores"]) {
  return {
    proveedores: l.elementos,
    sinDocumentacion: new Set(l.documentacion_faltante),
    modo: l.modo_exigencia,
    conSalteo: new Set(l.con_salteo),
    salteoPropio: new Set(l.salteo_de_quien_prepara),
    venceSalteo: new Map(l.vence_salteo.map((v) => [v.proveedor_id, v.vence_en])),
    vencidoSinUsar: new Set(l.salteo_vencido_sin_usar),
  };
}

/** FL-SAL-01 (tanda 9): el aviso del salteo que sirve dice hasta cuándo. */
export const textoConSalteo = (vence: string | undefined) => (vence === undefined ? CON_SALTEO : `${CON_SALTEO} Sirve hasta el ${momentoAlFinal(vence)}`);
/** FL-SAL-04 (tanda 9): el último salteo venció sin que se preparara la orden. */
export const SALTEO_VENCIDO_SIN_USAR = "El último salteo por urgencia de este proveedor venció sin usarse.";

/** Qué pasa con el proveedor bloqueado (informe de fricción, C8P-01 y C8P-02): sin un salteo que le
 * sirva a quien prepara, la base no deja crear la orden, ni como borrador. Lo dice la lista de
 * proveedores, con la misma regla que la base (`salteo_de_quien_prepara`). */
type SalteoDelElegido = "con_salteo" | "salteo_propio" | "sin_salteo";

type Datos = {
  proveedor: string;
  importe: string;
  moneda: string;
  concepto: string;
  comprobante: string;
  reserva: boolean;
  /** El contrato o abono que la respalda ("" = ninguno): solo así es rutinaria. */
  contrato: string;
};

const opcional = (v: string) => (v.trim() === "" ? null : v.trim());
/** El CUIT para comparar: solo sus números (la factura puede traerlo con guiones). */
const soloDigitos = (cuit: string | null) => (cuit ?? "").replace(/\D/g, "");

/** Los contratos que puede declarar una orden: vigentes, del proveedor y de la moneda elegidos.
 * La base vuelve a verificar que lo cubra (alcance, importe máximo, fechas) al evaluarla. */
export function contratosAplicables(contratos: Contrato[], proveedor: string, moneda: string): Contrato[] {
  return contratos.filter((k) => k.estado === "vigente" && k.proveedor_id === proveedor && k.moneda === moneda);
}

/** Si el contrato cubre el importe escrito (C8P-04): uno con un máximo menor no se ofrece. Sin un
 * importe que se entienda, todavía no se descarta ninguno. */
export const cubreElImporte = (k: Contrato, centavos: number | null) => centavos === null || centavos <= k.importe_maximo_centavos;

/** Las monedas del edificio que el formulario ofrece, en el orden de `MONEDAS` (P-14). */
const monedasOfrecidas = (monedas: readonly string[]) => MONEDAS.filter((m) => monedas.includes(m));
/** La factura leída en una moneda que el edificio no tiene habilitada (propietario, 16-11-2026): el
 * importe leído no es en esa moneda, y en un edificio solo en pesos no hay campo para cambiarla. */
export const textoMonedaAjena = (moneda: string, soloPesos: boolean) => {
  const nombre = (MONEDAS as readonly string[]).includes(moneda)
    ? NOMBRE_MONEDA[moneda as (typeof MONEDAS)[number]].toLocaleLowerCase("es-AR")
    : moneda;
  return soloPesos
    ? `La factura está en ${nombre} y este edificio opera solo en pesos: el importe leído no está en pesos. Revisar antes de guardar.`
    : `La factura está en ${nombre} y este edificio no opera en esa moneda: revisar antes de guardar.`;
};

/** Si se pregunta la moneda: solo cuando el edificio opera en más de una. */
const conCampoMoneda = (monedas: readonly string[]) => monedasOfrecidas(monedas).length > 1;

/** Una orden nueva va en la primera moneda del edificio: el peso, que el servidor pone siempre primero. */
const datosVacios = (proveedor: string, moneda: string): Datos => ({
  proveedor,
  importe: "",
  moneda,
  concepto: "",
  comprobante: "",
  reserva: false,
  contrato: "",
});

/** Una versión en el formulario; un contrato que ya no aplica no se trae. */
function datosDe(v: Version, contratos: Contrato[]): Datos {
  const aplica = contratosAplicables(contratos, v.proveedor_id, v.moneda).some((k) => k.id === v.contrato_id);
  return {
    proveedor: v.proveedor_id,
    importe: aTexto(v.importe_centavos),
    moneda: v.moneda,
    concepto: v.concepto,
    comprobante: v.comprobante ?? "",
    reserva: v.usa_fondo_reserva,
    contrato: aplica && v.contrato_id !== null ? v.contrato_id : "",
  };
}

/** Con qué abre una orden nueva. */
export type OrdenInicial = {
  proveedor?: string;
  importe?: string;
  moneda?: string;
  concepto?: string;
  comprobante?: string;
  /** Lo que vuelve de Proveedores (FL-PRV-02, FL-ORD-04): nada de lo escrito se pierde en el viaje. */
  reserva?: boolean;
  contrato?: string;
  /** La factura ya subida (vuelve del alta del proveedor o de su ficha). */
  factura?: Cargado;
};

/** Lo que lleva el enlace a la ficha de un proveedor para volver a la orden con lo escrito y la
 * factura (FL-ORD-04): un estado de navegación, nada guardado en el navegador. */
export type VueltaALaOrden = { consorcio: string; abrir: string; orden?: OrdenInicial };

/** Lo que lleva la pantalla de Proveedores para dar de alta el proveedor de la factura leída y volver
 * a la orden con lo que ya estaba escrito. Nunca una cuenta: la lectura no la trae. */
export type AltaDesdeLaFactura = {
  cuit: string | null;
  razon_social: string | null;
  domicilio: string | null;
  telefono: string | null;
  correo: string | null;
};

type Campo = keyof Datos;

/** Los obligatorios que la lectura puede completar: si quedan vacíos después de leer, se marcan. */
const OBLIGATORIOS_LEIDOS = ["proveedor", "importe", "concepto"] as const satisfies readonly Campo[];

/** Texto comparable: sin espacios de más y sin distinguir mayúsculas. */
const comparable = (texto: string) => texto.trim().replace(/\s+/g, " ").toLocaleLowerCase("es-AR");

/** Si lo leído dice lo mismo que lo que ya estaba: el importe por su valor, los textos sin espacios
 * de más ni mayúsculas, el proveedor y la moneda tal cual. */
function dicenLoMismo(campo: Campo, antes: string, leido: string): boolean {
  if (campo === "importe") return aCentavos(antes) === aCentavos(leido);
  if (campo === "concepto" || campo === "comprobante") return comparable(antes) === comparable(leido);
  return antes === leido;
}

/** Los campos que la lectura de la factura completa, con su valor en el formulario. La moneda leída,
 * solo si el edificio opera en ella: en uno que opera solo en pesos el campo no se muestra. El
 * proveedor, solo si se le puede preparar una orden (FL-ORD-02): a uno bloqueado no lo elige sola. */
function camposLeidos(l: LecturaCarga, proveedores: Proveedor[], monedas: readonly string[], admite: (p: Proveedor) => boolean): Partial<Datos> {
  const sugerido = proveedores.find((p) => p.id === l.proveedor_sugerido?.id && admite(p));
  return {
    ...(l.importe_centavos === null ? {} : { importe: aTexto(l.importe_centavos) }),
    ...(l.moneda !== null && conCampoMoneda(monedas) && (monedasOfrecidas(monedas) as readonly string[]).includes(l.moneda)
      ? { moneda: l.moneda }
      : {}),
    ...(l.numero_comprobante === null ? {} : { comprobante: l.numero_comprobante.slice(0, 200) }),
    ...(l.concepto === null ? {} : { concepto: l.concepto }),
    ...(sugerido === undefined ? {} : { proveedor: sugerido.id }),
  };
}

type Modificar = {
  orden: string;
  version: Version;
  /** Guardar deja sin efecto aprobaciones: se confirma antes. */
  pierdeAprobaciones: boolean;
};

type Props = {
  consorcio: string;
  /** Las monedas en que opera el edificio (`ConsorcioResumen.monedas`): con una sola no se pregunta. */
  monedas: MonedasDelEdificio;
  acto: ActoEnCurso;
  aEntrar: () => void;
  aInicio: () => void;
  /** Sin esto, prepara una orden nueva. */
  modificar?: Modificar;
  /** Con qué abre una orden nueva; el proveedor, si sigue activo. */
  inicial?: OrdenInicial;
  /** `quedo`: «enviada» a aprobar; «guardada» (un borrador o los cambios de una versión); «sin_enviar»,
   * la orden quedó guardada, pero el envío a aprobar no salió. */
  alGuardar: (orden: string, texto: string, quedo: "enviada" | "guardada" | "sin_enviar") => void;
  /** La orden nueva ya existe, aunque falte enviarla: quien pide el código sabe adónde ir si se cancela. */
  alCrear?: (orden: string) => void;
  alCancelar: () => void;
  /** La orden cambió: la ficha la vuelve a leer y el formulario sigue sobre lo nuevo. */
  alCambiarLaOrden?: () => void;
};

/** Lo que dice la ficha cuando la orden quedó guardada y el envío no salió. */
export const noSeEnvio = (motivo: string) =>
  `La orden quedó guardada como borrador, pero no se envió a aprobar. ${motivo} Para enviarla, usar «Enviar a aprobar».`;

export function FormularioOrden({ consorcio, monedas, acto, aEntrar, aInicio, modificar, inicial, alGuardar, alCrear, alCancelar, alCambiarLaOrden }: Props) {
  const id = useId();
  /** Qué botón se tocó: una orden nueva se envía a aprobar salvo que se pida guardarla como borrador. */
  const destino = useRef<"enviar" | "borrador">("enviar");
  const { enviando, setAviso } = acto;
  const [lectura, setLectura] = useState<Lectura>({ tipo: "cargando" });
  const [vuelta, setVuelta] = useState(0);
  /** Cada guardar con algo que falta lleva el foco al primer campo marcado, ya dibujado. */
  const [moverFoco, setMoverFoco] = useState(0);
  const [datos, setDatos] = useState<Datos | null>(null);
  /** Otra orden parecida, encontrada al guardar (decisión 4 del 13-11-2026): se avisa y no bloquea. */
  const [parecida, setParecida] = useState<OrdenParecida | null>(null);
  /** Los datos con los que se eligió «Guardar igual»: con otros, se vuelve a mirar. */
  const guardarIgual = useRef<string | null>(null);
  const formulario = useRef<HTMLFormElement>(null);
  /** Los datos de la versión que se abrió, para saber qué cambió la persona. */
  const vistos = useRef<Datos | null>(null);
  const versionVista = modificar?.version;
  /** La moneda de una orden nueva: la primera del edificio, el peso. Va como texto en las dependencias. */
  const monedaBase = monedas[0];
  /** La versión del archivo de la factura que se manda al guardar: la de la versión abierta, la que
   * vuelve del alta o la subida acá. */
  const [factura, setFactura] = useState<string | null>(
    modificar?.version.factura_version_id ?? inicial?.factura?.version ?? null,
  );
  const facturaTocada = useRef(false);
  /** Los campos que la persona eligió o escribió, o que trajo la llegada (`inicial`): en una orden
   * nueva, solo lo que nadie tocó (la moneda base por omisión) no cuenta como escrito. */
  const elegidos = useRef<Set<Campo>>(
    new Set(
      (Object.entries(inicial ?? {}) as [keyof OrdenInicial, unknown][])
        .filter(([k, v]) => k !== "factura" && v !== undefined)
        .map(([k]) => k as Campo),
    ),
  );
  const facturaDeLaVersion = useVersionVisible(
    consorcio,
    inicial?.factura === undefined ? (modificar?.version.documento_id ?? null) : null,
    inicial?.factura === undefined ? (modificar?.version.factura_version_id ?? null) : null,
    aEntrar,
  );
  /** Los campos en que la lectura cambió lo que ya estaba, con lo que estaba, mientras la persona no
   * los revisa. */
  const [diferencias, setDiferencias] = useState<ReadonlyMap<Campo, string>>(new Map());
  /** Ya terminó una lectura con datos: desde ahí se marcan los obligatorios vacíos. */
  const [leida, setLeida] = useState(false);
  /** La lectura encontró un CUIT que no cierra (el aviso lo da la carga). */
  const [cuitInvalido, setCuitInvalido] = useState(false);
  /** La moneda leída, si el edificio no opera en ella, mientras la persona no la da por revisada. */
  const [monedaAjena, setMonedaAjena] = useState<string | null>(null);
  /** La lectura con un proveedor que no está cargado: se ofrece darlo de alta. */
  const [sinProveedor, setSinProveedor] = useState<AltaDesdeLaFactura | null>(null);
  /** Un archivo de factura subiéndose, en el antivirus o leyéndose: hasta que termine no se guarda. */
  const [facturaEnEspera, setFacturaEnEspera] = useState(false);
  /** La factura subida en este formulario, ya aprobada por el antivirus. */
  const [subida, setSubida] = useState<Cargado | null>(null);
  /** Lo obligatorio que faltaba al tocar guardar (decisión 4: el botón siempre activo). */
  const [errores, setErrores] = useState<ErroresDeCampo>(SIN_ERRORES);
  const proveedorElegido = datos?.proveedor ?? "";
  // La base niega solo la versión 1 o una versión que cambia de proveedor (`exigir_documentacion_legal`, app 0104):
  // al modificar una orden sin cambiarle el proveedor no hay bloqueo (Codex en #257).
  const esBloqueado = (proveedor: string) =>
    lectura.tipo === "ok" &&
    lectura.modo === "bloquear" &&
    proveedor !== "" &&
    !(modificar !== undefined && proveedor === modificar.version.proveedor_id) &&
    lectura.sinDocumentacion.has(proveedor);
  /** Si se le puede preparar una orden (FL-ORD-03): activo y, bloqueado, con un salteo que le sirva. */
  const admiteOrden = (p: Proveedor) => p.estado === "activo" && !(esBloqueado(p.id) && lectura.tipo === "ok" && !lectura.conSalteo.has(p.id));
  const bloqueado = esBloqueado(proveedorElegido);
  /** El proveedor de la factura leída cuando no se le puede preparar una orden (FL-ORD-02, FL-PRV-01):
   * no se elige solo ni se ofrece darlo de alta; se dice y se lleva a su ficha. */
  const [leidoSinOrden, setLeidoSinOrden] = useState<{ proveedor: Proveedor; inactivo: boolean } | null>(null);

  useEffect(() => {
    let vigente = true;
    const ruta = { params: { path: { consorcio_id: consorcio } } };
    void Promise.all([
      api.GET("/api/v1/consorcios/{consorcio_id}/proveedores", ruta),
      api.GET("/api/v1/consorcios/{consorcio_id}/contratos", ruta),
    ])
      .then(([proveedores, contratos]) => {
        exigirSesion(proveedores.response);
        exigirSesion(contratos.response);
        if (!vigente) return;
        if (proveedores.data !== undefined && contratos.data !== undefined) {
          setLectura({ tipo: "ok", contratos: contratos.data.elementos, ...deLaLista(proveedores.data) });
          return;
        }
        const falla = proveedores.data === undefined ? proveedores : contratos;
        const codigo = codigoDe(falla.error);
        if (falla.response.status === ESTADO.sinPermiso && codigo === "sin_permiso") aInicio();
        else {
          setLectura({ tipo: "sin_servicio" });
          setAviso(sinPisarLoHecho(mensajeDeError(falla.response.status, codigo)));
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
  }, [consorcio, vuelta, aEntrar, aInicio, setAviso]);

  // FL-SAL-06: un salteo registrado (o la documentación cargada) mientras el formulario está abierto en
  // otra pestaña o en otro dispositivo: al volver a esta pestaña se leen de nuevo los proveedores y sus
  // bloqueos, sin tocar lo escrito. Volver desde la ficha del proveedor monta el formulario y lee todo.
  useEffect(() => {
    let vigente = true;
    const releer = () => {
      if (document.visibilityState !== "visible") return;
      void api
        .GET("/api/v1/consorcios/{consorcio_id}/proveedores", { params: { path: { consorcio_id: consorcio } } })
        .then(({ data, error, response }) => {
          exigirSesion(response);
          if (!vigente) return;
          if (data === undefined) setAviso(sinPisarLoHecho(mensajeDeError(response.status, codigoDe(error))));
          else setLectura((l) => (l.tipo === "ok" ? { ...l, ...deLaLista(data) } : l));
        })
        .catch((e: unknown) => {
          if (!vigente) return;
          if (e instanceof SinSesion) aEntrar();
          else setAviso(sinPisarLoHecho(SIN_CONEXION));
        });
    };
    document.addEventListener("visibilitychange", releer);
    return () => {
      vigente = false;
      document.removeEventListener("visibilitychange", releer);
    };
  }, [consorcio, aEntrar, setAviso]);

  useEffect(() => {
    if (moverFoco > 0) formulario.current?.querySelector<HTMLElement>('[aria-invalid="true"]')?.focus();
  }, [moverFoco]);

  // Al llegar los datos, o una versión nueva con el formulario abierto.
  useEffect(() => {
    if (lectura.tipo !== "ok") return;
    if (versionVista === undefined) {
      setDatos((d) => {
        if (d !== null) return d;
        // Un proveedor que ya no admite órdenes también se elige: el formulario dice por qué y dónde se
        // resuelve, en vez de abrir sin proveedor y sin decir nada (FL-ORD-05).
        const elegido = lectura.proveedores.find((p) => p.id === inicial?.proveedor);
        const { importe: importeInicial, moneda, concepto, comprobante, reserva, contrato } = inicial ?? {};
        return {
          ...datosVacios(elegido?.id ?? "", monedaBase),
          ...(importeInicial === undefined ? {} : { importe: importeInicial }),
          ...(moneda === undefined ? {} : { moneda }),
          ...(concepto === undefined ? {} : { concepto }),
          ...(comprobante === undefined ? {} : { comprobante }),
          ...(reserva === undefined ? {} : { reserva }),
          ...(contrato === undefined ? {} : { contrato }),
        };
      });
      return;
    }
    const nuevos = datosDe(versionVista, lectura.contratos);
    const antes = vistos.current;
    vistos.current = nuevos;
    setDatos((d) => (d === null || antes === null ? nuevos : sobreLoNuevo(d, antes, nuevos)));
    // La factura sigue a la versión, salvo que la persona haya subido otra.
    if (!facturaTocada.current) setFactura(versionVista.factura_version_id ?? null);
    if (antes === null && versionVista.contrato_id !== null && nuevos.contrato === "")
      setAviso({ texto: "El contrato de esta orden ya no rige: se quitó del formulario. Al guardar, pide aprobación.", ok: false });
  }, [lectura, versionVista, inicial, monedaBase, setAviso]);

  function revisado(campo: Campo) {
    setDiferencias((marcados) => {
      if (!marcados.has(campo)) return marcados;
      const otros = new Map(marcados);
      otros.delete(campo);
      return otros;
    });
  }

  function cargarFactura(cargada: Cargado | null) {
    facturaTocada.current = true;
    // Mientras el archivo nuevo no pasa el antivirus, no hay factura que mandar.
    setFactura(cargada?.version ?? null);
  }

  /** Lo que estaba en un campo, como lo ve la persona: el proveedor por su nombre, la moneda por el suyo. */
  function mostrable(campo: Campo, valor: string): string {
    if (campo === "proveedor" && lectura.tipo === "ok") return lectura.proveedores.find((p) => p.id === valor)?.razon_social ?? valor;
    if (campo === "moneda") return (MONEDAS as readonly string[]).includes(valor) ? NOMBRE_MONEDA[valor as (typeof MONEDAS)[number]] : valor;
    return valor;
  }

  function leerFactura(l: LecturaCarga) {
    if (lectura.tipo !== "ok") return;
    const leidos = camposLeidos(l, lectura.proveedores, monedas, admiteOrden);
    // Lo que ya estaba escrito y lo leído dice otra cosa. En una orden nueva, lo que sigue como al
    // abrir vacía sin que nadie lo eligiera (la moneda base por omisión) no es algo escrito; elegido a
    // propósito, aunque sea el mismo valor, sí. En una versión, todo lo es.
    const base = modificar === undefined ? datosVacios("", monedaBase) : null;
    const nuevas = new Map<Campo, string>();
    if (datos !== null) {
      for (const [campo, leido] of Object.entries(leidos) as [Campo, string][]) {
        const antes = datos[campo];
        if (typeof antes !== "string" || antes.trim() === "" || (base !== null && antes === base[campo] && !elegidos.current.has(campo))) continue;
        if (!dicenLoMismo(campo, antes, leido)) nuevas.set(campo, mostrable(campo, antes));
      }
    }
    setDiferencias(nuevas);
    setLeida(true);
    setCuitInvalido(l.cuit_invalido);
    setMonedaAjena(l.moneda !== null && !(monedasOfrecidas(monedas) as readonly string[]).includes(l.moneda) ? l.moneda : null);
    setDatos((d) => {
      if (d === null) return d;
      const nuevo = { ...d, ...leidos };
      if (leidos.proveedor !== undefined && leidos.proveedor !== d.proveedor) nuevo.contrato = "";
      if (leidos.moneda !== undefined && leidos.moneda !== d.moneda) nuevo.contrato = "";
      return nuevo;
    });
    // El servidor sugiere solo un proveedor activo: uno suspendido o de baja con ese CUIT ya está
    // cargado, y darlo de alta terminaría en «Ya hay un proveedor con ese CUIT» (FL-PRV-01).
    const cuitLeido = soloDigitos(l.cuit);
    const inactivo =
      l.proveedor_sugerido === null && cuitLeido !== ""
        ? lectura.proveedores.find((p) => p.estado !== "activo" && soloDigitos(p.cuit) === cuitLeido)
        : undefined;
    const sugerido = lectura.proveedores.find((p) => p.id === l.proveedor_sugerido?.id);
    setLeidoSinOrden(
      inactivo !== undefined
        ? { proveedor: inactivo, inactivo: true }
        : sugerido !== undefined && !admiteOrden(sugerido)
          ? { proveedor: sugerido, inactivo: sugerido.estado !== "activo" }
          : null,
    );
    setSinProveedor(
      l.proveedor_sugerido === null && inactivo === undefined && (l.cuit !== null || l.razon_social !== null)
        ? { cuit: l.cuit, razon_social: l.razon_social, domicilio: l.domicilio, telefono: l.telefono, correo: l.correo }
        : null,
    );
  }

  function cambiar<K extends keyof Datos>(campo: K, valor: Datos[K]) {
    revisado(campo);
    setErrores((e) => sinElDe(e, campo));
    elegidos.current.add(campo);
    setParecida(null);
    // Elegido a mano un proveedor, el aviso del de la factura ya no hace falta.
    if (campo === "proveedor") setLeidoSinOrden(null);
    setDatos((d) => {
      if (d === null) return d;
      const nuevo = { ...d, [campo]: valor };
      if (campo === "proveedor" || campo === "moneda") nuevo.contrato = "";
      return nuevo;
    });
  }

  function guardar(evento: FormEvent) {
    evento.preventDefault();
    // Guardar ahora mandaría la orden sin el archivo que todavía no terminó (o sin la factura anterior).
    if (datos === null || facturaEnEspera) return;
    const faltan = faltaCompletar({
      proveedor: [datos.proveedor, FALTA_PROVEEDOR],
      importe: [datos.importe, FALTA_IMPORTE],
      concepto: [datos.concepto, FALTA_CONCEPTO],
    });
    if (hayErrores(faltan)) {
      setErrores(faltan);
      setMoverFoco((n) => n + 1);
      return;
    }
    const centavos = aCentavos(datos.importe);
    if (centavos === null) {
      setAviso({ texto: "Hay que escribir el importe en pesos, por ejemplo 12345,50.", ok: false });
      return;
    }
    // Antes de guardar, si no es rutinaria (sin contrato ni abono), se mira si hay otra parecida.
    const comparable = { proveedor_id: datos.proveedor, importe_centavos: centavos, moneda: datos.moneda };
    const contrato = contratoQueCubre(datos, centavos);
    if (contrato === "" && guardarIgual.current !== JSON.stringify(comparable)) {
      mirarParecida(comparable);
      return;
    }
    const esencial = {
      importe_centavos: centavos,
      moneda: datos.moneda,
      concepto: datos.concepto.trim(),
      proveedor_id: datos.proveedor,
      comprobante: opcional(datos.comprobante),
      usa_fondo_reserva: datos.reserva,
      contrato_id: contrato === "" ? null : contrato,
      factura_version_id: factura,
    };
    void acto.hacer({
      quePide: ["guardar", modificar?.orden, modificar?.version.numero, datos, factura],
      nombre: "guardar la orden",
      pedir: (clave) => {
        const header = { [ENCABEZADO_IDEMPOTENCIA]: clave };
        return modificar === undefined
          ? api.POST("/api/v1/consorcios/{consorcio_id}/ordenes-pago", {
              headers: encabezadoCsrf(),
              params: { path: { consorcio_id: consorcio }, header },
              body: esencial,
            })
          : api.POST("/api/v1/consorcios/{consorcio_id}/ordenes-pago/{orden_id}/versiones", {
              headers: encabezadoCsrf(),
              params: { path: { consorcio_id: consorcio, orden_id: modificar.orden }, header },
              body: { ...esencial, version_esperada: modificar.version.numero },
            });
      },
      alTerminar: (data) => {
        if (modificar !== undefined) alGuardar(data.id, "Listo: los cambios quedaron guardados.", "guardada");
        else if (destino.current === "borrador") alGuardar(data.id, "Listo: la orden quedó en borrador.", "guardada");
        else {
          alCrear?.(data.id);
          enviarLaNueva(data);
        }
      },
      alFallar: (estado, codigo) => {
        if (cambioLaOrden(codigo)) alCambiarLaOrden?.();
        else if (cambioLoVisto(estado, codigo) && !SE_CORRIGE_EN_EL_FORMULARIO.has(codigo ?? "")) {
          alCambiarLaOrden?.();
          alCancelar();
        }
      },
    });
  }

  /** El contrato elegido, si cubre el importe (C8P-04); si no, ninguno. */
  function contratoQueCubre(d: Datos, centavos: number | null): string {
    if (d.contrato === "" || lectura.tipo !== "ok") return d.contrato;
    const elegido = lectura.contratos.find((k) => k.id === d.contrato);
    return elegido !== undefined && cubreElImporte(elegido, centavos) ? d.contrato : "";
  }

  /** Si hay otra orden parecida, la muestra; si no, guarda. Mirar no cambia nada. */
  function mirarParecida(comparable: { proveedor_id: string; importe_centavos: number; moneda: string }) {
    acto.setEnviando(true);
    setAviso(null);
    void api
      .GET("/api/v1/consorcios/{consorcio_id}/pagos/parecida", {
        params: { path: { consorcio_id: consorcio }, query: { ...comparable, orden_id: modificar?.orden } },
      })
      .then(({ data, error, response }) => {
        exigirSesion(response);
        if (data === undefined) {
          setAviso({ texto: mensajeDeActo(response.status, error), ok: false });
          return;
        }
        if (data.parecida !== null) {
          setParecida(data.parecida);
          return;
        }
        guardarIgual.current = JSON.stringify(comparable);
        formulario.current?.requestSubmit();
      })
      .catch((e: unknown) => (e instanceof SinSesion ? aEntrar() : setAviso({ texto: SIN_CONEXION, ok: false })))
      .finally(() => acto.setEnviando(false));
  }

  /** El segundo pedido de «Guardar y enviar a aprobar», sobre la versión recién guardada. */
  function enviarLaNueva(nueva: { id: string; version: number }) {
    const siSigueAbierto = (hacer: () => void) => {
      if (acto.montado.current) hacer();
    };
    void acto.hacer({
      quePide: ["enviar", nueva.id, nueva.version],
      nombre: "enviar la orden a aprobar",
      pedir: (clave) =>
        api.POST("/api/v1/consorcios/{consorcio_id}/ordenes-pago/{orden_id}/enviar-a-revision", {
          headers: encabezadoCsrf(),
          params: { path: { consorcio_id: consorcio, orden_id: nueva.id }, header: { [ENCABEZADO_IDEMPOTENCIA]: clave } },
          body: { version_esperada: nueva.version },
        }),
      alTerminar: () => {
        // Hasta saber en qué quedó, el botón no se vuelve a tocar: la orden ya existe.
        acto.setEnviando(true);
        void leerComoQuedo(consorcio, nueva.id)
          .then((texto) => siSigueAbierto(() => alGuardar(nueva.id, texto, "enviada")))
          .catch((e: unknown) =>
            siSigueAbierto(() => (e instanceof SinSesion ? aEntrar() : alGuardar(nueva.id, TEXTO_ENVIADA, "enviada"))),
          )
          .finally(() => acto.setEnviando(false));
      },
      alFallar: (estado, _codigo, error) => alGuardar(nueva.id, noSeEnvio(mensajeDeActo(estado, error)), "sin_enviar"),
      alNoLlegar: () => alGuardar(nueva.id, noSeEnvio(SIN_CONEXION), "sin_enviar"),
    });
  }

  if (lectura.tipo === "cargando") return <p role="status">Cargando…</p>;
  if (lectura.tipo === "sin_servicio")
    return (
      <FallaDeLectura
        alReintentar={() => {
          setAviso(sinLaFalla);
          setLectura({ tipo: "cargando" });
          setVuelta((v) => v + 1);
        }}
        avisar={setAviso}
      />
    );
  if (modificar === undefined && !lectura.proveedores.some((p) => p.estado === "activo"))
    return (
      <p className="tarjeta">
        Para preparar una orden hace falta un proveedor activo:{" "}
        <Link to={RUTA_PROVEEDORES} state={{ consorcio, paraPreparar: true }}>
          cargarlo en Proveedores
        </Link>
        .
      </p>
    );
  if (datos === null) return null;

  const centavosEscritos = aCentavos(datos.importe);
  const aplicables = contratosAplicables(lectura.contratos, datos.proveedor, datos.moneda);
  /** Los que cubren el importe escrito (C8P-04); el elegido que no lo cubre se dice junto al campo. */
  const ofrecidos = aplicables.filter((k) => cubreElImporte(k, centavosEscritos));
  const contrato = contratoQueCubre(datos, centavosEscritos);
  const excedido = contrato !== datos.contrato ? lectura.contratos.find((k) => k.id === datos.contrato) : undefined;
  const ofrecidas = monedasOfrecidas(monedas);
  /** Los obligatorios vacíos después de leer. El proveedor no cargado, o al que no se le puede
   * preparar una orden, ya tiene su aviso. */
  const faltan = leida
    ? OBLIGATORIOS_LEIDOS.filter((c) => datos[c].trim() === "" && !(c === "proveedor" && (sinProveedor !== null || leidoSinOrden !== null)))
    : [];
  const quedaPorRevisar =
    diferencias.size > 0 || faltan.length > 0 || sinProveedor !== null || leidoSinOrden !== null || cuitInvalido || monedaAjena !== null;
  const marca = (campo: Campo) => {
    const antes = diferencias.get(campo);
    if (antes !== undefined) return <MarcaDeDiferencia antes={antes} alRevisar={() => revisado(campo)} />;
    // Lo que falta al tocar guardar ya lo dice el error del campo.
    return (faltan as readonly Campo[]).includes(campo) && errores[campo] === undefined ? <MarcaDeFalta /> : null;
  };
  const facturaVisible: Cargado | undefined = subida ?? inicial?.factura ?? facturaDeLaVersion ?? undefined;
  /** Lo escrito y la factura, para volver a la orden desde Proveedores (FL-PRV-02, FL-ORD-04). Solo en
   * una orden nueva: la que se modifica vive en su ficha. */
  const loEscrito: OrdenInicial = {
    importe: datos.importe,
    moneda: datos.moneda,
    concepto: datos.concepto,
    comprobante: datos.comprobante,
    reserva: datos.reserva,
    factura: facturaVisible,
  };
  const alAlta = sinProveedor === null ? null : {
    consorcio,
    alta: sinProveedor,
    documento: facturaVisible,
    ...(modificar === undefined ? { paraPreparar: true, orden: loEscrito } : {}),
  };
  /** El enlace a la ficha de un proveedor: la abre y, en una orden nueva, vuelve con lo escrito (FL-ORD-04). */
  const aSuFicha = (p: Proveedor) => {
    const estado: VueltaALaOrden = {
      consorcio,
      abrir: p.id,
      ...(modificar === undefined ? { orden: { ...loEscrito, ...(datos.contrato === "" ? {} : { contrato: datos.contrato }) } } : {}),
    };
    return { texto: textoAbrirFicha(p.razon_social), a: RUTA_PROVEEDORES, estado };
  };
  const elegido = lectura.proveedores.find((p) => p.id === datos.proveedor);
  /** El proveedor elegido está suspendido o de baja (C8P-06; también al llegar con uno, FL-ORD-05). */
  const inactivo = elegido !== undefined && elegido.estado !== "activo";
  const salteoDelElegido: SalteoDelElegido | null = !bloqueado
    ? null
    : lectura.conSalteo.has(datos.proveedor)
      ? "con_salteo"
      : lectura.salteoPropio.has(datos.proveedor)
        ? "salteo_propio"
        : "sin_salteo";
  /** Sin un salteo que le sirva, la base no deja crear la orden (ni como borrador): no se ofrece guardar (C8P-01, C8P-02). */
  const sinSalteo = bloqueado && salteoDelElegido !== "con_salteo";
  /** C9 (comentario 41 del propietario, FL-ORD-01): ya se sabe que la orden no se puede hacer con este
   * proveedor. Quedan solo el desplegable (elegir otro es la salida), el aviso con el enlace a su ficha y
   * «Cancelar». Lo demás se esconde sin perderse: al elegir otro proveedor vuelve con lo que tenía, y la
   * factura subida sigue subida. */
  const impedido = sinSalteo || inactivo;
  /** Al modificar, sin tocar nada no hay qué guardar (C8P-05). */
  const sinCambios =
    modificar !== undefined &&
    vistos.current !== null &&
    (Object.keys(datos) as Campo[]).every((c) => datos[c] === vistos.current?.[c]) &&
    factura === (modificar.version.factura_version_id ?? null);
  const seGuarda = !impedido && !sinCambios;
  const idProveedor = `${id}-proveedor`;
  const idImporte = `${id}-importe`;
  const idConcepto = `${id}-concepto`;
  /** Lo que se ofrece: los activos, y el elegido aunque no lo esté. */
  const ofrecibles = lectura.proveedores.filter((p) => p.estado === "activo" || p.id === datos.proveedor);
  const conOrden = ofrecibles.filter(admiteOrden);
  const sinOrden = ofrecibles.filter((p) => !admiteOrden(p));

  return (
    <>
      <form ref={formulario} className="formulario tarjeta" onSubmit={guardar} noValidate
        aria-label={modificar === undefined ? "Preparar una orden" : "Modificar la orden"}>
        {/* Se avisa al abrir, no después de completar todo (P-32). */}
        {modificar?.pierdeAprobaciones === true && <p className="aviso-info" role="note">{AVISO_MODIFICAR}</p>}
        <div className="campos-de-la-orden" hidden={impedido}>
          <CargaConLectura consorcio={consorcio} tipo="factura" titulo="Factura" de="de la factura" textoVer="Ver la factura"
            deshabilitada={enviando} inicial={inicial?.factura ?? facturaDeLaVersion} aEntrar={aEntrar}
            alCargar={(c) => {
              setSubida(c);
              cargarFactura(c);
            }}
            alLeer={leerFactura} alEsperar={setFacturaEnEspera} quedaPorRevisar={quedaPorRevisar} />
          {alAlta !== null && (
            <div className="aviso-info" role="note">
              <p>
                El proveedor de la factura no está cargado en este edificio
                {sinProveedor?.razon_social != null && <> ({sinProveedor.razon_social})</>}.
              </p>
              <Link to={RUTA_PROVEEDORES} state={alAlta}>
                Dar de alta el proveedor
              </Link>
            </div>
          )}
          {leidoSinOrden !== null && (
            <ComandoNoDisponible
              motivo={leidoSinOrden.inactivo ? textoLeidoInactivo(leidoSinOrden.proveedor.razon_social) : textoLeidoSinOrden(leidoSinOrden.proveedor.razon_social)}
              resolver={aSuFicha(leidoSinOrden.proveedor)}
            />
          )}
        </div>
        <label htmlFor={idProveedor}>Proveedor</label>
        <select id={idProveedor} value={datos.proveedor} disabled={enviando} {...marcaDe(idProveedor, errores.proveedor)}
          onChange={(e) => cambiar("proveedor", e.target.value)}>
          <option value="" disabled>
            Elegir un proveedor
          </option>
          {conOrden.map((p) => (
            <option key={p.id} value={p.id}>
              {esBloqueado(p.id) ? `${p.razon_social} ${MARCA_CON_SALTEO}` : p.razon_social}
            </option>
          ))}
          {/* Siguen en la lista (decisión 1 del propietario, 18-11-2026): elegirlo dice por qué y dónde
              se resuelve. Van aparte y con el nombre solo, para que la marca no se corte (FL-ORD-03). */}
          {sinOrden.length > 0 && (
            <optgroup label={GRUPO_SIN_ORDEN}>
              {sinOrden.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.razon_social}
                </option>
              ))}
            </optgroup>
          )}
        </select>
        <ErrorDeCampo idCampo={idProveedor} mensaje={errores.proveedor} />
        {marca("proveedor")}
        {elegido !== undefined && inactivo && <ComandoNoDisponible motivo={PROVEEDOR_INACTIVO} resolver={aSuFicha(elegido)} />}
        {elegido !== undefined && !inactivo && salteoDelElegido === "sin_salteo" && (
          <>
            <ComandoNoDisponible motivo={SIN_DOCUMENTACION} resolver={aSuFicha(elegido)} />
            {lectura.vencidoSinUsar.has(elegido.id) && <p className="aviso-pendiente" role="note">{SALTEO_VENCIDO_SIN_USAR}</p>}
            {/* Pregunta 3 del propietario (18-11-2026, tanda 9): el salteo se le pide al Consejo desde acá. */}
            {/* Con la clave del proveedor, lo enviado para uno no queda al elegir otro (Codex en app #259). */}
            <PedidoDeSalteo key={elegido.id} consorcio={consorcio} proveedor={elegido} monedas={monedas}
              inicial={{ importe: datos.importe, moneda: datos.moneda }} aEntrar={aEntrar} alCambiar={() => setVuelta((v) => v + 1)} />
          </>
        )}
        {elegido !== undefined && !inactivo && salteoDelElegido === "salteo_propio" && (
          <ComandoNoDisponible motivo={SALTEO_PROPIO} resolver={aSuFicha(elegido)} />
        )}
        <div className="campos-de-la-orden" hidden={impedido}>
          {salteoDelElegido === "con_salteo" && (
            <p className="aviso-pendiente" role="note">
              {textoConSalteo(lectura.venceSalteo.get(datos.proveedor))}
            </p>
          )}
          {/* FL-ORD-06: sin cuenta destino la orden no se puede pagar; se avisa y se deja guardar (decisión 2). */}
          {elegido !== undefined && elegido.cuenta_ultimos_digitos === null && (
            <p className="aviso-pendiente" role="note">
              {SIN_CUENTA_DESTINO}{" "}
              <Link to={RUTA_PROVEEDORES} state={aSuFicha(elegido).estado} className="enlace-suelto">
                {textoAbrirFicha(elegido.razon_social)}
              </Link>
            </p>
          )}
          <label htmlFor={idImporte}>Importe</label>
          <CampoImporte id={idImporte} valor={datos.importe} deshabilitado={enviando} {...marcaDe(idImporte, errores.importe)}
            alCambiar={(texto) => cambiar("importe", texto)} />
          <ErrorDeCampo idCampo={idImporte} mensaje={errores.importe} />
          {marca("importe")}
          {monedaAjena !== null && (
            <p className="marca-lectura" role="alert">
              {textoMonedaAjena(monedaAjena, !conCampoMoneda(monedas))}{" "}
              <button type="button" className="sutil" onClick={() => setMonedaAjena(null)}>
                Está bien
              </button>
            </p>
          )}
          {/* Solo en un edificio que opera en más de una moneda (P-14); si no, la orden va en pesos. */}
          {conCampoMoneda(monedas) && (
            <>
              <label htmlFor={`${id}-moneda`}>Moneda</label>
              <select id={`${id}-moneda`} value={datos.moneda} disabled={enviando} onChange={(e) => cambiar("moneda", e.target.value)}>
                {ofrecidas.map((m) => (
                  <option key={m} value={m}>
                    {NOMBRE_MONEDA[m]}
                  </option>
                ))}
              </select>
              {marca("moneda")}
            </>
          )}
          {/* Solo si el proveedor elegido tiene un contrato vigente en esa moneda (P-14) que cubra el importe. */}
          {ofrecidos.length > 0 && (
            <>
              <label htmlFor={`${id}-contrato`}>Contrato o abono (opcional)</label>
              <select id={`${id}-contrato`} value={contrato} disabled={enviando}
                onChange={(e) => cambiar("contrato", e.target.value)}>
                <option value="">Ninguno</option>
                {ofrecidos.map((k) => (
                  <option key={k.id} value={k.id}>
                    {k.alcance} (hasta {importe(k.importe_maximo_centavos, k.moneda)})
                  </option>
                ))}
              </select>
            </>
          )}
          {excedido !== undefined && (
            <p className="aviso-pendiente" role="note">
              {textoContratoExcedido(importe(excedido.importe_maximo_centavos, excedido.moneda))}
            </p>
          )}
          <label htmlFor={idConcepto}>Concepto</label>
          <input id={idConcepto} maxLength={300} value={datos.concepto} disabled={enviando} {...marcaDe(idConcepto, errores.concepto)}
            onChange={(e) => cambiar("concepto", e.target.value)} />
          <ErrorDeCampo idCampo={idConcepto} mensaje={errores.concepto} />
          {marca("concepto")}
          <label htmlFor={`${id}-comprobante`}>Número de la factura (opcional)</label>
          <input id={`${id}-comprobante`} maxLength={200} value={datos.comprobante} disabled={enviando}
            onChange={(e) => cambiar("comprobante", e.target.value)} />
          {marca("comprobante")}
          <label>
            <input type="checkbox" checked={datos.reserva} disabled={enviando}
              onChange={(e) => cambiar("reserva", e.target.checked)} />{" "}
            Usa el fondo de reserva (siempre lo aprueba el Consejo)
          </label>
          {facturaEnEspera && (
            <p id={`${id}-espera`} className="aviso-info">
              {TEXTO_ESPERA_PARA_GUARDAR}
            </p>
          )}
          {sinCambios && <p role="note">{SIN_CAMBIOS}</p>}
        </div>
        <div className="acciones">
          {seGuarda && (
            <button type="submit" disabled={enviando || facturaEnEspera}
              aria-describedby={facturaEnEspera ? `${id}-espera` : undefined} onClick={() => (destino.current = "enviar")}>
              {modificar === undefined ? "Guardar y enviar a aprobar" : modificar.pierdeAprobaciones ? "Guardar y enviar a aprobar de nuevo" : "Guardar la orden"}
            </button>
          )}
          {seGuarda && modificar === undefined && (
            <button type="submit" className="secundario" disabled={enviando || facturaEnEspera}
              aria-describedby={facturaEnEspera ? `${id}-espera` : undefined} onClick={() => (destino.current = "borrador")}>
              Guardar como borrador
            </button>
          )}
          <button type="button" className="secundario" disabled={enviando} onClick={alCancelar}>
            Cancelar
          </button>
        </div>
      </form>
      {/* La pregunta de la orden parecida, en su ventana (ORD-06): fuera del formulario, para que su
          envío no sea el de la orden; mientras está abierta, los botones de guardar no quedan a mano. */}
      {parecida !== null && (
        <VentanaDeFormulario titulo={TITULO_PARECIDA} textoEnviar={GUARDAR_IGUAL} alCerrar={() => setParecida(null)}
          alEnviar={() => {
            guardarIgual.current = JSON.stringify({ proveedor_id: datos.proveedor, importe_centavos: centavosEscritos, moneda: datos.moneda });
            formulario.current?.requestSubmit();
            return "cerrar";
          }}>
          <p>
            {textoParecidaAntes(fechaCorta(parecida.creada_en))} {NOMBRE_ESTADO[parecida.estado_visible]}.
          </p>
          {/* En otra pestaña: lo escrito en el formulario no se pierde. */}
          <p>
            <Link to={rutaFicha(parecida.orden_id)} target="_blank" rel="noopener" className="enlace-suelto">
              {VER_LA_OTRA}
            </Link>
          </p>
        </VentanaDeFormulario>
      )}
    </>
  );
}
