import { type FormEvent, Fragment, useEffect, useId, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router";

import { CabeceraInterna } from "../CabeceraInterna";
import { api } from "../api/cliente";
import type { components } from "../api/tipos";
import { encabezadoCsrf } from "../csrf";
import { codigoDe, SIN_CONEXION } from "../fallas";
import {
  type Aviso,
  cambioLaOrden,
  cambioLoVisto,
  cuentaEnmascarada,
  ENCABEZADO_IDEMPOTENCIA,
  ESTADO,
  type EstadoProveedor,
  mensajeDeError,
  momento,
  renovarClave,
  SEGUNDO_FACTOR_REQUERIDO,
  sinLaFalla,
  sinPisarLoHecho,
  sobreLoNuevo,
  TEXTO_ESTADO_PROVEEDOR,
} from "./comun";
import { ElegirConsorcio } from "./ElegirConsorcio";
import { AvisoPantalla, ConsorcioSinServicio, FallaDeLectura } from "./EstadoPantalla";
import { REQUISITOS } from "../permisos";
import { RUTA_NUEVA, RUTA_PAGOS } from "../pagos/rutas";
import { PedirCodigo } from "./PedirCodigo";
import { exigirSesion, SinSesion, useConsorcio, useMontado } from "./useConsorcio";

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
  | { tipo: "sin_servicio" }
  | { tipo: "ok"; proveedores: Proveedor[] };

type Datos = {
  cuit: string;
  razon: string;
  rubro: string;
  contacto: string;
  telefono: string;
  whatsapp: string;
  correo: string;
  domicilio: string;
  emergencias: boolean;
  cuenta: string;
  estado: EstadoProveedor;
};
const VACIO: Datos = {
  cuit: "",
  razon: "",
  rubro: "",
  contacto: "",
  telefono: "",
  whatsapp: "",
  correo: "",
  domicilio: "",
  emergencias: false,
  cuenta: "",
  estado: "activo",
};

type Cambio = { modo: "cambio"; proveedor: Proveedor };
type CargaCuit = { modo: "cuit"; proveedor: Proveedor };
type Formulario = { modo: "alta" } | Cambio | CargaCuit;
type Pendiente = { acto: string; reintentar: () => void };

const opcional = (v: string) => (v.trim() === "" ? null : v.trim());

/** Un proveedor en el formulario. La cuenta completa solo llega a la revisión del Consejo: acá el
 * campo empieza vacío y vacío quiere decir «conservar la que tiene». */
const datosDe = (p: Proveedor): Datos => ({
  cuit: p.cuit ?? "",
  razon: p.razon_social,
  rubro: p.rubro ?? "",
  contacto: p.contacto ?? "",
  telefono: p.telefono ?? "",
  whatsapp: p.whatsapp ?? "",
  correo: p.correo ?? "",
  domicilio: p.domicilio ?? "",
  emergencias: p.atiende_emergencias,
  cuenta: "",
  estado: p.estado,
});

/** Si se llegó desde Órdenes de pago porque faltaba un proveedor (`state.paraPreparar`). */
function vieneAPreparar(estado: unknown): boolean {
  return typeof estado === "object" && estado !== null && "paraPreparar" in estado && estado.paraPreparar === true;
}

/** En un cambio, el campo vacío conserva la cuenta del proveedor. La pantalla nunca la tiene
 * completa: lo pide con `conservar_cuenta` y el backend la toma de la versión esperada. */
function conservaCuenta(f: Formulario, d: Datos): boolean {
  return f.modo === "cambio" && d.cuenta.trim() === "";
}

/** El aviso de cambio de cuenta (decisión del 30-10, punto 21). */
export const AVISO_CUENTA =
  "Cambiar la cuenta crea una versión nueva del proveedor. Las órdenes ya preparadas siguen apuntando a la versión anterior; las nuevas usan la cuenta nueva. Un cambio material obliga a una revisión nueva cuando corresponde.";

export const AVISO_PROVEEDOR_CAMBIO = "El proveedor cambió mientras lo editabas. Revisá los datos y volvé a guardar.";

type PropsDelEdificio = ReturnType<typeof useConsorcio> & { vuelveAPreparar: boolean };

// Cada edificio monta la pantalla de nuevo: nada de un edificio (estado, respuestas tardías,
// el reintento pendiente después del código) llega a la pantalla de otro.
export function Proveedores() {
  const elegido = useConsorcio(REQUISITOS.proveedores);
  const vuelveAPreparar = useRef(vieneAPreparar(useLocation().state));
  return <ProveedoresDelEdificio key={elegido.consorcio} {...elegido} vuelveAPreparar={vuelveAPreparar.current} />;
}

/** Lo que se sabe para ubicarlo: solo lo que está cargado. */
function DatosDeContacto({ proveedor: p }: { proveedor: Proveedor }) {
  const filas: readonly (readonly [string, string | null])[] = [
    ["Contacto", p.contacto],
    ["Teléfono", p.telefono],
    ["WhatsApp", p.whatsapp],
    ["Correo", p.correo],
    ["Domicilio", p.domicilio],
    ["Emergencias", p.atiende_emergencias ? "Atiende emergencias" : null],
  ];
  return filas
    .filter(([, valor]) => valor !== null)
    .map(([nombre, valor]) => (
      <Fragment key={nombre}>
        <dt>{nombre}</dt>
        <dd>{valor}</dd>
      </Fragment>
    ));
}

function ProveedoresDelEdificio({ pantalla, consorcio, setConsorcio, aEntrar, aInicio, reintentar, vuelveAPreparar }: PropsDelEdificio) {
  const id = useId();
  const navegar = useNavigate();
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
  const montado = useMontado();

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
        else if (response.status === ESTADO.sinPermiso && codigo === "sin_permiso") aInicio();
        else {
          setLectura({ tipo: "sin_servicio" });
          setAviso(sinPisarLoHecho(mensajeDeError(response.status, codigo)));
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
    setFicha(null);
    setVuelta((v) => v + 1);
  }

  function reintentarLectura() {
    setAviso(sinLaFalla);
    volverAPedir();
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
    setDatos(f.modo === "alta" ? VACIO : datosDe(f.proveedor));
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
    const cambiaCuenta = formulario.modo === "cambio" && !conservaCuenta(formulario, datos);
    if (cambiaCuenta && !confirmarCuenta) {
      setConfirmarCuenta(true);
      return;
    }
    pedir(formulario);
  }

  /** El proveedor cambió mientras se editaba: el formulario sigue con lo escrito sobre la versión
   * nueva, y cambiar la cuenta se vuelve a confirmar. Otra falla lo deja como está: reintentar
   * vuelve a pasar por acá. */
  function seguirSobreLaVersionNueva(f: Cambio) {
    void api
      .GET("/api/v1/consorcios/{consorcio_id}/proveedores/{proveedor_id}", {
        params: { path: { consorcio_id: consorcio, proveedor_id: f.proveedor.id } },
      })
      .then(({ data, response }) => {
        exigirSesion(response);
        if (!montado.current) return;
        if (data !== undefined) {
          const { proveedor } = data;
          setDatos((d) => sobreLoNuevo(d, datosDe(f.proveedor), datosDe(proveedor)));
          setFormulario({ modo: "cambio", proveedor });
          setConfirmarCuenta(false);
        } else if (response.status === ESTADO.noEncontrado) {
          cerrar();
          volverAPedir();
        }
      })
      .catch((e: unknown) => (e instanceof SinSesion ? aEntrar() : setAviso({ texto: SIN_CONEXION, ok: false })));
  }

  function pedir(f: Formulario) {
    if (!montado.current) return;
    clave.current ??= crypto.randomUUID();
    const encabezado = { [ENCABEZADO_IDEMPOTENCIA]: clave.current };
    const comunes = {
      razon_social: datos.razon.trim(),
      rubro: opcional(datos.rubro),
      contacto: opcional(datos.contacto),
      telefono: opcional(datos.telefono),
      whatsapp: opcional(datos.whatsapp),
      correo: opcional(datos.correo),
      domicilio: opcional(datos.domicilio),
      atiende_emergencias: datos.emergencias,
      cuenta_destino: conservaCuenta(f, datos) ? null : opcional(datos.cuenta),
    };
    setEnviando(true);
    setAviso(null);
    const pedido =
      f.modo === "cuit"
        ? api.POST("/api/v1/consorcios/{consorcio_id}/proveedores/{proveedor_id}/cuit", {
            headers: encabezadoCsrf(),
            params: { path: { consorcio_id: consorcio, proveedor_id: f.proveedor.id }, header: encabezado },
            body: { cuit: datos.cuit.trim(), version_esperada: f.proveedor.version },
          })
        : f.modo === "alta"
        ? api.POST("/api/v1/consorcios/{consorcio_id}/proveedores", {
            headers: encabezadoCsrf(),
            params: { path: { consorcio_id: consorcio }, header: encabezado },
            body: { ...comunes, cuit: datos.cuit.trim() },
          })
        : api.POST("/api/v1/consorcios/{consorcio_id}/proveedores/{proveedor_id}/versiones", {
            headers: encabezadoCsrf(),
            params: { path: { consorcio_id: consorcio, proveedor_id: f.proveedor.id }, header: encabezado },
            body: {
              ...comunes,
              documento_id: f.proveedor.documento_id,
              estado: datos.estado,
              version_esperada: f.proveedor.version,
              conservar_cuenta: conservaCuenta(f, datos),
            },
          });
    void pedido
      .then(({ data, error, response }) => {
        exigirSesion(response);
        if (data !== undefined) {
          cerrar();
          const hecho =
            f.modo === "alta"
              ? "Listo: dimos de alta el proveedor."
              : f.modo === "cuit"
                ? `Listo: cargaste el CUIT y el proveedor quedó en la versión ${data.version}. Una orden ya aprobada hay que modificarla.`
                : `Listo: el proveedor quedó en la versión ${data.version}.`;
          if (f.modo === "alta" && vuelveAPreparar) {
            navegar(RUTA_NUEVA, { state: { consorcio, proveedor: data.id, aviso: hecho } });
            return;
          }
          setAviso({ texto: hecho, ok: true });
          volverAPedir();
          return;
        }
        const codigo = codigoDe(error);
        if (codigo === SEGUNDO_FACTOR_REQUERIDO) {
          setPendiente({ acto: "guardar el proveedor", reintentar: () => pedir(f) });
          return;
        }
        if (renovarClave(response.status, codigo)) clave.current = null;
        if (f.modo === "cambio" && cambioLaOrden(codigo)) {
          setAviso({ texto: AVISO_PROVEEDOR_CAMBIO, ok: false });
          seguirSobreLaVersionNueva(f);
          // La lista de atrás también quedó vieja; sin «Cargando», para no sacar el formulario.
          setVuelta((v) => v + 1);
          return;
        }
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
      <CabeceraInterna titulo="Proveedores" volver={RUTA_PAGOS} destino="Pagos" />
      <ElegirConsorcio pantalla={pantalla} consorcio={consorcio} deshabilitado={enviando}
        elegir={setConsorcio} />

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
          alCancelar={() => {
            setPendiente(null);
            setEnviando(false);
          }}
        />
      )}

      {pantalla.tipo === "lista" && (
        <>
          {lectura.tipo === "cargando" && <p role="status">Cargando los proveedores…</p>}
          {lectura.tipo === "sin_servicio" && <FallaDeLectura alReintentar={reintentarLectura} avisar={setAviso} />}
          {lectura.tipo === "ok" && formulario === null && ficha === null && (
            <>
              <button type="button" onClick={() => abrir({ modo: "alta" })}>
                Dar de alta un proveedor
              </button>
              {lectura.proveedores.length === 0 ? (
                <p className="tarjeta">El edificio no tiene proveedores cargados.</p>
              ) : (
                <div className="tabla-lista tabla-tarjetas">
                  <table aria-label="Proveedores">
                    <thead>
                      <tr>
                        <th scope="col">Proveedor</th>
                        <th scope="col">CUIT</th>
                        <th scope="col">Estado</th>
                        <th scope="col">Cuenta</th>
                        <th scope="col">
                          <span className="solo-lector">Acciones</span>
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {lectura.proveedores.map((p) => (
                        <tr key={p.id}>
                          <th scope="row">
                            <h2>{p.razon_social}</h2>
                          </th>
                          <td data-etiqueta="CUIT">{p.cuit ?? "Sin CUIT"}</td>
                          <td data-etiqueta="Estado">{TEXTO_ESTADO_PROVEEDOR[p.estado]}</td>
                          <td data-etiqueta="Cuenta">{cuentaEnmascarada(p.cuenta_ultimos_digitos)}</td>
                          <td>
                            <button type="button" className="sutil" onClick={() => verFicha(p)}>
                              Ver la ficha<span className="solo-lector"> de {p.razon_social}</span>
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
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
                <DatosDeContacto proveedor={ficha.proveedor} />
                <dt>Estado</dt>
                <dd>{TEXTO_ESTADO_PROVEEDOR[ficha.proveedor.estado]}</dd>
                <dt>Cuenta</dt>
                <dd>{cuentaEnmascarada(ficha.proveedor.cuenta_ultimos_digitos)}</dd>
              </dl>
              {ficha.versiones.length > 1 && (
                <details className="historial">
                  <summary>Versiones anteriores</summary>
                  <ol className="versiones">
                    {ficha.versiones
                      .filter((v) => v.version !== ficha.proveedor.version)
                      .map((v) => (
                        <li key={v.version}>
                          Versión {v.version}, {momento(v.creada_en)}: {v.razon_social},{" "}
                          {TEXTO_ESTADO_PROVEEDOR[v.estado]}, cuenta {cuentaEnmascarada(v.cuenta_ultimos_digitos)}
                        </li>
                      ))}
                  </ol>
                </details>
              )}
              <div className="formulario">
                <div className="acciones">
                  <button type="button" onClick={() => abrir({ modo: "cambio", proveedor: ficha.proveedor })}>
                    Cambiar los datos
                  </button>
                  {ficha.proveedor.cuit === null && (
                    <button type="button" onClick={() => abrir({ modo: "cuit", proveedor: ficha.proveedor })}>
                      Cargar el CUIT
                    </button>
                  )}
                  <button type="button" className="secundario" onClick={() => setFicha(null)}>
                    Volver a la lista
                  </button>
                </div>
              </div>
            </section>
          )}

          {formulario?.modo === "cuit" && (
            <form className="formulario tarjeta" onSubmit={enviar} aria-labelledby={`${id}-form`}>
              <h2 id={`${id}-form`}>Cargar el CUIT de {formulario.proveedor.razon_social}</h2>
              <p>Se carga una sola vez y después no cambia: revisalo antes de guardar. Deja una versión nueva del proveedor.</p>
              <label htmlFor={`${id}-cuit`}>CUIT (11 números, sin guiones)</label>
              <input id={`${id}-cuit`} inputMode="numeric" required pattern={CUIT} value={datos.cuit} disabled={enviando}
                onChange={(e) => cambiar("cuit", e.target.value)} />
              <div className="acciones">
                <button type="submit" disabled={enviando}>
                  Guardar el CUIT
                </button>
                <button type="button" className="secundario" disabled={enviando} onClick={cerrar}>
                  Cancelar
                </button>
              </div>
            </form>
          )}

          {formulario !== null && formulario.modo !== "cuit" && (
            <form className="formulario tarjeta" onSubmit={enviar} aria-labelledby={`${id}-form`}>
              <h2 id={`${id}-form`}>
                {formulario.modo === "alta" ? "Alta de un proveedor" : `Cambiar ${formulario.proveedor.razon_social}`}
              </h2>
              {formulario.modo === "alta" && (
                <>
                  <label htmlFor={`${id}-cuit`}>CUIT (11 números, sin guiones)</label>
                  <input id={`${id}-cuit`} inputMode="numeric" required pattern={CUIT} value={datos.cuit} disabled={enviando}
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
              <label htmlFor={`${id}-telefono`}>Teléfono (opcional)</label>
              <input id={`${id}-telefono`} type="tel" autoComplete="off" maxLength={50} value={datos.telefono} disabled={enviando}
                onChange={(e) => cambiar("telefono", e.target.value)} />
              <label htmlFor={`${id}-whatsapp`}>WhatsApp (opcional)</label>
              <input id={`${id}-whatsapp`} type="tel" autoComplete="off" maxLength={50} value={datos.whatsapp} disabled={enviando}
                onChange={(e) => cambiar("whatsapp", e.target.value)} />
              <label htmlFor={`${id}-correo`}>Correo (opcional)</label>
              <input id={`${id}-correo`} type="email" autoComplete="off" maxLength={254} value={datos.correo} disabled={enviando}
                onChange={(e) => cambiar("correo", e.target.value)} />
              <label htmlFor={`${id}-domicilio`}>Domicilio (opcional)</label>
              <input id={`${id}-domicilio`} autoComplete="off" maxLength={200} value={datos.domicilio} disabled={enviando}
                onChange={(e) => cambiar("domicilio", e.target.value)} />
              <label className="opcion">
                <input type="checkbox" checked={datos.emergencias} disabled={enviando}
                  onChange={(e) => cambiar("emergencias", e.target.checked)} />
                Atiende emergencias
              </label>
              <label htmlFor={`${id}-cuenta`}>
                {formulario.modo === "alta"
                  ? "Cuenta destino: CBU o CVU de 22 números (opcional)"
                  : `Cuenta destino nueva: CBU o CVU de 22 números (vacío conserva ${cuentaEnmascarada(formulario.proveedor.cuenta_ultimos_digitos)})`}
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
