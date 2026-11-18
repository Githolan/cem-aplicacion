import { type RefObject, useEffect, useId, useMemo, useRef, useState } from "react";
import { Link } from "react-router";

import { api } from "../api/cliente";
import type { components } from "../api/tipos";
import { useVersionVisible } from "../CargaConLectura";
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
  mensajeDeActo,
  mensajeDeError,
  fechaLegible,
  momento,
  momentoAlFinal,
  sinLaFalla,
  sinPisarLoHecho,
  TEXTO_ALERTA_FRACCIONAMIENTO,
  TEXTO_CONDICION,
  textoFranja,
} from "../egresos/comun";
import { TEXTO_TIPO, textoEstado } from "../egresos/DocumentacionProveedor";
import { AvisoPantalla, FallaDeLectura } from "../egresos/EstadoPantalla";
import { PedirCodigo } from "../egresos/PedirCodigo";
import { textoSalteoDeLaVersion } from "../egresos/SalteoDocumentacion";
import { exigirSesion, type MonedasDelEdificio, SinSesion, useConsultaDelAntivirus } from "../egresos/useConsorcio";
import { ErrorDeCampo, erroresDe, type ErroresDeCampo, faltaCompletar, hayErrores, marcaDe, SIN_ERRORES, sinElDe, soloDe } from "../ErrorDeCampo";
import { codigoDe, SIN_CONEXION } from "../fallas";
import { Hoja } from "../Hoja";
import { useListoHastaOtroActo } from "../listoHastaOtroActo";
import { type FinDeEnvio, VentanaDeFormulario } from "../VentanaDeFormulario";
import { VisorDocumento } from "../VisorDocumento";
import { Acciones } from "./Acciones";
import { type AccionPago, TEXTO_ACCION } from "./textosDeAcciones";
import { Barra } from "./Barra";
import { BotonCopiar } from "./BotonCopiar";
import { nombreDeMoneda } from "./CampoMonedaSalida";
import { leerComoQuedo, leerComoQuedoAlAprobar, TEXTO_APROBADA, TEXTO_ENVIADA } from "./comoQuedo";
import { FormularioOrden } from "./FormularioOrden";
import { RegistroPago } from "./RegistroPago";
import { RUTA_NUEVA, rutaFicha } from "./rutas";
import { REEMPLAZAR_FACTURA, SUBIR_FACTURA, SubirFactura } from "./SubirFactura";
import {
  AVISO_DIFERENCIA_ESPERA,
  AVISO_ESPERA_COMPROBANTE,
  AVISO_LECTURA_INCOMPLETA,
  AVISO_LECTURA_DISPUTADA,
  AVISO_POR_EL_CONSEJO,
  AVISO_PASA_SOLA,
  AVISO_NO_TRANSFIERAS,
  AVISO_NO_TRANSFIERE,
  AVISO_OBSERVAR,
  AVISO_RECLAMO,
  AVISO_SIN_CUENTA,
  TEXTO_SIN_ACTO,
  AVISO_SIN_SALIDA_CAUSA,
  AVISO_TOMAR,
  AVISO_VER_LA_CUENTA_QUEDA,
  CARGANDO_LA_CUENTA,
  CONFIRMAR_RETIRAR,
  DAR_POR_PAGADO,
  FALTA_LA_CUENTA,
  FALTA_MOTIVO,
  fechaCorta,
  NOMBRE_ESTADO,
  type Paso,
  PREGUNTA_CERRAR_SIN_PAGAR,
  PREGUNTA_RETIRAR,
  RECLAMAR,
  TEXTO_CERRADA_SIN_PAGO,
  TEXTO_RETIRADA,
  textoCierreSinPago,
  textoQuienRetiro,
  textoRepetida,
  TEXTO_LE_TOCA_A,
  TONO_ESTADO,
  VERBO_CONCILIAR,
  VERBO_PASA_SOLA,
} from "./textos";
import { type FinDelActo, useActo } from "./useActo";
import { VisorComprobante } from "./VisorComprobante";

// La ficha de una orden, una sola para todos los estados (§3). No deduce nada: el estado, la barra,
// la frase y las acciones (`acciones: [{codigo, tipo}]`) los manda el servidor, que los saca de la
// tabla de la pantalla y de lo que la base acepta (informe de fricción, parte 4). Revisar no es una
// pantalla más: la ficha muestra los datos y el botón principal confirma.
//
// La cuenta completa no viaja con la ficha. Llega con el acto de ejecución o, a quien decide la
// orden o concilia su pago, con `ordenes-pago/{id}/revision`, porque es lo que aprueba y lo que
// compara: quien preparó no la recibe (la revisión la manda nula). Vive en el estado de esta ficha y
// se va al cerrarla o cuando la orden cambia de versión. Sin ver la cuenta no se aprueba.

type FichaPago = components["schemas"]["FichaPago"];
type Cuenta = components["schemas"]["CuentaParaPagoManual"];
type Version = components["schemas"]["VersionOrdenPago"];
type FacturaAdjunta = components["schemas"]["FacturaAdjunta"];

type Lectura = { tipo: "cargando" } | { tipo: "sin_servicio" } | { tipo: "no_esta" } | { tipo: "ok"; ficha: FichaPago };

/** Lo que se dice si la orden no está en ningún edificio de quien mira (FL-BAN-02). */
export const NO_ESTA_EN_ESTE = "Esa orden no está en este edificio.";
export const NO_ESTA_EN_NINGUNO = "Esa orden no está en ninguno de los edificios de esta cuenta.";
/** El único campo de los formularios de motivo, por su nombre en la pantalla. */
const CAMPOS_MOTIVO = { motivo: "el motivo" } as const;

type ConMotivo = "rechazar" | "declarar" | "conforme" | "observar" | "cerrar" | "cerrar_sin_pagar";
type Diferencia = components["schemas"]["DiferenciaPagoManual"];
type Resolucion = components["schemas"]["ResolucionDiferencia"];
type LecturaComprobante = components["schemas"]["LecturaComprobante"];
/** Lo que se registró con el comprobante: un pago o lo que salió del banco. */
type Registrado = { centavos: number; moneda: string; numero_operacion: string; fecha_operacion: string };

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
          El comprobante dice <strong>{nombreDeMoneda(lectura.moneda ?? "")}</strong> y registraron <strong>{nombreDeMoneda(registrado.moneda)}</strong>.
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

/** La versión vigente con su factura: la propia o, si no tiene, la subida después de aprobada que
 * pasó el antivirus (app 0099). Es la que se ve y la que lleva una modificación, como al renovar. */
export function conFacturaAdjunta(v: Version, adjunta: FacturaAdjunta | null): Version {
  if (v.factura_version_id != null || adjunta === null || adjunta.estado !== "disponible" || adjunta.version !== v.numero) return v;
  return { ...v, documento_id: adjunta.documento_id, factura_version_id: adjunta.factura_version_id };
}

/** Lo que se dice de la factura subida después de aprobada (app 0099): quién, cuándo y cómo quedó. */
export function textoDeLaAdjunta(a: FacturaAdjunta): string {
  if (a.estado === "en_cuarentena") return `${a.adjuntada_por_nombre} subió la factura el ${momento(a.adjuntada_en)}: el antivirus la está revisando.`;
  if (a.estado === "rechazado") return `La factura que subió ${a.adjuntada_por_nombre} el ${momento(a.adjuntada_en)} no pasó el antivirus.`;
  if (a.reemplaza_a !== null) return `Factura que reemplazó a la anterior, subida por ${a.adjuntada_por_nombre}, el ${momentoAlFinal(a.adjuntada_en)}`;
  return `Factura subida después de aprobada la orden, por ${a.adjuntada_por_nombre}, el ${momentoAlFinal(a.adjuntada_en)}`;
}

/** Lo que se dice de la subida que quiere reemplazar a la adjunta: mientras tanto, o si el antivirus
 * la rechaza, vale la anterior (decisión del propietario del 13-11-2026). */
export function textoDelReemplazo(a: FacturaAdjunta): string {
  if (a.estado === "rechazado")
    return `La factura que subió ${a.adjuntada_por_nombre} el ${momento(a.adjuntada_en)} para reemplazarla no pasó el antivirus: sigue valiendo la anterior.`;
  return `${a.adjuntada_por_nombre} subió otra factura el ${momento(a.adjuntada_en)} para reemplazarla: el antivirus la está revisando. Mientras tanto vale la anterior.`;
}

/** Una subida anterior, como historia: no se borra, queda dicha con cómo terminó. */
export function textoDeLaAnterior(a: FacturaAdjunta): string {
  const cual = `la factura subida por ${a.adjuntada_por_nombre} el ${momento(a.adjuntada_en)}`;
  if (!a.documento_vigente) return `Borrada por soporte: ${cual}.`;
  if (a.estado === "rechazado") return `No pasó el antivirus: ${cual}.`;
  return `Reemplazada: ${cual}.`;
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

/** Qué cambió de una versión a la siguiente, para «Más datos»: «importe $ 40.000 → $ 45.000». Vacío si
 *  no cambió nada que se vea (comentario 25, 09-11-2026). */
export function cambiosEntreVersiones(antes: Version, despues: Version): string[] {
  const cambios: string[] = [];
  if (antes.importe_centavos !== despues.importe_centavos || antes.moneda !== despues.moneda)
    cambios.push(`importe ${importe(antes.importe_centavos, antes.moneda)} → ${importe(despues.importe_centavos, despues.moneda)}`);
  if (antes.proveedor_id !== despues.proveedor_id) cambios.push(`proveedor ${antes.razon_social} → ${despues.razon_social}`);
  if (antes.cuenta_ultimos_digitos !== despues.cuenta_ultimos_digitos)
    cambios.push(`cuenta ${cuentaEnmascarada(antes.cuenta_ultimos_digitos)} → ${cuentaEnmascarada(despues.cuenta_ultimos_digitos)}`);
  if (antes.concepto !== despues.concepto) cambios.push(`concepto «${antes.concepto}» → «${despues.concepto}»`);
  return cambios;
}

/** A qué paso se sigue, y si la ficha que lo pide sigue abierta cuando llega la bandeja. */
export type Seguir = { paso: Paso; abierta: RefObject<boolean> };

type Props = {
  consorcio: string;
  /** Las monedas en que opera el edificio, para modificar la orden (P-14). */
  monedas: MonedasDelEdificio;
  orden: string;
  aEntrar: () => void;
  aInicio: () => void;
  /** El acto terminó: pasa a la pantalla siguiente con el aviso; `seguir`, al próximo de ese paso.
   * `paso`: el paso que se acaba de hacer, para que en PC «Seguir con lo pendiente» siga por ahí. */
  terminar: (texto: string, seguir?: Seguir, paso?: Paso | null) => void;
  /** Algo cambió sin dejar la ficha: la bandeja de al lado se vuelve a leer. */
  alCambiar: () => void;
  /** Los otros edificios de quien mira, donde se busca la orden que no está en este (FL-BAN-02). */
  otrosEdificios: readonly string[];
  /** La orden está en otro edificio: la pantalla pasa a ese. */
  aOtroEdificio: (consorcio: string) => void;
};

export function Ficha({ consorcio, monedas, orden, aEntrar, aInicio, terminar: terminarActo, alCambiar, otrosEdificios, aOtroEdificio }: Props) {
  const id = useId();
  const acto = useActo(aEntrar, alCambiar);
  const { enviando, aviso, setAviso, pendiente, setPendiente, montado } = acto;
  // El «Listo» de un acto que deja la ficha abierta se va con el acto siguiente (FIC-11).
  const zonaAviso = useRef<HTMLDivElement>(null);
  useListoHastaOtroActo(zonaAviso, () => {
    if (aviso?.ok === true) setAviso(null);
  });
  /** Lo que dijo la última lectura que falló: se va cuando una lectura sale bien. */
  const fallaDeLectura = useRef<string | null>(null);
  const [lectura, setLectura] = useState<Lectura>({ tipo: "cargando" });
  const [vuelta, setVuelta] = useState(0);
  const [cuenta, setCuenta] = useState<Cuenta | null>(null);
  const [modificando, setModificando] = useState(false);
  const [retirando, setRetirando] = useState(false);
  const [conMotivo, setConMotivo] = useState<ConMotivo | null>(null);
  const [motivo, setMotivo] = useState("");
  // El error del motivo se dice junto al campo (tanda 3): p. ej. un número de cuenta escrito ahí.
  const [erroresMotivo, setErroresMotivo] = useState<ErroresDeCampo>(SIN_ERRORES);
  /** La cuenta que se decide, con la versión que muestra; `cuenta` nula si no hay qué mostrar. */
  const [decision, setDecision] = useState<{ version: number; cuenta: string | null } | null>(null);
  const [vueltaDecision, setVueltaDecision] = useState(0);
  const [faltaCuenta, setFaltaCuenta] = useState(false);
  /** El camino que eligió quien resuelve la diferencia, en su confirmación (FIC-04). */
  const [resolucion, setResolucion] = useState<Resolucion | null>(null);
  /** Registrar el pago, o corregirlo, en su ventana (REG-01). */
  const [registrando, setRegistrando] = useState(false);
  /** El botón que abre el registro no queda detrás de su ventana (dos «Registrar el pago»): al
   * cerrarla vuelve, y el foco con él. */
  const abreElRegistro = useRef<HTMLButtonElement>(null);
  const registroAbierto = useRef(false);
  useEffect(() => {
    if (registrando) registroAbierto.current = true;
    else if (registroAbierto.current) {
      registroAbierto.current = false;
      abreElRegistro.current?.focus();
    }
  }, [registrando]);
  /** Pidió tomar el pago: falta que confirme que no se transfirió. */
  const [confirmaToma, setConfirmaToma] = useState(false);
  /** Pidió corregir la diferencia que registró. */
  const [corrigeDiferencia, setCorrigeDiferencia] = useState(false);

  const leida = lectura.tipo === "ok" ? lectura.ficha : null;
  const decide = leida !== null && (leida.paso === "aprobar" || leida.puede_aprobar || leida.puede_rechazar || leida.puede_conciliar);
  const versionLeida = leida?.orden.version_vigente;
  /** El archivo exacto de la factura aprobada con la versión vigente (0088), si quien mira puede
   * abrirlo; se ve en una hoja. */
  const vigenteLeida = useMemo(() => (leida === null ? null : conFacturaAdjunta(vigenteDe(leida), leida.factura_adjunta)), [leida]);
  const factura = useVersionVisible(consorcio, vigenteLeida?.documento_id ?? null, vigenteLeida?.factura_version_id ?? null, aEntrar);
  const [viendoFactura, setViendoFactura] = useState(false);
  /** «Subir la factura» que falta o «Reemplazar la factura» adjunta, en una hoja (app 0099). */
  const [subiendoFactura, setSubiendoFactura] = useState<{ reemplaza: string | null } | null>(null);

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

  // Sin la cuenta no se aprueba: si la lectura falló, se repite sola al volver la red (FIC-14).
  useEffect(() => {
    if (!faltaCuenta) return;
    const otraVez = () => setVueltaDecision((n) => n + 1);
    window.addEventListener("online", otraVez);
    return () => window.removeEventListener("online", otraVez);
  }, [faltaCuenta]);

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

  // Con más de un edificio, la orden que no está en este se busca en los demás antes de decir que
  // no está (FL-BAN-02): un enlace a una orden puede llegar con otro edificio elegido.
  const [buscada, setBuscada] = useState<"buscando" | "en_ninguno" | null>(null);
  const noEsta = lectura.tipo === "no_esta";
  useEffect(() => {
    if (!noEsta || otrosEdificios.length === 0) return;
    let vigente = true;
    setBuscada("buscando");
    void (async () => {
      for (const otro of otrosEdificios) {
        const { data, response } = await api.GET("/api/v1/consorcios/{consorcio_id}/pagos/{orden_id}", {
          params: { path: { consorcio_id: otro, orden_id: orden } },
        });
        exigirSesion(response);
        if (!vigente) return;
        if (data !== undefined) return aOtroEdificio(otro);
      }
      setBuscada("en_ninguno");
    })().catch((e: unknown) => {
      if (!vigente) return;
      if (e instanceof SinSesion) aEntrar();
      else setBuscada("en_ninguno");
    });
    return () => {
      vigente = false;
    };
  }, [noEsta, otrosEdificios, orden, aOtroEdificio, aEntrar]);

  const releer = () => setVuelta((v) => v + 1);
  // La factura subida (o su reemplazo) sigue en el antivirus aunque se haya cerrado su hoja: la
  // ficha se vuelve a leer hasta que termina, para decir si la aceptó o la rechazó.
  const facturaEnRevision =
    leida !== null && (leida.factura_adjunta?.estado === "en_cuarentena" || leida.factura_reemplazo?.estado === "en_cuarentena");
  useConsultaDelAntivirus(facturaEnRevision, releer);

  /** Un acto con motivo que falla: el campo marcado, si el error nombra el motivo; lo demás, como siempre. */
  function fallaDelMotivo(estado: number, codigo: string | undefined, error: unknown) {
    setErroresMotivo(soloDe(erroresDe(error, mensajeDeActo(estado, error)), CAMPOS_MOTIVO));
    siCambio(estado, codigo);
  }

  function siCambio(estado: number, codigo: string | undefined) {
    if (cambioLaOrden(codigo) || cambioLoVisto(estado, codigo)) {
      releer();
      alCambiar();
    }
  }

  if (lectura.tipo === "cargando") return <p role="status">Cargando la orden…</p>;
  if (lectura.tipo === "no_esta") {
    if (otrosEdificios.length === 0) return <p className="tarjeta">{NO_ESTA_EN_ESTE}</p>;
    if (buscada === "en_ninguno") return <p className="tarjeta">{NO_ESTA_EN_NINGUNO}</p>;
    return <p role="status">Cargando la orden…</p>;
  }
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
  // Todo acto dice qué paso se hizo: el de la ficha que se tenía a la vista al hacerlo.
  const terminar = (texto: string, seguir?: Seguir) => terminarActo(texto, seguir, paso);
  const v = vigenteDe(ficha);
  const ruta = { consorcio_id: consorcio, orden_id: orden };
  // Lo que se espera con los mismos datos: decide la base, y el aviso final dice lo que pasó.
  const pasaSola = diferencia === null && !v.alerta_fraccionamiento && (v.franja === "rutinaria" || v.franja === "bajo_umbral");
  const pago = ejecucion?.pagos.at(-1);
  // La cuenta era de la versión que se abrió: de otra versión no se muestra.
  const cuentaVista = cuenta?.version === v.numero ? cuenta : null;
  const cuentaDecidida = decide && decision?.version === v.numero ? decision : null;
  const anterior = ficha.versiones.find((x) => x.numero === v.numero - 1);
  // Cada versión anterior, con lo que cambió en la que le siguió; sin las que no cambiaron nada visible.
  const enDecision = situacion === "borrador" || situacion === "en_revision";
  // Mientras se decide, el último cambio se dice en el cuerpo de la ficha: «Más datos» no lo repite (FIC-08).
  const hasta = enDecision && anterior !== undefined ? anterior.numero : v.numero;
  const cambiosPorVersion = [...ficha.versiones]
    .sort((x, y) => x.numero - y.numero)
    .flatMap((x, i, todas) => {
      const siguiente = todas[i + 1];
      if (siguiente === undefined || x.numero >= hasta) return [];
      const cambios = cambiosEntreVersiones(x, siguiente);
      return cambios.length === 0 ? [] : [{ numero: x.numero, cambios }];
    });
  /** Lo que el servidor ofrece a quien mira: la ficha no ofrece nada más. */
  const ofrece = (codigo: AccionPago) => ficha.acciones.some((a) => a.codigo === codigo);
  const corrige = ofrece("corregir");
  const registra = corrige || ofrece("subir_comprobante");
  const declara = ofrece("declarar_no_pago");
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
  /** Si el acto principal sigue con la próxima orden del mismo paso (la tabla lo dice). */
  const seguir: Seguir | undefined = ficha.despues === "mismo_paso" && paso !== null ? { paso, abierta: montado } : undefined;
  const lecturaIgual = decideElPago && comprobanteLeido.coincide_con_lo_registrado;
  // Sin diferencias no quiere decir que coincida: lo que no se leyó no se comparó.
  const lecturaIncompleta = decideElPago && comprobanteLeido.estado === "leida" && !lecturaDistinta && !lecturaIgual;

  function escribir(cual: ConMotivo) {
    setMotivo("");
    setErroresMotivo(SIN_ERRORES);
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
      alTerminar: decirComoQuedoAlEnviar,
      alFallar: siCambio,
    });
  }

  /** Después de enviar, el aviso dice en qué quedó la orden: lo decide la base, y se lee (P-12). */
  function decirComoQuedoAlEnviar() {
    const decir = (texto: string) => (montado.current ? terminar(texto) : alCambiar());
    acto.setEnviando(true);
    void leerComoQuedo(consorcio, orden)
      .then(decir)
      .catch((e: unknown) => {
        if (e instanceof SinSesion && montado.current) aEntrar();
        else decir(TEXTO_ENVIADA);
      })
      .finally(() => acto.setEnviando(false));
  }

  function retirar() {
    return acto.hacerYEsperar({
      enVentana: true,
      quePide: ["retirar", orden, v.numero],
      nombre: "retirar la orden",
      pedir: (clave) =>
        api.POST("/api/v1/consorcios/{consorcio_id}/ordenes-pago/{orden_id}/retirar", {
          headers: encabezadoCsrf(),
          params: { path: ruta, header: { [ENCABEZADO_IDEMPOTENCIA]: clave } },
          body: { version_esperada: v.numero },
        }),
      alTerminar: () => terminar(TEXTO_RETIRADA),
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
      // Dice en qué quedó la orden (FL-APR-03) y, en el teléfono, sigue con la próxima por aprobar
      // si la tabla lo dice (FL-APR-01, decisión 12 del 18-11).
      alTerminar: decirComoQuedoAlAprobar,
      alFallar: siCambio,
    });
  }

  function decirComoQuedoAlAprobar() {
    const decir = (texto: string) => (montado.current ? terminar(texto, seguir) : alCambiar());
    acto.setEnviando(true);
    void leerComoQuedoAlAprobar(consorcio, orden)
      .then(decir)
      .catch((e: unknown) => {
        if (e instanceof SinSesion && montado.current) aEntrar();
        else decir(TEXTO_APROBADA);
      })
      .finally(() => acto.setEnviando(false));
  }

  function rechazar(texto: string) {
    return acto.hacerYEsperar({
      enVentana: true,
      quePide: ["rechazar", orden, v.numero, v.huella, texto],
      nombre: "rechazar la orden",
      pedir: (clave) =>
        api.POST("/api/v1/consorcios/{consorcio_id}/ordenes-pago/{orden_id}/rechazar", {
          headers: encabezadoCsrf(),
          params: { path: ruta, header: { [ENCABEZADO_IDEMPOTENCIA]: clave } },
          body: { version: v.numero, motivo: texto },
        }),
      alTerminar: () => terminar("Listo: la orden se rechazó.", seguir),
      alFallar: fallaDelMotivo,
    });
  }

  function declarar(texto: string) {
    return acto.hacerYEsperar({
      enVentana: true,
      quePide: ["declarar", orden, v.numero, texto],
      nombre: "declarar que no se pagó",
      pedir: (clave) =>
        api.POST("/api/v1/consorcios/{consorcio_id}/ordenes-pago/{orden_id}/pago-manual/no-realizado", {
          headers: encabezadoCsrf(),
          params: { path: ruta, header: { [ENCABEZADO_IDEMPOTENCIA]: clave } },
          body: { version: v.numero, motivo: texto },
        }),
      alTerminar: () => terminar("Listo: quedó declarado que no se pagó. Lo confirma otro miembro del Consejo."),
      alFallar: fallaDelMotivo,
    });
  }

  function conciliar(pagoId: string, resultado: "conforme" | "observado", texto: string | null) {
    return acto.hacerYEsperar({
      // Con motivo, desde su ventana; sin motivo (conforme a tiempo), desde la ficha.
      enVentana: texto !== null,
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
            ? `Listo: quedó conciliado el pago a ${v.razon_social}.`
            : "Listo: el pago volvió a quien lo registró para que lo corrija.",
          seguir,
        ),
      alFallar: fallaDelMotivo,
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
      alTerminar: () => terminar("Listo: quedó confirmado que no se pagó."),
      alFallar: siCambio,
    });
  }

  function resolver(d: Diferencia, elegida: Resolucion) {
    return acto.hacerYEsperar({
      enVentana: true,
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
    return acto.hacerYEsperar({
      enVentana: true,
      quePide: ["cerrar-reclamo", orden, d.id, texto],
      nombre: "cerrar el reclamo",
      pedir: (clave) =>
        api.POST("/api/v1/consorcios/{consorcio_id}/ordenes-pago/{orden_id}/pago-manual/diferencia/{diferencia_id}/cierre", {
          headers: encabezadoCsrf(),
          params: { path: { ...ruta, diferencia_id: d.id }, header: { [ENCABEZADO_IDEMPOTENCIA]: clave } },
          body: { motivo: texto },
        }),
      alTerminar: () => terminar("Listo: el reclamo se cerró sin recuperar la plata. La diferencia queda en la historia."),
      alFallar: fallaDelMotivo,
    });
  }

  /** Decisión 12 (12-11-2026): no hace falta pagarla. Queda «No se pagó», con quién, cuándo y por
   * qué, y sale de la bandeja. */
  function cerrarSinPagar(texto: string) {
    return acto.hacerYEsperar({
      enVentana: true,
      quePide: ["cerrar-sin-pagar", orden, v.numero, texto],
      nombre: "cerrar la orden sin pagar",
      pedir: (clave) =>
        api.POST("/api/v1/consorcios/{consorcio_id}/ordenes-pago/{orden_id}/cerrar-sin-pagar", {
          headers: encabezadoCsrf(),
          params: { path: ruta, header: { [ENCABEZADO_IDEMPOTENCIA]: clave } },
          body: { version_esperada: v.numero, motivo: texto },
        }),
      alTerminar: () => terminar(TEXTO_CERRADA_SIN_PAGO),
      alFallar: fallaDelMotivo,
    });
  }

  /** No da la cuenta: suelta a quien la vio. La ficha que vuelve dice qué sigue. */
  function tomar() {
    return acto.hacerYEsperar({
      enVentana: true,
      quePide: ["tomar", orden, v.numero, v.huella],
      nombre: "hacerse cargo del pago",
      pedir: (clave) =>
        api.POST("/api/v1/consorcios/{consorcio_id}/ordenes-pago/{orden_id}/pago-manual/tomar", {
          headers: encabezadoCsrf(),
          params: { path: ruta, header: { [ENCABEZADO_IDEMPOTENCIA]: clave } },
          body: { version: v.numero, huella: v.huella, no_se_transfirio: true },
        }),
      // Sin «Listo»: la ficha releída ya dice quién tomó el pago y qué corresponde (FIC-11).
      alTerminar: () => {
        releer();
        alCambiar();
      },
      alFallar: siCambio,
    });
  }

  /** Después de mandar los mismos datos, dice en qué quedó la orden: la franja la vuelve a calcular
   * la base. Si no se puede leer, dice solo lo que se sabe, y la bandeja muestra el resto. */
  function decirEnQueQuedo() {
    const hecho = "Listo: la orden tiene una versión nueva, con los mismos datos.";
    // Si la ficha ya se cerró, la respuesta no mueve la pantalla que se abrió después.
    const decir = (texto: string) => (montado.current ? terminar(texto) : alCambiar());
    // La versión ya cambió: hasta saber en qué quedó, el botón no se vuelve a tocar.
    acto.setEnviando(true);
    void api
      .GET("/api/v1/consorcios/{consorcio_id}/pagos/{orden_id}", { params: { path: ruta } })
      .then(({ data, response }) => {
        exigirSesion(response);
        if (data === undefined) decir(hecho);
        else if (data.orden.estado === "aprobada") decir("Listo: la orden quedó lista para pagar.");
        else {
          const quienSigue = data.le_toca_a === null || data.le_toca_a === "vos" ? "" : ` ${TEXTO_LE_TOCA_A[data.le_toca_a]}`;
          decir(`Listo: la orden volvió a aprobación.${quienSigue}`);
        }
      })
      .catch((e: unknown) => {
        if (e instanceof SinSesion && montado.current) aEntrar();
        else decir(hecho);
      })
      .finally(() => acto.setEnviando(false));
  }

  /** Los mismos datos: la versión nueva pasa otra vez por aprobación, por su franja si fue simple
   * y por el Consejo si hubo una diferencia (decisión 11, desvío de §9). Para cambiar algo está
   * «Modificar». */
  function volverAPagar() {
    void acto.hacer({
      quePide: ["volver-a-pagar", orden, v.numero],
      nombre: "enviar la orden de nuevo",
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
      alTerminar: decirEnQueQuedo,
      alFallar: siCambio,
    });
  }

  /** Cómo termina la ventana según cómo terminó el acto: un rechazo que nombra el motivo se dice
   * junto al campo; los demás, adentro de la ventana. */
  function finDeVentana(fin: FinDelActo): FinDeEnvio {
    if (fin === "hecho") return "cerrar";
    if (fin === "seguir") return "seguir";
    return fin.estado !== null && hayErrores(soloDe(erroresDe(fin.error), CAMPOS_MOTIVO)) ? "seguir" : { rechazo: fin.rechazo };
  }

  async function enviarMotivo(): Promise<FinDeEnvio> {
    const faltan = faltaCompletar({ motivo: [motivo, FALTA_MOTIVO] });
    if (hayErrores(faltan)) {
      setErroresMotivo(faltan);
      return "seguir";
    }
    const texto = motivo.trim();
    const fin =
      conMotivo === "rechazar" ? await rechazar(texto)
      : conMotivo === "declarar" ? await declarar(texto)
      : conMotivo === "cerrar" && diferencia !== null ? await cerrarReclamo(diferencia, texto)
      : conMotivo === "cerrar_sin_pagar" ? await cerrarSinPagar(texto)
      : pago !== undefined ? await conciliar(pago.id, conMotivo === "observar" ? "observado" : "conforme", texto)
      : "seguir";
    return finDeVentana(fin);
  }

  // Lo que se escribe para decidir, sobre la misma ficha. Sin la acción, el formulario se va.
  const motivoAbierto =
    (conMotivo === "rechazar" && ofrece("rechazar")) ||
    (conMotivo === "declarar" && declara) ||
    (conMotivo === "cerrar" && ofrece("cerrar_reclamo")) ||
    (conMotivo === "cerrar_sin_pagar" && ofrece("cerrar_sin_pagar")) ||
    ((conMotivo === "conforme" || conMotivo === "observar") && ofrece("conciliar"))
      ? conMotivo
      : null;
  // Cada motivo en su ventana (FIC-01), con el título del botón que la abrió.
  const TEXTOS_MOTIVO: Record<ConMotivo, { titulo: string; etiqueta: string; boton: string; largo: number }> = {
    rechazar: { titulo: TEXTO_ACCION.rechazar, etiqueta: "Motivo del rechazo", boton: "Rechazar la orden", largo: 500 },
    declarar: { titulo: TEXTO_ACCION.declarar_no_pago, etiqueta: "Por qué no se pagó", boton: "Declarar que no se pagó", largo: 200 },
    conforme: { titulo: VERBO_CONCILIAR, etiqueta: "Motivo para aceptarlo fuera de término", boton: "Dar el pago por conforme", largo: 300 },
    observar: { titulo: TEXTO_ACCION.observar, etiqueta: "Qué no coincide", boton: "Devolverlo a corregir", largo: 300 },
    cerrar: { titulo: TEXTO_ACCION.cerrar_reclamo, etiqueta: "Por qué se cierra sin recuperar", boton: "Cerrar sin recuperar", largo: 300 },
    cerrar_sin_pagar: { titulo: TEXTO_ACCION.cerrar_sin_pagar, etiqueta: "Por qué ya no hace falta pagarla", boton: "Cerrar sin pagar", largo: 300 },
  };

  const estaModificando = modificando && ofrece("modificar");
  /** Registrar o corregir el pago abre su ventana con un solo botón (REG-01). */
  const estaRegistrando = registrando && registra;
  const estaCorrigiendoDiferencia = corrigeDiferencia && ofrece("corregir_diferencia") && diferencia !== null;
  /** Una ventana de carga larga abierta: el aviso de la ficha va adentro, donde se actúa. */
  const avisoEnVentana = estaModificando || estaRegistrando || estaCorrigiendoDiferencia;
  /** Lo que hace cada acción que el servidor ofrece. Las de formulario o enlace no pasan por acá. */
  function elegir(codigo: AccionPago) {
    switch (codigo) {
      case "enviar": return enviar();
      case "modificar": return setModificando(true);
      case "retirar": return setRetirando(true);
      case "aprobar": return aprobar();
      case "rechazar": return escribir("rechazar");
      case "renovar": return renovar();
      case "pagar": return verCuenta();
      case "ver_cuenta_de_nuevo":
      case "retomar_pago": return abrirCuenta();
      case "tomar_pago": return setConfirmaToma(true);
      case "declarar_no_pago": return escribir("declarar");
      case "conciliar":
        if (pago === undefined) return;
        return pago.fuera_de_termino ? escribir("conforme") : conciliar(pago.id, "conforme", null);
      case "observar": return escribir("observar");
      case "confirmar_no_pago":
        if (declaracion !== null) confirmarNoPago(declaracion.id);
        return;
      case "recuperar":
        if (diferencia !== null) recuperar(diferencia);
        return;
      case "cerrar_reclamo": return escribir("cerrar");
      case "corregir_diferencia": return setCorrigeDiferencia(true);
      case "volver_a_pagar": return volverAPagar();
      case "cerrar_sin_pagar": return escribir("cerrar_sin_pagar");
      default: return;
    }
  }
  // La cuenta ya a la vista esconde el botón que la abrió. Una ventana abierta no: queda detrás,
  // inerte, y el foco vuelve a él al cerrarla.
  const ocultas = new Set<AccionPago>([
    // Sin la cuenta destino a la vista no se ofrece aprobar (FIC-14, C8P-09).
    ...(cuentaDecidida === null ? (["aprobar"] as const) : []),
    ...(cuentaVista !== null ? (["pagar", "ver_cuenta_de_nuevo", "retomar_pago"] as const) : []),
  ]);
  const falta = diferencia?.falta_centavos;
  const enlaces = {
    preparar_otra: { a: RUTA_NUEVA, estado: { consorcio, proveedor: v.proveedor_id } },
    ...(falta != null && {
      preparar_faltante: {
        a: RUTA_NUEVA,
        estado: { consorcio, proveedor: v.proveedor_id, importe: aTexto(falta), moneda: v.moneda, concepto: `Lo que faltó de: ${v.concepto}` },
      },
    }),
  };
  const repetida = ficha.posible_duplicada_de;
  /** Lo que se registró del comprobante, en el cuerpo: no mientras se corrige (FIC-07). */
  const muestraRegistro = pago !== undefined && diferencia === null && !corrige;
  /** La factura en el cuerpo, solo mientras está en curso o si falló; lo demás es historia (FIC-10). */
  const adjuntaEnCurso = ficha.factura_adjunta !== null && ficha.factura_adjunta.estado !== "disponible" ? ficha.factura_adjunta : null;
  const adjuntaAceptada = ficha.factura_adjunta?.estado === "disponible" ? ficha.factura_adjunta : null;
  const avisoDeLaFicha = (
    <div ref={zonaAviso}>
      <AvisoPantalla aviso={aviso} />
    </div>
  );
  const alCambiarLaOrden = () => {
    releer();
    alCambiar();
  };
  const tituloRegistro = corrige ? TEXTO_ACCION.corregir : ficha.comprobante_actual === null ? TEXTO_ACCION.subir_comprobante : "Registrar el pago";

  return (
    <>
      {!avisoEnVentana && avisoDeLaFicha}
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
            <div className="ficha-pago__arriba">
              <p>Importe de la orden</p>
              <p className="etiqueta-situacion" data-tono={TONO_ESTADO[ficha.estado_visible]}>
                {NOMBRE_ESTADO[ficha.estado_visible]}
              </p>
            </div>
            <div className="fila-importe">
              <p className="importe-ficha">{importe(v.importe_centavos, v.moneda)}</p>
              {cuentaVista !== null && <BotonCopiar valor={importeParaCopiar(v.importe_centavos)} nombre="el importe" />}
            </div>
            <h2 id={`${id}-titulo`}>{v.razon_social}</h2>
            <p>{v.concepto}</p>
          </div>
          <Barra barra={ficha.barra} />
          {/* Lo que pasa con esta orden y desde cuándo espera (P-16). Pagada y cotejada, la etiqueta
              ya lo dice todo (FIC-05). */}
          {ficha.estado_visible !== "pagada_y_cotejada" && (
            <p>
              {ficha.que_decir}
              {ficha.esperando_desde !== null && ` Desde el ${fechaCorta(ficha.esperando_desde)}.`}
            </p>
          )}
          {/* FL-APR-02, FL-ORD-06 (tanda 9): por qué no se le ofrece el acto. Con el paso «Falta la
              cuenta», lo que pasa ya lo dice: no se repite. */}
          {ficha.motivo_sin_acto != null && !(ficha.motivo_sin_acto === "proveedor_sin_cuenta" && paso === "falta_cuenta") && (
            <p className="aviso-info" role="note">
              {TEXTO_SIN_ACTO[ficha.motivo_sin_acto]}
            </p>
          )}
          {repetida !== null && (
            <p className="aviso-info" role="note">
              {textoRepetida(fechaCorta(repetida.creada_en), repetida.estado_visible)}{" "}
              <Link to={rutaFicha(repetida.orden_id)} state={{ consorcio }} className="enlace-suelto">
                Ver la otra orden
              </Link>
            </p>
          )}
          {ficha.cierre_sin_pago !== null && (
            <p className="aviso-info" role="note">
              {textoCierreSinPago(ficha.cierre_sin_pago.cerrada_por_nombre, momento(ficha.cierre_sin_pago.cerrada_en), ficha.cierre_sin_pago.motivo)}
            </p>
          )}

          {/* RF50 (app 0103): lo vencido o por vencer del proveedor marca la orden y no la frena. */}
          {ficha.documentacion_proveedor.length > 0 && (
            <p className="aviso-pendiente" role="note">
              Documentación del proveedor:{" "}
              {ficha.documentacion_proveedor.map((d) => `${TEXTO_TIPO[d.tipo]} ${d.descripcion}: ${textoEstado(d).toLowerCase()}`).join("; ")}.
              No impide aprobar ni pagar.
            </p>
          )}
          {/* RF50 (app 0106): la versión que se preparó con un salteo por urgencia lo dice siempre. */}
          {ficha.versiones.map(
            (x) =>
              x.salteo_documentacion != null && (
                <p key={`salteo-${x.numero}`} className="aviso-pendiente" role="note">
                  {textoSalteoDeLaVersion(x.salteo_documentacion)}
                  {ficha.versiones.length > 1 ? ` (versión ${x.numero})` : ""}
                </p>
              ),
          )}
          {enDecision && v.alerta_fraccionamiento && (
            <p className="aviso-info" role="note">
              {TEXTO_ALERTA_FRACCIONAMIENTO}
            </p>
          )}
          {enDecision && anterior !== undefined && cambiosDeVersion(v, anterior).map((c) => <p key={c}>{c}</p>)}
          {ficha.orden.motivo_rechazo !== null && situacion === "rechazada" && (
            <p>
              <strong>Motivo del rechazo:</strong> {ficha.orden.motivo_rechazo}
            </p>
          )}
          {/* «No se pagó» ya lo dicen la etiqueta y la frase: acá, solo lo que no dicen (FIC-05). */}
          {situacion === "no_se_pago" && ficha.cierre_sin_pago === null && (diferencia !== null || pasaSola) && (
            <p>
              {diferencia?.cerrada_en != null
                ? `Se cerró el reclamo sin recuperar la plata: ${diferencia.motivo_cierre}. `
                : diferencia?.recuperada_en != null
                  ? "Se recuperó la plata. "
                  : ""}
              {diferencia !== null ? AVISO_POR_EL_CONSEJO : AVISO_PASA_SOLA}
            </p>
          )}
          {observacion !== null && (
            <p className="aviso-error" role="note">
              Motivo: «{observacion.motivo}». {AVISO_NO_TRANSFIERAS}
            </p>
          )}
          {declaracion !== null && (
            <p className="aviso-info" role="note">
              Declarado el {momento(declaracion.declarado_en)}: {declaracion.motivo}. Esta orden no se paga mientras tanto.
            </p>
          )}
          {paso === "espera_resolver" && (
            <p className="aviso-error" role="note">
              {AVISO_DIFERENCIA_ESPERA}
            </p>
          )}
          {paso === "espera_comprobante" && (
            <p className="aviso-info" role="note">
              {AVISO_ESPERA_COMPROBANTE}
              {ficha.tomar_desde !== null && ` Si no aparece, desde el ${momento(ficha.tomar_desde)} otra persona puede hacerse cargo del pago.`}
            </p>
          )}
          {ficha.toma_vigente !== null && (
            <p className="aviso-info" role="note">
              {ficha.toma_vigente.tomado_por_nombre} tomó este pago el {momentoAlFinal(ficha.toma_vigente.tomada_en)}
              {ficha.puede_registrar && !ficha.puede_reabrir_cuenta && " Si la transferencia la hizo esta cuenta, corresponde registrarla ahora."}
            </p>
          )}
          {situacion === "retirada" && ficha.retiro !== null && (
            <p className="aviso-info" role="note">
              {textoQuienRetiro(ficha.retiro.retirada_por_nombre, momentoAlFinal(ficha.retiro.retirada_en))}
            </p>
          )}

          {cuentaVista !== null && (
            <div className="tarjeta">
              <h3>Datos para transferir</h3>
              <p>{AVISO_NO_TRANSFIERE}</p>
              <dl className="detalles">
                <dt>CBU/CVU</dt>
                <dd>
                  <code>{cuentaVista.cuenta_destino}</code>
                  <BotonCopiar valor={cuentaVista.cuenta_destino.replace(/\s/g, "")} nombre="el CBU" />
                </dd>
                {/* Sin CUIT no hay nada que comparar en el banco: no se dibuja el renglón (FL-PAG-03). */}
                {v.cuit !== null && (
                  <>
                    <dt>CUIT del titular</dt>
                    <dd>{v.cuit}</dd>
                  </>
                )}
                <dt>Pagar antes de</dt>
                <dd>{momento(cuentaVista.aprobacion_vence_en)}</dd>
              </dl>
              <p className="aviso-pendiente" role="note">
                Antes de confirmar en el banco: el titular debe ser <strong>{v.razon_social}</strong>
              </p>
            </div>
          )}

          <div className={conVisor ? "comprobante-y-datos" : undefined}>
            {conVisor && ficha.comprobante_actual !== null && (
              <VisorComprobante key={ficha.comprobante_actual.version_documento_id} consorcio={consorcio}
                documento={ficha.comprobante_actual.documento_id} version={ficha.comprobante_actual.version_documento_id} aEntrar={aEntrar} />
            )}
            <div className="tarjeta">
              {lecturaDistinta && <DistintoDeLoRegistrado lectura={comprobanteLeido} registrado={registrado} />}
              {lecturaIncompleta && <p className="aviso-info" role="note">{AVISO_LECTURA_INCOMPLETA}</p>}
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
                    <dd>{fechaLegible(diferencia.fecha_operacion)}</dd>
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
              {/* Solo lo que puede diferir: la cuenta no se compara acá, el registro no se acepta con otra (FIC-09). */}
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
                  </tbody>
                </table>
              )}
              {adjuntaEnCurso !== null && <p className="aviso-info">{textoDeLaAdjunta(adjuntaEnCurso)}</p>}
              {ficha.factura_reemplazo !== null && <p className="aviso-info">{textoDelReemplazo(ficha.factura_reemplazo)}</p>}
              {(factura !== null || ficha.puede_subir_factura || ficha.puede_reemplazar_factura) && (
                <div className="acciones">
                  {factura !== null && (
                    <button type="button" className="secundario" onClick={() => setViendoFactura(true)}>
                      Ver la factura
                    </button>
                  )}
                  {/* Solo sin factura: una orden que ya la tiene no invita a subir otra. */}
                  {ficha.puede_subir_factura && (
                    <button type="button" className="secundario" onClick={() => setSubiendoFactura({ reemplaza: null })}>
                      {SUBIR_FACTURA}
                    </button>
                  )}
                  {ficha.puede_reemplazar_factura && ficha.factura_adjunta !== null && (
                    <button type="button" className="secundario" onClick={() => setSubiendoFactura({ reemplaza: ficha.factura_adjunta?.id ?? null })}>
                      {REEMPLAZAR_FACTURA}
                    </button>
                  )}
                </div>
              )}
              {subiendoFactura !== null && (
                <SubirFactura consorcio={consorcio} orden={orden} version={v.numero}
                  reemplaza={subiendoFactura.reemplaza} cerrarAlSubir aEntrar={aEntrar}
                  alCerrar={(cambio) => {
                    setSubiendoFactura(null);
                    if (cambio) alCambiarLaOrden();
                  }} />
              )}
              {viendoFactura && factura !== null && (
                <VisorDocumento consorcio={consorcio} documento={factura.documento} version={factura.version} nombre={factura.nombre}
                  alCerrar={() => setViendoFactura(false)} aEntrar={aEntrar} />
              )}
              <dl className="detalles">
                {enDecision && (
                  <>
                    <dt>Cuenta destino</dt>
                    <dd>{cuentaDecidida?.cuenta ?? cuentaEnmascarada(v.cuenta_ultimos_digitos)}</dd>
                    <dt>Número de la factura</dt>
                    <dd>{v.comprobante ?? "Sin número de factura"}</dd>
                  </>
                )}
                {paso === "conciliar" && (
                  <>
                    <dt>Cuenta destino</dt>
                    <dd>{cuentaDecidida?.cuenta ?? cuentaEnmascarada(v.cuenta_ultimos_digitos)}</dd>
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
                {muestraRegistro && (
                  <>
                    <dt>Número de operación</dt>
                    <dd>{pago.numero_operacion}</dd>
                    <dt>Fecha de la transferencia</dt>
                    <dd>{fechaLegible(pago.fecha_operacion)}</dd>
                    {paso !== "conciliar" && (
                      <>
                        <dt>Importe registrado</dt>
                        <dd>{importe(pago.importe_centavos, pago.moneda)}</dd>
                      </>
                    )}
                  </>
                )}
                {pago !== undefined && diferencia === null && (
                  <>
                    <dt>Registró</dt>
                    <dd>
                      {pago.registrado_por_nombre}, {momento(pago.registrado_en)}
                    </dd>
                  </>
                )}
                {pago?.lectura_disputada === true && (
                  <>
                    <dt>Lectura del comprobante</dt>
                    <dd>{AVISO_LECTURA_DISPUTADA}: los datos los escribió a mano. Conviene compararlos con el archivo.</dd>
                  </>
                )}
                {pago?.fuera_de_termino === true && (
                  <>
                    <dt>Fuera de término</dt>
                    <dd>Se transfirió después de que venció la aprobación.</dd>
                  </>
                )}
              </dl>

              {paso === "pagar" && ficha.puede_pagar && cuentaVista === null && (
                <p className="aviso-info" role="note">{AVISO_VER_LA_CUENTA_QUEDA}</p>
              )}
              {paso === "subir_comprobante" && situacion === "pagando" && !ficha.puede_reabrir_cuenta && cuentaVista === null && (
                <p className="aviso-error" role="note">
                  {AVISO_SIN_CUENTA}
                </p>
              )}
              {paso === "declarar_no_pago" && (
                <p className="aviso-error" role="note">
                  {AVISO_SIN_SALIDA_CAUSA}
                </p>
              )}

              {/* Registrar o corregir el pago: un solo botón, y la carga en su ventana (REG-01). */}
              {registra && !estaRegistrando && (
                <div className="acciones">
                  <button ref={abreElRegistro} type="button" onClick={() => setRegistrando(true)}>
                    {tituloRegistro}
                  </button>
                </div>
              )}
              {/* Cómo se resuelve la diferencia: cada camino con su botón y su confirmación (FIC-04). */}
              {ofrece("resolver_diferencia") && diferencia !== null && (
                <div className="acciones">
                  {diferencia.se_puede_dar_por_pagada && (
                    <button type="button" disabled={enviando} onClick={() => setResolucion("pagada_por_lo_real")}>
                      {DAR_POR_PAGADO}
                    </button>
                  )}
                  <button type="button" className={diferencia.se_puede_dar_por_pagada ? "secundario" : undefined} disabled={enviando}
                    onClick={() => setResolucion("reclamo")}>
                    {RECLAMAR}
                  </button>
                </div>
              )}
              <div className="formulario">
                {/* Los actos de la orden: exactamente los que mandó el servidor (§4.3). */}
                <Acciones acciones={ficha.acciones} enviando={enviando} alElegir={elegir} ocultas={ocultas}
                  textos={{ conciliar: VERBO_CONCILIAR, ...(pasaSola ? { volver_a_pagar: VERBO_PASA_SOLA } : {}) }} enlaces={enlaces}>
                  {decide && cuentaDecidida === null && ofrece("aprobar") && !faltaCuenta && (
                    <p role="status">{CARGANDO_LA_CUENTA}</p>
                  )}
                  {decide && faltaCuenta && (
                    <>
                      <p className="comando-no-disponible">{FALTA_LA_CUENTA}</p>
                      <button type="button" className="secundario" disabled={enviando}
                        onClick={() => {
                          setAviso(sinLaFalla);
                          setVueltaDecision((n) => n + 1);
                        }}>
                        Volver a pedir la cuenta
                      </button>
                    </>
                  )}
                </Acciones>
              </div>
            </div>
          </div>

          <details className="historial">
            <summary>Más datos</summary>
            <dl className="detalles">
              {/* Sin repetir lo que ya se ve arriba ni lo habitual (comentario 25, 09-11-2026). */}
              {cuentaVista === null && v.cuit !== null && (
                <>
                  <dt>CUIT</dt>
                  <dd>{v.cuit}</dd>
                </>
              )}
              {v.condicion_beneficiario !== "conocido" && (
                <>
                  <dt>Beneficiario</dt>
                  <dd>{TEXTO_CONDICION[v.condicion_beneficiario]}</dd>
                </>
              )}
              {v.franja !== "bajo_umbral" && (
                <>
                  <dt>Franja</dt>
                  <dd>{textoFranja(v.franja, ficha.orden.estado)}</dd>
                </>
              )}
              {v.usa_fondo_reserva && (
                <>
                  <dt>Fondo de reserva</dt>
                  <dd>Sí</dd>
                </>
              )}
              <dt>Preparada</dt>
              <dd>{momento(v.creada_en)}</dd>
              {ficha.cotejado_en !== null && (
                <>
                  <dt>Cotejada con el banco</dt>
                  <dd>{momento(ficha.cotejado_en)}</dd>
                </>
              )}
              {adjuntaAceptada !== null && (
                <>
                  <dt>Factura</dt>
                  <dd>{textoDeLaAdjunta(adjuntaAceptada)}</dd>
                </>
              )}
            </dl>
            {ficha.facturas_anteriores.length > 0 && (
              <>
                <h3>Facturas anteriores</h3>
                <ul className="lista-simple" aria-label="Facturas anteriores">
                  {ficha.facturas_anteriores.map((a) => (
                    <li key={a.id}>{textoDeLaAnterior(a)}</li>
                  ))}
                </ul>
              </>
            )}
            {cambiosPorVersion.length > 0 && (
              <>
                <h3>Cambios anteriores</h3>
                <ol className="versiones">
                  {cambiosPorVersion.map(({ numero, cambios }) => (
                    <li key={numero}>{cambios.join("; ")}</li>
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
                      {/* «a. m.» ya trae su punto: momentoAlFinal no lo repite («a. m.. Observado»). */}
                      {p.registrado_por_nombre},{" "}
                      {p.motivo_conciliacion === null ? momento(p.registrado_en) : `${momentoAlFinal(p.registrado_en)} Observado: ${p.motivo_conciliacion}`}
                    </li>
                  ))}
                </ol>
              </>
            )}
          </details>
        </section>

        {/* Las ventanas de los actos de la ficha (criterio 3): cada una con solo su pregunta o sus campos. */}
        {motivoAbierto !== null && (
          <VentanaDeFormulario titulo={TEXTOS_MOTIVO[motivoAbierto].titulo} textoEnviar={TEXTOS_MOTIVO[motivoAbierto].boton}
            peligro={motivoAbierto !== "conforme"} alCerrar={() => setConMotivo(null)} alEnviar={enviarMotivo}>
            {motivoAbierto === "cerrar_sin_pagar" && <p>{PREGUNTA_CERRAR_SIN_PAGAR}</p>}
            {motivoAbierto === "observar" && <p>{AVISO_OBSERVAR}</p>}
            <label htmlFor={`${id}-motivo`}>{TEXTOS_MOTIVO[motivoAbierto].etiqueta}</label>
            <input id={`${id}-motivo`} data-foco maxLength={TEXTOS_MOTIVO[motivoAbierto].largo} value={motivo}
              {...marcaDe(`${id}-motivo`, erroresMotivo.motivo)}
              onChange={(e) => {
                setMotivo(e.target.value);
                setErroresMotivo((errores) => sinElDe(errores, "motivo"));
              }} />
            <ErrorDeCampo idCampo={`${id}-motivo`} mensaje={erroresMotivo.motivo} />
          </VentanaDeFormulario>
        )}
        {/* Primero pregunta: retirar no se deshace (comentario 23, 09-11-2026). */}
        {ofrece("retirar") && retirando && (
          <VentanaDeFormulario titulo={TEXTO_ACCION.retirar} textoEnviar={CONFIRMAR_RETIRAR} peligro
            alCerrar={() => setRetirando(false)} alEnviar={async () => finDeVentana(await retirar())}>
            <p>{PREGUNTA_RETIRAR}</p>
          </VentanaDeFormulario>
        )}
        {ofrece("tomar_pago") && confirmaToma && (
          <VentanaDeFormulario titulo={TEXTO_ACCION.tomar_pago} textoEnviar="Nadie transfirió: hacerse cargo del pago"
            alCerrar={() => setConfirmaToma(false)} alEnviar={async () => finDeVentana(await tomar())}>
            <p className="aviso-error">{AVISO_TOMAR}</p>
          </VentanaDeFormulario>
        )}
        {ofrece("resolver_diferencia") && diferencia !== null && resolucion !== null && (
          <VentanaDeFormulario titulo={resolucion === "reclamo" ? RECLAMAR : DAR_POR_PAGADO}
            textoEnviar={resolucion === "reclamo" ? RECLAMAR : DAR_POR_PAGADO}
            alCerrar={() => setResolucion(null)} alEnviar={async () => finDeVentana(await resolver(diferencia, resolucion))}>
            <p>
              {resolucion === "reclamo"
                ? AVISO_RECLAMO
                : `Se dan por pagados ${importe(centavosReales(diferencia), diferencia.moneda)}${
                    diferencia.falta_centavos !== null ? ` y se prepara otra orden por ${importe(diferencia.falta_centavos, diferencia.moneda)}` : ""
                  }.`}
            </p>
          </VentanaDeFormulario>
        )}
        {estaModificando && (
          <Hoja titulo={TEXTO_ACCION.modificar} ancha alEscapar={enviando ? undefined : () => setModificando(false)}>
            {avisoDeLaFicha}
            <FormularioOrden consorcio={consorcio} monedas={monedas} acto={acto} aEntrar={aEntrar} aInicio={aInicio}
              modificar={{ orden, version: vigenteLeida ?? v, pierdeAprobaciones: situacion === "en_revision" || situacion === "para_pagar" }}
              alGuardar={(_, texto) => {
                // Con aprobaciones en juego, guardar la manda a aprobar de nuevo: termina como los demás actos (FIC-03).
                if (situacion === "en_revision" || situacion === "para_pagar") return terminar(texto);
                setModificando(false);
                setAviso({ texto, ok: true });
                alCambiarLaOrden();
              }}
              alCancelar={() => setModificando(false)}
              alCambiarLaOrden={alCambiarLaOrden} />
          </Hoja>
        )}
        {estaRegistrando && (
          <Hoja titulo={tituloRegistro} ancha alEscapar={enviando ? undefined : () => setRegistrando(false)}>
            {avisoDeLaFicha}
            <RegistroPago key={paso} consorcio={consorcio} orden={orden} version={v} corregido={corrige ? pago : undefined}
              comprobante={ficha.comprobante_actual} subido={ficha.comprobante_subido} lectura={comprobanteLeido}
              puedeOtraCosa={ficha.puede_registrar} acto={acto} aEntrar={aEntrar}
              alCancelar={() => setRegistrando(false)}
              alRegistrar={(texto) => terminar(texto)} releer={releer} alCambiarLaOrden={alCambiarLaOrden} />
          </Hoja>
        )}
        {estaCorrigiendoDiferencia && (
          <Hoja titulo={TEXTO_ACCION.corregir_diferencia} ancha alEscapar={enviando ? undefined : () => setCorrigeDiferencia(false)}>
            {avisoDeLaFicha}
            <RegistroPago key={`diferencia-${diferencia.id}`} consorcio={consorcio} orden={orden} version={v} corregido={undefined}
              diferencia={diferencia} alCancelar={() => setCorrigeDiferencia(false)} comprobante={ficha.comprobante_actual} subido={null}
              lectura={null} puedeOtraCosa={false} acto={acto} aEntrar={aEntrar}
              alRegistrar={(texto) => terminar(texto)} releer={releer} alCambiarLaOrden={alCambiarLaOrden} />
          </Hoja>
        )}
      </div>
    </>
  );
}
