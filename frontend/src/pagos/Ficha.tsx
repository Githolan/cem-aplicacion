import { type FormEvent, type MouseEvent, useEffect, useId, useState } from "react";
import { Link } from "react-router";

import { api } from "../api/cliente";
import type { components } from "../api/tipos";
import { encabezadoCsrf } from "../csrf";
import {
  cambioLaOrden,
  cambioLoVisto,
  cuentaEnmascarada,
  ENCABEZADO_IDEMPOTENCIA,
  ESTADO,
  importe,
  mensajeDeError,
  momento,
  SEGUNDO_FACTOR_REQUERIDO,
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
import { FormularioOrden } from "./FormularioOrden";
import { RegistroPago } from "./RegistroPago";
import { RUTA_NUEVA, RUTA_PAGOS } from "./rutas";
import {
  AVISO_NO_TRANSFIERAS,
  AVISO_RETOMAR,
  AVISO_SIN_CUENTA,
  AVISO_SIN_SALIDA,
  AVISO_VER_LA_CUENTA,
  AVISO_VERIFICAR_CUENTA,
  type Paso,
  TEXTO_LE_TOCA_A,
  TEXTO_SITUACION,
} from "./textos";
import { useActo } from "./useActo";

// La ficha de una orden, una sola para todos los estados (§3). No deduce nada: lo que dice sale de
// `situacion`, y lo que ofrece, de `paso` y `puede_*`, que el servidor saca de las reglas de la
// base. Revisar no es una pantalla más: la ficha muestra los datos y el botón principal confirma.
//
// La cuenta completa no viaja con la ficha. Llega solo con el acto de ejecución, vive en el estado
// de esta ficha y se va al cerrarla o cuando la orden cambia de versión.

type FichaPago = components["schemas"]["FichaPago"];
type Cuenta = components["schemas"]["CuentaParaPagoManual"];
type Version = components["schemas"]["VersionOrdenPago"];
type Pago = components["schemas"]["PagoManual"];

type Lectura = { tipo: "cargando" } | { tipo: "sin_servicio" } | { tipo: "no_esta" } | { tipo: "ok"; ficha: FichaPago };
type ConMotivo = "rechazar" | "declarar" | "conforme" | "observar";

/** Los pasos en que quien puede declarar tiene a mano «No se pudo pagar». */
const SE_DECLARA = new Set<Paso | null>(["subir_comprobante", "corregir", "declarar_no_pago", "espera_comprobante"]);

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

function descarga(consorcio: string, pago: Pago): string {
  return `/api/v1/consorcios/${consorcio}/documentos/${pago.documento_id}/versiones/${pago.version_documento_id}/descargar`;
}

type Props = {
  consorcio: string;
  orden: string;
  /** `h1` si la ficha es toda la pantalla; `h2` si está al lado de la bandeja. */
  titulo: "h1" | "h2";
  aEntrar: () => void;
  aInicio: () => void;
  /** El acto terminó: pasa a la pantalla siguiente con el aviso; `seguirCon`, al próximo de ese paso. */
  terminar: (texto: string, seguirCon?: Paso) => void;
  /** Algo cambió sin dejar la ficha: la bandeja de al lado se vuelve a leer. */
  alCambiar: () => void;
};

export function Ficha({ consorcio, orden, titulo: Titulo, aEntrar, aInicio, terminar, alCambiar }: Props) {
  const id = useId();
  const acto = useActo(aEntrar);
  const { enviando, aviso, setAviso, pendiente, setPendiente } = acto;
  const [lectura, setLectura] = useState<Lectura>({ tipo: "cargando" });
  const [vuelta, setVuelta] = useState(0);
  const [cuenta, setCuenta] = useState<Cuenta | null>(null);
  const [modificando, setModificando] = useState(false);
  const [conMotivo, setConMotivo] = useState<ConMotivo | null>(null);
  const [motivo, setMotivo] = useState("");

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
        if (data !== undefined) setLectura({ tipo: "ok", ficha: data });
        else if (response.status === ESTADO.sinPermiso && codigo === "sin_permiso") aInicio();
        // Una dirección que no es de una orden responde 422: tampoco está en este edificio.
        else if (response.status === ESTADO.noEncontrado || response.status === ESTADO.invalido) setLectura({ tipo: "no_esta" });
        else {
          // Con la ficha a la vista, lo que se escribió queda: solo se dice qué pasó.
          setLectura((l) => (l.tipo === "ok" ? l : { tipo: "sin_servicio" }));
          setAviso(sinPisarLoHecho(mensajeDeError(response.status, codigo)));
        }
      })
      .catch((e: unknown) => {
        if (!vigente) return;
        if (e instanceof SinSesion) aEntrar();
        else {
          setLectura((l) => (l.tipo === "ok" ? l : { tipo: "sin_servicio" }));
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
    return (
      <>
        <p className="tarjeta">Esa orden no está en este edificio.</p>
        <p>
          <Link to={RUTA_PAGOS}>Volver a Pagos</Link>
        </p>
      </>
    );
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
  const { situacion, paso, ejecucion, declaracion_vigente: declaracion, observacion_vigente: observacion } = ficha;
  const v = vigenteDe(ficha);
  const ruta = { consorcio_id: consorcio, orden_id: orden };
  const pago = ejecucion?.pagos.at(-1);
  // La cuenta era de la versión que se abrió: de otra versión no se muestra.
  const cuentaVista = cuenta?.version === v.numero ? cuenta : null;
  const anterior = ficha.versiones.find((x) => x.numero === v.numero - 1);
  const enDecision = situacion === "borrador" || situacion === "en_revision";
  const registra = paso === "corregir" ? ficha.puede_corregir : paso === "subir_comprobante" && ficha.puede_subir_comprobante;
  const declara = ficha.puede_declarar && SE_DECLARA.has(paso);

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
      alTerminar: () => terminar("Listo: la orden se envió. Si su franja no pide aprobación, ya quedó aprobada."),
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
          "conciliar",
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

  /** Antes de dejar la pantalla pregunta si el comprobante se puede bajar: la descarga es un 302 a
   * una URL firmada de otro origen, que un `fetch` no puede seguir (CSP `connect-src 'self'`). Con
   * `redirect: "manual"` el 302 no se sigue, y un error llega con su cuerpo para decirlo acá en
   * lugar de mostrar el JSON. Si está, recién entonces navega; el archivo viene como adjunto. */
  function verComprobante(evento: MouseEvent<HTMLAnchorElement>, p: Pago) {
    evento.preventDefault();
    setAviso(null);
    void api
      .GET("/api/v1/consorcios/{consorcio_id}/documentos/{documento_id}/versiones/{version_id}/descargar", {
        params: { path: { consorcio_id: consorcio, documento_id: p.documento_id, version_id: p.version_documento_id } },
        redirect: "manual",
      })
      .then(({ error, response }) => {
        exigirSesion(response);
        if (response.type === "opaqueredirect") {
          const enlace = document.createElement("a");
          enlace.href = descarga(consorcio, p);
          enlace.click();
          return;
        }
        const codigo = codigoDe(error);
        if (codigo === SEGUNDO_FACTOR_REQUERIDO)
          setPendiente({ acto: "ver el comprobante", reintentar: () => verComprobante(evento, p) });
        else setAviso({ texto: mensajeDeError(response.status, codigo), ok: false });
      })
      .catch((e: unknown) => (e instanceof SinSesion ? aEntrar() : setAviso({ texto: SIN_CONEXION, ok: false })));
  }

  function enviarMotivo(evento: FormEvent) {
    evento.preventDefault();
    const texto = motivo.trim();
    if (texto === "") return;
    if (conMotivo === "rechazar") rechazar(texto);
    else if (conMotivo === "declarar") declarar(texto);
    else if (pago !== undefined) conciliar(pago.id, conMotivo === "observar" ? "observado" : "conforme", texto);
  }

  // Lo que se escribe para decidir, sobre la misma ficha. Sin la acción, el formulario se va.
  const motivoAbierto =
    (conMotivo === "rechazar" && ficha.puede_rechazar) ||
    (conMotivo === "declarar" && declara) ||
    ((conMotivo === "conforme" || conMotivo === "observar") && ficha.puede_conciliar)
      ? conMotivo
      : null;
  const TEXTOS_MOTIVO: Record<ConMotivo, { etiqueta: string; boton: string; largo: number }> = {
    rechazar: { etiqueta: "Motivo del rechazo", boton: "Rechazar la orden", largo: 500 },
    declarar: { etiqueta: "Por qué no se pagó", boton: "Declarar que no se pagó", largo: 200 },
    conforme: { etiqueta: "Por qué lo aceptás fuera de término", boton: "Dar el pago por conforme", largo: 300 },
    observar: { etiqueta: "Qué no coincide", boton: "Observar el pago", largo: 300 },
  };

  const estaModificando = modificando && ficha.puede_modificar;
  const Subtitulo = Titulo === "h1" ? "h2" : "h3";

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
          <p className="etiqueta-situacion">{TEXTO_SITUACION[situacion]}</p>
          <Titulo id={`${id}-titulo`}>{v.razon_social}</Titulo>
          <p className="importe-ficha">{importe(v.importe_centavos, v.moneda)}</p>
          <p>{v.concepto}</p>

          {enDecision && v.alerta_fraccionamiento && (
            <p className="aviso-error" role="note">
              {TEXTO_ALERTA_FRACCIONAMIENTO}
            </p>
          )}
          {enDecision && anterior !== undefined && cambiosDeVersion(v, anterior).map((c) => <p key={c}>{c}</p>)}
          {ficha.orden.motivo_rechazo !== null && situacion === "rechazada" && (
            <p>
              <strong>Motivo del rechazo:</strong> {ficha.orden.motivo_rechazo}
            </p>
          )}
          {situacion === "no_se_pago" && <p>El Consejo confirmó que no se pagó. Para volver a pagarla, pasa otra vez por aprobación.</p>}
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
          {paso === "espera_comprobante" && <p role="note">Ya se vio la cuenta: no pagues esta orden mientras se espera el comprobante.</p>}
          {ficha.le_toca_a !== null && ficha.le_toca_a !== "vos" && <p>{TEXTO_LE_TOCA_A[ficha.le_toca_a]}</p>}

          {cuentaVista !== null && (
            <div className="tarjeta">
              <Subtitulo>Transferí desde tu banco</Subtitulo>
              <p className="aviso-error" role="note">
                {AVISO_VERIFICAR_CUENTA}
              </p>
              <dl className="detalles">
                <dt>CBU/CVU</dt>
                <dd>
                  <code>{cuentaVista.cuenta_destino}</code>
                </dd>
                <dt>Pagar antes de</dt>
                <dd>{momento(cuentaVista.aprobacion_vence_en)}</dd>
              </dl>
            </div>
          )}

          {!estaModificando && (
            <div className="tarjeta">
              <dl className="detalles">
                {enDecision && (
                  <>
                    <dt>Cuenta destino</dt>
                    <dd>{cuentaEnmascarada(v.cuenta_ultimos_digitos)}</dd>
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
                {pago !== undefined && (
                  <>
                    <dt>Número de operación</dt>
                    <dd>{pago.numero_operacion}</dd>
                    <dt>Fecha de la transferencia</dt>
                    <dd>{pago.fecha_operacion}</dd>
                    <dt>Importe registrado</dt>
                    <dd>{importe(pago.importe_centavos, pago.moneda)}</dd>
                    <dt>Registrado</dt>
                    <dd>{momento(pago.registrado_en)}</dd>
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
                {paso === "conciliar" && pago !== undefined && (
                  <>
                    <dt>Comprobante de la transferencia</dt>
                    <dd>
                      <a href={descarga(consorcio, pago)} onClick={(e) => verComprobante(e, pago)}>
                        Ver el comprobante
                      </a>
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
              {ficha.puede_reabrir_cuenta && situacion === "no_pago_declarado" && <p role="note">{AVISO_RETOMAR}</p>}

              {/* Queda montado: ir a declarar y volver no pierde lo escrito. */}
              <div hidden={motivoAbierto !== null}>
                  {registra && (
                    <RegistroPago consorcio={consorcio} orden={orden} version={v} corrige={paso === "corregir"} subtitulo={Subtitulo} acto={acto} aEntrar={aEntrar}
                      alRegistrar={(texto) => terminar(texto)}
                      alCambiarLaOrden={() => {
                        releer();
                        alCambiar();
                      }} />
                  )}
                  <div className="formulario">
                    <div className="acciones">
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
                        <button type="button" disabled={enviando} onClick={() => setModificando(true)}>
                          Volver a pagar
                        </button>
                      )}
                      {ficha.puede_aprobar && (
                        <button type="button" disabled={enviando} onClick={aprobar}>
                          Aprobar esta versión
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
                      {ficha.puede_conciliar && pago !== undefined && (
                        <>
                          <button type="button" disabled={enviando}
                            onClick={() => (pago.fuera_de_termino ? escribir("conforme") : conciliar(pago.id, "conforme", null))}>
                            Conforme
                          </button>
                          <button type="button" className="secundario" disabled={enviando} onClick={() => escribir("observar")}>
                            Observar
                          </button>
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
                      {ficha.puede_modificar && paso !== "volver_a_pagar" && (
                        <button type="button" className="secundario" disabled={enviando} onClick={() => setModificando(true)}>
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
              {paso === "rechazada" && (
                <p>
                  <Link to={RUTA_NUEVA} state={{ consorcio, proveedor: v.proveedor_id }}>
                    Preparar otra orden
                  </Link>
                </p>
              )}
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
                <Subtitulo>Versiones anteriores</Subtitulo>
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
            {ejecucion !== null && ejecucion.pagos.length > 1 && (
              <>
                <Subtitulo>Registros anteriores</Subtitulo>
                <ol className="versiones">
                  {ejecucion.pagos.slice(0, -1).map((p) => (
                    <li key={p.id}>
                      Operación {p.numero_operacion}, {p.fecha_operacion}: {importe(p.importe_centavos, p.moneda)}
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
