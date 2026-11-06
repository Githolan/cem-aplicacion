import { type FormEvent, type MutableRefObject, useEffect, useId, useRef, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router";

import { api } from "../api/cliente";
import type { components } from "../api/tipos";
import { encabezadoCsrf } from "../csrf";
import { codigoDe, SIN_CONEXION } from "../fallas";
import { REQUISITOS, usePuede } from "../permisos";
import { CampoImporte } from "./CampoImporte";
import {
  aCentavos,
  aTexto,
  type Aviso,
  cambioLaOrden,
  cambioLoVisto,
  ENCABEZADO_IDEMPOTENCIA,
  ESTADO,
  importe,
  mensajeDeActo,
  mensajeDeError,
  momento,
  renovarClave,
  SEGUNDO_FACTOR_REQUERIDO,
  sinLaFalla,
  sinPisarLoHecho,
  soltarClaveSiCambio,
  sobreLoNuevo,
  TEXTO_ALERTA_FRACCIONAMIENTO,
  TEXTO_ESTADO_ORDEN,
  textoFranja,
  vencida,
} from "./comun";
import { ElegirConsorcio } from "./ElegirConsorcio";
import { AvisoPantalla, ConsorcioSinServicio, FallaDeLectura } from "./EstadoPantalla";
import { RUTA_PAGOS } from "./PagosManuales";
import { PedirCodigo } from "./PedirCodigo";
import { RUTA_PROVEEDORES } from "./Proveedores";
import { exigirSesion, SinSesion, useConsorcio, useMontado } from "./useConsorcio";

// Página /egresos/ordenes (pantalla 2, aprobada el 30-10-2026; app 0045 a 0050): la lista de
// quien prepara, con estado, importe, proveedor, fecha y franja; preparar y modificar con el
// proveedor elegido de la lista (nunca escrito a mano); enviar a revisión, renovar una aprobación
// vencida y emitir. Emitir deja la orden en ejecución: no mueve dinero.

type Orden = components["schemas"]["OrdenPago"];
type Proveedor = components["schemas"]["Proveedor"];
type Contrato = components["schemas"]["Contrato"];
type Version = components["schemas"]["VersionOrdenPago"];

export const RUTA_ORDENES = "/egresos/ordenes";
const MONEDAS = ["ARS", "USD"] as const;
/** Estados en que la orden se puede modificar (`modificar_orden_pago`, 0045). */
const MODIFICABLES = new Set<Orden["estado"]>(["borrador", "en_revision", "aprobada", "fallida"]);
/** Rechazos que se arreglan cambiando lo escrito: el formulario sigue abierto con sus datos. */
const SE_CORRIGE_EN_EL_FORMULARIO = new Set(["sin_cambios", "proveedor_inactivo"]);

type Lectura =
  | { tipo: "cargando" }
  | { tipo: "sin_servicio" }
  | { tipo: "ok"; ordenes: Orden[]; proveedores: Proveedor[]; contratos: Contrato[] };

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
/** `vistos`: los datos de la versión que se abrió, para saber qué cambió la persona. */
type Modificar = { modo: "modificar"; orden: Orden; vistos: Datos };
type Formulario = { modo: "preparar" } | Modificar;
type Acto =
  | { tipo: "guardar"; formulario: Formulario }
  | { tipo: "enviar"; orden: Orden }
  | { tipo: "renovar"; orden: Orden }
  | { tipo: "emitir"; orden: Orden; huella: string };
type Pendiente = { acto: string; reintentar: () => void };

const opcional = (v: string) => (v.trim() === "" ? null : v.trim());

/** Los contratos que puede declarar una orden: vigentes, del proveedor y de la moneda elegidos.
 * La base vuelve a verificar que lo cubra (alcance, importe máximo, fechas) al evaluarla. */
export function contratosAplicables(contratos: Contrato[], proveedor: string, moneda: string): Contrato[] {
  return contratos.filter((k) => k.estado === "vigente" && k.proveedor_id === proveedor && k.moneda === moneda);
}

/** Por qué el proveedor no deja emitir la orden (el código con que respondería la API), o null. Si
 * no está en la lista no se sabe: decide la API. `proveedor_cambiado` no se puede anticipar acá. */
export function trabaDelProveedor(o: Orden, proveedores: Proveedor[]): string | null {
  const proveedor = proveedores.find((p) => p.id === o.proveedor_id);
  if (proveedor === undefined) return null;
  if (proveedor.estado !== "activo") return "proveedor_inactivo";
  return proveedor.cuenta_ultimos_digitos === null ? "proveedor_sin_cuenta" : null;
}

const esperaUnaAccion = (o: Orden) => o.estado === "borrador" || vencida(o);

/** Lo que espera una acción primero; después, el resto en el orden de la API. */
export function ordenadas(ordenes: Orden[]): Orden[] {
  return [...ordenes.filter(esperaUnaAccion), ...ordenes.filter((o) => !esperaUnaAccion(o))];
}

const datosVacios = (proveedor: string): Datos => ({
  proveedor,
  importe: "",
  moneda: "ARS",
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

/** El proveedor recién dado de alta con el que se vuelve a preparar la orden (`state.proveedor`). */
function proveedorDe(estado: unknown): string | null {
  if (typeof estado !== "object" || estado === null || !("proveedor" in estado)) return null;
  return typeof estado.proveedor === "string" ? estado.proveedor : null;
}

export const AVISO_MODIFICAR =
  "Esta orden ya está en revisión o aprobada: modificarla crea una versión nueva, la vuelve a revisión y las aprobaciones anteriores dejan de contar.";

type PropsDelEdificio = ReturnType<typeof useConsorcio> & { paraPreparar: MutableRefObject<string | null> };

// Cada edificio monta la pantalla de nuevo: nada de un edificio (estado, respuestas tardías,
// el reintento pendiente después del código) llega a la pantalla de otro.
export function OrdenesPago() {
  const elegido = useConsorcio(REQUISITOS.ordenes);
  // Se lee una vez, al llegar: `AvisoLlegada` borra el `state` apenas lo ve.
  const paraPreparar = useRef(proveedorDe(useLocation().state));
  return <OrdenesPagoDelEdificio key={elegido.consorcio} {...elegido} paraPreparar={paraPreparar} />;
}

function OrdenesPagoDelEdificio({ pantalla, consorcio, setConsorcio, aEntrar, aInicio, reintentar, paraPreparar }: PropsDelEdificio) {
  const id = useId();
  const navegar = useNavigate();
  const puede = usePuede(consorcio);
  const [lectura, setLectura] = useState<Lectura>({ tipo: "cargando" });
  const [vuelta, setVuelta] = useState(0);
  const [formulario, setFormulario] = useState<Formulario | null>(null);
  const [datos, setDatos] = useState<Datos | null>(null);
  const [confirmar, setConfirmar] = useState(false);
  const [enviando, setEnviando] = useState(false);
  const [aviso, setAviso] = useState<Aviso | null>(null);
  const [pendiente, setPendiente] = useState<Pendiente | null>(null);
  const clave = useRef<string | null>(null);
  const ultimoActo = useRef<string | null>(null);
  const montado = useMontado();
  /** Órdenes cuyo contrato dejó de regir (lo dijo la API al emitir): se ofrece renovarlas. */
  const [sinContrato, setSinContrato] = useState<ReadonlySet<string>>(new Set());
  /** La orden recién guardada: la lista la deja a la vista, con su botón a mano. */
  const [recien, setRecien] = useState<string | null>(null);
  const tarjetaRecien = useRef<HTMLLIElement | null>(null);
  const enfocarRecien = useRef(false);

  useEffect(() => {
    if (!enfocarRecien.current || tarjetaRecien.current === null) return;
    enfocarRecien.current = false;
    tarjetaRecien.current.focus();
  }, [lectura, recien]);

  useEffect(() => {
    const proveedor = paraPreparar.current;
    if (proveedor === null || lectura.tipo !== "ok") return;
    paraPreparar.current = null;
    if (!lectura.proveedores.some((p) => p.id === proveedor && p.estado === "activo")) return;
    setDatos(datosVacios(proveedor));
    setFormulario({ modo: "preparar" });
  }, [lectura, paraPreparar]);

  useEffect(() => {
    if (consorcio === "") return;
    let vigente = true;
    const ruta = { params: { path: { consorcio_id: consorcio } } };
    void Promise.all([
      api.GET("/api/v1/consorcios/{consorcio_id}/ordenes-pago", ruta),
      api.GET("/api/v1/consorcios/{consorcio_id}/proveedores", ruta),
      api.GET("/api/v1/consorcios/{consorcio_id}/contratos", ruta),
    ])
      .then(([ordenes, proveedores, contratos]) => {
        exigirSesion(ordenes.response);
        exigirSesion(proveedores.response);
        exigirSesion(contratos.response);
        if (!vigente) return;
        if (ordenes.data !== undefined && proveedores.data !== undefined && contratos.data !== undefined) {
          setLectura({
            tipo: "ok",
            ordenes: ordenadas(ordenes.data.elementos),
            proveedores: proveedores.data.elementos,
            contratos: contratos.data.elementos,
          });
          return;
        }
        const falla = ordenes.data === undefined ? ordenes : proveedores.data === undefined ? proveedores : contratos;
        const codigo = codigoDe(falla.error);
        if (codigo === SEGUNDO_FACTOR_REQUERIDO)
          setPendiente({ acto: "ver las órdenes", reintentar: () => setVuelta((v) => v + 1) });
        else if (falla.response.status === ESTADO.sinPermiso && codigo === "sin_permiso") aInicio();
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
  }, [consorcio, vuelta, aEntrar, aInicio]);

  function volverAPedir() {
    setLectura({ tipo: "cargando" });
    setVuelta((v) => v + 1);
  }

  function reintentarLectura() {
    setAviso(sinLaFalla);
    volverAPedir();
  }

  function abrirPreparar(proveedores: Proveedor[]) {
    clave.current = null;
    setConfirmar(false);
    setAviso(null);
    const activo = proveedores.find((p) => p.estado === "activo");
    setDatos(datosVacios(activo?.id ?? ""));
    setFormulario({ modo: "preparar" });
  }

  /** Modificar parte de la versión vigente: el comprobante solo está en la ficha. */
  function abrirModificar(o: Orden) {
    setAviso(null);
    void api
      .GET("/api/v1/consorcios/{consorcio_id}/ordenes-pago/{orden_id}", {
        params: { path: { consorcio_id: consorcio, orden_id: o.id } },
      })
      .then(({ data, error, response }) => {
        exigirSesion(response);
        const v = data?.versiones.find((x) => x.numero === data.orden.version_vigente);
        if (data !== undefined && v !== undefined) {
          clave.current = null;
          setConfirmar(false);
          const vistos = datosDe(v, lectura.tipo === "ok" ? lectura.contratos : []);
          if (v.contrato_id !== null && vistos.contrato === "")
            setAviso({ texto: "El contrato de esta orden ya no rige: se quitó del formulario. Si guardás, pide aprobación.", ok: false });
          setDatos(vistos);
          setFormulario({ modo: "modificar", orden: data.orden, vistos });
        } else if (codigoDe(error) === SEGUNDO_FACTOR_REQUERIDO)
          setPendiente({ acto: "modificar la orden", reintentar: () => abrirModificar(o) });
        else setAviso({ texto: mensajeDeError(response.status, codigoDe(error)), ok: false });
      })
      .catch((e: unknown) => (e instanceof SinSesion ? aEntrar() : setAviso({ texto: SIN_CONEXION, ok: false })));
  }

  /** La orden cambió mientras se modificaba: el formulario sigue abierto con lo escrito, sobre la
   * versión nueva, y lo que se confirmó para la anterior se vuelve a confirmar. Si ya no se puede
   * modificar, se cierra. Otra falla lo deja como está: reintentar vuelve a pasar por acá. */
  function seguirSobreLaVersionNueva(f: Modificar) {
    void api
      .GET("/api/v1/consorcios/{consorcio_id}/ordenes-pago/{orden_id}", {
        params: { path: { consorcio_id: consorcio, orden_id: f.orden.id } },
      })
      .then(({ data, response }) => {
        exigirSesion(response);
        if (!montado.current) return;
        const v = data?.versiones.find((x) => x.numero === data.orden.version_vigente);
        if (data !== undefined && v !== undefined && MODIFICABLES.has(data.orden.estado)) {
          const vistos = datosDe(v, lectura.tipo === "ok" ? lectura.contratos : []);
          setDatos((d) => (d === null ? d : sobreLoNuevo(d, f.vistos, vistos)));
          setFormulario({ modo: "modificar", orden: data.orden, vistos });
          setConfirmar(false);
        } else if (data !== undefined || response.status === ESTADO.noEncontrado) {
          cerrar();
          volverAPedir();
        }
      })
      .catch((e: unknown) => (e instanceof SinSesion ? aEntrar() : setAviso({ texto: SIN_CONEXION, ok: false })));
  }

  /** Emitir manda la versión y la huella vistas: si la orden cambió, la API responde 409. */
  function emitir(o: Orden) {
    setAviso(null);
    void api
      .GET("/api/v1/consorcios/{consorcio_id}/ordenes-pago/{orden_id}", {
        params: { path: { consorcio_id: consorcio, orden_id: o.id } },
      })
      .then(({ data, error, response }) => {
        exigirSesion(response);
        const v = data?.versiones.find((x) => x.numero === o.version_vigente);
        if (data !== undefined && v !== undefined && data.orden.version_vigente === o.version_vigente)
          hacer({ tipo: "emitir", orden: o, huella: v.huella });
        else if (data !== undefined) {
          setAviso({ texto: mensajeDeError(ESTADO.conflicto, "version_desactualizada"), ok: false });
          volverAPedir();
        } else if (codigoDe(error) === SEGUNDO_FACTOR_REQUERIDO)
          setPendiente({ acto: "emitir la orden", reintentar: () => emitir(o) });
        else setAviso({ texto: mensajeDeError(response.status, codigoDe(error)), ok: false });
      })
      .catch((e: unknown) => (e instanceof SinSesion ? aEntrar() : setAviso({ texto: SIN_CONEXION, ok: false })));
  }

  function cerrar() {
    clave.current = null;
    setConfirmar(false);
    setFormulario(null);
    setDatos(null);
  }

  function cambiar<K extends keyof Datos>(campo: K, valor: Datos[K]) {
    clave.current = null;
    setConfirmar(false);
    setDatos((d) => {
      if (d === null) return d;
      const nuevo = { ...d, [campo]: valor };
      if (campo === "proveedor" || campo === "moneda") nuevo.contrato = "";
      return nuevo;
    });
  }

  function enviar(evento: FormEvent) {
    evento.preventDefault();
    if (formulario === null || datos === null) return;
    if (aCentavos(datos.importe) === null) {
      setAviso({ texto: "Escribí el importe en pesos, por ejemplo 12345,50.", ok: false });
      return;
    }
    const conAprobaciones = formulario.modo === "modificar" && ["en_revision", "aprobada"].includes(formulario.orden.estado);
    if (conAprobaciones && !confirmar) {
      setConfirmar(true);
      return;
    }
    hacer({ tipo: "guardar", formulario });
  }

  /** `reintento`: el mismo acto después de verificar el segundo factor, con la misma clave. */
  function hacer(acto: Acto, reintento = false) {
    if (!montado.current) return;
    const sobre = acto.tipo !== "guardar" ? acto.orden : acto.formulario.modo === "modificar" ? acto.formulario.orden : null;
    const quePide = [acto.tipo, sobre?.id, sobre?.version_vigente, acto.tipo === "guardar" ? datos : null, acto.tipo === "emitir" ? acto.huella : null];
    if (!reintento) soltarClaveSiCambio(clave, ultimoActo, JSON.stringify(quePide));
    clave.current ??= crypto.randomUUID();
    const encabezado = { [ENCABEZADO_IDEMPOTENCIA]: clave.current };
    const opciones = { headers: encabezadoCsrf() };
    setEnviando(true);
    setAviso(null);
    setRecien(null);
    let pedido;
    let texto: string;
    if (acto.tipo === "guardar") {
      const d = datos as Datos;
      const esencial = {
        importe_centavos: aCentavos(d.importe) as number,
        moneda: d.moneda,
        concepto: d.concepto.trim(),
        proveedor_id: d.proveedor,
        comprobante: opcional(d.comprobante),
        usa_fondo_reserva: d.reserva,
        contrato_id: d.contrato === "" ? null : d.contrato,
      };
      const f = acto.formulario;
      pedido =
        f.modo === "preparar"
          ? api.POST("/api/v1/consorcios/{consorcio_id}/ordenes-pago", {
              ...opciones,
              params: { path: { consorcio_id: consorcio }, header: encabezado },
              body: esencial,
            })
          : api.POST("/api/v1/consorcios/{consorcio_id}/ordenes-pago/{orden_id}/versiones", {
              ...opciones,
              params: { path: { consorcio_id: consorcio, orden_id: f.orden.id }, header: encabezado },
              body: { ...esencial, version_esperada: f.orden.version_vigente },
            });
      texto = f.modo === "preparar" ? "Listo: la orden quedó en borrador." : "Listo: guardamos la versión nueva.";
    } else {
      const ruta = { path: { consorcio_id: consorcio, orden_id: acto.orden.id }, header: encabezado };
      const version = acto.orden.version_vigente;
      if (acto.tipo === "enviar") {
        pedido = api.POST("/api/v1/consorcios/{consorcio_id}/ordenes-pago/{orden_id}/enviar-a-revision", {
          ...opciones, params: ruta, body: { version_esperada: version },
        });
        texto = "Listo: la orden se envió. Si su franja no pide aprobación, ya quedó aprobada.";
      } else if (acto.tipo === "renovar") {
        pedido = api.POST("/api/v1/consorcios/{consorcio_id}/ordenes-pago/{orden_id}/renovar", {
          ...opciones, params: ruta, body: { version_esperada: version },
        });
        texto = "Listo: la orden tiene una versión nueva y volvió a revisión.";
      } else {
        pedido = api.POST("/api/v1/consorcios/{consorcio_id}/ordenes-pago/{orden_id}/emitir", {
          ...opciones, params: ruta, body: { version, huella: acto.huella },
        });
        texto = "Listo: la orden quedó en ejecución. No se movió dinero.";
      }
    }
    void pedido
      .then(({ data, error, response }) => {
        exigirSesion(response);
        if (data !== undefined) {
          // Lo que sigue a emitir es pagar. La lista no abre la ficha sola: ver la cuenta es un
          // acto auditado que hace ejecutor a quien la ve, y lo decide esa persona con su botón.
          if (acto.tipo === "emitir") {
            navegar(RUTA_PAGOS, { state: { aviso: texto, consorcio } });
            return;
          }
          cerrar();
          setAviso({ texto, ok: true });
          if (acto.tipo === "guardar") {
            enfocarRecien.current = true;
            setRecien(data.id);
          }
          if (acto.tipo === "renovar")
            setSinContrato((s) => new Set([...s].filter((x) => x !== acto.orden.id)));
          volverAPedir();
          return;
        }
        const codigo = codigoDe(error);
        if (codigo === SEGUNDO_FACTOR_REQUERIDO) {
          // Se reintenta el mismo acto con la misma clave.
          const actual = clave.current;
          setPendiente({
            acto: "seguir con la orden",
            reintentar: () => {
              clave.current = actual;
              hacer(acto, true);
            },
          });
          return;
        }
        if (renovarClave(response.status, codigo)) clave.current = null;
        setAviso({ texto: mensajeDeActo(response.status, codigo), ok: false });
        if (codigo === "contrato_no_vigente" && acto.tipo === "emitir")
          setSinContrato((s) => new Set(s).add(acto.orden.id));
        if (acto.tipo === "renovar" && cambioLoVisto(response.status, codigo))
          setSinContrato((s) => new Set([...s].filter((x) => x !== acto.orden.id)));
        if (acto.tipo === "guardar" && acto.formulario.modo === "modificar" && cambioLaOrden(codigo)) {
          seguirSobreLaVersionNueva(acto.formulario);
          // La lista de atrás también quedó vieja; sin «Cargando», para no sacar el formulario.
          setVuelta((v) => v + 1);
          return;
        }
        const seCorrige = acto.tipo === "guardar" && SE_CORRIGE_EN_EL_FORMULARIO.has(codigo ?? "");
        if (cambioLoVisto(response.status, codigo) && codigo !== "sin_cambios" && !seCorrige) {
          cerrar();
          volverAPedir();
        }
      })
      .catch((e: unknown) => (e instanceof SinSesion ? aEntrar() : setAviso({ texto: SIN_CONEXION, ok: false })))
      .finally(() => setEnviando(false));
  }

  return (
    <>
      <h1>Órdenes de pago</h1>
      <ElegirConsorcio pantalla={pantalla} consorcio={consorcio} deshabilitado={enviando}
        elegir={setConsorcio} />
      <p>
        <Link to={RUTA_PROVEEDORES}>Proveedores</Link>
      </p>

      {pantalla.tipo === "cargando" && <p role="status">Cargando…</p>}
      {pantalla.tipo === "sin_consorcios" && <p className="tarjeta">No tenés edificios en Consorcio en Mano.</p>}
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
          alCancelar={() => setPendiente(null)}
        />
      )}

      {pantalla.tipo === "lista" && pendiente === null && (
        <>
          {lectura.tipo === "cargando" && <p role="status">Cargando las órdenes…</p>}
          {lectura.tipo === "sin_servicio" && <FallaDeLectura alReintentar={reintentarLectura} avisar={setAviso} />}
          {lectura.tipo === "ok" && formulario === null && (
            <>
              {lectura.proveedores.some((p) => p.estado === "activo") ? (
                <button type="button" disabled={enviando} onClick={() => abrirPreparar(lectura.proveedores)}>
                  Preparar una orden
                </button>
              ) : (
                <p className="tarjeta">
                  Para preparar una orden hace falta un proveedor activo:{" "}
                  <Link to={RUTA_PROVEEDORES} state={{ consorcio, paraPreparar: true }}>
                    cargalo en Proveedores
                  </Link>
                  .
                </p>
              )}
              {lectura.ordenes.length === 0 ? (
                <p className="tarjeta">El edificio no tiene órdenes de pago.</p>
              ) : (
                <ul className="versiones" aria-label="Órdenes de pago">
                  {lectura.ordenes.map((o) => {
                    const traba = trabaDelProveedor(o, lectura.proveedores);
                    const seEmite = o.estado === "aprobada" && !vencida(o) && !sinContrato.has(o.id) && !o.aprobada_por_vos && puede("egresos.ejecutar_pago");
                    return (
                    <li key={o.id} className="tarjeta" ref={o.id === recien ? tarjetaRecien : undefined}
                      tabIndex={o.id === recien ? -1 : undefined}>
                      <h2>
                        {o.razon_social}: {importe(o.importe_centavos, o.moneda)}
                      </h2>
                      <dl className="detalles">
                        <dt>Estado</dt>
                        <dd>{TEXTO_ESTADO_ORDEN[o.estado]}</dd>
                        <dt>Concepto</dt>
                        <dd>{o.concepto}</dd>
                        <dt>Preparada</dt>
                        <dd>{momento(o.creada_en)}</dd>
                        <dt>Franja</dt>
                        <dd>{textoFranja(o.franja, o.estado)}</dd>
                        {o.alerta_fraccionamiento && (
                          <>
                            <dt>Alerta</dt>
                            <dd>{TEXTO_ALERTA_FRACCIONAMIENTO}</dd>
                          </>
                        )}
                        {o.aprobacion_vence_en !== null && (
                          <>
                            <dt>Se emite hasta</dt>
                            <dd>{vencida(o) ? "La aprobación venció: renovala" : momento(o.aprobacion_vence_en)}</dd>
                          </>
                        )}
                        {seEmite && traba !== null && (
                          <>
                            <dt>Para emitir</dt>
                            <dd>{mensajeDeError(ESTADO.conflicto, traba)}</dd>
                          </>
                        )}
                        {o.motivo_rechazo !== null && (
                          <>
                            <dt>Motivo del rechazo</dt>
                            <dd>{o.motivo_rechazo}</dd>
                          </>
                        )}
                      </dl>
                      <div className="formulario">
                        <div className="acciones">
                          {o.estado === "borrador" && (
                            <button type="button" disabled={enviando} onClick={() => hacer({ tipo: "enviar", orden: o })}>
                              Enviar a revisión
                            </button>
                          )}
                          {seEmite && traba === null && (
                            <button type="button" disabled={enviando} onClick={() => emitir(o)}>
                              Emitir
                            </button>
                          )}
                          {(vencida(o) || (o.estado === "aprobada" && sinContrato.has(o.id))) && (
                            <button type="button" disabled={enviando} onClick={() => hacer({ tipo: "renovar", orden: o })}>
                              Renovar
                            </button>
                          )}
                          {MODIFICABLES.has(o.estado) && (
                            <button type="button" className="secundario" disabled={enviando} onClick={() => abrirModificar(o)}>
                              Modificar
                            </button>
                          )}
                        </div>
                      </div>
                    </li>
                    );
                  })}
                </ul>
              )}
            </>
          )}

          {lectura.tipo === "ok" && formulario !== null && datos !== null && (
            <form className="formulario tarjeta" onSubmit={enviar} aria-labelledby={`${id}-form`}>
              <h2 id={`${id}-form`}>{formulario.modo === "preparar" ? "Preparar una orden" : "Modificar la orden"}</h2>
              <label htmlFor={`${id}-proveedor`}>Proveedor</label>
              <select id={`${id}-proveedor`} required value={datos.proveedor} disabled={enviando}
                onChange={(e) => cambiar("proveedor", e.target.value)}>
                <option value="" disabled>
                  Elegí un proveedor
                </option>
                {lectura.proveedores
                  .filter((p) => p.estado === "activo" || p.id === datos.proveedor)
                  .map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.razon_social}
                    </option>
                  ))}
              </select>
              <label htmlFor={`${id}-importe`}>Importe</label>
              <CampoImporte id={`${id}-importe`} valor={datos.importe} deshabilitado={enviando}
                alCambiar={(texto) => cambiar("importe", texto)} />
              <label htmlFor={`${id}-moneda`}>Moneda</label>
              <select id={`${id}-moneda`} value={datos.moneda} disabled={enviando} onChange={(e) => cambiar("moneda", e.target.value)}>
                {MONEDAS.map((m) => (
                  <option key={m} value={m}>
                    {m}
                  </option>
                ))}
              </select>
              <label htmlFor={`${id}-contrato`}>Contrato o abono (opcional)</label>
              <select id={`${id}-contrato`} value={datos.contrato} disabled={enviando}
                onChange={(e) => cambiar("contrato", e.target.value)}>
                <option value="">Ninguno: no es un gasto rutinario</option>
                {contratosAplicables(lectura.contratos, datos.proveedor, datos.moneda).map((k) => (
                  <option key={k.id} value={k.id}>
                    {k.alcance} (hasta {importe(k.importe_maximo_centavos, k.moneda)})
                  </option>
                ))}
              </select>
              <label htmlFor={`${id}-concepto`}>Concepto</label>
              <input id={`${id}-concepto`} required maxLength={300} value={datos.concepto} disabled={enviando}
                onChange={(e) => cambiar("concepto", e.target.value)} />
              <label htmlFor={`${id}-comprobante`}>Comprobante (opcional)</label>
              <input id={`${id}-comprobante`} maxLength={200} value={datos.comprobante} disabled={enviando}
                onChange={(e) => cambiar("comprobante", e.target.value)} />
              <label>
                <input type="checkbox" checked={datos.reserva} disabled={enviando}
                  onChange={(e) => cambiar("reserva", e.target.checked)} />{" "}
                Usa el fondo de reserva (siempre lo aprueba el Consejo)
              </label>
              {confirmar && (
                <p className="aviso-error" role="alert">
                  {AVISO_MODIFICAR}
                </p>
              )}
              <div className="acciones">
                <button type="submit" disabled={enviando || datos.proveedor === ""}>
                  {confirmar ? "Sí, crear la versión nueva" : "Guardar"}
                </button>
                <button type="button" className="secundario" disabled={enviando} onClick={cerrar}>
                  Cancelar
                </button>
              </div>
            </form>
          )}
        </>
      )}
    </>
  );
}
