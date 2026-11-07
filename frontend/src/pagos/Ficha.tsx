import { type FormEvent, type RefObject, useEffect, useId, useRef, useState } from "react";
import { Link } from "react-router";

import { api } from "../api/cliente";
import type { components } from "../api/tipos";
import { encabezadoCsrf } from "../csrf";
import {
  cambioLaOrden,
  cambioLoVisto,
  cuentaEnmascarada,
  aCentavos,
  aTexto,
  ENCABEZADO_IDEMPOTENCIA,
  ESTADO,
  importe,
  importeParaCopiar,
  mensajeDeError,
  momento,
  sinLaFalla,
  sinPisarLoHecho,
  TEXTO_ALERTA_FRACCIONAMIENTO,
  TEXTO_CONDICION,
  textoFranja,
} from "../egresos/comun";
import { AvisoPantalla, FallaDeLectura } from "../egresos/EstadoPantalla";
import { PedirCodigo } from "../egresos/PedirCodigo";
import { exigirSesion, SinSesion } from "../egresos/useConsorcio";
import { codigoDe, SIN_CONEXION } from "../fallas";
import { BotonCopiar } from "./BotonCopiar";
import { FormularioOrden } from "./FormularioOrden";
import { RegistroPago } from "./RegistroPago";
import { RUTA_NUEVA } from "./rutas";
import {
  AVISO_DIFERENCIA_ESPERA,
  AVISO_LECTURA_COINCIDE,
  AVISO_LECTURA_INCOMPLETA,
  AVISO_LECTURA_DISPUTADA,
  AVISO_LO_DECIDE_EL_CONSEJO,
  AVISO_POR_EL_CONSEJO,
  AVISO_POR_FRANJA,
  AVISO_NO_TRANSFIERAS,
  AVISO_PROVEEDOR_CAMBIADO,
  AVISO_RETOMAR,
  AVISO_SIN_CUENTA,
  AVISO_SIN_SALIDA,
  AVISO_TOMAR,
  AVISO_VER_LA_CUENTA,
  type Paso,
  TEXTO_LE_TOCA_A,
  TEXTO_SITUACION,
  TONO_SITUACION,
} from "./textos";
import { useActo } from "./useActo";
import { VisorComprobante } from "./VisorComprobante";

// La ficha de una orden, una sola para todos los estados (§3). No deduce nada: lo que dice sale de
// `situacion`, y lo que ofrece, de `paso` y `puede_*`, que el servidor saca de las reglas de la
// base. Revisar no es una pantalla más: la ficha muestra los datos y el botón principal confirma.
//
// La cuenta completa no viaja con la ficha. Llega con el acto de ejecución o, a quien decide la
// orden o concilia su pago, con `ordenes-pago/{id}/revision`, porque es lo que aprueba y lo que
// compara: quien preparó no la recibe (la revisión la manda nula). Vive en el estado de esta ficha y
// se va al cerrarla o cuando la orden cambia de versión. Sin ver la cuenta no se aprueba.

type FichaPago = components["schemas"]["FichaPago"];
type Cuenta = components["schemas"]["CuentaParaPagoManual"];
type Version = components["schemas"]["VersionOrdenPago"];

type Lectura = { tipo: "cargando" } | { tipo: "sin_servicio" } | { tipo: "no_esta" } | { tipo: "ok"; ficha: FichaPago };
type ConMotivo = "rechazar" | "declarar" | "conforme" | "observar" | "cerrar";
type Diferencia = components["schemas"]["DiferenciaPagoManual"];
type Resolucion = components["schemas"]["ResolucionDiferencia"];
type LecturaComprobante = components["schemas"]["LecturaComprobante"];
/** Lo que se registró con el comprobante: un pago o lo que salió del banco. */
type Registrado = { centavos: number; moneda: string; numero_operacion: string; fecha_operacion: string };

/** Los pasos en que quien puede declarar tiene a mano «No se pudo pagar». */
const SE_DECLARA = new Set<Paso | null>(["subir_comprobante", "corregir", "declarar_no_pago", "espera_comprobante"]);

/** El importe de la API en centavos: es decimal y siempre positivo. */
function centavosReales(d: Diferencia): number {
  const centavos = aCentavos(d.importe_real);
  if (centavos === null) throw new Error(`la diferencia ${d.id} trae un importe que no se entiende`);
  return centavos;
}

/** Lo que el comprobante dice y no es lo que se registró. La lectura puede equivocarse: quien
 * concilia lo mira y decide. */
function DistintoDeLoRegistrado({ lectura, registrado }: { lectura: LecturaComprobante; registrado: Registrado }) {
  const campos = lectura.distinto_de_lo_registrado;
  const leido = lectura.importe_centavos;
  return (
    <div className="aviso-error" role="alert">
      {campos.includes("importe") && leido !== null && (
        <p>
          El comprobante dice <strong>{importe(leido, lectura.moneda ?? registrado.moneda)}</strong> y registraron{" "}
          <strong>{importe(registrado.centavos, registrado.moneda)}</strong>.
        </p>
      )}
      {campos.includes("moneda") && (
        <p>
          El comprobante dice <strong>{lectura.moneda}</strong> y registraron <strong>{registrado.moneda}</strong>.
        </p>
      )}
      {campos.includes("cuenta") && (
        <p>
          El comprobante dice la cuenta <strong>{cuentaEnmascarada(lectura.cuenta_ultimos_digitos)}</strong>, que no es la registrada.
        </p>
      )}
      {campos.includes("numero_operacion") && (
        <p>
          El comprobante dice la operación <strong>{lectura.numero_operacion}</strong> y registraron <strong>{registrado.numero_operacion}</strong>.
        </p>
      )}
      {campos.includes("fecha") && (
        <p>
          El comprobante dice la fecha <strong>{lectura.fecha}</strong> y registraron <strong>{registrado.fecha_operacion}</strong>.
        </p>
      )}
    </div>
  );
}

function vigenteDe(f: FichaPago): Version {
  const v = f.versiones.find((x) => x.numero === f.orden.version_vigente);
  if (v === undefined) throw new Error(`la ficha no trae la versión vigente de ${f.orden.id}`);
  return v;
}

/** Lo que cambió de una versión a la otra, para no tener que comparar a ojo. */
export function cambiosDeVersion(v: Version, anterior: Version): string[] {
  const cambios: string[] = [];
  if (v.importe_centavos !== anterior.importe_centavos || v.moneda !== anterior.moneda)
    cambios.push(`Cambió el importe: antes era ${importe(anterior.importe_centavos, anterior.moneda)}.`);
  if (v.proveedor_id !== anterior.proveedor_id) cambios.push(`Cambió el proveedor: antes era ${anterior.razon_social}.`);
  if (v.cuenta_ultimos_digitos !== anterior.cuenta_ultimos_digitos)
    cambios.push(`Cambió la cuenta: antes era ${cuentaEnmascarada(anterior.cuenta_ultimos_digitos)}.`);
  if (v.concepto !== anterior.concepto) cambios.push(`Cambió el concepto: antes era «${anterior.concepto}».`);
  return cambios;
}

/** A qué paso se sigue, y si la ficha que lo pide sigue abierta cuando llega la bandeja. */
export type Seguir = { paso: Paso; abierta: RefObject<boolean> };

type Props = {
  consorcio: string;
  orden: string;
  aEntrar: () => void;
  aInicio: () => void;
  /** El acto terminó: pasa a la pantalla siguiente con el aviso; `seguir`, al próximo de ese paso. */
  terminar: (texto: string, seguir?: Seguir) => void;
  /** Algo cambió sin dejar la ficha: la bandeja de al lado se vuelve a leer. */
  alCambiar: () => void;
};

export function Ficha({ consorcio, orden, aEntrar, aInicio, terminar, alCambiar }: Props) {
  const id = useId();
  const acto = useActo(aEntrar, alCambiar);
  const { enviando, aviso, setAviso, pendiente, setPendiente, montado } = acto;
  /** Lo que dijo la última lectura que falló: se va cuando una lectura sale bien. */
  const fallaDeLectura = useRef<string | null>(null);
  const [lectura, setLectura] = useState<Lectura>({ tipo: "cargando" });
  const [vuelta, setVuelta] = useState(0);
  const [cuenta, setCuenta] = useState<Cuenta | null>(null);
  const [modificando, setModificando] = useState(false);
  const [conMotivo, setConMotivo] = useState<ConMotivo | null>(null);
  const [motivo, setMotivo] = useState("");
  /** La cuenta que se decide, con la versión que muestra; `cuenta` nula si no hay qué mostrar. */
  const [decision, setDecision] = useState<{ version: number; cuenta: string | null } | null>(null);
  const [vueltaDecision, setVueltaDecision] = useState(0);
  const [faltaCuenta, setFaltaCuenta] = useState(false);
  /** Lo que eligió quien resuelve; sin elegir, lo primero que se ofrece. */
  const [resolucion, setResolucion] = useState<Resolucion | null>(null);
  /** Pidió tomar el pago: falta que confirme que no se transfirió. */
  const [confirmaToma, setConfirmaToma] = useState(false);
  /** Pidió corregir la diferencia que registró. */
  const [corrigeDiferencia, setCorrigeDiferencia] = useState(false);

  const leida = lectura.tipo === "ok" ? lectura.ficha : null;
  const decide = leida !== null && (leida.paso === "aprobar" || leida.puede_aprobar || leida.puede_rechazar || leida.puede_conciliar);
  const versionLeida = leida?.orden.version_vigente;

  useEffect(() => {
    if (!decide) return;
    let vigente = true;
    setFaltaCuenta(false);
    void api
      .GET("/api/v1/consorcios/{consorcio_id}/ordenes-pago/{orden_id}/revision", {
        params: { path: { consorcio_id: consorcio, orden_id: orden } },
      })
      .then(({ data, error, response }) => {
        exigirSesion(response);
        if (!vigente) return;
        const version = data?.versiones.find((x) => x.numero === data.orden.version_vigente);
        if (version !== undefined) {
          setDecision({ version: version.numero, cuenta: version.cuenta_destino });
          // La orden cambió entre las dos lecturas: la cuenta es de una versión que esta ficha no muestra.
          if (version.numero !== versionLeida) setVuelta((n) => n + 1);
        } else {
          setFaltaCuenta(true);
          setAviso(sinPisarLoHecho(mensajeDeError(response.status, codigoDe(error))));
        }
      })
      .catch((e: unknown) => {
        if (!vigente) return;
        if (e instanceof SinSesion) aEntrar();
        else {
          setFaltaCuenta(true);
          setAviso(sinPisarLoHecho(SIN_CONEXION));
        }
      });
    return () => {
      vigente = false;
    };
  }, [consorcio, orden, decide, versionLeida, vueltaDecision, aEntrar, setAviso]);

  useEffect(() => {
    let vigente = true;
    void api
      .GET("/api/v1/consorcios/{consorcio_id}/pagos/{orden_id}", {
        params: { path: { consorcio_id: consorcio, orden_id: orden } },
      })
      .then(({ data, error, response }) => {
        exigirSesion(response);
        if (!vigente) return;
        const codigo = codigoDe(error);
        if (data !== undefined) {
          setLectura({ tipo: "ok", ficha: data });
          const falla = fallaDeLectura.current;
          fallaDeLectura.current = null;
          if (falla !== null) setAviso((a) => (a?.texto === falla ? null : a));
        } else if (response.status === ESTADO.sinPermiso && codigo === "sin_permiso") aInicio();
        // Una dirección que no es de una orden responde 422: tampoco está en este edificio.
        else if (response.status === ESTADO.noEncontrado || response.status === ESTADO.invalido) setLectura({ tipo: "no_esta" });
        else {
          // Con la ficha a la vista, lo que se escribió queda: solo se dice qué pasó.
          setLectura((l) => (l.tipo === "ok" ? l : { tipo: "sin_servicio" }));
          fallaDeLectura.current = mensajeDeError(response.status, codigo);
          setAviso(sinPisarLoHecho(fallaDeLectura.current));
        }
      })
      .catch((e: unknown) => {
        if (!vigente) return;
        if (e instanceof SinSesion) aEntrar();
        else {
          setLectura((l) => (l.tipo === "ok" ? l : { tipo: "sin_servicio" }));
          fallaDeLectura.current = SIN_CONEXION;
          setAviso(sinPisarLoHecho(SIN_CONEXION));
        }
      });
    return () => {
      vigente = false;
    };
  }, [consorcio, orden, vuelta, aEntrar, aInicio, setAviso]);

  const releer = () => setVuelta((v) => v + 1);

  function siCambio(estado: number, codigo: string | undefined) {
    if (cambioLaOrden(codigo) || cambioLoVisto(estado, codigo)) {
      releer();
      alCambiar();
    }
  }

  if (lectura.tipo === "cargando") return <p role="status">Cargando la orden…</p>;
  if (lectura.tipo === "no_esta")
    return <p className="tarjeta">Esa orden no está en este edificio.</p>;
  if (lectura.tipo === "sin_servicio")
    return (
      <>
        <AvisoPantalla aviso={aviso} />
        <FallaDeLectura
          alReintentar={() => {
            setAviso(sinLaFalla);
            setLectura({ tipo: "cargando" });
            releer();
          }}
          avisar={setAviso}
        />
      </>
    );

  const { ficha } = lectura;
  const { situacion, paso, ejecucion, declaracion_vigente: declaracion, observacion_vigente: observacion, diferencia_vigente: diferencia } = ficha;
  const v = vigenteDe(ficha);
  const ruta = { consorcio_id: consorcio, orden_id: orden };
  const pago = ejecucion?.pagos.at(-1);
  // La cuenta era de la versión que se abrió: de otra versión no se muestra.
  const cuentaVista = cuenta?.version === v.numero ? cuenta : null;
  const cuentaDecidida = decide && decision?.version === v.numero ? decision : null;
  const anterior = ficha.versiones.find((x) => x.numero === v.numero - 1);
  const enDecision = situacion === "borrador" || situacion === "en_revision";
  const registra = paso === "corregir" ? ficha.puede_corregir : paso === "subir_comprobante" && ficha.puede_subir_comprobante;
  const declara = ficha.puede_declarar && SE_DECLARA.has(paso);
  const conVisor = ficha.comprobante_actual !== null && !registra;
  const comprobanteLeido = ficha.lectura_comprobante;
  const registrado: Registrado | null =
    diferencia !== null
      ? { ...diferencia, centavos: centavosReales(diferencia) }
      : pago !== undefined
        ? { ...pago, centavos: pago.importe_centavos }
        : null;
  // Lo ve quien decide sobre lo registrado. Si no coincide, Observar pasa a ser el botón principal.
  const decideElPago = (paso === "conciliar" || ficha.puede_resolver_diferencia) && registrado !== null && comprobanteLeido !== null;
  const lecturaDistinta = decideElPago && comprobanteLeido.distinto_de_lo_registrado.length > 0;
  const lecturaIgual = decideElPago && comprobanteLeido.coincide_con_lo_registrado;
  // Sin diferencias no quiere decir que coincida: lo que no se leyó no se comparó.
  const lecturaIncompleta = decideElPago && comprobanteLeido.estado === "leida" && !lecturaDistinta && !lecturaIgual;

  function escribir(cual: ConMotivo) {
    setMotivo("");
    setConMotivo(cual);
  }

  function abrirCuenta() {
    void acto.hacer({
      quePide: ["cuenta", orden, v.numero, v.huella],
      nombre: "ver la cuenta para pagar",
      pedir: () =>
        api.POST("/api/v1/consorcios/{consorcio_id}/ordenes-pago/{orden_id}/pago-manual/cuenta", {
          headers: encabezadoCsrf(),
          params: { path: ruta },
          body: { version: v.numero, huella: v.huella },
        }),
      alTerminar: (data) => {
        setCuenta(data);
        releer();
        alCambiar();
      },
      alFallar: siCambio,
    });
  }

  /** Emitir deja de ser un paso (§3): aprobada, se emite y se abre la cuenta con un solo gesto.
   * Las dos rutas valen con el mismo segundo factor reciente. `pago-manual/cuenta` sola no pasa de
   * aprobada a en ejecución. */
  function verCuenta() {
    if (situacion !== "para_pagar") return abrirCuenta();
    void acto.hacer({
      quePide: ["emitir", orden, v.numero, v.huella],
      nombre: "ver la cuenta para pagar",
      pedir: (clave) =>
        api.POST("/api/v1/consorcios/{consorcio_id}/ordenes-pago/{orden_id}/emitir", {
          headers: encabezadoCsrf(),
          params: { path: ruta, header: { [ENCABEZADO_IDEMPOTENCIA]: clave } },
          body: { version: v.numero, huella: v.huella },
        }),
      alTerminar: () => {
        releer();
        alCambiar();
        abrirCuenta();
      },
      alFallar: siCambio,
    });
  }

  function enviar() {
    void acto.hacer({
      quePide: ["enviar", orden, v.numero],
      nombre: "enviar la orden",
      pedir: (clave) =>
        api.POST("/api/v1/consorcios/{consorcio_id}/ordenes-pago/{orden_id}/enviar-a-revision", {
          headers: encabezadoCsrf(),
          params: { path: ruta, header: { [ENCABEZADO_IDEMPOTENCIA]: clave } },
          body: { version_esperada: v.numero },
        }),
      alTerminar: () =>
        terminar(v.alerta_fraccionamiento ? "Listo: la orden se envió. La aprueba el Consejo." : "Listo: la orden se envió. Si su franja no pide aprobación, ya quedó aprobada."),
      alFallar: siCambio,
    });
  }

  function renovar() {
    void acto.hacer({
      quePide: ["renovar", orden, v.numero],
      nombre: "renovar la orden",
      pedir: (clave) =>
        api.POST("/api/v1/consorcios/{consorcio_id}/ordenes-pago/{orden_id}/renovar", {
          headers: encabezadoCsrf(),
          params: { path: ruta, header: { [ENCABEZADO_IDEMPOTENCIA]: clave } },
          body: { version_esperada: v.numero },
        }),
      alTerminar: () => terminar("Listo: la orden tiene una versión nueva y volvió a revisión."),
      alFallar: siCambio,
    });
  }

  /** Manda la versión y la huella que se ven: si la orden cambió, la ficha muestra la nueva y la
   * aprobación no pasa sola a otra versión. */
  function aprobar() {
    void acto.hacer({
      quePide: ["aprobar", orden, v.numero, v.huella],
      nombre: "aprobar la orden",
      pedir: (clave) =>
        api.POST("/api/v1/consorcios/{consorcio_id}/ordenes-pago/{orden_id}/aprobaciones", {
          headers: encabezadoCsrf(),
          params: { path: ruta, header: { [ENCABEZADO_IDEMPOTENCIA]: clave } },
          body: { version: v.numero, huella: v.huella },
        }),
      alTerminar: () => terminar(`Listo: aprobaste la versión ${v.numero}.`),
      alFallar: siCambio,
    });
  }

  function rechazar(texto: string) {
    void acto.hacer({
      quePide: ["rechazar", orden, v.numero, v.huella, texto],
      nombre: "rechazar la orden",
      pedir: (clave) =>
        api.POST("/api/v1/consorcios/{consorcio_id}/ordenes-pago/{orden_id}/rechazar", {
          headers: encabezadoCsrf(),
          params: { path: ruta, header: { [ENCABEZADO_IDEMPOTENCIA]: clave } },
          body: { version: v.numero, motivo: texto },
        }),
      alTerminar: () => terminar("Listo: rechazaste la orden."),
      alFallar: siCambio,
    });
  }

  function declarar(texto: string) {
    void acto.hacer({
      quePide: ["declarar", orden, v.numero, texto],
      nombre: "declarar que no se pagó",
      pedir: (clave) =>
        api.POST("/api/v1/consorcios/{consorcio_id}/ordenes-pago/{orden_id}/pago-manual/no-realizado", {
          headers: encabezadoCsrf(),
          params: { path: ruta, header: { [ENCABEZADO_IDEMPOTENCIA]: clave } },
          body: { version: v.numero, motivo: texto },
        }),
      alTerminar: () => terminar("Listo: declaraste que no se pagó. Lo confirma otro miembro del Consejo."),
      alFallar: siCambio,
    });
  }

  function conciliar(pagoId: string, resultado: "conforme" | "observado", texto: string | null) {
    void acto.hacer({
      quePide: ["conciliar", orden, pagoId, resultado, texto],
      nombre: "conciliar el pago",
      pedir: (clave) =>
        api.POST("/api/v1/consorcios/{consorcio_id}/ordenes-pago/{orden_id}/pago-manual/{pago_id}/conciliacion", {
          headers: encabezadoCsrf(),
          params: { path: { ...ruta, pago_id: pagoId }, header: { [ENCABEZADO_IDEMPOTENCIA]: clave } },
          body: texto === null ? { resultado } : { resultado, motivo: texto },
        }),
      alTerminar: () =>
        terminar(
          resultado === "conforme"
            ? "Listo: conciliaste el pago. La orden quedó pagada."
            : "Listo: observaste el pago. Quien lo registró tiene que corregirlo.",
          { paso: "conciliar", abierta: montado },
        ),
      alFallar: siCambio,
    });
  }

  function confirmarNoPago(declaracionId: string) {
    void acto.hacer({
      quePide: ["confirmar", orden, declaracionId],
      nombre: "confirmar que no se pagó",
      pedir: (clave) =>
        api.POST("/api/v1/consorcios/{consorcio_id}/ordenes-pago/{orden_id}/pago-manual/no-realizado/{declaracion_id}/confirmacion", {
          headers: encabezadoCsrf(),
          params: { path: { ...ruta, declaracion_id: declaracionId }, header: { [ENCABEZADO_IDEMPOTENCIA]: clave } },
        }),
      alTerminar: () => terminar("Listo: confirmaste que no se pagó."),
      alFallar: siCambio,
    });
  }

  function resolver(d: Diferencia, elegida: Resolucion) {
    void acto.hacer({
      quePide: ["resolver", orden, d.id, elegida],
      nombre: "resolver el pago",
      pedir: (clave) =>
        api.POST("/api/v1/consorcios/{consorcio_id}/ordenes-pago/{orden_id}/pago-manual/diferencia/{diferencia_id}/resolucion", {
          headers: encabezadoCsrf(),
          params: { path: { ...ruta, diferencia_id: d.id }, header: { [ENCABEZADO_IDEMPOTENCIA]: clave } },
          body: { resolucion: elegida },
        }),
      alTerminar: () =>
        terminar(elegida === "reclamo" ? "Listo: la orden queda abierta hasta recuperar la plata." : "Listo: la orden quedó pagada por lo que salió del banco."),
      alFallar: siCambio,
    });
  }

  function recuperar(d: Diferencia) {
    void acto.hacer({
      quePide: ["recuperar", orden, d.id],
      nombre: "decir que se recuperó la plata",
      pedir: (clave) =>
        api.POST("/api/v1/consorcios/{consorcio_id}/ordenes-pago/{orden_id}/pago-manual/diferencia/{diferencia_id}/recuperada", {
          headers: encabezadoCsrf(),
          params: { path: { ...ruta, diferencia_id: d.id }, header: { [ENCABEZADO_IDEMPOTENCIA]: clave } },
        }),
      alTerminar: () => terminar("Listo: la orden quedó como no pagada, y se puede volver a pagar."),
      alFallar: siCambio,
    });
  }

  function cerrarReclamo(d: Diferencia, texto: string) {
    void acto.hacer({
      quePide: ["cerrar-reclamo", orden, d.id, texto],
      nombre: "cerrar el reclamo",
      pedir: (clave) =>
        api.POST("/api/v1/consorcios/{consorcio_id}/ordenes-pago/{orden_id}/pago-manual/diferencia/{diferencia_id}/cierre", {
          headers: encabezadoCsrf(),
          params: { path: { ...ruta, diferencia_id: d.id }, header: { [ENCABEZADO_IDEMPOTENCIA]: clave } },
          body: { motivo: texto },
        }),
      alTerminar: () => terminar("Listo: cerraste el reclamo sin recuperar la plata. La diferencia queda en la historia."),
      alFallar: siCambio,
    });
  }

  /** No da la cuenta: suelta a quien la vio. La ficha que vuelve dice qué sigue. */
  function tomar() {
    void acto.hacer({
      quePide: ["tomar", orden, v.numero, v.huella],
      nombre: "tomar el pago",
      pedir: (clave) =>
        api.POST("/api/v1/consorcios/{consorcio_id}/ordenes-pago/{orden_id}/pago-manual/tomar", {
          headers: encabezadoCsrf(),
          params: { path: ruta, header: { [ENCABEZADO_IDEMPOTENCIA]: clave } },
          body: { version: v.numero, huella: v.huella, no_se_transfirio: true },
        }),
      alTerminar: () => {
        setConfirmaToma(false);
        setAviso({ texto: "Listo: tomaste el pago.", ok: true });
        releer();
        alCambiar();
      },
      alFallar: siCambio,
    });
  }

  /** Los mismos datos: la versión nueva pasa otra vez por aprobación, por su franja si fue simple
   * y por el Consejo si hubo una diferencia (decisión 11, desvío de §9). Para cambiar algo está
   * «Modificar». */
  function volverAPagar() {
    void acto.hacer({
      quePide: ["volver-a-pagar", orden, v.numero],
      nombre: "volver a pagar",
      pedir: (clave) =>
        api.POST("/api/v1/consorcios/{consorcio_id}/ordenes-pago/{orden_id}/versiones", {
          headers: encabezadoCsrf(),
          params: { path: ruta, header: { [ENCABEZADO_IDEMPOTENCIA]: clave } },
          body: {
            version_esperada: v.numero,
            importe_centavos: v.importe_centavos,
            moneda: v.moneda,
            concepto: v.concepto,
            proveedor_id: v.proveedor_id,
            comprobante: v.comprobante,
            usa_fondo_reserva: v.usa_fondo_reserva,
            contrato_id: v.contrato_id,
          },
        }),
      alTerminar: () => terminar(`Listo: la orden tiene una versión nueva, con los mismos datos. ${diferencia === null ? AVISO_POR_FRANJA : AVISO_POR_EL_CONSEJO}`),
      alFallar: siCambio,
    });
  }

  function enviarMotivo(evento: FormEvent) {
    evento.preventDefault();
    const texto = motivo.trim();
    if (texto === "") return;
    if (conMotivo === "rechazar") rechazar(texto);
    else if (conMotivo === "declarar") declarar(texto);
    else if (conMotivo === "cerrar") {
      if (diferencia !== null) cerrarReclamo(diferencia, texto);
    }
    else if (pago !== undefined) conciliar(pago.id, conMotivo === "observar" ? "observado" : "conforme", texto);
  }

  // Lo que se escribe para decidir, sobre la misma ficha. Sin la acción, el formulario se va.
  const motivoAbierto =
    (conMotivo === "rechazar" && ficha.puede_rechazar) ||
    (conMotivo === "declarar" && declara) ||
    (conMotivo === "cerrar" && ficha.puede_recuperar) ||
    ((conMotivo === "conforme" || conMotivo === "observar") && ficha.puede_conciliar)
      ? conMotivo
      : null;
  const TEXTOS_MOTIVO: Record<ConMotivo, { etiqueta: string; boton: string; largo: number }> = {
    rechazar: { etiqueta: "Motivo del rechazo", boton: "Rechazar la orden", largo: 500 },
    declarar: { etiqueta: "Por qué no se pagó", boton: "Declarar que no se pagó", largo: 200 },
    conforme: { etiqueta: "Por qué lo aceptás fuera de término", boton: "Dar el pago por conforme", largo: 300 },
    observar: { etiqueta: "Qué no coincide", boton: "Observar el pago", largo: 300 },
    cerrar: { etiqueta: "Por qué se cierra sin recuperar", boton: "Cerrar sin recuperar", largo: 300 },
  };

  const estaModificando = modificando && ficha.puede_modificar;
  const elegida: Resolucion = diferencia?.se_puede_dar_por_pagada ? (resolucion ?? "pagada_por_lo_real") : "reclamo";

  return (
    <>
      <AvisoPantalla aviso={aviso} />
      {pendiente !== null && (
        <PedirCodigo
          acto={pendiente.acto}
          alVerificar={() => {
            const { reintentar } = pendiente;
            setPendiente(null);
            reintentar();
          }}
          alCancelar={() => setPendiente(null)}
        />
      )}

      {/* Mientras se pide el código, lo escrito sigue montado: el reintento no lo pierde. */}
      <div hidden={pendiente !== null}>
        <section className="ficha-pago" aria-labelledby={`${id}-titulo`}>
          <div className="ficha-pago__resumen">
          <p className="etiqueta-situacion" data-tono={TONO_SITUACION[situacion]}>
            {TEXTO_SITUACION[situacion]}
          </p>
          <h2 id={`${id}-titulo`}>{v.razon_social}</h2>
          <div className="fila-importe">
            <p className="importe-ficha">{importe(v.importe_centavos, v.moneda)}</p>
            {cuentaVista !== null && <BotonCopiar valor={importeParaCopiar(v.importe_centavos)} nombre="el importe" />}
          </div>
          <p>{v.concepto}</p>
          </div>

          {enDecision && v.alerta_fraccionamiento && (
            <p className="aviso-error" role="note">
              {TEXTO_ALERTA_FRACCIONAMIENTO}
            </p>
          )}
          {situacion === "borrador" && v.alerta_fraccionamiento && <p role="note">{AVISO_LO_DECIDE_EL_CONSEJO}</p>}
          {enDecision && anterior !== undefined && cambiosDeVersion(v, anterior).map((c) => <p key={c}>{c}</p>)}
          {ficha.orden.motivo_rechazo !== null && situacion === "rechazada" && (
            <p>
              <strong>Motivo del rechazo:</strong> {ficha.orden.motivo_rechazo}
            </p>
          )}
          {situacion === "no_se_pago" && (
            <p>
              {diferencia?.cerrada_en != null
                ? `Se cerró el reclamo sin recuperar la plata: ${diferencia.motivo_cierre}.`
                : diferencia?.recuperada_en == null
                  ? "El Consejo confirmó que no se pagó."
                  : "Se recuperó la plata."}{" "}
              {diferencia === null ? AVISO_POR_FRANJA : AVISO_POR_EL_CONSEJO}
            </p>
          )}
          {observacion !== null && (
            <p className="aviso-error" role="note">
              El Consejo observó el pago: «{observacion.motivo}». {AVISO_NO_TRANSFIERAS}
            </p>
          )}
          {declaracion !== null && (
            <p role="note">
              Se declaró que no se pagó el {momento(declaracion.declarado_en)}: {declaracion.motivo}. No pagues esta orden mientras tanto.
            </p>
          )}
          {paso === "espera_resolver" && (
            <p className="aviso-error" role="note">
              {AVISO_DIFERENCIA_ESPERA}
            </p>
          )}
          {paso === "espera_comprobante" && (
            <p role="note">
              Ya se vio la cuenta: no pagues esta orden mientras se espera el comprobante.
              {ficha.tomar_desde !== null && ` Si no aparece, desde el ${momento(ficha.tomar_desde)} vas a poder tomar el pago.`}
            </p>
          )}
          {ficha.toma_vigente !== null && (
            <p role="note">
              {ficha.toma_vigente.tomado_por_nombre} tomó este pago el {momento(ficha.toma_vigente.tomada_en)}.
              {ficha.puede_registrar && !ficha.puede_reabrir_cuenta && " Si transferiste vos, registralo ahora."}
            </p>
          )}
          {ficha.le_toca_a !== null && ficha.le_toca_a !== "vos" && <p>{TEXTO_LE_TOCA_A[ficha.le_toca_a]}</p>}

          {cuentaVista !== null && (
            <div className="tarjeta">
              <h3>Transferí desde tu banco</h3>
              <dl className="detalles">
                <dt>CBU/CVU</dt>
                <dd>
                  <code>{cuentaVista.cuenta_destino}</code>
                  <BotonCopiar valor={cuentaVista.cuenta_destino.replace(/\s/g, "")} nombre="el CBU" />
                </dd>
                <dt>CUIT del titular</dt>
                <dd>{v.cuit ?? "Sin CUIT"}</dd>
                <dt>Pagar antes de</dt>
                <dd>{momento(cuentaVista.aprobacion_vence_en)}</dd>
              </dl>
              <p className="aviso-error" role="note">
                Antes de confirmar en tu banco, mirá que el titular sea <strong>{v.razon_social}</strong>
              </p>
            </div>
          )}

          {!estaModificando && (
            <div className={conVisor ? "comprobante-y-datos" : undefined}>
            {conVisor && ficha.comprobante_actual !== null && (
              <VisorComprobante key={ficha.comprobante_actual.version_documento_id} consorcio={consorcio}
                documento={ficha.comprobante_actual.documento_id} version={ficha.comprobante_actual.version_documento_id} aEntrar={aEntrar} />
            )}
            <div className="tarjeta">
              {lecturaDistinta && <DistintoDeLoRegistrado lectura={comprobanteLeido} registrado={registrado} />}
              {lecturaIgual && <p className="aviso-ok">{AVISO_LECTURA_COINCIDE}</p>}
              {lecturaIncompleta && <p role="note">{AVISO_LECTURA_INCOMPLETA}</p>}
              {diferencia !== null && (
                <>
                  <table className="comparar-pago">
                    <caption>La orden contra lo que salió del banco</caption>
                    <thead>
                      <tr>
                        <td />
                        <th scope="col">Orden</th>
                        <th scope="col">Salió</th>
                      </tr>
                    </thead>
                    <tbody>
                      <tr>
                        <th scope="row">Importe</th>
                        <td>{importe(diferencia.importe_orden_centavos, diferencia.moneda_orden)}</td>
                        <td>{importe(centavosReales(diferencia), diferencia.moneda)}</td>
                      </tr>
                      <tr>
                        <th scope="row">Cuenta</th>
                        <td>{cuentaEnmascarada(v.cuenta_ultimos_digitos)}</td>
                        <td>{diferencia.tipo === "importe" ? "La misma" : cuentaEnmascarada(diferencia.cuenta_ultimos_digitos)}</td>
                      </tr>
                    </tbody>
                  </table>
                  <dl className="detalles">
                    {diferencia.falta_centavos !== null && (
                      <>
                        <dt>Falta pagar</dt>
                        <dd>{importe(diferencia.falta_centavos, diferencia.moneda)}</dd>
                      </>
                    )}
                    {diferencia.sobra_centavos !== null && (
                      <>
                        <dt>Se pagó de más</dt>
                        <dd>{importe(diferencia.sobra_centavos, diferencia.moneda)}</dd>
                      </>
                    )}
                    <dt>Qué dijo quien pagó</dt>
                    <dd>{diferencia.explicacion}</dd>
                    <dt>Número de operación</dt>
                    <dd>{diferencia.numero_operacion}</dd>
                    <dt>Fecha de la transferencia</dt>
                    <dd>{diferencia.fecha_operacion}</dd>
                    <dt>Registró</dt>
                    <dd>
                      {diferencia.registrada_por_nombre}, {momento(diferencia.registrada_en)}
                    </dd>
                    {diferencia.resuelta_en !== null && (
                      <>
                        <dt>{diferencia.resolucion === "reclamo" ? "Reclamo desde" : "Se dio por pagado"}</dt>
                        <dd>{momento(diferencia.resuelta_en)}</dd>
                      </>
                    )}
                  </dl>
                </>
              )}
              {paso === "conciliar" && pago !== undefined && (
                <table className="comparar-pago">
                  <caption>La orden contra lo registrado</caption>
                  <thead>
                    <tr>
                      <td />
                      <th scope="col">Orden</th>
                      <th scope="col">Registrado</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr>
                      <th scope="row">Importe</th>
                      <td>{importe(v.importe_centavos, v.moneda)}</td>
                      <td>{importe(pago.importe_centavos, pago.moneda)}</td>
                    </tr>
                    <tr>
                      <th scope="row">Cuenta</th>
                      <td>{cuentaDecidida?.cuenta ?? cuentaEnmascarada(v.cuenta_ultimos_digitos)}</td>
                      <td>La misma: el registro no se acepta con otra</td>
                    </tr>
                  </tbody>
                </table>
              )}
              <dl className="detalles">
                {enDecision && (
                  <>
                    <dt>Cuenta destino</dt>
                    <dd>{cuentaDecidida?.cuenta ?? cuentaEnmascarada(v.cuenta_ultimos_digitos)}</dd>
                    <dt>Comprobante</dt>
                    <dd>{v.comprobante ?? "Sin comprobante"}</dd>
                  </>
                )}
                {situacion === "en_revision" && (
                  <>
                    <dt>Aprobaciones</dt>
                    <dd>
                      {v.aprobada_por.length} de {v.aprobaciones_requeridas}
                    </dd>
                  </>
                )}
                {paso === "pagar" && ficha.orden.aprobacion_vence_en !== null && (
                  <>
                    <dt>Pagar antes de</dt>
                    <dd>{momento(ficha.orden.aprobacion_vence_en)}</dd>
                  </>
                )}
                {paso === "renovar" && ficha.orden.aprobacion_vence_en !== null && (
                  <>
                    <dt>La aprobación vencía</dt>
                    <dd>{momento(ficha.orden.aprobacion_vence_en)}</dd>
                  </>
                )}
                {pago !== undefined && diferencia === null && (
                  <>
                    <dt>Número de operación</dt>
                    <dd>{pago.numero_operacion}</dd>
                    <dt>Fecha de la transferencia</dt>
                    <dd>{pago.fecha_operacion}</dd>
                    {paso !== "conciliar" && (
                      <>
                        <dt>Importe registrado</dt>
                        <dd>{importe(pago.importe_centavos, pago.moneda)}</dd>
                      </>
                    )}
                    <dt>Registró</dt>
                    <dd>
                      {pago.registrado_por_nombre}, {momento(pago.registrado_en)}
                    </dd>
                  </>
                )}
                {pago?.lectura_disputada === true && (
                  <>
                    <dt>Lectura del comprobante</dt>
                    <dd>{AVISO_LECTURA_DISPUTADA}: los datos los escribió a mano. Comparalos con el archivo.</dd>
                  </>
                )}
                {pago?.fuera_de_termino === true && (
                  <>
                    <dt>Fuera de término</dt>
                    <dd>
                      Se transfirió después de que venció la aprobación.
                      {ficha.puede_conciliar && " Para darlo por conforme, escribí por qué lo aceptás."}
                    </dd>
                  </>
                )}
              </dl>

              {paso === "pagar" && ficha.puede_pagar && cuentaVista === null && (
                <p role="note">{AVISO_VER_LA_CUENTA}</p>
              )}
              {paso === "subir_comprobante" && situacion === "pagando" && !ficha.puede_reabrir_cuenta && cuentaVista === null && (
                <p className="aviso-error" role="note">
                  {AVISO_SIN_CUENTA}
                </p>
              )}
              {paso === "declarar_no_pago" && (
                <p className="aviso-error" role="note">
                  {AVISO_SIN_SALIDA}
                </p>
              )}
              {paso === "modificar" && (
                <p className="aviso-error" role="note">
                  {AVISO_PROVEEDOR_CAMBIADO}
                </p>
              )}
              {paso === "tomar_pago" && <p role="note">Quien vio la cuenta no registró el pago ni dijo que no salió.</p>}
              {confirmaToma && ficha.puede_tomar && (
                <p className="aviso-error" role="alert">
                  {AVISO_TOMAR}
                </p>
              )}
              {ficha.puede_reabrir_cuenta && situacion === "no_pago_declarado" && <p role="note">{AVISO_RETOMAR}</p>}

              {/* Queda montado: ir a declarar y volver no pierde lo escrito. */}
              <div hidden={motivoAbierto !== null}>
                  {registra && (
                    <RegistroPago key={paso} consorcio={consorcio} orden={orden} version={v} corregido={paso === "corregir" ? pago : undefined}
                      comprobante={ficha.comprobante_actual} subido={ficha.comprobante_subido} lectura={comprobanteLeido}
                      puedeOtraCosa={ficha.puede_registrar} acto={acto} aEntrar={aEntrar}
                      alRegistrar={(texto) => terminar(texto)} releer={releer}
                      alCambiarLaOrden={() => {
                        releer();
                        alCambiar();
                      }} />
                  )}
                  {corrigeDiferencia && ficha.puede_corregir_diferencia && diferencia !== null && (
                    <RegistroPago key={`diferencia-${diferencia.id}`} consorcio={consorcio} orden={orden} version={v} corregido={undefined}
                      diferencia={diferencia} alCancelar={() => setCorrigeDiferencia(false)} comprobante={ficha.comprobante_actual} subido={null}
                      lectura={null} puedeOtraCosa={false} acto={acto} aEntrar={aEntrar}
                      alRegistrar={(texto) => terminar(texto)} releer={releer}
                      alCambiarLaOrden={() => {
                        releer();
                        alCambiar();
                      }} />
                  )}
                  {ficha.puede_resolver_diferencia && diferencia !== null && (
                    <fieldset className="formulario">
                      <legend>Qué hacemos</legend>
                      {diferencia.se_puede_dar_por_pagada && (
                        <label className="opcion-formulario">
                          <input type="radio" name={`${id}-resolucion`} checked={elegida === "pagada_por_lo_real"} disabled={enviando}
                            onChange={() => setResolucion("pagada_por_lo_real")} />
                          <span>
                            {`Dar por pagados ${importe(centavosReales(diferencia), diferencia.moneda)}`}
                            {diferencia.falta_centavos !== null && ` y preparar otra orden por ${importe(diferencia.falta_centavos, diferencia.moneda)}`}
                          </span>
                        </label>
                      )}
                      <label className="opcion-formulario">
                        <input type="radio" name={`${id}-resolucion`} checked={elegida === "reclamo"} disabled={enviando}
                          onChange={() => setResolucion("reclamo")} />
                        <span>Reclamar la devolución: la orden queda abierta hasta recuperar la plata</span>
                      </label>
                      <div className="acciones">
                        <button type="button" disabled={enviando} onClick={() => resolver(diferencia, elegida)}>
                          Resolver
                        </button>
                      </div>
                    </fieldset>
                  )}
                  <div className="formulario">
                    <div className="acciones">
                      {ficha.puede_corregir_diferencia && !corrigeDiferencia && (
                        <button type="button" className="secundario" disabled={enviando} onClick={() => setCorrigeDiferencia(true)}>
                          Corregir lo que registré
                        </button>
                      )}
                      {ficha.puede_recuperar && diferencia !== null && (
                        <button type="button" disabled={enviando} onClick={() => recuperar(diferencia)}>
                          Ya recuperamos la plata
                        </button>
                      )}
                      {ficha.puede_recuperar && diferencia !== null && (
                        <button type="button" className="secundario" disabled={enviando} onClick={() => escribir("cerrar")}>
                          Cerrar sin recuperar
                        </button>
                      )}
                      {paso === "enviar" && ficha.puede_enviar && (
                        <button type="button" disabled={enviando} onClick={enviar}>
                          Enviar a aprobar
                        </button>
                      )}
                      {paso === "renovar" && ficha.puede_renovar && (
                        <button type="button" disabled={enviando} onClick={renovar}>
                          Renovar la aprobación
                        </button>
                      )}
                      {paso === "volver_a_pagar" && ficha.puede_modificar && (
                        <button type="button" disabled={enviando} onClick={volverAPagar}>
                          Volver a pagar
                        </button>
                      )}
                      {ficha.puede_aprobar && (
                        <button type="button" disabled={enviando || cuentaDecidida === null} onClick={aprobar}>
                          Aprobar esta versión
                        </button>
                      )}
                      {decide && faltaCuenta && (
                        <button type="button" className="secundario" disabled={enviando}
                          onClick={() => {
                            setAviso(sinLaFalla);
                            setVueltaDecision((n) => n + 1);
                          }}>
                          Volver a pedir la cuenta
                        </button>
                      )}
                      {ficha.puede_rechazar && (
                        <button type="button" className={ficha.puede_aprobar ? "secundario" : undefined} disabled={enviando}
                          onClick={() => escribir("rechazar")}>
                          Rechazar
                        </button>
                      )}
                      {paso === "pagar" && ficha.puede_pagar && cuentaVista === null && (
                        <button type="button" disabled={enviando} onClick={verCuenta}>
                          Ver la cuenta para pagar
                        </button>
                      )}
                      {ficha.puede_tomar && !confirmaToma && (
                        <button type="button" disabled={enviando} onClick={() => setConfirmaToma(true)}>
                          Tomar el pago
                        </button>
                      )}
                      {ficha.puede_tomar && confirmaToma && (
                        <>
                          <button type="button" disabled={enviando} onClick={tomar}>
                            No se transfirió: tomar el pago
                          </button>
                          <button type="button" className="secundario" disabled={enviando} onClick={() => setConfirmaToma(false)}>
                            Cancelar
                          </button>
                        </>
                      )}
                      {ficha.puede_conciliar && pago !== undefined && (
                        <>
                          {lecturaDistinta && (
                            <button type="button" disabled={enviando} onClick={() => escribir("observar")}>
                              Observar
                            </button>
                          )}
                          <button type="button" className={lecturaDistinta ? "secundario" : undefined} disabled={enviando}
                            onClick={() => (pago.fuera_de_termino ? escribir("conforme") : conciliar(pago.id, "conforme", null))}>
                            Conforme
                          </button>
                          {!lecturaDistinta && (
                            <button type="button" className="secundario" disabled={enviando} onClick={() => escribir("observar")}>
                              Observar
                            </button>
                          )}
                        </>
                      )}
                      {ficha.puede_confirmar && declaracion !== null && (
                        <button type="button" disabled={enviando} onClick={() => confirmarNoPago(declaracion.id)}>
                          Confirmar que no se pagó
                        </button>
                      )}
                      {ficha.puede_reabrir_cuenta && cuentaVista === null && (
                        <button type="button" className="secundario" disabled={enviando} onClick={abrirCuenta}>
                          {situacion === "no_pago_declarado" ? "Retomar el pago" : "Ver la cuenta de nuevo"}
                        </button>
                      )}
                      {declara && (
                        <button type="button" className={paso === "declarar_no_pago" ? undefined : "secundario"} disabled={enviando}
                          onClick={() => escribir("declarar")}>
                          No se pudo pagar
                        </button>
                      )}
                      {ficha.puede_modificar && (
                        <button type="button" className={paso === "modificar" ? undefined : "secundario"} disabled={enviando} onClick={() => setModificando(true)}>
                          Modificar
                        </button>
                      )}
                    </div>
                  </div>
              </div>
              {motivoAbierto !== null && (
                <form className="formulario" onSubmit={enviarMotivo}>
                  <label htmlFor={`${id}-motivo`}>{TEXTOS_MOTIVO[motivoAbierto].etiqueta}</label>
                  <input id={`${id}-motivo`} required maxLength={TEXTOS_MOTIVO[motivoAbierto].largo} value={motivo} disabled={enviando}
                    onChange={(e) => setMotivo(e.target.value)} />
                  <div className="acciones">
                    <button type="submit" disabled={enviando || motivo.trim() === ""}>
                      {TEXTOS_MOTIVO[motivoAbierto].boton}
                    </button>
                    <button type="button" className="secundario" disabled={enviando} onClick={() => setConMotivo(null)}>
                      Cancelar
                    </button>
                  </div>
                </form>
              )}
              {ficha.puede_preparar_faltante && diferencia?.falta_centavos != null && (
                <p>
                  <Link to={RUTA_NUEVA}
                    state={{ consorcio, proveedor: v.proveedor_id, importe: aTexto(diferencia.falta_centavos), moneda: v.moneda, concepto: `Lo que faltó de: ${v.concepto}` }}>
                    Preparar la orden por lo que falta
                  </Link>
                </p>
              )}
              {paso === "rechazada" && (
                <p>
                  <Link to={RUTA_NUEVA} state={{ consorcio, proveedor: v.proveedor_id }}>
                    Preparar otra orden
                  </Link>
                </p>
              )}
            </div>
            </div>
          )}

          {estaModificando && (
            <FormularioOrden consorcio={consorcio} acto={acto} aEntrar={aEntrar} aInicio={aInicio}
              modificar={{ orden, version: v, pierdeAprobaciones: situacion === "en_revision" || situacion === "para_pagar" }}
              alGuardar={(_, texto) => {
                setModificando(false);
                setAviso({ texto, ok: true });
                releer();
                alCambiar();
              }}
              alCancelar={() => setModificando(false)}
              alCambiarLaOrden={() => {
                releer();
                alCambiar();
              }} />
          )}

          <details className="historial">
            <summary>Más datos</summary>
            <dl className="detalles">
              <dt>CUIT</dt>
              <dd>{v.cuit ?? "Sin CUIT"}</dd>
              <dt>Beneficiario</dt>
              <dd>{TEXTO_CONDICION[v.condicion_beneficiario]}</dd>
              <dt>Franja</dt>
              <dd>{textoFranja(v.franja, ficha.orden.estado)}</dd>
              <dt>Fondo de reserva</dt>
              <dd>{v.usa_fondo_reserva ? "Sí" : "No"}</dd>
              <dt>Versión de la orden</dt>
              <dd>{v.numero}</dd>
              <dt>Versión del proveedor</dt>
              <dd>{v.proveedor_version}</dd>
              <dt>Preparada</dt>
              <dd>{momento(v.creada_en)}</dd>
            </dl>
            {ficha.versiones.length > 1 && (
              <>
                <h3>Versiones anteriores</h3>
                <ol className="versiones">
                  {ficha.versiones
                    .filter((x) => x.numero !== v.numero)
                    .map((x) => (
                      <li key={x.numero}>
                        Versión {x.numero}: {importe(x.importe_centavos, x.moneda)}, {x.razon_social}, cuenta{" "}
                        {cuentaEnmascarada(x.cuenta_ultimos_digitos)}
                      </li>
                    ))}
                </ol>
              </>
            )}
            {ficha.diferencias_anteriores.length > 0 && (
              <>
                <h3>Lo que se registró antes</h3>
                <ol className="versiones">
                  {ficha.diferencias_anteriores.map((d) => (
                    <li key={d.id}>
                      Operación {d.numero_operacion}, {d.fecha_operacion}: {importe(centavosReales(d), d.moneda)}, cuenta{" "}
                      {cuentaEnmascarada(d.cuenta_ultimos_digitos)}. Lo registró {d.registrada_por_nombre}, {momento(d.registrada_en)}
                    </li>
                  ))}
                </ol>
              </>
            )}
            {ejecucion !== null && ejecucion.pagos.length > 1 && (
              <>
                <h3>Registros anteriores</h3>
                <ol className="versiones">
                  {ejecucion.pagos.slice(0, -1).map((p) => (
                    <li key={p.id}>
                      Operación {p.numero_operacion}, {p.fecha_operacion}: {importe(p.importe_centavos, p.moneda)}. Lo registró{" "}
                      {p.registrado_por_nombre}, {momento(p.registrado_en)}
                      {p.motivo_conciliacion !== null && `. Observado: ${p.motivo_conciliacion}`}
                    </li>
                  ))}
                </ol>
              </>
            )}
          </details>
        </section>
      </div>
    </>
  );
}
