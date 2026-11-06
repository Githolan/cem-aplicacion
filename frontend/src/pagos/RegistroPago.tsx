import { type FormEvent, useEffect, useId, useRef, useState } from "react";

import { api } from "../api/cliente";
import type { components } from "../api/tipos";
import { encabezadoCsrf } from "../csrf";
import { mensajeDeError as mensajeDeCarga, problemaDelArchivo } from "../Documentos";
import { CampoImporte } from "../egresos/CampoImporte";
import {
  aCentavos,
  cambioLaOrden,
  cambioLoVisto,
  ENCABEZADO_IDEMPOTENCIA,
  LARGO_TITULO,
  mensajeDeActo,
  mensajeDeError,
  renovarClave,
  SEGUNDO_FACTOR_REQUERIDO,
} from "../egresos/comun";
import { exigirSesion, SinSesion, useAlVolverLaRed, useEsperaAntivirus } from "../egresos/useConsorcio";
import { codigoDe, SIN_CONEXION } from "../fallas";
import type { ActoEnCurso } from "./useActo";

// Registrar el pago o corregir un registro observado, con su comprobante. Registrar no es pagar: la
// orden queda pagada recién con la conciliación. El comprobante funciona como antes del rediseño,
// con su lista; un solo paso llega en la fase 3 (§4).
//
// Corregir no pide la cuenta otra vez: `registrar_pago_manual` exige haberla visto en esta
// ejecución, no una apertura nueva (§8). Los datos se escriben del comprobante, nunca de la orden.

type DocumentoEnLista = components["schemas"]["DocumentoEnLista"];
type Version = components["schemas"]["VersionOrdenPago"];
type Registro = { numero: string; fecha: string; importe: string; cuenta: string; documento: string };

const FALLA_COMPROBANTES = "No pudimos actualizar los comprobantes.";
const hoy = () => new Date().toLocaleDateString("en-CA");

/** Los comprobantes que pueden respaldar el pago de esa orden: cargados desde su pago (app 0063) y con un archivo que pasó el antivirus. */
export function comprobantesDisponibles(documentos: DocumentoEnLista[], orden: string): DocumentoEnLista[] {
  return documentos.filter((d) => d.orden_pago_id === orden && d.versiones.some((v) => v.estado === "disponible"));
}

/** Cuántos comprobantes de esa orden espera revisar el antivirus. */
export function comprobantesEnRevision(documentos: DocumentoEnLista[], orden: string): number {
  return documentos.filter(
    (d) => d.orden_pago_id === orden && !d.versiones.some((v) => v.estado === "disponible") && d.versiones.some((v) => v.estado === "en_cuarentena"),
  ).length;
}

type Props = {
  consorcio: string;
  orden: string;
  version: Version;
  corrige: boolean;
  subtitulo: "h2" | "h3";
  acto: ActoEnCurso;
  aEntrar: () => void;
  alRegistrar: (texto: string) => void;
  /** Otra persona cambió la orden: lo escrito queda sobre lo que se vuelva a leer. */
  alCambiarLaOrden: () => void;
};

export function RegistroPago({ consorcio, orden, version, corrige, subtitulo: Subtitulo, acto, aEntrar, alRegistrar, alCambiarLaOrden }: Props) {
  const id = useId();
  const { enviando, setEnviando, setAviso, setPendiente, pendiente, montado } = acto;
  const [registro, setRegistro] = useState<Registro>({ numero: "", fecha: hoy(), importe: "", cuenta: "", documento: "" });
  const [comprobantes, setComprobantes] = useState<DocumentoEnLista[]>([]);
  const [archivo, setArchivo] = useState<File | null>(null);
  const claveCarga = useRef<string | null>(null);
  const claveArchivo = useRef<string | null>(null);
  /** El comprobante ya creado cuyo archivo todavía no entró: el reintento sube solo el archivo. */
  const sinArchivo = useRef<string | null>(null);
  /** El comprobante recién subido: se elige solo apenas pasa el antivirus. */
  const recienSubido = useRef<string | null>(null);
  const [esperandoAntivirus, setEsperandoAntivirus] = useState(false);
  /** El antivirus agotó la espera: deja de mirar solo hasta que la persona lo pida. */
  const [esperaAgotada, setEsperaAgotada] = useState(false);
  /** Lo que no se pudo actualizar de los comprobantes: el formulario sigue y lo dice. */
  const [fallaComprobantes, setFallaComprobantes] = useState<string | null>(null);
  /** Cambia para vaciar el campo del archivo después de subirlo. */
  const [entrada, setEntrada] = useState(0);

  /** Lee los comprobantes: una falla no se lleva lo escrito. */
  async function leerComprobantes() {
    try {
      const { data, error, response } = await api.GET("/api/v1/consorcios/{consorcio_id}/documentos", {
        params: { path: { consorcio_id: consorcio } },
      });
      exigirSesion(response);
      if (!montado.current) return;
      if (data === undefined) {
        const codigo = codigoDe(error);
        if (codigo === SEGUNDO_FACTOR_REQUERIDO)
          setPendiente({ acto: "ver los comprobantes", reintentar: () => void leerComprobantes() });
        else setFallaComprobantes(`${FALLA_COMPROBANTES} ${mensajeDeError(response.status, codigo)}`);
        return;
      }
      const deOrdenes = data.documentos.filter((d) => d.orden_pago_id != null);
      setFallaComprobantes(null);
      setComprobantes(deOrdenes);
      const nuevo = deOrdenes.find((d) => d.id === recienSubido.current);
      if (nuevo === undefined) return;
      if (nuevo.versiones.some((v) => v.estado === "disponible")) {
        recienSubido.current = null;
        setEsperandoAntivirus(false);
        setRegistro((r) => ({ ...r, documento: nuevo.id }));
        setAviso({ texto: "Listo: el comprobante pasó el antivirus y ya quedó elegido.", ok: true });
      } else if (!nuevo.versiones.some((v) => v.estado === "en_cuarentena")) {
        recienSubido.current = null;
        setEsperandoAntivirus(false);
        setAviso({ texto: "El antivirus rechazó el archivo: subí otro.", ok: false });
      }
    } catch (e: unknown) {
      if (e instanceof SinSesion) aEntrar();
      else setFallaComprobantes(`${FALLA_COMPROBANTES} ${SIN_CONEXION}`);
    }
  }

  const leer = useRef(leerComprobantes);
  leer.current = leerComprobantes;
  useEffect(() => {
    void leer.current();
  }, []);

  // Mientras un comprobante espera al antivirus, se mira solo: no hace falta un botón.
  const enRevision = comprobantesEnRevision(comprobantes, orden);
  useEsperaAntivirus((esperandoAntivirus || enRevision > 0) && !esperaAgotada && pendiente === null, () => void leerComprobantes(), () => {
    setEsperandoAntivirus(false);
    setEsperaAgotada(true);
    setFallaComprobantes("El antivirus sigue revisando el comprobante. Tocá «Actualizar comprobantes» en un rato.");
  });
  useAlVolverLaRed(fallaComprobantes !== null, () => void leerComprobantes());

  /** Carga el comprobante desde el pago (app 0063): el documento nace visible solo para quien lo sube y el Consejo; después va el archivo. */
  async function subirComprobante(elegido: File) {
    if (!montado.current) return;
    const problema = problemaDelArchivo(elegido);
    if (problema !== null) {
      // Ese archivo no va a entrar: el campo queda vacío para elegir otro.
      setArchivo(null);
      setEntrada((n) => n + 1);
      setAviso({ texto: problema, ok: false });
      return;
    }
    setEnviando(true);
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
          if (codigo === SEGUNDO_FACTOR_REQUERIDO) {
            setPendiente({ acto: "cargar el comprobante", reintentar: () => void subirComprobante(elegido) });
            return;
          }
          if (renovarClave(creado.response.status, codigo)) claveCarga.current = null;
          setAviso({ texto: mensajeDeActo(creado.response.status, codigo), ok: false });
          if (cambioLaOrden(codigo) || cambioLoVisto(creado.response.status, codigo)) alCambiarLaOrden();
          return;
        }
        claveCarga.current = null;
        sinArchivo.current = creado.data.id;
      }
      claveArchivo.current ??= crypto.randomUUID();
      const subido = await api.POST("/api/v1/consorcios/{consorcio_id}/documentos/{documento_id}/versiones", {
        headers: { ...encabezadoCsrf(), "Content-Type": elegido.type },
        params: {
          path: { consorcio_id: consorcio, documento_id: sinArchivo.current },
          header: { [ENCABEZADO_IDEMPOTENCIA]: claveArchivo.current },
        },
        // El archivo crudo, como en Documentos: `bodySerializer` lo deja pasar sin JSON.
        body: elegido as unknown as string,
        bodySerializer: (cuerpo) => cuerpo,
      });
      exigirSesion(subido.response);
      if (subido.data === undefined) {
        const codigo = codigoDe(subido.error);
        if (renovarClave(subido.response.status, codigo)) claveArchivo.current = null;
        const texto = mensajeDeCarga(subido.response.status, codigo, "subir", [], subido.response.headers.has("Retry-After"));
        setAviso({ texto: `El archivo no se subió. ${texto}`, ok: false });
        return;
      }
      claveArchivo.current = null;
      recienSubido.current = sinArchivo.current;
      sinArchivo.current = null;
      setArchivo(null);
      setEntrada((n) => n + 1);
      setAviso({ texto: "Listo: subimos el comprobante. El antivirus lo revisa en unos segundos y queda elegido solo.", ok: true });
      setEsperaAgotada(false);
      setEsperandoAntivirus(true);
      void leerComprobantes();
    } catch (e: unknown) {
      if (e instanceof SinSesion) aEntrar();
      else setAviso({ texto: SIN_CONEXION, ok: false });
    } finally {
      setEnviando(false);
    }
  }

  function registrar(evento: FormEvent) {
    evento.preventDefault();
    const centavos = aCentavos(registro.importe);
    if (centavos === null) {
      setAviso({ texto: "Escribí el importe tal como figura en el comprobante.", ok: false });
      return;
    }
    void acto.hacer({
      quePide: ["registrar", orden, version.numero, version.huella, registro],
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
            cuenta_destino: registro.cuenta.replace(/\D/g, ""),
            documento_id: registro.documento,
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

  return (
    <form className="formulario" onSubmit={registrar}>
      <Subtitulo>{corrige ? "Corregir el registro" : "Después de transferir, registrá la evidencia"}</Subtitulo>
      <p>Registrar no es pagar: la orden queda pagada recién cuando otro miembro del Consejo lo concilia.</p>
      <label htmlFor={`${id}-numero`}>Número de operación</label>
      <input id={`${id}-numero`} required maxLength={60} value={registro.numero} disabled={enviando}
        onChange={(e) => cambiar("numero", e.target.value)} />
      <label htmlFor={`${id}-fecha`}>Fecha de la transferencia</label>
      <input id={`${id}-fecha`} type="date" required value={registro.fecha} disabled={enviando}
        onChange={(e) => cambiar("fecha", e.target.value)} />
      <label htmlFor={`${id}-importe`}>Importe transferido (como figura en el comprobante)</label>
      <CampoImporte id={`${id}-importe`} valor={registro.importe} deshabilitado={enviando}
        alCambiar={(texto) => cambiar("importe", texto)} />
      <label htmlFor={`${id}-cuenta`}>CBU/CVU de destino (como figura en el comprobante)</label>
      <input id={`${id}-cuenta`} required inputMode="numeric" maxLength={30} value={registro.cuenta} disabled={enviando}
        onChange={(e) => cambiar("cuenta", e.target.value)} />
      <label htmlFor={`${id}-archivo`}>Archivo del comprobante (PDF, JPEG o PNG, hasta 20 MB)</label>
      <input key={entrada} id={`${id}-archivo`} type="file" accept="application/pdf,image/jpeg,image/png" disabled={enviando}
        onChange={(e) => {
          const elegido = e.target.files?.[0] ?? null;
          claveArchivo.current = null;
          setArchivo(elegido);
          if (elegido !== null) void subirComprobante(elegido);
        }} />
      {/* Sin esto, reintentar sería volver a elegir el mismo archivo, y el navegador no avisa. */}
      {archivo !== null && !enviando && (
        <div className="acciones">
          <button type="button" onClick={() => void subirComprobante(archivo)}>
            Reintentar la subida
          </button>
        </div>
      )}
      <p>
        Desde que lo subís lo ven solo vos y el Consejo, salvo quien preparó la orden. Aparece en la lista cuando el
        antivirus lo aprueba.
        {enRevision > 0 && ` En revisión: ${enRevision}.`}
      </p>
      {fallaComprobantes !== null && (
        <>
          <p className="aviso-error" role="alert">
            {fallaComprobantes}
          </p>
          <div className="acciones">
            <button type="button" className="secundario" disabled={enviando}
              onClick={() => {
                setEsperaAgotada(false);
                void leerComprobantes();
              }}>
              Actualizar comprobantes
            </button>
          </div>
        </>
      )}
      <label htmlFor={`${id}-comprobante`}>Comprobante de la transferencia</label>
      <select id={`${id}-comprobante`} required value={registro.documento} disabled={enviando}
        onChange={(e) => cambiar("documento", e.target.value)}>
        <option value="">Elegí el comprobante</option>
        {comprobantesDisponibles(comprobantes, orden).map((d) => (
          <option key={d.id} value={d.id}>
            {d.titulo} ({d.fecha})
          </option>
        ))}
      </select>
      <div className="acciones">
        <button type="submit" disabled={enviando || registro.documento === ""}>
          {corrige ? "Guardar la corrección" : "Registrar el pago"}
        </button>
      </div>
    </form>
  );
}
