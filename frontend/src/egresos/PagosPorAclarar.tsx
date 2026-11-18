import { type ReactNode, useEffect, useId, useRef, useState } from "react";
import { Link } from "react-router";

import { api } from "../api/cliente";
import { CabeceraInterna } from "../CabeceraInterna";
import { RUTA_INICIO } from "../enlace";
import type { components } from "../api/tipos";
import { encabezadoCsrf } from "../csrf";
import { useAEntrar } from "../sesionEncima";
import { TIPOS_DE_DOCUMENTO, TOPE_BYTES } from "../archivos";
import { mensajeDeError as mensajeDeCarga } from "../Documentos";
import { ErrorDeCampo, type ErroresDeCampo, faltaCompletar, hayErrores, marcaDe, SIN_ERRORES, sinElDe } from "../ErrorDeCampo";
import { FichaDeRegistro, FilaDeRegistro, useFichas } from "../FichaDeRegistro";
import { codigoDe, SIN_CONEXION } from "../fallas";
import { Hoja } from "../Hoja";
import { CampoMonedaSalida, FALTA_CODIGO_MONEDA, monedaValida } from "../pagos/CampoMonedaSalida";
import { type Resultado, ResultadoDelActo } from "../ResultadoDelActo";
import { SubirArchivo } from "../SubirArchivo";
import { type FinDeEnvio, VentanaDeFormulario } from "../VentanaDeFormulario";
import { CampoImporte } from "./CampoImporte";
import {
  aCentavos,
  aDecimal,
  type Aviso,
  ENCABEZADO_IDEMPOTENCIA,
  importe,
  mensajeDeActo,
  mensajeDeError,
  momentoAlFinal,
  renovarClave,
  SEGUNDO_FACTOR_REQUERIDO,
  sinLaFalla,
  sinPisarLoHecho,
  soltarClaveSiCambio,
} from "./comun";
import { AvisoPantalla, FallaDeLectura } from "./EstadoPantalla";
import { useCodigoEnMedio } from "./useCodigoEnMedio";
import { exigirSesion, SinSesion, useAlVolverLaRed, useConsultaDelAntivirus, useMontado } from "./useConsorcio";

// Página /pagos/por-aclarar (app 0067, P3 179): quien vio la cuenta en el acto de ejecución y
// ya no ejecuta pagos en ese edificio (renunció o lo reemplazaron) declara que no transfirió.
// Sin esa declaración la orden queda trabada: el Consejo no puede confirmar que no se pagó. Lo
// confirma otro miembro del Consejo. La lista viene de todos los edificios (sin el mandato, el
// edificio ya no aparece en la lista de la persona) y nunca trae la cuenta.
//
// Si transfirió (app 0068, P3 184), sube el comprobante y registra el pago desde acá; la cuenta
// la escribe como figura en el comprobante. Otro miembro del Consejo lo concilia.
//
// El comprobante subido no tiene «Ver»: la lista no trae el id de la versión, y quien está acá ya no
// ejecuta pagos en ese edificio, así que puede no tener `documentos.ver` para abrir `…/contenido`.
//
// Si transfirió otra cosa (decisión 3 del 07-11-2026), el mismo formulario registra lo que salió del
// banco. La orden no queda pagada: lo resuelve el Consejo.
//
// Informe de fricción, tanda 8: cada pago es una fila que abre su ficha (ACL-01); «No se transfirió»
// pide el motivo en su ventana (C8P-18) y «Sí se transfirió» abre la suya, que hasta que el antivirus
// acepta el comprobante muestra solo la carga y su estado (ACL-02, C8P-10). El antivirus se sigue
// mirando solo, sin «Actualizar» (ACL-04); la subida no lleva aviso propio (ACL-05). Aclarado el
// último, queda el «Listo» con la vuelta al inicio (ACL-06).

type PorAclarar = components["schemas"]["PagoPorAclarar"];

export const RUTA_POR_ACLARAR = "/pagos/por-aclarar";

type Lectura = { tipo: "cargando" } | { tipo: "sin_servicio" } | { tipo: "ok"; elementos: PorAclarar[] };
type Registro = { numero: string; fecha: string; importe: string; moneda: string; cuenta: string; explicacion: string };
/** La ventana abierta sobre la ficha: declarar que no se transfirió o registrar la transferencia. */
type Accion = "no" | "si";

const REGISTRO_VACIO: Registro = { numero: "", fecha: "", importe: "", moneda: "", cuenta: "", explicacion: "" };
const hoy = () => new Date().toLocaleDateString("en-CA");
/** El título del comprobante cargado desde acá. */
const TITULO_COMPROBANTE = "Comprobante de la transferencia";
export const NO_SE_TRANSFIRIO = "No se transfirió";
export const SI_SE_TRANSFIRIO = "Sí se transfirió";
export const REGISTRAR_TRANSFERENCIA = "Registrar la transferencia";
export const OTRA_COSA = "Se transfirió otro importe o a otra cuenta";
export const SIN_PAGOS = "No hay pagos para aclarar.";

/** Qué pasa con el comprobante, según el estado de su último archivo. */
function textoDelComprobante(p: PorAclarar): string {
  if (p.comprobante_estado === "disponible") return "El comprobante pasó el antivirus: ya se puede registrar el pago.";
  if (p.comprobante_estado === "en_cuarentena") return "El antivirus revisa el comprobante: esto se actualiza solo en unos segundos.";
  if (p.comprobante_estado === "rechazado") return "El antivirus rechazó el archivo: hay que subir otro.";
  return "Primero hay que subir el comprobante de la transferencia.";
}

const tituloDe = (p: PorAclarar) => `${p.consorcio}: ${importe(p.importe_centavos, p.moneda)}`;
/** FL-ACL-03 (tanda 9, app 0110): la fila dice a qué proveedor era, sin la cuenta. */
export const resumenDe = (p: Pick<PorAclarar, "razon_social" | "concepto">) => `${p.razon_social} · ${p.concepto}`;

export function PagosPorAclarar() {
  const id = useId();
  const aEntrar = useAEntrar();
  const [lectura, setLectura] = useState<Lectura>({ tipo: "cargando" });
  const [vuelta, setVuelta] = useState(0);
  const [enviando, setEnviando] = useState(false);
  /** Por qué no llegó la lista (sin ninguna ventana abierta). */
  const [aviso, setAviso] = useState<Aviso | null>(null);
  /** El resultado del último acto, junto a la lista donde estaba la fila (tanda 1, punto 7). */
  const [resultado, setResultado] = useState<Resultado | null>(null);
  const fichas = useFichas<string>();
  const [accion, setAccion] = useState<Accion | null>(null);
  const [motivo, setMotivo] = useState("");
  const [registro, setRegistro] = useState<Registro>(REGISTRO_VACIO);
  const [errores, setErrores] = useState<ErroresDeCampo>(SIN_ERRORES);
  const [otraCosa, setOtraCosa] = useState(false);
  const [archivo, setArchivo] = useState<File | null>(null);
  /** El nombre del último archivo que entró desde esta ventana; la API no informa el del comprobante. */
  const [subido, setSubido] = useState<string | null>(null);
  /** Por qué no se subió el archivo: dentro de la ventana, junto a la carga. */
  const [fallaSubida, setFallaSubida] = useState<string | null>(null);
  /** Lo que no se pudo actualizar con una ventana abierta: sigue a la vista con lo escrito. */
  const [fallaLista, setFallaLista] = useState<string | null>(null);
  const conVentana = useRef(false);
  const claveCarga = useRef<string | null>(null);
  const claveArchivo = useRef<string | null>(null);
  /** El comprobante ya creado cuyo archivo todavía no entró: el reintento sube solo el archivo. */
  const sinArchivo = useRef<string | null>(null);
  const clave = useRef<string | null>(null);
  const ultimoActo = useRef<string | null>(null);
  const montado = useMontado();
  const codigo = useCodigoEnMedio();

  useEffect(() => {
    let vigente = true;
    function noLlego(texto: string) {
      if (conVentana.current) setFallaLista(`No se pudo actualizar la lista. ${texto}`);
      else {
        setLectura({ tipo: "sin_servicio" });
        setAviso(sinPisarLoHecho(texto));
      }
    }
    void api
      .GET("/api/v1/pagos-por-aclarar")
      .then(({ data, error, response }) => {
        exigirSesion(response);
        if (!vigente) return;
        if (data !== undefined) {
          setFallaLista(null);
          setLectura({ tipo: "ok", elementos: data.elementos });
        } else noLlego(mensajeDeError(response.status, codigoDe(error)));
      })
      .catch((e: unknown) => {
        if (!vigente) return;
        if (e instanceof SinSesion) aEntrar();
        else noLlego(SIN_CONEXION);
      });
    return () => {
      vigente = false;
    };
  }, [vuelta, aEntrar]);

  function reintentarLectura() {
    setAviso(sinLaFalla);
    setLectura({ tipo: "cargando" });
    setVuelta((v) => v + 1);
  }

  const elementos = lectura.tipo === "ok" ? lectura.elementos : [];
  const abierto = elementos.find((p) => p.orden_id === fichas.abierta);
  conVentana.current = abierto !== undefined && accion !== null;
  // Mientras el antivirus revisa el comprobante, se vuelve a mirar sin rendirse (ACL-04): una
  // lectura que falla en el medio se repite en la vuelta siguiente.
  useConsultaDelAntivirus(abierto?.comprobante_estado === "en_cuarentena" && accion === "si", () => setVuelta((v) => v + 1));
  useAlVolverLaRed(fallaLista !== null, () => setVuelta((v) => v + 1));

  /** Terminó un acto: se cierran la ventana y la ficha, y el resultado queda junto a la lista. */
  function terminar(texto: string) {
    setAccion(null);
    fichas.cerrar(false);
    setResultado({ texto, ok: true });
    setVuelta((v) => v + 1);
  }

  /** El pedido de un acto con su clave; ante el segundo factor, la hoja del código encima de la
   * ventana y, confirmado, el mismo pedido con la misma clave. */
  async function conClave<T>(
    acto: string,
    huella: string,
    pedir: (clave: string) => Promise<{ data?: T; error?: unknown; response: Response }>,
  ): Promise<{ ok: true; data: T } | { ok: false; fin: FinDeEnvio }> {
    soltarClaveSiCambio(clave, ultimoActo, huella);
    for (;;) {
      clave.current ??= crypto.randomUUID();
      try {
        const { data, error, response } = await pedir(clave.current);
        exigirSesion(response);
        if (data !== undefined) {
          clave.current = null;
          return { ok: true, data };
        }
        const codigoError = codigoDe(error);
        if (codigoError === SEGUNDO_FACTOR_REQUERIDO) {
          if (await codigo.pedir(acto)) continue;
          return { ok: false, fin: "seguir" };
        }
        if (renovarClave(response.status, codigoError)) clave.current = null;
        setVuelta((v) => v + 1);
        return { ok: false, fin: { rechazo: mensajeDeActo(response.status, error) } };
      } catch (e: unknown) {
        if (!(e instanceof SinSesion)) return { ok: false, fin: { rechazo: SIN_CONEXION } };
        aEntrar();
        return { ok: false, fin: "seguir" };
      }
    }
  }

  async function declarar(p: PorAclarar): Promise<FinDeEnvio> {
    const faltan = faltaCompletar({ motivo: [motivo, "Falta decir por qué no se transfirió."] });
    if (hayErrores(faltan)) {
      setErrores(faltan);
      return "seguir";
    }
    if (!montado.current) return "cerrar";
    const texto = motivo.trim();
    const r = await conClave("declarar que no se transfirió", JSON.stringify(["declarar", p.orden_id, p.version, texto]), (k) =>
      api.POST("/api/v1/consorcios/{consorcio_id}/pagos-por-aclarar/{orden_id}/no-realizado", {
        headers: encabezadoCsrf(),
        params: { path: { consorcio_id: p.consorcio_id, orden_id: p.orden_id }, header: { [ENCABEZADO_IDEMPOTENCIA]: k } },
        body: { version: p.version, motivo: texto },
      }),
    );
    if (!r.ok) return r.fin;
    terminar("Listo: quedó declarado que no se transfirió. Ahora lo confirma otro miembro del Consejo.");
    return "cerrar";
  }

  /** Lo que falta en el registro, campo por campo (decisión 4: el botón siempre activo). */
  function faltaDelRegistro(): ErroresDeCampo {
    const faltan: Record<string, string> = {
      ...faltaCompletar({
        numero: [registro.numero, "Falta escribir el número de operación."],
        fecha: [registro.fecha, "Falta elegir la fecha de la transferencia."],
        cuenta: [registro.cuenta, "Falta escribir el CBU o CVU de destino."],
        ...(otraCosa ? { explicacion: [registro.explicacion, "Falta escribir qué pasó."] as const } : {}),
      }),
    };
    if (aCentavos(registro.importe) === null)
      faltan.importe = registro.importe.trim() === "" ? "Falta escribir el importe." : "Escribir el importe con números, como 15000 o 15.000,50.";
    if (otraCosa && !monedaValida(registro.moneda)) faltan.moneda = FALTA_CODIGO_MONEDA;
    return faltan;
  }

  async function registrar(p: PorAclarar): Promise<FinDeEnvio> {
    const faltan = faltaDelRegistro();
    if (hayErrores(faltan)) {
      setErrores(faltan);
      return "seguir";
    }
    const documento = p.comprobante_id;
    const centavos = aCentavos(registro.importe);
    if (documento == null || centavos === null || !montado.current) return "seguir";
    const ruta = { consorcio_id: p.consorcio_id, orden_id: p.orden_id };
    const huella = JSON.stringify([otraCosa ? "otra-cosa" : "registrar", p.orden_id, p.version, p.huella, documento, registro]);
    const cuenta = registro.cuenta.replace(/\D/g, "");
    const r = otraCosa
      ? await conClave("registrar lo que pasó", huella, (k) =>
          api.POST("/api/v1/consorcios/{consorcio_id}/pagos-por-aclarar/{orden_id}/diferencia", {
            headers: encabezadoCsrf(),
            params: { path: ruta, header: { [ENCABEZADO_IDEMPOTENCIA]: k } },
            body: {
              version: p.version,
              huella: p.huella,
              numero_operacion: registro.numero.trim(),
              fecha_operacion: registro.fecha,
              importe_real: aDecimal(centavos),
              moneda: registro.moneda,
              cuenta_real: cuenta,
              documento_id: documento,
              explicacion: registro.explicacion.trim(),
            },
          }),
        )
      : await conClave("registrar el pago", huella, (k) =>
          api.POST("/api/v1/consorcios/{consorcio_id}/pagos-por-aclarar/{orden_id}/pago", {
            headers: encabezadoCsrf(),
            params: { path: ruta, header: { [ENCABEZADO_IDEMPOTENCIA]: k } },
            body: {
              version: p.version,
              huella: p.huella,
              lectura_mal: false,
              numero_operacion: registro.numero.trim(),
              fecha_operacion: registro.fecha,
              importe_centavos: centavos,
              moneda: p.moneda,
              cuenta_destino: cuenta,
              documento_id: documento,
            },
          }),
        );
    if (!r.ok) return r.fin;
    terminar(
      otraCosa
        ? "Listo: quedó registrado lo que pasó. La orden no queda pagada: lo resuelve el Consejo."
        : "Listo: el pago quedó registrado. Ahora lo concilia otro miembro del Consejo.",
    );
    return "cerrar";
  }

  /** Crea el comprobante (si todavía no hay uno propio de la orden) y sube el archivo, que ya pasó el
   * control de tipo y tamaño de `SubirArchivo`. Lo que pasa después lo dice la línea de estado del
   * comprobante, que se actualiza sola (ACL-05). */
  async function subirComprobante(p: PorAclarar, elegido: File) {
    if (!montado.current) return;
    const ruta = { consorcio_id: p.consorcio_id, orden_id: p.orden_id };
    setEnviando(true);
    setFallaSubida(null);
    try {
      for (;;) {
        sinArchivo.current ??= p.comprobante_id ?? null;
        if (sinArchivo.current === null) {
          claveCarga.current ??= crypto.randomUUID();
          const creado = await api.POST("/api/v1/consorcios/{consorcio_id}/pagos-por-aclarar/{orden_id}/comprobante", {
            headers: encabezadoCsrf(),
            params: { path: ruta, header: { [ENCABEZADO_IDEMPOTENCIA]: claveCarga.current } },
            // Un título fijo: el concepto de la orden puede no pasar el filtro de contactos del título.
            body: { titulo: TITULO_COMPROBANTE, fecha: registro.fecha || hoy() },
          });
          exigirSesion(creado.response);
          if (creado.data === undefined) {
            const codigoError = codigoDe(creado.error);
            if (codigoError === SEGUNDO_FACTOR_REQUERIDO) {
              if (await codigo.pedir("cargar el comprobante")) continue;
              return;
            }
            if (renovarClave(creado.response.status, codigoError)) claveCarga.current = null;
            setFallaSubida(mensajeDeActo(creado.response.status, creado.error));
            setVuelta((v) => v + 1);
            return;
          }
          claveCarga.current = null;
          sinArchivo.current = creado.data.id;
        }
        claveArchivo.current ??= crypto.randomUUID();
        const subido = await api.POST(
          "/api/v1/consorcios/{consorcio_id}/pagos-por-aclarar/{orden_id}/comprobante/{documento_id}/versiones",
          {
            headers: { ...encabezadoCsrf(), "Content-Type": elegido.type },
            params: {
              path: { ...ruta, documento_id: sinArchivo.current },
              header: { [ENCABEZADO_IDEMPOTENCIA]: claveArchivo.current },
            },
            // El archivo crudo, como en Documentos: `bodySerializer` lo deja pasar sin JSON.
            body: elegido as unknown as string,
            bodySerializer: (cuerpo) => cuerpo,
          },
        );
        exigirSesion(subido.response);
        if (subido.data === undefined) {
          const codigoError = codigoDe(subido.error);
          if (codigoError === SEGUNDO_FACTOR_REQUERIDO) {
            if (await codigo.pedir("subir el comprobante")) continue;
            return;
          }
          if (renovarClave(subido.response.status, codigoError)) claveArchivo.current = null;
          const texto = mensajeDeCarga(subido.response.status, codigoError, "subir", [], subido.response.headers.has("Retry-After"));
          setFallaSubida(`El archivo no se subió. ${texto}`);
          return;
        }
        claveArchivo.current = null;
        sinArchivo.current = null;
        setSubido(elegido.name);
        setArchivo(null);
        setVuelta((v) => v + 1);
        return;
      }
    } catch (e: unknown) {
      if (e instanceof SinSesion) aEntrar();
      else setFallaSubida(`El archivo no se subió. ${SIN_CONEXION}`);
    } finally {
      setEnviando(false);
    }
  }

  function abrirAccion(p: PorAclarar, a: Accion) {
    setErrores(SIN_ERRORES);
    setFallaLista(null);
    if (a === "no") setMotivo("");
    else {
      setRegistro({ ...REGISTRO_VACIO, moneda: p.moneda });
      setOtraCosa(false);
      setArchivo(null);
      setSubido(null);
      setFallaSubida(null);
      claveCarga.current = null;
      claveArchivo.current = null;
      sinArchivo.current = null;
    }
    setAccion(a);
  }

  const cerrarAccion = () => {
    setAccion(null);
    setFallaLista(null);
  };

  function cambiar(campo: keyof Registro, valor: string) {
    setRegistro((r) => ({ ...r, [campo]: valor }));
    setErrores((e) => sinElDe(e, campo));
  }

  /** Un campo del registro con su etiqueta y su error. */
  function campo(nombre: keyof Registro, etiqueta: string, control: (marca: ReturnType<typeof marcaDe>) => ReactNode) {
    const idCampo = `${id}-${nombre}`;
    return (
      <>
        <label htmlFor={idCampo}>{etiqueta}</label>
        {control(marcaDe(idCampo, errores[nombre]))}
        <ErrorDeCampo idCampo={idCampo} mensaje={errores[nombre]} />
      </>
    );
  }

  function ventanaNo(p: PorAclarar) {
    const idMotivo = `${id}-motivo`;
    return (
      <VentanaDeFormulario titulo="Declarar que no se transfirió" textoEnviar={NO_SE_TRANSFIRIO} alEnviar={() => declarar(p)} alCerrar={cerrarAccion}>
        <label htmlFor={idMotivo}>Por qué no se transfirió</label>
        <input id={idMotivo} data-foco maxLength={200} value={motivo} {...marcaDe(idMotivo, errores.motivo)}
          onChange={(e) => {
            setMotivo(e.target.value);
            setErrores((actuales) => sinElDe(actuales, "motivo"));
          }} />
        <ErrorDeCampo idCampo={idMotivo} mensaje={errores.motivo} />
      </VentanaDeFormulario>
    );
  }

  /** La carga del comprobante y su estado: lo primero de la ventana de la transferencia. */
  function comprobante(p: PorAclarar, conFoco: boolean) {
    return (
      <>
        <p role="status">{textoDelComprobante(p)}</p>
        {fallaLista !== null && (
          <p className="aviso-error" role="alert">
            {fallaLista}
          </p>
        )}
        {fallaSubida !== null && (
          <p className="aviso-error" role="alert">
            {fallaSubida}
          </p>
        )}
        {/* DOC-16: con el comprobante subido (o elegido y subiéndose), un solo «Cambiar el archivo»; si el
            antivirus lo rechazó, las formas de elegir a la vista, porque hay que subir otro. */}
        <SubirArchivo titulo={p.comprobante_id == null ? "Subir el comprobante" : "Comprobante de la transferencia"} de="del comprobante"
          tipos={TIPOS_DE_DOCUMENTO} tope={TOPE_BYTES} reemplaza={p.comprobante_id != null} deshabilitada={enviando}
          elegido={archivo?.name ?? subido}
          conArchivo={archivo !== null || (p.comprobante_id != null && p.comprobante_estado !== "rechazado")}
          refPrimero={conFoco ? () => undefined : undefined}
          alElegir={(elegido) => {
            claveArchivo.current = null;
            setArchivo(elegido);
            void subirComprobante(p, elegido);
          }} />
        {/* Sin esto, reintentar sería volver a elegir el mismo archivo, y el navegador no avisa. */}
        {archivo !== null && !enviando && fallaSubida !== null && (
          <div className="acciones">
            <button className="secundario" type="button" onClick={() => void subirComprobante(p, archivo)}>
              Reintentar la subida
            </button>
          </div>
        )}
      </>
    );
  }

  function ventanaSi(p: PorAclarar) {
    // Hasta que el antivirus acepta el comprobante, solo la carga y su estado: los datos del pago y
    // el botón de registrar llegan con el comprobante aceptado (C8P-10, como en la ficha de la orden).
    if (p.comprobante_estado !== "disponible")
      return (
        <Hoja titulo={REGISTRAR_TRANSFERENCIA} alEscapar={enviando ? undefined : cerrarAccion}>
          <div className="formulario formulario-movil">
            {comprobante(p, true)}
            <div className="acciones">
              <button type="button" className="secundario" disabled={enviando} onClick={cerrarAccion}>
                Cancelar
              </button>
            </div>
          </div>
        </Hoja>
      );
    return (
      <VentanaDeFormulario titulo={otraCosa ? OTRA_COSA : REGISTRAR_TRANSFERENCIA} textoEnviar={otraCosa ? "Registrar lo que pasó" : "Registrar el pago"}
        alEnviar={() => registrar(p)} alCerrar={cerrarAccion} ancha>
        {comprobante(p, false)}
        {campo("numero", "Número de operación", (m) => (
          <input id={`${id}-numero`} data-foco maxLength={60} value={registro.numero} {...m} onChange={(e) => cambiar("numero", e.target.value)} />
        ))}
        {campo("fecha", "Fecha de la transferencia", (m) => (
          <input id={`${id}-fecha`} type="date" value={registro.fecha} {...m} onChange={(e) => cambiar("fecha", e.target.value)} />
        ))}
        {campo("importe", otraCosa ? "Importe que salió del banco" : "Importe transferido (como figura en el comprobante)", () => (
          <CampoImporte id={`${id}-importe`} valor={registro.importe} deshabilitado={false} alCambiar={(texto) => cambiar("importe", texto)} />
        ))}
        {otraCosa &&
          // Por su nombre, con la de la orden elegida (FL-ACL-02); el código, solo si fue otra.
          campo("moneda", "Moneda que salió del banco", (m) => (
            <CampoMonedaSalida id={`${id}-moneda`} valor={registro.moneda} marca={m} alCambiar={(codigo) => cambiar("moneda", codigo)} />
          ))}
        {campo("cuenta", "CBU/CVU de destino (como figura en el comprobante)", (m) => (
          <input id={`${id}-cuenta`} inputMode="numeric" maxLength={30} value={registro.cuenta} {...m} onChange={(e) => cambiar("cuenta", e.target.value)} />
        ))}
        {otraCosa &&
          campo("explicacion", "Qué pasó", () => (
            <>
              <textarea id={`${id}-explicacion`} maxLength={300} rows={3} value={registro.explicacion}
                {...marcaDe(`${id}-explicacion`, errores.explicacion, `${id}-explicacion-ayuda`)}
                onChange={(e) => cambiar("explicacion", e.target.value)} />
              {/* Que la orden no queda pagada lo dice el aviso del final (ACL-03). */}
              <p id={`${id}-explicacion-ayuda`}>Sin números de cuenta.</p>
            </>
          ))}
        <div className="acciones">
          <button type="button" className="secundario"
            onClick={() => {
              setErrores(SIN_ERRORES);
              setOtraCosa((v) => !v);
            }}>
            {otraCosa ? "Volver al registro" : OTRA_COSA}
          </button>
        </div>
      </VentanaDeFormulario>
    );
  }

  function ficha() {
    if (abierto === undefined) return null;
    const p = abierto;
    return (
      <FichaDeRegistro
        titulo={tituloDe(p)}
        alCerrar={() => {
          setAccion(null);
          fichas.cerrar();
        }}
        ocupada={enviando}
        datos={[
          { etiqueta: "Proveedor", valor: p.razon_social },
          { etiqueta: "Concepto", valor: p.concepto },
          { etiqueta: "Cuenta vista el", valor: momentoAlFinal(p.cuenta_vista_en) },
        ]}
        ventana={accion === "no" ? ventanaNo(p) : accion === "si" ? ventanaSi(p) : null}
      >
        <p className="ficha-registro__acciones acciones">
          <button type="button" className="secundario" onClick={() => abrirAccion(p, "no")}>
            {NO_SE_TRANSFIRIO}
          </button>
          <button type="button" className="secundario" onClick={() => abrirAccion(p, "si")}>
            {SI_SE_TRANSFIRIO}
          </button>
        </p>
      </FichaDeRegistro>
    );
  }

  return (
    <>
      <CabeceraInterna titulo="Pagos para aclarar" volver={RUTA_INICIO} destino="Inicio" />
      <AvisoPantalla aviso={aviso} />
      <ResultadoDelActo resultado={resultado} alOtroActo={() => setResultado(null)} />
      {lectura.tipo === "cargando" && <p role="status">Cargando…</p>}
      {lectura.tipo === "sin_servicio" && <FallaDeLectura alReintentar={reintentarLectura} avisar={setAviso} />}
      {lectura.tipo === "ok" &&
        (elementos.length === 0 ? (
          // Aclarado el último, el «Listo» de arriba ya lo dice: queda la vuelta al inicio (ACL-06).
          resultado !== null ? (
            <p>
              <Link to={RUTA_INICIO} className="enlace-suelto">
                Volver al inicio
              </Link>
            </p>
          ) : (
            <p className="tarjeta">{SIN_PAGOS}</p>
          )
        ) : (
          <ul className="filas-registro" aria-label="Pagos para aclarar">
            {elementos.map((p) => (
              <li key={p.orden_id}>
                <FilaDeRegistro titulo={tituloDe(p)} resumen={resumenDe(p)} alAbrir={() => fichas.abrir(p.orden_id)}
                  refFila={fichas.refFila(p.orden_id)} abierta={fichas.abierta === p.orden_id} />
              </li>
            ))}
          </ul>
        ))}
      {ficha()}
      {codigo.hoja}
    </>
  );
}
