import { type FormEvent, useId, useRef, useState } from "react";

import { api } from "../api/cliente";
import type { components } from "../api/tipos";
import { encabezadoCsrf } from "../csrf";
import { mensajeDeError as mensajeDeCarga, problemaDelArchivo } from "../Documentos";
import { CampoImporte } from "../egresos/CampoImporte";
import {
  aCentavos,
  aTexto,
  cambioLaOrden,
  cambioLoVisto,
  cuentaEnmascarada,
  ENCABEZADO_IDEMPOTENCIA,
  LARGO_TITULO,
  mensajeDeActo,
  renovarClave,
} from "../egresos/comun";
import { exigirSesion, SinSesion, useConsultaDelAntivirus } from "../egresos/useConsorcio";
import { codigoDe, SIN_CONEXION } from "../fallas";
import { AVISO_ANTIVIRUS_TARDA, AVISO_ARCHIVO_RECHAZADO } from "./textos";
import type { ActoEnCurso } from "./useActo";
import { useUrlDelArchivo, VisorComprobante } from "./VisorComprobante";
import { ZonaComprobante } from "./ZonaComprobante";

// Registrar el pago o corregir un registro observado, con el comprobante al lado. Registrar no es
// pagar: la orden queda pagada recién con la conciliación.
//
// El comprobante va en un paso (§4): elegir el archivo dispara todo y la ficha dice en qué va. No hay
// lista: vale `comprobante_actual`, y volver a subir lo reemplaza. La lectura del antivirus la hace
// la ficha, que se vuelve a leer sola, y sobrevive a salir y volver.
//
// Los datos se escriben del comprobante, nunca de la orden: si no, la comparación no vale. Corregir
// abre con lo registrado, y no pide la cuenta (§8): ni una apertura nueva, ni escribirla otra vez.

type Comprobante = components["schemas"]["ComprobanteActual"];
type Subido = components["schemas"]["ComprobanteSubido"];
type Version = components["schemas"]["VersionOrdenPago"];
type Pago = components["schemas"]["PagoManual"];
type Registro = { numero: string; fecha: string; importe: string; cuenta: string };

const hoy = () => new Date().toLocaleDateString("en-CA");

function datosIniciales(corregido: Pago | undefined): Registro {
  if (corregido === undefined) return { numero: "", fecha: hoy(), importe: "", cuenta: "" };
  return {
    numero: corregido.numero_operacion,
    fecha: corregido.fecha_operacion,
    importe: aTexto(corregido.importe_centavos),
    cuenta: "",
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
  subtitulo: "h2" | "h3";
  acto: ActoEnCurso;
  aEntrar: () => void;
  alRegistrar: (texto: string) => void;
  /** Vuelve a leer la ficha, para ver en qué va el antivirus. */
  releer: () => void;
  /** Otra persona cambió la orden: lo escrito queda sobre lo que se vuelva a leer. */
  alCambiarLaOrden: () => void;
};

export function RegistroPago(props: Props) {
  const { consorcio, orden, version, corregido, comprobante, subido, subtitulo: Subtitulo, acto, aEntrar, alRegistrar, releer, alCambiarLaOrden } = props;
  const id = useId();
  const { enviando, setEnviando, setAviso, montado } = acto;
  const corrige = corregido !== undefined;
  const [registro, setRegistro] = useState<Registro>(() => datosIniciales(corregido));
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
          setAviso({ texto: mensajeDeActo(creado.response.status, codigo), ok: false });
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

  function elegir(archivo: File) {
    const problema = problemaDelArchivo(archivo);
    if (problema !== null) {
      setAviso({ texto: problema, ok: false });
      return;
    }
    // Otro archivo es otro pedido: si el anterior no entró, sirve su documento, no su clave.
    claveArchivo.current = null;
    setElegido(archivo);
    void subir(archivo);
  }

  function registrar(evento: FormEvent, documento: string) {
    evento.preventDefault();
    const centavos = aCentavos(registro.importe);
    if (centavos === null) {
      setAviso({ texto: "Escribí el importe tal como figura en el comprobante.", ok: false });
      return;
    }
    void acto.hacer({
      quePide: ["registrar", orden, version.numero, version.huella, registro, documento],
      nombre: "registrar el pago",
      pedir: (clave) =>
        api.POST("/api/v1/consorcios/{consorcio_id}/ordenes-pago/{orden_id}/pago-manual", {
          headers: encabezadoCsrf(),
          params: { path: { consorcio_id: consorcio, orden_id: orden }, header: { [ENCABEZADO_IDEMPOTENCIA]: clave } },
          body: {
            version: version.numero,
            huella: version.huella,
            numero_operacion: registro.numero.trim(),
            fecha_operacion: registro.fecha,
            importe_centavos: centavos,
            moneda: version.moneda,
            ...(corrige ? {} : { cuenta_destino: registro.cuenta.replace(/\D/g, "") }),
            documento_id: documento,
          },
        }),
      alTerminar: () => alRegistrar("Listo: registraste el pago. Falta que otro miembro del Consejo lo concilie."),
      alFallar: (estado, codigo) => {
        if (cambioLaOrden(codigo) || cambioLoVisto(estado, codigo)) alCambiarLaOrden();
      },
    });
  }

  function cambiar<K extends keyof Registro>(campo: K, valor: Registro[K]) {
    setRegistro((r) => ({ ...r, [campo]: valor }));
  }

  const conDatos = comprobante !== null && !enCurso;

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
        {(subiendo || revisando) && (
          <div role="status">
            <Subtitulo>Estamos revisando el comprobante</Subtitulo>
            <ol className="avance-comprobante">
              <li aria-current={subiendo ? "step" : undefined}>{subiendo ? "Subiendo el archivo…" : "Subido"}</li>
              <li aria-current={revisando ? "step" : undefined}>{revisando ? "El antivirus lo está revisando…" : "Antivirus"}</li>
            </ol>
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
            <button type="button" disabled={enviando} onClick={() => void subir(elegido)}>
              Reintentar
            </button>
            <button type="button" className="secundario" disabled={enviando} onClick={() => setNoSubio(false)}>
              Elegir otro archivo
            </button>
          </div>
        )}
        {!enCurso && <ZonaComprobante deshabilitada={enviando} reemplaza={comprobante !== null} alElegir={elegir} />}
      </section>

      {conDatos && (
        <form className="formulario" onSubmit={(e) => registrar(e, comprobante.documento_id)}>
          <Subtitulo>{corrige ? "Corregir el registro" : "Registrar el pago"}</Subtitulo>
          <p>{corrige ? "Cambiá lo que está mal. Queda registrado qué cambió y quién lo cambió." : "Escribí los datos como figuran en el comprobante."}</p>
          <label htmlFor={`${id}-numero`}>Número de operación</label>
          <input id={`${id}-numero`} required maxLength={60} value={registro.numero} disabled={enviando}
            onChange={(e) => cambiar("numero", e.target.value)} />
          <label htmlFor={`${id}-fecha`}>Fecha de la transferencia</label>
          <input id={`${id}-fecha`} type="date" required value={registro.fecha} disabled={enviando}
            onChange={(e) => cambiar("fecha", e.target.value)} />
          <label htmlFor={`${id}-importe`}>Importe transferido (como figura en el comprobante)</label>
          <CampoImporte id={`${id}-importe`} valor={registro.importe} deshabilitado={enviando}
            alCambiar={(texto) => cambiar("importe", texto)} />
          {corrige ? (
            <p>
              Cuenta de destino: {cuentaEnmascarada(version.cuenta_ultimos_digitos)}, la que ya registraste. Si la plata fue a otra
              cuenta, no la corrijas acá: avisale al Consejo.
            </p>
          ) : (
            <>
              <label htmlFor={`${id}-cuenta`}>CBU/CVU de destino (como figura en el comprobante)</label>
              <input id={`${id}-cuenta`} required inputMode="numeric" maxLength={30} value={registro.cuenta} disabled={enviando}
                onChange={(e) => cambiar("cuenta", e.target.value)} />
            </>
          )}
          <div className="acciones">
            <button type="submit" disabled={enviando}>
              {corrige ? "Guardar la corrección" : "Registrar el pago"}
            </button>
          </div>
          <p>Queda pagada cuando otro miembro del Consejo lo concilie.</p>
        </form>
      )}
    </div>
  );
}
