import { type FormEvent, Fragment, useEffect, useId, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router";

import { CabeceraInterna } from "../CabeceraInterna";
import { CampoCuit, cuitConError } from "../CampoCuit";
import { FilaDeRegistro, useFichas } from "../FichaDeRegistro";
import { type Cargado, CargaConLectura, type LecturaCarga, MarcaDeLectura, TEXTO_ESPERA_PARA_GUARDAR } from "../CargaConLectura";
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
//
// El alta empieza por el documento que respalda los datos (0088): la IA lee CUIT, razón social,
// domicilio, teléfono y correo, y los marca para revisar. La cuenta se escribe siempre a mano: la
// lectura nunca la trae. Desde una orden, llega con lo leído de la factura y vuelve a la orden.

type Proveedor = components["schemas"]["Proveedor"];
type Version = components["schemas"]["VersionProveedor"];

export const RUTA_PROVEEDORES = "/egresos/proveedores";
const CBU = "[0-9]{22}";

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

type CampoLeido = "cuit" | "razon" | "domicilio" | "telefono" | "correo";

/** Lo que trae la orden al dar de alta el proveedor de su factura (`AltaDesdeLaFactura`), con el
 * edificio de esa factura: en otro edificio no se usa. */
type DesdeLaFactura = { consorcio: string; datos: Partial<Datos>; documento: Cargado | null; orden: Record<string, unknown> | null };

/** Los datos del proveedor que leyó la IA; nunca la cuenta. */
function datosLeidos(l: Pick<LecturaCarga, "cuit" | "razon_social" | "domicilio" | "telefono" | "correo">): Partial<Datos> {
  return {
    ...(l.cuit === null ? {} : { cuit: l.cuit }),
    ...(l.razon_social === null ? {} : { razon: l.razon_social }),
    ...(l.domicilio === null ? {} : { domicilio: l.domicilio }),
    ...(l.telefono === null ? {} : { telefono: l.telefono }),
    ...(l.correo === null ? {} : { correo: l.correo }),
  };
}

const textoONulo = (v: unknown) => (typeof v === "string" ? v : null);

function desdeLaFactura(estado: unknown): DesdeLaFactura | null {
  if (typeof estado !== "object" || estado === null || !("alta" in estado)) return null;
  const { alta, documento, orden, consorcio } = estado as Record<string, unknown>;
  if (typeof alta !== "object" || alta === null || typeof consorcio !== "string") return null;
  const a = alta as Record<string, unknown>;
  const d = typeof documento === "object" && documento !== null ? (documento as Record<string, unknown>) : null;
  const cargado =
    d !== null && typeof d.documento === "string" && typeof d.version === "string" && typeof d.nombre === "string"
      ? { documento: d.documento, version: d.version, nombre: d.nombre }
      : null;
  return {
    consorcio,
    datos: datosLeidos({ cuit: textoONulo(a.cuit), razon_social: textoONulo(a.razon_social), domicilio: textoONulo(a.domicilio),
      telefono: textoONulo(a.telefono), correo: textoONulo(a.correo) }),
    documento: cargado,
    orden: typeof orden === "object" && orden !== null ? (orden as Record<string, unknown>) : null,
  };
}

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

type PropsDelEdificio = ReturnType<typeof useConsorcio> & { vuelveAPreparar: boolean; factura: DesdeLaFactura | null };

// Cada edificio monta la pantalla de nuevo: nada de un edificio (estado, respuestas tardías,
// el reintento pendiente después del código) llega a la pantalla de otro.
export function Proveedores() {
  const elegido = useConsorcio(REQUISITOS.proveedores);
  const estado = useLocation().state;
  const vuelveAPreparar = useRef(vieneAPreparar(estado));
  const factura = useRef(desdeLaFactura(estado));
  // Lo leído de la factura es del edificio de la orden: al elegir otro edificio no se usa.
  const deEsteEdificio = factura.current?.consorcio === elegido.consorcio ? factura.current : null;
  return <ProveedoresDelEdificio key={elegido.consorcio} {...elegido} vuelveAPreparar={vuelveAPreparar.current} factura={deEsteEdificio} />;
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

function ProveedoresDelEdificio({ pantalla, consorcio, setConsorcio, aEntrar, aInicio, reintentar, vuelveAPreparar, factura }: PropsDelEdificio) {
  const id = useId();
  const navegar = useNavigate();
  const [lectura, setLectura] = useState<Lectura>({ tipo: "cargando" });
  const [vuelta, setVuelta] = useState(0);
  const [ficha, setFicha] = useState<{ proveedor: Proveedor; versiones: Version[] } | null>(null);
  /** La fila que abrió la ficha (comentario 21): al volver a la lista, el foco vuelve a ella. */
  const { abrir: abrirFila, cerrar: cerrarFila, refFila } = useFichas<string>();
  const [formulario, setFormulario] = useState<Formulario | null>(factura === null ? null : { modo: "alta" });
  const [datos, setDatos] = useState<Datos>(factura === null ? VACIO : { ...VACIO, ...factura.datos });
  /** El documento que respalda el alta (0088), ya aprobado por el antivirus. */
  const [respaldo, setRespaldo] = useState<Cargado | null>(factura?.documento ?? null);
  /** El documento del alta subiéndose, en el antivirus o leyéndose: hasta que termine no se guarda. */
  const [respaldoEnEspera, setRespaldoEnEspera] = useState(false);
  /** Los campos que completó la lectura y la persona todavía no revisó. */
  const [deLaLectura, setDeLaLectura] = useState<ReadonlySet<CampoLeido>>(
    new Set(factura === null ? [] : (Object.keys(factura.datos) as CampoLeido[])),
  );
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
    cerrarFila(false);
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
        if (data !== undefined) {
          setFicha(data);
          abrirFila(p.id);
        } else if (codigoDe(error) === SEGUNDO_FACTOR_REQUERIDO)
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
    setRespaldo(null);
    setDeLaLectura(new Set());
    setFormulario(f);
  }

  function revisado(campo: keyof Datos) {
    setDeLaLectura((marcados) => {
      if (!marcados.has(campo as CampoLeido)) return marcados;
      const otros = new Set(marcados);
      otros.delete(campo as CampoLeido);
      return otros;
    });
  }

  function leerDocumento(l: LecturaCarga) {
    const leidos = datosLeidos(l);
    clave.current = null;
    setDatos((d) => ({ ...d, ...leidos }));
    setDeLaLectura(new Set(Object.keys(leidos) as CampoLeido[]));
  }

  function cerrar() {
    clave.current = null;
    setConfirmarCuenta(false);
    setFormulario(null);
  }

  function cambiar<K extends keyof Datos>(campo: K, valor: Datos[K]) {
    revisado(campo);
    clave.current = null;
    setConfirmarCuenta(false);
    setDatos((d) => ({ ...d, [campo]: valor }));
  }

  function enviar(evento: FormEvent) {
    evento.preventDefault();
    // Dar de alta ahora mandaría el proveedor sin el documento que todavía no terminó.
    if (formulario === null || (formulario.modo === "alta" && respaldoEnEspera)) return;
    // El CUIT con el verificador mal ya se ve marcado en el campo: no se manda.
    if (formulario.modo !== "cambio" && cuitConError(datos.cuit.trim())) return;
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
            body: { ...comunes, cuit: datos.cuit.trim(), documento_id: respaldo?.documento ?? null },
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
            navegar(RUTA_NUEVA, { state: { ...(factura?.orden ?? {}), consorcio, proveedor: data.id, aviso: hecho } });
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

  const marca = (campo: CampoLeido) => (deLaLectura.has(campo) ? <MarcaDeLectura alRevisar={() => revisado(campo)} /> : null);

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
                // La fila entera abre la ficha del proveedor; las acciones están en la ficha.
                <ul className="filas-registro" aria-label="Proveedores">
                  {lectura.proveedores.map((p) => (
                    <li key={p.id}>
                      <FilaDeRegistro titulo={p.razon_social} refFila={refFila(p.id)} deshabilitada={enviando} alAbrir={() => verFicha(p)}
                        resumen={`${p.cuit ?? "Sin CUIT"} · ${TEXTO_ESTADO_PROVEEDOR[p.estado]} · Cuenta ${cuentaEnmascarada(p.cuenta_ultimos_digitos)}`} />
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
                  <button
                    type="button"
                    className="secundario"
                    onClick={() => {
                      setFicha(null);
                      cerrarFila();
                    }}
                  >
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
              <CampoCuit id={`${id}-cuit`} valor={datos.cuit} deshabilitado={enviando} alCambiar={(v) => cambiar("cuit", v)} />
              <div className="acciones">
                <button type="submit" disabled={enviando}>
                  Guardar el CUIT
                </button>
                <button type="button" className="secundario peligro" disabled={enviando} onClick={cerrar}>
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
                  <CargaConLectura consorcio={consorcio} tipo="proveedor" titulo="Documento del proveedor" de="del proveedor"
                    textoVer="Ver el documento" deshabilitada={enviando} inicial={factura?.documento ?? null} aEntrar={aEntrar}
                    alCargar={(c) => {
                      clave.current = null;
                      setRespaldo(c);
                    }}
                    alLeer={leerDocumento} alEsperar={setRespaldoEnEspera} />
                  <CampoCuit id={`${id}-cuit`} valor={datos.cuit} deshabilitado={enviando} alCambiar={(v) => cambiar("cuit", v)} />
                  {marca("cuit")}
                </>
              )}
              <label htmlFor={`${id}-razon`}>Razón social</label>
              <input id={`${id}-razon`} required maxLength={200} value={datos.razon} disabled={enviando}
                onChange={(e) => cambiar("razon", e.target.value)} />
              {marca("razon")}
              <label htmlFor={`${id}-rubro`}>Rubro (opcional)</label>
              <input id={`${id}-rubro`} maxLength={100} value={datos.rubro} disabled={enviando}
                onChange={(e) => cambiar("rubro", e.target.value)} />
              <label htmlFor={`${id}-contacto`}>Contacto (opcional)</label>
              <input id={`${id}-contacto`} maxLength={200} value={datos.contacto} disabled={enviando}
                onChange={(e) => cambiar("contacto", e.target.value)} />
              <label htmlFor={`${id}-telefono`}>Teléfono (opcional)</label>
              <input id={`${id}-telefono`} type="tel" autoComplete="off" maxLength={50} value={datos.telefono} disabled={enviando}
                onChange={(e) => cambiar("telefono", e.target.value)} />
              {marca("telefono")}
              <label htmlFor={`${id}-whatsapp`}>WhatsApp (opcional)</label>
              <input id={`${id}-whatsapp`} type="tel" autoComplete="off" maxLength={50} value={datos.whatsapp} disabled={enviando}
                onChange={(e) => cambiar("whatsapp", e.target.value)} />
              <label htmlFor={`${id}-correo`}>Correo (opcional)</label>
              <input id={`${id}-correo`} type="email" autoComplete="off" maxLength={254} value={datos.correo} disabled={enviando}
                onChange={(e) => cambiar("correo", e.target.value)} />
              {marca("correo")}
              <label htmlFor={`${id}-domicilio`}>Domicilio (opcional)</label>
              <input id={`${id}-domicilio`} autoComplete="off" maxLength={200} value={datos.domicilio} disabled={enviando}
                onChange={(e) => cambiar("domicilio", e.target.value)} />
              {marca("domicilio")}
              <label className="opcion">
                <input type="checkbox" checked={datos.emergencias} disabled={enviando}
                  onChange={(e) => cambiar("emergencias", e.target.checked)} />
                Atiende emergencias
              </label>
              <label htmlFor={`${id}-cuenta`}>
                {formulario.modo === "alta"
                  ? "Cuenta destino: CBU o CVU de 22 números (opcional)"
                  : "Cuenta destino: CBU o CVU de 22 números. Cambia solo si se escribe otra"}
              </label>
              {/* La cuenta que tiene va dentro del campo (comentario 27): vacío la conserva. */}
              <input id={`${id}-cuenta`} inputMode="numeric" pattern={CBU} value={datos.cuenta} disabled={enviando}
                placeholder={formulario.modo === "cambio" ? cuentaEnmascarada(formulario.proveedor.cuenta_ultimos_digitos) : undefined}
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
              {formulario.modo === "alta" && respaldoEnEspera && (
                <p id={`${id}-espera`} className="aviso-info">
                  {TEXTO_ESPERA_PARA_GUARDAR}
                </p>
              )}
              <div className="acciones">
                <button type="submit" disabled={enviando || (formulario.modo === "alta" && respaldoEnEspera)}
                  aria-describedby={formulario.modo === "alta" && respaldoEnEspera ? `${id}-espera` : undefined}>
                  {confirmarCuenta ? "Sí, cambiar la cuenta" : formulario.modo === "alta" ? "Dar de alta el proveedor" : "Guardar los cambios"}
                </button>
                <button type="button" className="secundario peligro" disabled={enviando} onClick={cerrar}>
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
