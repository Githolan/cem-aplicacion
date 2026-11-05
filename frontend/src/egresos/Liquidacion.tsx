import { type FormEvent, useEffect, useId, useRef, useState } from "react";

import { api } from "../api/cliente";
import type { components } from "../api/tipos";
import { encabezadoCsrf } from "../csrf";
import { codigoDe, SIN_CONEXION } from "../fallas";
import {
  type Aviso,
  cambioLoVisto,
  ENCABEZADO_IDEMPOTENCIA,
  ESTADO,
  importe,
  mensajeDeError,
  renovarClave,
  SEGUNDO_FACTOR_REQUERIDO,
  sinLaFalla,
  sinPisarLoHecho,
} from "./comun";
import { ElegirConsorcio } from "./ElegirConsorcio";
import { AvisoPantalla, ConsorcioSinServicio, FallaDeLectura, Reintentar } from "./EstadoPantalla";
import { REQUISITOS } from "../permisos";
import { PedirCodigo } from "./PedirCodigo";
import { exigirSesion, SinSesion, useConsorcio, useMontado } from "./useConsorcio";

// Página /liquidacion (app 0057): la administración prepara la liquidación de un período. Abre el
// borrador, le agrega órdenes pagadas con el grupo de gasto de cada una y ve la simulación: cuánto
// le toca a cada unidad según el esquema de coeficientes vigente del grupo. Todavía no se revisa,
// aprueba ni publica (decisiones 4.2 y 5 del propietario). La simulación no se guarda.

type Resumen = components["schemas"]["Liquidacion"];
type Detalle = components["schemas"]["LiquidacionDetalle"];
type Liquidable = components["schemas"]["OrdenLiquidable"];

export const RUTA_LIQUIDACION = "/liquidacion";

type Lectura =
  | { tipo: "cargando" }
  | { tipo: "sin_servicio" }
  | { tipo: "ok"; liquidaciones: Resumen[]; liquidables: Liquidable[] };
type Pendiente = { acto: string; reintentar: () => void };
type Acto =
  | { tipo: "preparar"; periodo: string }
  | { tipo: "agregar"; liquidacion: string; orden: string; grupo: string }
  | { tipo: "quitar"; liquidacion: string; gasto: string }
  | { tipo: "descartar"; liquidacion: string; motivo: string };

/** Año y mes: lo que da `type="month"`, y lo que se pide escribir donde el navegador no lo tiene. */
const FORMATO_MES = /^\d{4}-(0[1-9]|1[0-2])$/;

const FORMATO_PERIODO = new Intl.DateTimeFormat("es-AR", { month: "long", year: "numeric", timeZone: "UTC" });
/** «2026-10-01» como «octubre de 2026». */
export const periodo = (iso: string) => FORMATO_PERIODO.format(new Date(`${iso}T00:00:00Z`));

/** "2026-10-01" → "2026-11-01": el primer día del mes siguiente, sin la zona del navegador. */
export function mesSiguiente(iso: string): string {
  const anio = Number(iso.slice(0, 4));
  const mes = Number(iso.slice(5, 7));
  return mes === 12 ? `${anio + 1}-01-01` : `${anio}-${String(mes + 1).padStart(2, "0")}-01`;
}

export function Liquidacion() {
  const elegido = useConsorcio(REQUISITOS.liquidacion);
  return <LiquidacionDelEdificio key={elegido.consorcio} {...elegido} />;
}

function LiquidacionDelEdificio({ pantalla, consorcio, setConsorcio, aEntrar, aInicio, reintentar }: ReturnType<typeof useConsorcio>) {
  const id = useId();
  const [lectura, setLectura] = useState<Lectura>({ tipo: "cargando" });
  const [abierta, setAbierta] = useState<string | null>(null);
  const [detalle, setDetalle] = useState<Detalle | null>(null);
  /** Por qué no llegó el detalle de la abierta: la lista sigue a la vista. */
  const [fallaDetalle, setFallaDetalle] = useState<string | null>(null);
  const [vuelta, setVuelta] = useState(0);
  const [mes, setMes] = useState("");
  const [orden, setOrden] = useState("");
  const [grupo, setGrupo] = useState("");
  const [motivo, setMotivo] = useState("");
  const [enviando, setEnviando] = useState(false);
  const [aviso, setAviso] = useState<Aviso | null>(null);
  const [pendiente, setPendiente] = useState<Pendiente | null>(null);

  // Otra liquidación tiene otros grupos: la orden, el grupo y el motivo de descarte elegidos no
  // pasan de una a otra (un motivo escrito para un borrador no descarta otro).
  function abrir(id: string) {
    setAbierta(id);
    setFallaDetalle(null);
    setOrden("");
    setGrupo("");
    setMotivo("");
  }

  function volverALaLista() {
    setAbierta(null);
    setDetalle(null);
    setFallaDetalle(null);
  }
  const clave = useRef<string | null>(null);
  const montado = useMontado();

  useEffect(() => {
    if (consorcio === "") return;
    let vigente = true;
    const ruta = { params: { path: { consorcio_id: consorcio } } };
    void Promise.all([
      api.GET("/api/v1/consorcios/{consorcio_id}/liquidaciones", ruta),
      api.GET("/api/v1/consorcios/{consorcio_id}/liquidaciones/ordenes-liquidables", ruta),
      abierta === null
        ? Promise.resolve(null)
        : api.GET("/api/v1/consorcios/{consorcio_id}/liquidaciones/{liquidacion_id}", {
            params: { path: { consorcio_id: consorcio, liquidacion_id: abierta } },
          }),
    ])
      .then(([lista, liquidables, uno]) => {
        for (const r of [lista, liquidables, uno]) if (r !== null) exigirSesion(r.response);
        if (!vigente) return;
        const falla = [lista, liquidables].find((r) => r.data === undefined);
        if (falla === undefined) {
          setLectura({ tipo: "ok", liquidaciones: lista.data?.elementos ?? [], liquidables: liquidables.data?.elementos ?? [] });
          if (uno !== null && uno.data === undefined && codigoDe(uno.error) === SEGUNDO_FACTOR_REQUERIDO) {
            setPendiente({ acto: "ver la liquidación", reintentar: () => setVuelta((v) => v + 1) });
            return;
          }
          setDetalle(uno?.data ?? null);
          setFallaDetalle(uno !== null && uno.data === undefined ? mensajeDeError(uno.response.status, codigoDe(uno.error)) : null);
          return;
        }
        const codigo = codigoDe(falla.error);
        if (codigo === SEGUNDO_FACTOR_REQUERIDO)
          setPendiente({ acto: "ver la liquidación", reintentar: () => setVuelta((v) => v + 1) });
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
  }, [consorcio, abierta, vuelta, aEntrar, aInicio]);

  function reintentarLectura() {
    setAviso(sinLaFalla);
    setLectura({ tipo: "cargando" });
    setVuelta((v) => v + 1);
  }

  function hacer(acto: Acto, reintento = false) {
    if (!montado.current) return;
    if (!reintento) clave.current = null;
    clave.current ??= crypto.randomUUID();
    const header = { [ENCABEZADO_IDEMPOTENCIA]: clave.current };
    const headers = encabezadoCsrf();
    setEnviando(true);
    setAviso(null);
    const pedido =
      acto.tipo === "preparar"
        ? api.POST("/api/v1/consorcios/{consorcio_id}/liquidaciones", {
            headers,
            params: { path: { consorcio_id: consorcio }, header },
            body: { periodo: acto.periodo },
          })
        : acto.tipo === "agregar"
          ? api.POST("/api/v1/consorcios/{consorcio_id}/liquidaciones/{liquidacion_id}/gastos", {
              headers,
              params: { path: { consorcio_id: consorcio, liquidacion_id: acto.liquidacion }, header },
              body: { orden_id: acto.orden, grupo: acto.grupo },
            })
          : acto.tipo === "quitar"
            ? api.POST("/api/v1/consorcios/{consorcio_id}/liquidaciones/{liquidacion_id}/gastos/{gasto_id}/quitar", {
                headers,
                params: { path: { consorcio_id: consorcio, liquidacion_id: acto.liquidacion, gasto_id: acto.gasto }, header },
              })
            : api.POST("/api/v1/consorcios/{consorcio_id}/liquidaciones/{liquidacion_id}/descartar", {
                headers,
                params: { path: { consorcio_id: consorcio, liquidacion_id: acto.liquidacion }, header },
                body: { motivo: acto.motivo },
              });
    void pedido
      .then(({ data, error, response }) => {
        exigirSesion(response);
        if (data !== undefined) {
          const texto = {
            preparar: "Listo: abriste el borrador del período.",
            agregar: "Listo: agregaste el gasto. La simulación ya lo reparte.",
            quitar: "Listo: quitaste el gasto del borrador.",
            descartar: "Listo: descartaste el borrador. Sus gastos vuelven a estar disponibles y el período admite uno nuevo.",
          }[acto.tipo];
          setAviso({ texto, ok: true });
          if (acto.tipo === "preparar") {
            setMes("");
            abrir(data.id);
          }
          if (acto.tipo === "agregar") setOrden("");
          if (acto.tipo === "descartar") {
            setMotivo("");
            volverALaLista();
          }
          setVuelta((v) => v + 1);
          return;
        }
        const codigo = codigoDe(error);
        if (codigo === SEGUNDO_FACTOR_REQUERIDO) {
          const actual = clave.current;
          setPendiente({
            acto: {
              preparar: "abrir el borrador",
              agregar: "agregar el gasto",
              quitar: "quitar el gasto",
              descartar: "descartar el borrador",
            }[acto.tipo],
            reintentar: () => {
              clave.current = actual;
              hacer(acto, true);
            },
          });
          return;
        }
        if (renovarClave(response.status, codigo)) clave.current = null;
        setAviso({ texto: mensajeDeError(response.status, codigo), ok: false });
        if (cambioLoVisto(response.status, codigo)) setVuelta((v) => v + 1);
      })
      .catch((e: unknown) => (e instanceof SinSesion ? aEntrar() : setAviso({ texto: SIN_CONEXION, ok: false })))
      .finally(() => setEnviando(false));
  }

  function preparar(evento: FormEvent) {
    evento.preventDefault();
    if (mes === "") return;
    if (!FORMATO_MES.test(mes)) {
      setAviso({ texto: "Escribí el período como año y mes, por ejemplo 2026-10.", ok: false });
      return;
    }
    hacer({ tipo: "preparar", periodo: `${mes}-01` });
  }

  function agregar(evento: FormEvent, liquidacion: string) {
    evento.preventDefault();
    if (orden === "" || grupo === "") return;
    hacer({ tipo: "agregar", liquidacion, orden, grupo });
  }

  function descartar(evento: FormEvent, liquidacion: string) {
    evento.preventDefault();
    if (motivo.trim() === "") return;
    hacer({ tipo: "descartar", liquidacion, motivo: motivo.trim() });
  }

  const liquidaciones = lectura.tipo === "ok" ? lectura.liquidaciones : [];
  const liquidables = lectura.tipo === "ok" ? lectura.liquidables : [];
  const simulacion = detalle?.simulacion;
  // Un grupo puede tener dos versiones del esquema en el mismo borrador: una columna por grupo.
  const grupos = [...new Set(simulacion?.grupos.map((g) => g.grupo) ?? [])];
  const moneda = simulacion?.moneda ?? null;
  // Un borrador tiene una sola moneda: con gastos, solo se ofrecen órdenes de esa moneda.
  const monedaBorrador = detalle?.gastos[0]?.moneda ?? null;
  // Como la regla del backend (0058): entra solo lo pagado hasta el último día del período (P3 136).
  const hasta = detalle === null ? null : mesSiguiente(detalle.liquidacion.periodo);
  const ofrecibles = liquidables.filter(
    (o) => (monedaBorrador === null || o.moneda === monedaBorrador) && (hasta === null || o.pagada_el < hasta),
  );

  return (
    <>
      <h1>Liquidación</h1>
      <ElegirConsorcio pantalla={pantalla} consorcio={consorcio} deshabilitado={enviando} elegir={setConsorcio} />

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
          {lectura.tipo === "cargando" && <p role="status">Cargando la liquidación…</p>}
          {lectura.tipo === "sin_servicio" && (
            <FallaDeLectura alReintentar={reintentarLectura}>
              {abierta !== null && (
                <button type="button" className="secundario" onClick={() => {
                  volverALaLista();
                  reintentarLectura();
                }}>
                  Volver a la lista
                </button>
              )}
            </FallaDeLectura>
          )}
          {lectura.tipo === "ok" && (
            <>
              <p>Es un borrador para revisar los números: todavía no se aprueba ni se publica a los propietarios.</p>
              <form className="formulario" onSubmit={preparar}>
                <label htmlFor={`${id}-mes`}>Período</label>
                <input id={`${id}-mes`} type="month" required value={mes} disabled={enviando}
                  placeholder="AAAA-MM" onChange={(e) => setMes(e.target.value)} />
                <div className="acciones">
                  <button type="submit" disabled={enviando || mes === ""}>
                    Abrir el borrador
                  </button>
                </div>
              </form>

              {liquidaciones.length === 0 ? (
                <p className="tarjeta">Todavía no hay liquidaciones en este edificio.</p>
              ) : (
                <ul className="versiones" aria-label="Liquidaciones">
                  {liquidaciones.map((l) => (
                    <li key={l.id}>
                      <button type="button" className="secundario" disabled={enviando || abierta === l.id}
                        aria-pressed={abierta === l.id} onClick={() => abrir(l.id)}>
                        {periodo(l.periodo)}, versión {l.version} ({l.estado})
                      </button>
                    </li>
                  ))}
                </ul>
              )}

              {fallaDetalle !== null && (
                <section className="tarjeta" aria-label="Detalle de la liquidación">
                  <p className="aviso-error" role="alert">
                    No pudimos abrir esta liquidación. {fallaDetalle}
                  </p>
                  <Reintentar alReintentar={() => setVuelta((v) => v + 1)}>
                    <button type="button" className="secundario" onClick={volverALaLista}>
                      Volver a la lista
                    </button>
                  </Reintentar>
                </section>
              )}

              {detalle !== null && simulacion !== undefined && (
                <section className="tarjeta" aria-label={`Borrador de ${periodo(detalle.liquidacion.periodo)}`}>
                  <h2>
                    {periodo(detalle.liquidacion.periodo)}, versión {detalle.liquidacion.version}
                  </h2>

                  <h3>Gastos</h3>
                  {detalle.gastos.length === 0 ? (
                    <p>Todavía no agregaste gastos.</p>
                  ) : (
                    <ul className="versiones" aria-label="Gastos del borrador">
                      {detalle.gastos.map((g) => (
                        <li key={g.id}>
                          {g.razon_social}: {importe(g.importe_centavos, g.moneda)}, {g.concepto} (grupo {g.grupo},
                          esquema versión {g.esquema_version}; pagada el {g.pagada_el}){" "}
                          {detalle.liquidacion.estado === "borrador" && (
                            <button type="button" className="secundario" disabled={enviando}
                              aria-label={`Quitar ${g.razon_social}, ${importe(g.importe_centavos, g.moneda)}`}
                              onClick={() => hacer({ tipo: "quitar", liquidacion: detalle.liquidacion.id, gasto: g.id })}>
                              Quitar
                            </button>
                          )}
                        </li>
                      ))}
                    </ul>
                  )}

                  {detalle.liquidacion.estado === "borrador" &&
                    (ofrecibles.length === 0 ? (
                      <p>No hay órdenes pagadas hasta el fin de {periodo(detalle.liquidacion.periodo)} para agregar.</p>
                    ) : detalle.grupos.length === 0 ? (
                      <p>El edificio no tiene esquemas de coeficientes vigentes en el período: soporte los carga.</p>
                    ) : (
                      <form className="formulario" onSubmit={(e) => agregar(e, detalle.liquidacion.id)}>
                        <label htmlFor={`${id}-orden`}>Orden pagada</label>
                        <select id={`${id}-orden`} required value={orden} disabled={enviando}
                          onChange={(e) => setOrden(e.target.value)}>
                          <option value="">Elegí una orden</option>
                          {ofrecibles.map((o) => (
                            <option key={o.orden_id} value={o.orden_id}>
                              {o.razon_social}: {importe(o.importe_centavos, o.moneda)}, {o.concepto}
                            </option>
                          ))}
                        </select>
                        <label htmlFor={`${id}-grupo`}>Grupo de gasto</label>
                        <select id={`${id}-grupo`} required value={grupo} disabled={enviando}
                          onChange={(e) => setGrupo(e.target.value)}>
                          <option value="">Elegí un grupo</option>
                          {detalle.grupos.map((g) => (
                            <option key={g.grupo} value={g.grupo}>
                              {g.grupo} (esquema versión {g.esquema_version})
                            </option>
                          ))}
                        </select>
                        <div className="acciones">
                          <button type="submit" disabled={enviando || orden === "" || grupo === ""}>
                            Agregar el gasto
                          </button>
                        </div>
                      </form>
                    ))}

                  {detalle.liquidacion.estado === "borrador" && (
                    <form className="formulario" onSubmit={(e) => descartar(e, detalle.liquidacion.id)}>
                      <label htmlFor={`${id}-motivo`}>Motivo para descartar el borrador</label>
                      <input id={`${id}-motivo`} required maxLength={500} value={motivo} disabled={enviando}
                        onChange={(e) => setMotivo(e.target.value)} />
                      <div className="acciones">
                        <button type="submit" className="secundario" disabled={enviando || motivo.trim() === ""}>
                          Descartar el borrador
                        </button>
                      </div>
                    </form>
                  )}

                  <h3>Simulación del reparto</h3>
                  {simulacion.bloqueo !== null ? (
                    <p className="aviso-error" role="alert">
                      No se puede repartir: {simulacion.bloqueo}
                    </p>
                  ) : simulacion.unidades.length === 0 || moneda === null ? (
                    <p>Sin gastos no hay nada que repartir.</p>
                  ) : (
                    <div className="tabla-desplazable">
                      <table>
                        <caption>
                          Total {importe(simulacion.total_centavos, moneda)}, repartido por unidad
                        </caption>
                        <thead>
                          <tr>
                            <th scope="col">Unidad</th>
                            {grupos.map((g) => (
                              <th key={g} scope="col">
                                {g}
                              </th>
                            ))}
                            <th scope="col">Total</th>
                          </tr>
                        </thead>
                        <tbody>
                          {simulacion.unidades.map((u) => (
                            <tr key={u.unidad_id}>
                              <th scope="row">{u.designacion}</th>
                              {grupos.map((g) => (
                                <td key={g}>{importe(u.por_grupo[g] ?? 0, moneda)}</td>
                              ))}
                              <td>{importe(u.total_centavos, moneda)}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </section>
              )}
            </>
          )}
        </>
      )}
    </>
  );
}
