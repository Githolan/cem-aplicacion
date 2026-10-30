import { type FormEvent, useEffect, useId, useRef, useState } from "react";
import { Link } from "react-router";

import { api } from "../api/cliente";
import type { components } from "../api/tipos";
import { encabezadoCsrf } from "../csrf";
import { codigoDe, SERVICIO_NO_DISPONIBLE, SIN_CONEXION } from "../fallas";
import {
  cambioLoVisto,
  cuentaEnmascarada,
  ENCABEZADO_IDEMPOTENCIA,
  ESTADO,
  type EstadoProveedor,
  mensajeDeError,
  momento,
  renovarClave,
  SEGUNDO_FACTOR_REQUERIDO,
  TEXTO_ESTADO_PROVEEDOR,
} from "./comun";
import { ElegirConsorcio } from "./ElegirConsorcio";
import { PedirCodigo } from "./PedirCodigo";
import { exigirSesion, SinSesion, useConsorcio } from "./useConsorcio";

// Página /egresos/proveedores (pantalla 1, aprobada el 30-10-2026; app 0048 y 0049): la lista con
// la cuenta enmascarada, la ficha con la historia de versiones, el alta y el cambio. Cambiar la
// cuenta pide confirmación explícita. Lo decide la API con `egresos.preparar_orden`.

type Proveedor = components["schemas"]["Proveedor"];
type Version = components["schemas"]["VersionProveedor"];

export const RUTA_PROVEEDORES = "/egresos/proveedores";
const CBU = "[0-9]{22}";
const CUIT = "[0-9]{11}";

type Lectura =
  | { tipo: "cargando" }
  | { tipo: "sin_permiso" }
  | { tipo: "sin_servicio" }
  | { tipo: "ok"; proveedores: Proveedor[] };

type Datos = { cuit: string; razon: string; rubro: string; contacto: string; cuenta: string; estado: EstadoProveedor };
const VACIO: Datos = { cuit: "", razon: "", rubro: "", contacto: "", cuenta: "", estado: "activo" };

type Formulario = { modo: "alta" } | { modo: "cambio"; proveedor: Proveedor };
type Pendiente = { acto: string; reintentar: () => void };
type Aviso = { texto: string; ok: boolean };

const opcional = (v: string) => (v.trim() === "" ? null : v.trim());

/** La cuenta que se manda: en un cambio, el campo vacío conserva la que tiene el proveedor. */
function cuentaPedida(f: Formulario, d: Datos): string | null {
  return f.modo === "cambio" && d.cuenta.trim() === "" ? f.proveedor.cuenta_destino : opcional(d.cuenta);
}

/** El aviso de cambio de cuenta (decisión del 30-10, punto 21). */
export const AVISO_CUENTA =
  "Cambiar la cuenta crea una versión nueva del proveedor. Las órdenes ya preparadas siguen apuntando a la versión anterior; las nuevas usan la cuenta nueva. Un cambio material obliga a una revisión nueva cuando corresponde.";

export function Proveedores() {
  const id = useId();
  const { pantalla, consorcio, setConsorcio, aEntrar } = useConsorcio();
  const [lectura, setLectura] = useState<Lectura>({ tipo: "cargando" });
  const [vuelta, setVuelta] = useState(0);
  const [ficha, setFicha] = useState<{ proveedor: Proveedor; versiones: Version[] } | null>(null);
  const [formulario, setFormulario] = useState<Formulario | null>(null);
  const [datos, setDatos] = useState<Datos>(VACIO);
  const [confirmarCuenta, setConfirmarCuenta] = useState(false);
  const [enviando, setEnviando] = useState(false);
  const [aviso, setAviso] = useState<Aviso | null>(null);
  const [pendiente, setPendiente] = useState<Pendiente | null>(null);
  const clave = useRef<string | null>(null);

  useEffect(() => {
    if (consorcio === "") return;
    let vigente = true;
    void api
      .GET("/api/v1/consorcios/{consorcio_id}/proveedores", { params: { path: { consorcio_id: consorcio } } })
      .then(({ data, error, response }) => {
        exigirSesion(response);
        if (!vigente) return;
        const codigo = codigoDe(error);
        if (data !== undefined) setLectura({ tipo: "ok", proveedores: data.elementos });
        else if (codigo === SEGUNDO_FACTOR_REQUERIDO)
          setPendiente({ acto: "ver los proveedores", reintentar: () => setVuelta((v) => v + 1) });
        else if (response.status === ESTADO.sinPermiso && codigo === "sin_permiso") setLectura({ tipo: "sin_permiso" });
        else {
          setLectura({ tipo: "sin_servicio" });
          setAviso({ texto: mensajeDeError(response.status, codigo), ok: false });
        }
      })
      .catch((e: unknown) => {
        if (!vigente) return;
        if (e instanceof SinSesion) aEntrar();
        else {
          setLectura({ tipo: "sin_servicio" });
          setAviso({ texto: SIN_CONEXION, ok: false });
        }
      });
    return () => {
      vigente = false;
    };
  }, [consorcio, vuelta, aEntrar]);

  function volverAPedir() {
    setLectura({ tipo: "cargando" });
    setFicha(null);
    setVuelta((v) => v + 1);
  }

  function verFicha(p: Proveedor) {
    setAviso(null);
    void api
      .GET("/api/v1/consorcios/{consorcio_id}/proveedores/{proveedor_id}", {
        params: { path: { consorcio_id: consorcio, proveedor_id: p.id } },
      })
      .then(({ data, error, response }) => {
        exigirSesion(response);
        if (data !== undefined) setFicha(data);
        else if (codigoDe(error) === SEGUNDO_FACTOR_REQUERIDO)
          setPendiente({ acto: "ver el proveedor", reintentar: () => verFicha(p) });
        else setAviso({ texto: mensajeDeError(response.status, codigoDe(error)), ok: false });
      })
      .catch((e: unknown) => (e instanceof SinSesion ? aEntrar() : setAviso({ texto: SIN_CONEXION, ok: false })));
  }

  function abrir(f: Formulario) {
    clave.current = null;
    setConfirmarCuenta(false);
    setAviso(null);
    setDatos(
      f.modo === "alta"
        ? VACIO
        : {
            cuit: f.proveedor.cuit ?? "",
            razon: f.proveedor.razon_social,
            rubro: f.proveedor.rubro ?? "",
            contacto: f.proveedor.contacto ?? "",
            // La cuenta completa solo se muestra en la revisión del Consejo: acá el campo empieza
            // vacío y vacío quiere decir «conservar la que tiene».
            cuenta: "",
            estado: f.proveedor.estado,
          },
    );
    setFormulario(f);
  }

  function cerrar() {
    clave.current = null;
    setConfirmarCuenta(false);
    setFormulario(null);
  }

  function cambiar<K extends keyof Datos>(campo: K, valor: Datos[K]) {
    clave.current = null;
    setConfirmarCuenta(false);
    setDatos((d) => ({ ...d, [campo]: valor }));
  }

  function enviar(evento: FormEvent) {
    evento.preventDefault();
    if (formulario === null) return;
    const cambiaCuenta = formulario.modo === "cambio" && cuentaPedida(formulario, datos) !== formulario.proveedor.cuenta_destino;
    if (cambiaCuenta && !confirmarCuenta) {
      setConfirmarCuenta(true);
      return;
    }
    pedir(formulario);
  }

  function pedir(f: Formulario) {
    clave.current ??= crypto.randomUUID();
    const encabezado = { [ENCABEZADO_IDEMPOTENCIA]: clave.current };
    const comunes = {
      razon_social: datos.razon.trim(),
      rubro: opcional(datos.rubro),
      contacto: opcional(datos.contacto),
      cuenta_destino: cuentaPedida(f, datos),
    };
    setEnviando(true);
    setAviso(null);
    const pedido =
      f.modo === "alta"
        ? api.POST("/api/v1/consorcios/{consorcio_id}/proveedores", {
            headers: encabezadoCsrf(),
            params: { path: { consorcio_id: consorcio }, header: encabezado },
            body: { ...comunes, cuit: opcional(datos.cuit) },
          })
        : api.POST("/api/v1/consorcios/{consorcio_id}/proveedores/{proveedor_id}/versiones", {
            headers: encabezadoCsrf(),
            params: { path: { consorcio_id: consorcio, proveedor_id: f.proveedor.id }, header: encabezado },
            body: {
              ...comunes,
              documento_id: f.proveedor.documento_id,
              estado: datos.estado,
              version_esperada: f.proveedor.version,
            },
          });
    void pedido
      .then(({ data, error, response }) => {
        exigirSesion(response);
        if (data !== undefined) {
          cerrar();
          setAviso({ texto: f.modo === "alta" ? "Listo: dimos de alta el proveedor." : `Listo: el proveedor quedó en la versión ${data.version}.`, ok: true });
          volverAPedir();
          return;
        }
        const codigo = codigoDe(error);
        if (codigo === SEGUNDO_FACTOR_REQUERIDO) {
          setPendiente({ acto: "guardar el proveedor", reintentar: () => pedir(f) });
          return;
        }
        if (renovarClave(response.status, codigo)) clave.current = null;
        setAviso({ texto: mensajeDeError(response.status, codigo), ok: false });
        if (cambioLoVisto(response.status, codigo) && codigo !== "sin_cambios" && codigo !== "cuit_repetido") {
          cerrar();
          volverAPedir();
        }
      })
      .catch((e: unknown) => (e instanceof SinSesion ? aEntrar() : setAviso({ texto: SIN_CONEXION, ok: false })))
      .finally(() => setEnviando(false));
  }

  return (
    <>
      <h1>Proveedores</h1>
      <ElegirConsorcio pantalla={pantalla} consorcio={consorcio} deshabilitado={enviando}
        elegir={(c) => {
          cerrar();
          setFicha(null);
          setAviso(null);
          setLectura({ tipo: "cargando" });
          setConsorcio(c);
        }} />
      <p>
        <Link to={RUTA_ORDENES_ENLACE}>Órdenes de pago</Link>
      </p>

      {pantalla.tipo === "cargando" && <p role="status">Cargando…</p>}
      {pantalla.tipo === "sin_consorcios" && <p className="tarjeta">No tenés edificios en Consorcio en Mano.</p>}
      {pantalla.tipo === "sin_servicio" && (
        <p className="aviso-error" role="alert">
          {pantalla.deConexion ? SIN_CONEXION : SERVICIO_NO_DISPONIBLE}
        </p>
      )}

      {aviso !== null && (
        <p className={aviso.ok ? "aviso-ok" : "aviso-error"} role={aviso.ok ? "status" : "alert"}>
          {aviso.texto}
        </p>
      )}

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

      {pantalla.tipo === "lista" && pendiente === null && (
        <>
          {lectura.tipo === "cargando" && <p role="status">Cargando los proveedores…</p>}
          {lectura.tipo === "sin_permiso" && (
            <p className="tarjeta">No tenés la facultad de preparar órdenes de pago en este edificio.</p>
          )}
          {lectura.tipo === "ok" && formulario === null && ficha === null && (
            <>
              <button type="button" onClick={() => abrir({ modo: "alta" })}>
                Dar de alta un proveedor
              </button>
              {lectura.proveedores.length === 0 ? (
                <p className="tarjeta">El edificio no tiene proveedores cargados.</p>
              ) : (
                <ul className="versiones" aria-label="Proveedores">
                  {lectura.proveedores.map((p) => (
                    <li key={p.id} className="tarjeta">
                      <h2>{p.razon_social}</h2>
                      <dl className="detalles">
                        <dt>CUIT</dt>
                        <dd>{p.cuit ?? "Sin CUIT"}</dd>
                        <dt>Estado</dt>
                        <dd>{TEXTO_ESTADO_PROVEEDOR[p.estado]}</dd>
                        <dt>Cuenta</dt>
                        <dd>{cuentaEnmascarada(p.cuenta_destino)}</dd>
                      </dl>
                      <button type="button" onClick={() => verFicha(p)}>
                        Ver la ficha de {p.razon_social}
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </>
          )}

          {ficha !== null && formulario === null && (
            <section className="tarjeta" aria-labelledby={`${id}-ficha`}>
              <h2 id={`${id}-ficha`}>{ficha.proveedor.razon_social}</h2>
              <dl className="detalles">
                <dt>CUIT</dt>
                <dd>{ficha.proveedor.cuit ?? "Sin CUIT"}</dd>
                <dt>Rubro</dt>
                <dd>{ficha.proveedor.rubro ?? "Sin rubro"}</dd>
                <dt>Estado</dt>
                <dd>{TEXTO_ESTADO_PROVEEDOR[ficha.proveedor.estado]}</dd>
                <dt>Cuenta</dt>
                <dd>{cuentaEnmascarada(ficha.proveedor.cuenta_destino)}</dd>
              </dl>
              <h3>Historia</h3>
              <ol className="versiones" aria-label="Versiones del proveedor">
                {ficha.versiones.map((v) => (
                  <li key={v.version}>
                    Versión {v.version}, {momento(v.creada_en)}: {v.razon_social}, {TEXTO_ESTADO_PROVEEDOR[v.estado]},
                    cuenta {cuentaEnmascarada(v.cuenta_destino)}
                  </li>
                ))}
              </ol>
              <div className="formulario">
                <div className="acciones">
                  <button type="button" onClick={() => abrir({ modo: "cambio", proveedor: ficha.proveedor })}>
                    Cambiar los datos
                  </button>
                  <button type="button" className="secundario" onClick={() => setFicha(null)}>
                    Volver a la lista
                  </button>
                </div>
              </div>
            </section>
          )}

          {formulario !== null && (
            <form className="formulario tarjeta" onSubmit={enviar} aria-labelledby={`${id}-form`}>
              <h2 id={`${id}-form`}>
                {formulario.modo === "alta" ? "Alta de un proveedor" : `Cambiar ${formulario.proveedor.razon_social}`}
              </h2>
              {formulario.modo === "alta" && (
                <>
                  <label htmlFor={`${id}-cuit`}>CUIT (11 números, sin guiones; opcional)</label>
                  <input id={`${id}-cuit`} inputMode="numeric" pattern={CUIT} value={datos.cuit} disabled={enviando}
                    onChange={(e) => cambiar("cuit", e.target.value)} />
                </>
              )}
              <label htmlFor={`${id}-razon`}>Razón social</label>
              <input id={`${id}-razon`} required maxLength={200} value={datos.razon} disabled={enviando}
                onChange={(e) => cambiar("razon", e.target.value)} />
              <label htmlFor={`${id}-rubro`}>Rubro (opcional)</label>
              <input id={`${id}-rubro`} maxLength={100} value={datos.rubro} disabled={enviando}
                onChange={(e) => cambiar("rubro", e.target.value)} />
              <label htmlFor={`${id}-contacto`}>Contacto (opcional)</label>
              <input id={`${id}-contacto`} maxLength={200} value={datos.contacto} disabled={enviando}
                onChange={(e) => cambiar("contacto", e.target.value)} />
              <label htmlFor={`${id}-cuenta`}>
                {formulario.modo === "alta"
                  ? "Cuenta destino: CBU o CVU de 22 números (opcional)"
                  : `Cuenta destino nueva: CBU o CVU de 22 números (vacío conserva ${cuentaEnmascarada(formulario.proveedor.cuenta_destino)})`}
              </label>
              <input id={`${id}-cuenta`} inputMode="numeric" pattern={CBU} value={datos.cuenta} disabled={enviando}
                onChange={(e) => cambiar("cuenta", e.target.value)} />
              {formulario.modo === "cambio" && (
                <>
                  <label htmlFor={`${id}-estado`}>Estado</label>
                  <select id={`${id}-estado`} value={datos.estado} disabled={enviando}
                    onChange={(e) => cambiar("estado", e.target.value as EstadoProveedor)}>
                    {(Object.keys(TEXTO_ESTADO_PROVEEDOR) as EstadoProveedor[]).map((e) => (
                      <option key={e} value={e}>{TEXTO_ESTADO_PROVEEDOR[e]}</option>
                    ))}
                  </select>
                </>
              )}
              {confirmarCuenta && (
                <p className="aviso-error" role="alert">
                  {AVISO_CUENTA}
                </p>
              )}
              <div className="acciones">
                <button type="submit" disabled={enviando}>
                  {confirmarCuenta ? "Sí, cambiar la cuenta" : "Guardar"}
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

const RUTA_ORDENES_ENLACE = "/egresos/ordenes";
