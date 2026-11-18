import { type FormEvent, useEffect, useId, useRef, useState } from "react";

import { api } from "../api/cliente";
import type { components } from "../api/tipos";
import { encabezadoCsrf } from "../csrf";
import { TIPOS_DE_DOCUMENTO, TOPE_BYTES } from "../archivos";
import { mensajeDeError as mensajeDeCarga } from "../Documentos";
import { CampoImporte } from "../egresos/CampoImporte";
import {
  aCentavos,
  aDecimal,
  aTexto,
  cambioLaOrden,
  cambioLoVisto,
  cuentaEnmascarada,
  ENCABEZADO_IDEMPOTENCIA,
  importe as importeLegible,
  LARGO_TITULO,
  mensajeDeActo,
  renovarClave,
} from "../egresos/comun";
import { exigirSesion, SinSesion, useConsultaDelAntivirus } from "../egresos/useConsorcio";
import { ErrorDeCampo, erroresDe, type ErroresDeCampo, faltaCompletar, hayErrores, marcaDe, resumenDe, SIN_ERRORES, sinElDe, soloDe } from "../ErrorDeCampo";
import { codigoDe, SIN_CONEXION } from "../fallas";
import { CampoMonedaSalida, FALTA_CODIGO_MONEDA, monedaValida, nombreDeMoneda } from "./CampoMonedaSalida";
import { AVISO_ANTIVIRUS_TARDA, AVISO_ARCHIVO_RECHAZADO, AVISO_LECTURA_FALLO, AVISO_OTRA_COSA } from "./textos";
import type { ActoEnCurso } from "./useActo";
import { SubirArchivo } from "../SubirArchivo";
import { useUrlDelArchivo } from "../VisorDocumento";
import { VisorComprobante } from "./VisorComprobante";

// Registrar el pago o corregir un registro observado, con el comprobante al lado. Registrar no es
// pagar: la orden queda pagada recién con la conciliación.
//
// El comprobante va en un paso (§4): elegir el archivo dispara todo y la ficha dice en qué va. No hay
// lista: vale `comprobante_actual`, y volver a subir lo reemplaza. La lectura del antivirus la hace
// la ficha, que se vuelve a leer sola, y sobrevive a salir y volver.
//
// Los datos se escriben del comprobante, nunca de la orden: si no, la comparación no vale. Corregir
// abre con lo registrado, y no pide la cuenta (§8): ni una apertura nueva, ni escribirla otra vez.
//
// «Se transfirió otro importe o a otra cuenta» (§7) usa el mismo comprobante: lo que salió del banco, y qué pasó. Siempre pide
// la cuenta del comprobante, porque la base es la que compara y decide qué fue distinto.
//
// La lectura con IA (§6), si está encendida, propone: llena los campos con lo leído, nunca con lo de
// la orden, y el servidor dice qué no coincide. Si no coincide, elige la persona. La cuenta leída no
// llega nunca: si coincide, no se pide; si no, se escribe. Una lectura que tarda de más se deja de
// esperar, y si llega después no pisa lo que ya se escribió. Con otro comprobante, lo leído del
// anterior se va.

type Comprobante = components["schemas"]["ComprobanteActual"];
type Subido = components["schemas"]["ComprobanteSubido"];
type Version = components["schemas"]["VersionOrdenPago"];
type Pago = components["schemas"]["PagoManual"];
type Lectura = components["schemas"]["LecturaComprobante"];
type Diferencia = components["schemas"]["DiferenciaPagoManual"];
type Registro = { numero: string; fecha: string; importe: string; moneda: string; cuenta: string; explicacion: string };

const hoy = () => new Date().toLocaleDateString("en-CA");

/** Los campos que el servidor puede marcar en el registro (D-43): el error va junto al campo. */
const CAMPOS_REGISTRO = { fecha_operacion: "la fecha de la transferencia" } as const;

/** El nombre del error de cada dato del registro: la fecha usa el del servidor (D-43). */
const ERROR_DE: Record<keyof Registro, string> = {
  numero: "numero",
  fecha: "fecha_operacion",
  importe: "importe",
  moneda: "moneda",
  cuenta: "cuenta",
  explicacion: "explicacion",
};

/** Lo registrado, para corregirlo. La cuenta no viaja: se vuelve a escribir del comprobante. */
function datosDeLaDiferencia(d: Diferencia): Registro {
  return {
    numero: d.numero_operacion,
    fecha: d.fecha_operacion,
    importe: aTexto(aCentavos(d.importe_real) ?? 0),
    moneda: d.moneda,
    cuenta: "",
    explicacion: d.explicacion,
  };
}

function datosIniciales(corregido: Pago | undefined, moneda: string): Registro {
  if (corregido === undefined) return { numero: "", fecha: hoy(), importe: "", moneda, cuenta: "", explicacion: "" };
  return {
    numero: corregido.numero_operacion,
    fecha: corregido.fecha_operacion,
    importe: aTexto(corregido.importe_centavos),
    moneda,
    cuenta: "",
    explicacion: "",
  };
}

/** Con los campos que se leyeron; lo que no se leyó queda para escribir. La fecha, vacía: la de hoy
 * sería un dato que el comprobante no dijo. */
function datosLeidos(l: Lectura, moneda: string): Registro {
  return {
    numero: l.numero_operacion ?? "",
    fecha: l.fecha ?? "",
    importe: l.importe_centavos === null ? "" : aTexto(l.importe_centavos),
    moneda: l.moneda ?? moneda,
    cuenta: "",
    explicacion: "",
  };
}

type Props = {
  consorcio: string;
  orden: string;
  version: Version;
  /** El registro observado que se corrige; sin él, es un registro nuevo. */
  corregido: Pago | undefined;
  comprobante: Comprobante | null;
  subido: Subido | null;
  /** La diferencia que se corrige (decisión 6): mismo formulario que «Se transfirió otro importe o a otra cuenta», con el
   * comprobante que ya tiene. Se puede dejar de corregir. */
  diferencia?: Diferencia;
  /** Cerrar la ventana donde se registra (o dejar de corregir la diferencia). */
  alCancelar?: () => void;
  /** Lo que la IA leyó del comprobante; nula si la lectura no corrió. */
  lectura: Lectura | null;
  /** Si se puede registrar lo que pasó de verdad. */
  puedeOtraCosa: boolean;
  acto: ActoEnCurso;
  aEntrar: () => void;
  alRegistrar: (texto: string) => void;
  /** Vuelve a leer la ficha, para ver en qué va el antivirus. */
  releer: () => void;
  /** Otra persona cambió la orden: lo escrito queda sobre lo que se vuelva a leer. */
  alCambiarLaOrden: () => void;
};

/** Lo leído que no es lo de la orden, antes de registrar: el servidor dice qué. */
function DistintoDeLaOrden({ leida, version }: { leida: Lectura; version: Version }) {
  const { importe_centavos: leido, cuenta_ultimos_digitos: ultimos } = leida;
  return (
    <div className="aviso-error" role="alert">
      {leida.distinto_de_la_orden.includes("importe") && leido !== null && (
        <p>
          El comprobante dice <strong>{importeLegible(leido, leida.moneda ?? version.moneda)}</strong> y la orden es por{" "}
          <strong>{importeLegible(version.importe_centavos, version.moneda)}</strong>.
        </p>
      )}
      {leida.distinto_de_la_orden.includes("moneda") && (
        <p>
          El comprobante dice <strong>{nombreDeMoneda(leida.moneda ?? "")}</strong> y la orden es en <strong>{nombreDeMoneda(version.moneda)}</strong>.
        </p>
      )}
      {leida.distinto_de_la_orden.includes("cuenta") &&
        (ultimos === version.cuenta_ultimos_digitos ? (
          <p>
            La cuenta del comprobante termina igual que la de la orden (<strong>{cuentaEnmascarada(ultimos)}</strong>), pero no es la misma
            cuenta.
          </p>
        ) : (
          <p>
            El comprobante dice la cuenta <strong>{cuentaEnmascarada(ultimos)}</strong> y la orden es a la cuenta{" "}
            <strong>{cuentaEnmascarada(version.cuenta_ultimos_digitos)}</strong>.
          </p>
        ))}
    </div>
  );
}

export function RegistroPago(props: Props) {
  const { consorcio, orden, version, corregido, diferencia, alCancelar, comprobante, subido, lectura, puedeOtraCosa, acto, aEntrar, alRegistrar, releer, alCambiarLaOrden } = props;
  const id = useId();
  const { enviando, setEnviando, setAviso, montado } = acto;
  const corrige = corregido !== undefined;
  const corrigeDiferencia = diferencia !== undefined;
  const [registro, setRegistro] = useState<Registro>(() => (diferencia !== undefined ? datosDeLaDiferencia(diferencia) : datosIniciales(corregido, version.moneda)));
  const [otraCosa, setOtraCosa] = useState(corrigeDiferencia);
  const [errores, setErrores] = useState<ErroresDeCampo>(SIN_ERRORES);
  /** Lo elegido en este dispositivo: se ve enseguida, y el reintento no pide elegirlo de nuevo. */
  const [elegido, setElegido] = useState<File | null>(null);
  const [subiendo, setSubiendo] = useState(false);
  const [noSubio, setNoSubio] = useState(false);
  /** El comprobante recién subido, hasta que la ficha diga en qué quedó. */
  const [esperado, setEsperado] = useState<string | null>(null);
  const claveCarga = useRef<string | null>(null);
  const claveArchivo = useRef<string | null>(null);
  /** El comprobante ya creado cuyo archivo todavía no entró: el reintento sube solo el archivo. */
  const sinArchivo = useRef<string | null>(null);

  const llego = esperado !== null && comprobante?.documento_id === esperado;
  const rechazado = subido?.estado === "rechazado" && (esperado === null || subido.documento_id === esperado);
  const revisando = !rechazado && !llego && (esperado !== null || subido?.estado === "en_cuarentena");
  const antivirusTarda = useConsultaDelAntivirus(revisando, releer);
  const enCurso = subiendo || revisando || noSubio;
  const imagenLocal = useUrlDelArchivo(enCurso && elegido !== null && elegido.type !== "application/pdf" ? elegido : null);

  const versionActual = comprobante?.version_documento_id ?? null;
  /** La versión cuya lectura se dejó de esperar: si llega después, ya no cuenta. */
  const [abandonada, setAbandonada] = useState<string | null>(null);
  const lecturaActual = lectura?.version_documento_id === versionActual && abandonada !== versionActual ? lectura : null;
  const leyendo = !enCurso && lecturaActual?.estado === "leyendo";
  const lecturaTarda = useConsultaDelAntivirus(leyendo, releer);
  useEffect(() => {
    if (lecturaTarda) setAbandonada(versionActual);
  }, [lecturaTarda, versionActual]);
  const noLeida = !enCurso && !leyendo && (lecturaActual?.estado === "no_leida" || (abandonada !== null && abandonada === versionActual));
  const leida = !enCurso && lecturaActual?.estado === "leida" ? lecturaActual : null;
  const difiere = leida !== null && leida.distinto_de_la_orden.length > 0;
  const coincide = leida !== null && !difiere && leida.importe_centavos !== null && leida.cuenta_coincide === true;

  /** La versión cuya lectura llenó los campos. Corregir abre con lo registrado: no se llena. */
  const [llenadaCon, setLlenadaCon] = useState<string | null>(null);
  /** Pidió ver o cambiar lo que se leyó; o cambiar el archivo que no coincide. */
  const [aMano, setAMano] = useState(false);
  /** Eligió «Se leyó mal»: queda en el registro y el Consejo lo ve (decisión 14). */
  const [dijoMal, setDijoMal] = useState(false);
  const [cambiaArchivo, setCambiaArchivo] = useState(false);
  const fuente = corrige || corrigeDiferencia || enCurso ? llenadaCon : (leida?.version_documento_id ?? null);
  if (fuente !== llenadaCon) {
    setLlenadaCon(fuente);
    setRegistro(leida === null ? datosIniciales(undefined, version.moneda) : datosLeidos(leida, version.moneda));
    setAMano(false);
    setDijoMal(false);
    setCambiaArchivo(false);
    setOtraCosa(false);
  }

  async function subir(archivo: File) {
    if (!montado.current) return;
    setSubiendo(true);
    setEnviando(true);
    setNoSubio(false);
    setAviso(null);
    try {
      // Si un intento anterior creó el documento y el archivo no entró, se reintenta solo el archivo.
      if (sinArchivo.current === null) {
        claveCarga.current ??= crypto.randomUUID();
        const creado = await api.POST("/api/v1/consorcios/{consorcio_id}/ordenes-pago/{orden_id}/pago-manual/comprobante", {
          headers: encabezadoCsrf(),
          params: { path: { consorcio_id: consorcio, orden_id: orden }, header: { [ENCABEZADO_IDEMPOTENCIA]: claveCarga.current } },
          body: { titulo: `Transferencia a ${version.razon_social}`.slice(0, LARGO_TITULO), fecha: registro.fecha || hoy() },
        });
        exigirSesion(creado.response);
        if (creado.data === undefined) {
          const codigo = codigoDe(creado.error);
          if (renovarClave(creado.response.status, codigo)) claveCarga.current = null;
          setNoSubio(true);
          setAviso({ texto: mensajeDeActo(creado.response.status, creado.error), ok: false });
          if (cambioLaOrden(codigo) || cambioLoVisto(creado.response.status, codigo)) alCambiarLaOrden();
          return;
        }
        claveCarga.current = null;
        sinArchivo.current = creado.data.id;
      }
      claveArchivo.current ??= crypto.randomUUID();
      const subida = await api.POST("/api/v1/consorcios/{consorcio_id}/documentos/{documento_id}/versiones", {
        headers: { ...encabezadoCsrf(), "Content-Type": archivo.type },
        params: {
          path: { consorcio_id: consorcio, documento_id: sinArchivo.current },
          header: { [ENCABEZADO_IDEMPOTENCIA]: claveArchivo.current },
        },
        // El archivo crudo, como en Documentos: `bodySerializer` lo deja pasar sin JSON.
        body: archivo as unknown as string,
        bodySerializer: (cuerpo) => cuerpo,
      });
      exigirSesion(subida.response);
      if (subida.data === undefined) {
        const codigo = codigoDe(subida.error);
        if (renovarClave(subida.response.status, codigo)) claveArchivo.current = null;
        const texto = mensajeDeCarga(subida.response.status, codigo, "subir", [], subida.response.headers.has("Retry-After"));
        setNoSubio(true);
        setAviso({ texto: `El archivo no se subió. ${texto}`, ok: false });
        return;
      }
      claveArchivo.current = null;
      setEsperado(sinArchivo.current);
      sinArchivo.current = null;
      releer();
    } catch (e: unknown) {
      if (e instanceof SinSesion) aEntrar();
      else {
        setNoSubio(true);
        setAviso({ texto: SIN_CONEXION, ok: false });
      }
    } finally {
      setSubiendo(false);
      setEnviando(false);
    }
  }

  /** El archivo ya pasó el control de tipo y tamaño de `SubirArchivo`. */
  function elegir(archivo: File) {
    // Otro archivo es otro pedido: si el anterior no entró, sirve su documento, no su clave.
    claveArchivo.current = null;
    setElegido(archivo);
    setCambiaArchivo(false);
    void subir(archivo);
  }

  /** El rechazo que nombra un campo del registro se dice junto al campo; arriba, solo cuál corregir. */
  function marcarCampos(estado: number, error: unknown) {
    const marcados = soloDe(erroresDe(error, mensajeDeActo(estado, error)), CAMPOS_REGISTRO);
    setErrores(marcados);
    if (Object.keys(marcados).length > 0) setAviso({ texto: resumenDe(marcados, CAMPOS_REGISTRO), ok: false });
  }

  /** Lo que falta o no se entiende, junto a cada campo (FL-PAG-02, decisión 4: el botón siempre
   * activo, sin el globo del navegador). Solo los campos que el formulario muestra. */
  function faltaDelRegistro(): ErroresDeCampo {
    const faltan: Record<string, string> = {
      ...faltaCompletar({
        numero: [registro.numero, "Falta escribir el número de operación."],
        fecha_operacion: [registro.fecha, "Falta elegir la fecha de la transferencia."],
        ...(pideCuenta ? { cuenta: [registro.cuenta, "Falta escribir el CBU o CVU de destino."] as const } : {}),
        ...(otraCosa ? { explicacion: [registro.explicacion, "Falta escribir qué pasó."] as const } : {}),
      }),
    };
    if (pideImporte && aCentavos(registro.importe) === null)
      faltan.importe =
        registro.importe.trim() !== ""
          ? "Escribir el importe con números, como 15000 o 15.000,50."
          : otraCosa
            ? "Falta escribir el importe que salió del banco."
            : "Falta escribir el importe, tal como figura en el comprobante.";
    if (otraCosa && !monedaValida(registro.moneda)) faltan.moneda = FALTA_CODIGO_MONEDA;
    return faltan;
  }

  /** Si falta algo, lo marca junto al campo y no se manda nada. */
  function faltaAlgo(): boolean {
    const faltan = faltaDelRegistro();
    setErrores(faltan);
    return hayErrores(faltan);
  }

  function registrar(evento: FormEvent, documento: string) {
    evento.preventDefault();
    if (faltaAlgo()) return;
    const centavos = aCentavos(registro.importe);
    if (centavos === null) return;
    void acto.hacer({
      quePide: ["registrar", orden, version.numero, version.huella, registro, documento, cuentaLeida],
      nombre: "registrar el pago",
      pedir: (clave) =>
        api.POST("/api/v1/consorcios/{consorcio_id}/ordenes-pago/{orden_id}/pago-manual", {
          headers: encabezadoCsrf(),
          params: { path: { consorcio_id: consorcio, orden_id: orden }, header: { [ENCABEZADO_IDEMPOTENCIA]: clave } },
          body: {
            version: version.numero,
            huella: version.huella,
            lectura_mal: dijoMal,
            numero_operacion: registro.numero.trim(),
            fecha_operacion: registro.fecha,
            importe_centavos: centavos,
            moneda: version.moneda,
            ...(corrige || cuentaLeida ? {} : { cuenta_destino: registro.cuenta.replace(/\D/g, "") }),
            documento_id: documento,
          },
        }),
      alTerminar: () => alRegistrar("Listo: el pago quedó registrado. Falta que el Consejo lo concilie."),
      alFallar: (estado, codigo, error) => {
        marcarCampos(estado, error);
        if (cambioLaOrden(codigo) || cambioLoVisto(estado, codigo)) alCambiarLaOrden();
      },
    });
  }

  function registrarOtraCosa(evento: FormEvent, documento: string) {
    evento.preventDefault();
    if (faltaAlgo()) return;
    const centavos = aCentavos(registro.importe);
    if (centavos === null) return;
    void acto.hacer({
      quePide: ["otra-cosa", orden, version.numero, version.huella, registro, documento, diferencia?.id],
      nombre: corrigeDiferencia ? "corregir lo registrado" : "registrar lo que pasó",
      pedir: (clave) => {
        const cuerpo = {
          version: version.numero,
          huella: version.huella,
          numero_operacion: registro.numero.trim(),
          fecha_operacion: registro.fecha,
          importe_real: aDecimal(centavos),
          moneda: registro.moneda,
          cuenta_real: registro.cuenta.replace(/\D/g, ""),
          documento_id: documento,
          explicacion: registro.explicacion.trim(),
        };
        const camino = { consorcio_id: consorcio, orden_id: orden };
        const encabezado = { [ENCABEZADO_IDEMPOTENCIA]: clave };
        if (diferencia !== undefined)
          return api.POST("/api/v1/consorcios/{consorcio_id}/ordenes-pago/{orden_id}/pago-manual/diferencia/{diferencia_id}/correccion", {
            headers: encabezadoCsrf(),
            params: { path: { ...camino, diferencia_id: diferencia.id }, header: encabezado },
            body: cuerpo,
          });
        return api.POST("/api/v1/consorcios/{consorcio_id}/ordenes-pago/{orden_id}/pago-manual/diferencia", {
          headers: encabezadoCsrf(),
          params: { path: camino, header: encabezado },
          body: cuerpo,
        });
      },
      alTerminar: () => alRegistrar(corrigeDiferencia ? `Listo: lo registrado quedó corregido. ${AVISO_OTRA_COSA}` : `Listo: quedó registrado lo que pasó. ${AVISO_OTRA_COSA}`),
      alFallar: (estado, codigo, error) => {
        marcarCampos(estado, error);
        if (cambioLaOrden(codigo) || cambioLoVisto(estado, codigo)) alCambiarLaOrden();
      },
    });
  }

  function cambiar<K extends keyof Registro>(campo: K, valor: Registro[K]) {
    setRegistro((r) => ({ ...r, [campo]: valor }));
    setErrores((antes) => sinElDe(antes, ERROR_DE[campo]));
  }

  const documentoDelRegistro = diferencia?.documento_id ?? comprobante?.documento_id ?? null;
  const conDatos = documentoDelRegistro !== null && !enCurso && !leyendo;
  /** Lo que no coincide espera a que la persona diga qué pasó; al corregir ya lo sabe. */
  const pregunta = difiere && !corrige && !aMano && !otraCosa && !corrigeDiferencia;
  /** La cuenta y el importe los leyó la IA y coinciden: no hay nada que escribir de eso. */
  const cuentaLeida = coincide && !corrige && !aMano;
  /** Lo que el formulario muestra para escribir: lo que no se muestra no se exige. */
  const pideImporte = !cuentaLeida || otraCosa;
  const pideCuenta = otraCosa || (!corrige && !cuentaLeida);

  return (
    <div className="comprobante-y-datos">
      <section aria-label="Comprobante">
        {enCurso && elegido !== null && imagenLocal !== null && (
          <div className="visor-comprobante">
            <img src={imagenLocal} alt={`Vista previa de ${elegido.name}`} />
          </div>
        )}
        {enCurso && elegido !== null && imagenLocal === null && <p className="archivo-elegido">{elegido.name}</p>}
        {!enCurso && comprobante !== null && (
          <VisorComprobante key={comprobante.version_documento_id} consorcio={consorcio} documento={comprobante.documento_id}
            version={comprobante.version_documento_id} aEntrar={aEntrar} />
        )}
        {(subiendo || revisando || leyendo) && (
          <div role="status">
            {/* Sin la lista de pasos (D-24): subir, el antivirus y la lectura son un solo «Revisando». */}
            <h3>Revisando el comprobante</h3>
            {antivirusTarda && <p>{AVISO_ANTIVIRUS_TARDA}</p>}
          </div>
        )}
        {rechazado && !enCurso && (
          <p className="aviso-error" role="alert">
            {AVISO_ARCHIVO_RECHAZADO}
          </p>
        )}
        {noSubio && elegido !== null && !subiendo && (
          <div className="zona-comprobante">
            <button className="secundario" type="button" disabled={enviando} onClick={() => void subir(elegido)}>
              Reintentar
            </button>
            <button type="button" className="secundario" disabled={enviando} onClick={() => setNoSubio(false)}>
              Elegir otro archivo
            </button>
          </div>
        )}
        {/* Sin comprobante, la zona para subirlo; con uno, cambiarlo es la excepción: un botón (REG-03). */}
        {!enCurso && !leyendo && !corrigeDiferencia && (comprobante === null || cambiaArchivo) && (
          <SubirArchivo titulo={comprobante === null ? "Comprobante de la transferencia" : "Otro comprobante"} de="del comprobante"
            tipos={TIPOS_DE_DOCUMENTO} tope={TOPE_BYTES} reemplaza={comprobante !== null} deshabilitada={enviando} alElegir={elegir} />
        )}
        {!enCurso && !leyendo && !corrigeDiferencia && comprobante !== null && !pregunta && (
          <div className="acciones">
            <button type="button" className="secundario" disabled={enviando} onClick={() => setCambiaArchivo((c) => !c)}>
              {cambiaArchivo ? "No cambiarlo" : "Cambiar el comprobante"}
            </button>
          </div>
        )}
      </section>

      {conDatos && noLeida && !corrige && <p role="note">{AVISO_LECTURA_FALLO}</p>}
      {conDatos && difiere && !otraCosa && <DistintoDeLaOrden leida={leida} version={version} />}
      {/* Tocado «Es otro comprobante», la pregunta deja lugar a la subida, con su «Cancelar» (REG-02). */}
      {conDatos && pregunta && cambiaArchivo && (
        <div className="acciones">
          <button type="button" className="secundario" disabled={enviando} onClick={() => setCambiaArchivo(false)}>
            Cancelar el cambio
          </button>
        </div>
      )}
      {conDatos && pregunta && !cambiaArchivo && (
        <div className="formulario">
          <h3>¿Qué pasó?</h3>
          <div className="acciones">
            <button type="button" disabled={enviando} onClick={() => setCambiaArchivo(true)}>
              Es otro comprobante: cambiarlo
            </button>
            <button
              type="button"
              className="secundario"
              disabled={enviando}
              onClick={() => {
                setAMano(true);
                setDijoMal(true);
              }}
            >
              Se leyó mal: corregir el dato
            </button>
            {puedeOtraCosa && (
              <button type="button" className="secundario" disabled={enviando} onClick={() => setOtraCosa(true)}>
                Se transfirió otro importe o a otra cuenta
              </button>
            )}
          </div>
        </div>
      )}

      {conDatos && !pregunta && (
        <form className="formulario" noValidate onSubmit={(e) => (otraCosa ? registrarOtraCosa(e, documentoDelRegistro) : registrar(e, documentoDelRegistro))}>
          {otraCosa ? (
            <>
              <h3>{corrigeDiferencia ? "Corregir lo registrado" : "Se transfirió otro importe o a otra cuenta"}</h3>
              <p>Lo que salió del banco, tal como figura en el comprobante.{corrigeDiferencia && " Lo anterior queda en la historia."}</p>
            </>
          ) : (
            <>
              {/* Sin título: es el de la ventana (REG-04). Leído con IA, se avisa solo lo que no coincide
                  o falta (D-25): lo que coincide no se dice. */}
              {cuentaLeida ? (
                leida.titular_distinto && (
                  <p role="note">
                    El comprobante dice que el titular es «{leida.titular_destino}» y la orden es a {version.razon_social}. Tiene que ser el mismo.
                  </p>
                )
              ) : (
                <p>
                  {corrige
                    ? "Queda registrado qué cambió y quién lo cambió."
                    : leida !== null
                      ? "Revisar lo que se leyó del comprobante y completar lo que falta."
                      : "Los datos van como figuran en el comprobante."}
                </p>
              )}
            </>
          )}
          <label htmlFor={`${id}-numero`}>Número de operación</label>
          <input id={`${id}-numero`} maxLength={60} value={registro.numero} disabled={enviando}
            {...marcaDe(`${id}-numero`, errores.numero)} onChange={(e) => cambiar("numero", e.target.value)} />
          <ErrorDeCampo idCampo={`${id}-numero`} mensaje={errores.numero} />
          <label htmlFor={`${id}-fecha`}>Fecha de la transferencia</label>
          <input id={`${id}-fecha`} type="date" value={registro.fecha} disabled={enviando}
            {...marcaDe(`${id}-fecha`, errores.fecha_operacion)} onChange={(e) => cambiar("fecha", e.target.value)} />
          <ErrorDeCampo idCampo={`${id}-fecha`} mensaje={errores.fecha_operacion} />
          {pideImporte && (
            <>
              <label htmlFor={`${id}-importe`}>{otraCosa ? "Importe que salió del banco" : "Importe transferido"}</label>
              <CampoImporte id={`${id}-importe`} valor={registro.importe} deshabilitado={enviando}
                {...marcaDe(`${id}-importe`, errores.importe)} alCambiar={(texto) => cambiar("importe", texto)} />
              <ErrorDeCampo idCampo={`${id}-importe`} mensaje={errores.importe} />
            </>
          )}
          {otraCosa && (
            <>
              <label htmlFor={`${id}-moneda`}>Moneda que salió del banco</label>
              <CampoMonedaSalida id={`${id}-moneda`} valor={registro.moneda} deshabilitado={enviando}
                marca={marcaDe(`${id}-moneda`, errores.moneda)} alCambiar={(codigo) => cambiar("moneda", codigo)} />
              <ErrorDeCampo idCampo={`${id}-moneda`} mensaje={errores.moneda} />
            </>
          )}
          {corrige && !otraCosa ? (
            <p>
              Cuenta de destino: {cuentaEnmascarada(version.cuenta_ultimos_digitos)}, la que ya está registrada. Si la plata fue a otra
              cuenta, corresponde «Se transfirió otro importe o a otra cuenta».
            </p>
          ) : cuentaLeida && !otraCosa ? null : (
            <>
              <label htmlFor={`${id}-cuenta`}>CBU/CVU de destino</label>
              <input id={`${id}-cuenta`} inputMode="numeric" maxLength={30} value={registro.cuenta} disabled={enviando}
                {...marcaDe(`${id}-cuenta`, errores.cuenta)} onChange={(e) => cambiar("cuenta", e.target.value)} />
              <ErrorDeCampo idCampo={`${id}-cuenta`} mensaje={errores.cuenta} />
            </>
          )}
          {otraCosa && (
            <>
              <label htmlFor={`${id}-explicacion`}>Qué pasó</label>
              <textarea id={`${id}-explicacion`} maxLength={300} rows={3} value={registro.explicacion} disabled={enviando}
                {...marcaDe(`${id}-explicacion`, errores.explicacion, `${id}-explicacion-ayuda`)}
                onChange={(e) => cambiar("explicacion", e.target.value)} />
              <ErrorDeCampo idCampo={`${id}-explicacion`} mensaje={errores.explicacion} />
              <p id={`${id}-explicacion-ayuda`}>Hasta 300 caracteres. Sin números de cuenta.</p>
            </>
          )}
          <div className="acciones">
            <button type="submit" disabled={enviando}>
              {corrigeDiferencia ? "Guardar la corrección" : otraCosa ? "Registrar lo que pasó" : corrige ? "Guardar la corrección" : "Registrar el pago"}
            </button>
            {cuentaLeida && !otraCosa && (
              <button type="button" className="secundario" disabled={enviando} onClick={() => setAMano(true)}>
                Corregir lo que se leyó
              </button>
            )}
            {puedeOtraCosa && !corrigeDiferencia && (
              <button type="button" className="secundario" disabled={enviando} onClick={() => setOtraCosa((v) => !v)}>
                {otraCosa ? "Volver al registro" : "Se transfirió otro importe o a otra cuenta"}
              </button>
            )}
            {alCancelar !== undefined && (
              <button type="button" className="secundario" disabled={enviando} onClick={alCancelar}>
                Cancelar
              </button>
            )}
          </div>
          {otraCosa && <p>{AVISO_OTRA_COSA}</p>}
        </form>
      )}
      {(!conDatos || pregunta) && alCancelar !== undefined && (
        <div className="acciones">
          <button type="button" className="secundario" disabled={enviando} onClick={alCancelar}>
            Cancelar
          </button>
        </div>
      )}
    </div>
  );
}
