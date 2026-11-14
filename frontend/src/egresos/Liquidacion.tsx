import { type FormEvent, useEffect, useId, useRef, useState } from "react";

import { api } from "../api/cliente";
import { CabeceraInterna } from "../CabeceraInterna";
import { RUTA_INICIO } from "../enlace";
import type { components } from "../api/tipos";
import { encabezadoCsrf } from "../csrf";
import { FilaDeRegistro } from "../FichaDeRegistro";
import { codigoDe, esPasajera, SIN_CONEXION } from "../fallas";
import { Hoja } from "../Hoja";
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
  soltarClaveSiCambio,
} from "./comun";
import { ElegirConsorcio } from "./ElegirConsorcio";
import { AvisoPantalla, ConsorcioSinServicio, FallaDeLectura, Reintentar } from "./EstadoPantalla";
import { REQUISITOS } from "../permisos";
import { PedirCodigo } from "./PedirCodigo";
import { exigirSesion, SinSesion, useAlVolverLaRed, useConsorcio, useMontado } from "./useConsorcio";

// Página /liquidacion (app 0057): la administración prepara la liquidación de un período. Abre el
// borrador, le agrega órdenes pagadas con el grupo de gasto de cada una y ve la simulación: cuánto
// le toca a cada unidad según el esquema de coeficientes vigente del grupo. Todavía no se revisa,
// aprueba ni publica (decisiones 4.2 y 5 del propietario). La simulación no se guarda.
//
// Informe de fricción, tanda 9: se eligen varias órdenes de una vez (AC-48), pero cada una va en su
// propio pedido, con su clave de idempotencia, y el servidor la valida igual que si fuera sola; si
// una no entra se dice cuál y por qué, y las demás siguen. Las descartadas salen de la lista de
// trabajo y quedan plegadas (AC-47). Descartar pide el motivo recién al tocarlo (AC-52).

type Resumen = components["schemas"]["Liquidacion"];
type Detalle = components["schemas"]["LiquidacionDetalle"];
type Liquidable = components["schemas"]["OrdenLiquidable"];
type EstadoLiquidacion = Resumen["estado"];

export const RUTA_LIQUIDACION = "/liquidacion";

type Lectura =
  | { tipo: "cargando" }
  | { tipo: "sin_servicio" }
  | { tipo: "ok"; liquidaciones: Resumen[]; liquidables: Liquidable[] };
/** `alCancelar`: qué queda a la vista si no se confirma (AC-53: nunca un «Cargando…» sin salida). */
type Pendiente = { acto: string; reintentar: () => void; alCancelar?: () => void };
/** La clave de un «agregar» que todavía no se sabe si llegó, con lo que identifica su gasto. */
type GastoRetenido = { clave: string; liquidacion: string; orden: string; grupo: string };
type Acto =
  | { tipo: "preparar"; periodo: string }
  | { tipo: "quitar"; liquidacion: string; gasto: string }
  | { tipo: "descartar"; liquidacion: string; motivo: string };

/** Los meses del desplegable «Mes» (AC-51): el período se elige, no se escribe como «2026-10». */
const MESES = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"];
/** Años hacia atrás que ofrece «Año», además del actual y el siguiente. */
const ANIOS_ATRAS = 3;

/** «2026-10»: el mes anterior al de hoy, que es el que se suele liquidar. */
function mesAnterior(hoy = new Date()): string {
  const anterior = new Date(hoy.getFullYear(), hoy.getMonth() - 1, 1);
  return `${anterior.getFullYear()}-${String(anterior.getMonth() + 1).padStart(2, "0")}`;
}

/** Del anterior más viejo que se ofrece al siguiente al actual. */
function aniosOfrecidos(hoy = new Date()): string[] {
  const actual = hoy.getFullYear();
  return Array.from({ length: ANIOS_ATRAS + 2 }, (_, i) => String(actual - ANIOS_ATRAS + i));
}

const FORMATO_PERIODO = new Intl.DateTimeFormat("es-AR", { month: "long", year: "numeric", timeZone: "UTC" });
/** «2026-10-01» como «octubre de 2026». */
export const periodo = (iso: string) => FORMATO_PERIODO.format(new Date(`${iso}T00:00:00Z`));
const FORMATO_DIA = new Intl.DateTimeFormat("es-AR", { day: "numeric", month: "long", year: "numeric", timeZone: "UTC" });
const dia = (iso: string) => FORMATO_DIA.format(new Date(`${iso}T00:00:00Z`));
const conMayuscula = (texto: string) => texto.charAt(0).toUpperCase() + texto.slice(1);

/** El nombre del estado de una liquidación, sin el código del servidor (AC-46). */
const NOMBRE_ESTADO_LIQUIDACION: Record<EstadoLiquidacion, string> = {
  borrador: "Borrador",
  descartada: "Descartada",
  publicada: "Publicada",
};

/** «Octubre de 2026 · Borrador»; la versión solo si el período tiene más de una (AC-46). */
export const tituloDeLiquidacion = (iso: string, estado: EstadoLiquidacion, version: number) =>
  `${conMayuscula(periodo(iso))} · ${NOMBRE_ESTADO_LIQUIDACION[estado]}${version > 1 ? ` (versión ${version})` : ""}`;

/** El grupo de gasto con palabras: «expensas_comunes» → «Expensas comunes» (AC-46). El código lo
 * define el esquema de coeficientes; la pantalla no lo muestra crudo. */
export const nombreDeGrupo = (codigo: string) => conMayuscula(codigo.replaceAll("_", " "));

/** Cómo se nombra una orden en los avisos. */
const nombreDeOrden = (o: Liquidable) => `${o.razon_social}, ${importe(o.importe_centavos, o.moneda)}`;

/** Por qué no entró una orden: cuando la regla del gasto la frena, el servidor dice la causa
 * concreta (pagada después del período, ya liquidada, otra moneda, grupo sin esquema) y se dice tal
 * cual; con cualquier otro rechazo, el mensaje de siempre. */
const CON_CAUSA = new Set(["orden_no_liquidable", "sin_esquema"]);
function motivoDelGasto(estado: number, error: unknown): string {
  const codigo = codigoDe(error);
  return codigo !== undefined && CON_CAUSA.has(codigo) ? (error as { mensaje: string }).mensaje : mensajeDeError(estado, codigo);
}

/** Lo que se dice después de agregar: el «Listo» si entraron todas; si no, cuáles no y por qué. */
function avisoDeAgregar(total: number, agregadas: number, fallas: readonly string[], corte: string | null): Aviso {
  if (fallas.length === 0 && corte === null)
    return {
      texto:
        agregadas === 1
          ? "Listo: el gasto quedó agregado. La simulación ya lo reparte."
          : `Listo: se agregaron ${agregadas} gastos. La simulación ya los reparte.`,
      ok: true,
    };
  const partes = [agregadas === 0 ? "No se agregó ningún gasto." : `Se agregaron ${agregadas} de ${total} gastos.`];
  for (const falla of fallas) partes.push(`No se agregó ${falla}`);
  if (corte !== null) partes.push(`${corte} Las órdenes que faltan siguen elegidas.`);
  return { texto: partes.join(" "), ok: false };
}

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
  /** El período elegido, «AAAA-MM»; empieza en el mes anterior (AC-51). */
  const [mes, setMes] = useState(mesAnterior);
  /** Las órdenes tildadas para agregar (AC-48). */
  const [elegidas, setElegidas] = useState<readonly string[]>([]);
  const [grupo, setGrupo] = useState("");
  const [motivo, setMotivo] = useState("");
  /** La hoja que pide el motivo para descartar está abierta (AC-52). */
  const [descartando, setDescartando] = useState(false);
  const [enviando, setEnviando] = useState(false);
  /** Mientras se agregan varias: cuántas van. */
  const [progreso, setProgreso] = useState<{ hechas: number; total: number } | null>(null);
  const [aviso, setAviso] = useState<Aviso | null>(null);
  const [pendiente, setPendiente] = useState<Pendiente | null>(null);

  // Otra liquidación tiene otros grupos: la orden, el grupo y el motivo de descarte elegidos no
  // pasan de una a otra (un motivo escrito para un borrador no descarta otro).
  function abrir(id: string) {
    setAbierta(id);
    setFallaDetalle(null);
    setElegidas([]);
    setGrupo("");
    setMotivo("");
    setDescartando(false);
  }

  function volverALaLista() {
    setAbierta(null);
    setDetalle(null);
    setFallaDetalle(null);
  }
  const clave = useRef<string | null>(null);
  const ultimoActo = useRef<string | null>(null);
  /** El acto de la clave retenida en `clave`, para saber cuándo la lectura ya muestra su efecto. */
  const actoRetenido = useRef<Acto | null>(null);
  /** Una clave por orden al agregar: repetir tras una falla pasajera no la agrega dos veces. */
  const clavesDeGasto = useRef(new Map<string, GastoRetenido>());

  /** Una clave retenida sirve solo para repetir el mismo pedido mientras no se sabe si llegó. Si la
   * lectura ya muestra su efecto, ese pedido terminó: la clave se suelta y un acto igual más
   * adelante es otro (Codex en #242: quitar el gasto y volver a agregar la misma orden reusaba la
   * clave y el servidor devolvía la respuesta guardada sin agregar nada). Quitar y descartar no se
   * repiten iguales: el gasto quitado y el borrador descartado ya no ofrecen ese acto. */
  function soltarLoYaHecho(lista: readonly Resumen[], visto: Detalle | null) {
    for (const [llave, r] of clavesDeGasto.current)
      if (visto?.liquidacion.id === r.liquidacion && visto.gastos.some((g) => g.orden_id === r.orden && g.grupo === r.grupo))
        clavesDeGasto.current.delete(llave);
    const acto = actoRetenido.current;
    if (acto?.tipo === "preparar" && lista.some((l) => l.periodo === acto.periodo && l.estado === "borrador")) {
      clave.current = null;
      actoRetenido.current = null;
    }
  }
  const montado = useMontado();
  useAlVolverLaRed(fallaDetalle !== null, () => setVuelta((v) => v + 1));

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
          soltarLoYaHecho(lista.data?.elementos ?? [], uno?.data ?? null);
          setLectura({ tipo: "ok", liquidaciones: lista.data?.elementos ?? [], liquidables: liquidables.data?.elementos ?? [] });
          if (uno !== null && uno.data === undefined && codigoDe(uno.error) === SEGUNDO_FACTOR_REQUERIDO) {
            const estado = uno.response.status;
            setPendiente({
              acto: "ver la liquidación",
              reintentar: () => setVuelta((v) => v + 1),
              alCancelar: () => setFallaDetalle(mensajeDeError(estado, SEGUNDO_FACTOR_REQUERIDO)),
            });
            return;
          }
          setDetalle(uno?.data ?? null);
          setFallaDetalle(uno !== null && uno.data === undefined ? mensajeDeError(uno.response.status, codigoDe(uno.error)) : null);
          return;
        }
        const codigo = codigoDe(falla.error);
        if (codigo === SEGUNDO_FACTOR_REQUERIDO)
          setPendiente({
            acto: "ver la liquidación",
            reintentar: () => setVuelta((v) => v + 1),
            alCancelar: () => setLectura({ tipo: "sin_servicio" }),
          });
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
    if (!reintento) soltarClaveSiCambio(clave, ultimoActo, JSON.stringify(acto));
    clave.current ??= crypto.randomUUID();
    actoRetenido.current = acto;
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
          clave.current = null;
          const texto = {
            preparar: "Listo: el borrador del período quedó abierto.",
            quitar: "Listo: el gasto salió del borrador.",
            descartar: "Listo: el borrador se descartó. Sus gastos vuelven a estar disponibles y el período admite uno nuevo.",
          }[acto.tipo];
          setAviso({ texto, ok: true });
          if (acto.tipo === "preparar") {
            // Vuelve a un período completo: los desplegables nunca quedan vacíos ni a medias (Codex en #236).
            setMes(mesAnterior());
            abrir(data.id);
          }
          if (acto.tipo === "descartar") {
            setMotivo("");
            setDescartando(false);
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
    const periodoElegido = `${mes}-01`;
    // Un período admite un solo borrador: si ya lo tiene, se abre ese y no se pide otro.
    const existente = liquidaciones.find((l) => l.periodo === periodoElegido && l.estado === "borrador");
    if (existente !== undefined) {
      setAviso(null);
      setMes(mesAnterior());
      abrir(existente.id);
      return;
    }
    hacer({ tipo: "preparar", periodo: periodoElegido });
  }

  /** Agrega las órdenes de a una, cada una con su pedido: el servidor valida cada gasto como
   * siempre. Que una orden no entre no frena a las demás; una falla pasajera (red, límite, servicio)
   * sí, y las que faltan quedan elegidas para repetir con las mismas claves. */
  async function agregarVarias(liquidacion: string, ordenes: readonly Liquidable[], grupoDeGasto: string) {
    setEnviando(true);
    setAviso(null);
    const agregadas: string[] = [];
    const fallas: string[] = [];
    let corte: string | null = null;
    try {
      for (const [i, o] of ordenes.entries()) {
        if (!montado.current) return;
        setProgreso({ hechas: i, total: ordenes.length });
        const llave = `${liquidacion}:${o.orden_id}:${grupoDeGasto}`;
        const claveDeGasto = clavesDeGasto.current.get(llave)?.clave ?? crypto.randomUUID();
        clavesDeGasto.current.set(llave, { clave: claveDeGasto, liquidacion, orden: o.orden_id, grupo: grupoDeGasto });
        const { data, error, response } = await api.POST("/api/v1/consorcios/{consorcio_id}/liquidaciones/{liquidacion_id}/gastos", {
          headers: encabezadoCsrf(),
          params: {
            path: { consorcio_id: consorcio, liquidacion_id: liquidacion },
            header: { [ENCABEZADO_IDEMPOTENCIA]: claveDeGasto },
          },
          body: { orden_id: o.orden_id, grupo: grupoDeGasto },
        });
        exigirSesion(response);
        if (data !== undefined) {
          clavesDeGasto.current.delete(llave);
          agregadas.push(o.orden_id);
          continue;
        }
        const codigo = codigoDe(error);
        if (codigo === SEGUNDO_FACTOR_REQUERIDO) {
          const faltan = ordenes.slice(i);
          setPendiente({ acto: "agregar los gastos", reintentar: () => void agregarVarias(liquidacion, faltan, grupoDeGasto) });
          break;
        }
        if (renovarClave(response.status, codigo)) clavesDeGasto.current.delete(llave);
        if (esPasajera(response.status)) {
          corte = mensajeDeError(response.status, codigo);
          break;
        }
        fallas.push(`${nombreDeOrden(o)}: ${motivoDelGasto(response.status, error)}`);
      }
    } catch (e: unknown) {
      if (e instanceof SinSesion) {
        aEntrar();
        return;
      }
      corte = SIN_CONEXION;
    } finally {
      setEnviando(false);
      setProgreso(null);
    }
    setElegidas((actuales) => actuales.filter((o) => !agregadas.includes(o)));
    if (agregadas.length > 0 || fallas.length > 0 || corte !== null) setAviso(avisoDeAgregar(ordenes.length, agregadas.length, fallas, corte));
    setVuelta((v) => v + 1);
  }

  function agregar(evento: FormEvent, liquidacion: string, ordenes: readonly Liquidable[], grupoDeGasto: string) {
    evento.preventDefault();
    if (ordenes.length === 0 || grupoDeGasto === "") return;
    void agregarVarias(liquidacion, ordenes, grupoDeGasto);
  }

  function alternar(orden: string) {
    setElegidas((actuales) => (actuales.includes(orden) ? actuales.filter((o) => o !== orden) : [...actuales, orden]));
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
  // Solo cuentan las tildadas que siguen ofrecidas: lo que se agrega es lo que se ve tildado.
  const aAgregar = ofrecibles.filter((o) => elegidas.includes(o.orden_id));
  const todas = ofrecibles.length > 0 && aAgregar.length === ofrecibles.length;
  // Con un solo grupo vigente no hay nada que elegir: va ese (AC-48, «un grupo por defecto»).
  const unicoGrupo = detalle !== null && detalle.grupos.length === 1 ? detalle.grupos[0]!.grupo : null;
  const grupoDeGasto = unicoGrupo ?? grupo;
  // Las descartadas salen de la lista de trabajo y quedan plegadas: nada se borra (AC-47).
  const enTrabajo = liquidaciones.filter((l) => l.estado !== "descartada");
  const descartadas = liquidaciones.filter((l) => l.estado === "descartada");
  // Con un borrador abierto, el trabajo es ese: abrir otro período pasa a ser secundario.
  const conBorrador = detalle?.liquidacion.estado === "borrador";
  const filaDe = (l: Resumen) => (
    <li key={l.id}>
      <FilaDeRegistro titulo={tituloDeLiquidacion(l.periodo, l.estado, l.version)}
        alAbrir={() => {
          // La abierta no se reabre: lo escrito en su borrador queda.
          if (abierta !== l.id) abrir(l.id);
        }} deshabilitada={enviando} abierta={abierta === l.id} />
    </li>
  );

  return (
    <>
      <CabeceraInterna titulo="Liquidación (borrador)" volver={RUTA_INICIO} destino="Inicio" />
      <ElegirConsorcio pantalla={pantalla} consorcio={consorcio} deshabilitado={enviando} elegir={setConsorcio} />

      {pantalla.tipo === "cargando" && <p role="status">Cargando…</p>}
      {pantalla.tipo === "sin_consorcios" && <p className="tarjeta">Esta cuenta no tiene edificios en Consorcio en Mano.</p>}
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
            pendiente.alCancelar?.();
          }}
        />
      )}

      {pantalla.tipo === "lista" && pendiente === null && (
        <>
          {lectura.tipo === "cargando" && <p role="status">Cargando la liquidación…</p>}
          {lectura.tipo === "sin_servicio" && (
            <FallaDeLectura alReintentar={reintentarLectura} avisar={setAviso}>
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
              <form className="formulario no-imprimir" onSubmit={preparar}>
                <fieldset>
                  <legend>Período</legend>
                  <label htmlFor={`${id}-mes`}>Mes</label>
                  <select id={`${id}-mes`} value={mes.slice(5, 7)} disabled={enviando}
                    onChange={(e) => {
                      const elegido = e.target.value;
                      setMes((actual) => `${actual.slice(0, 4)}-${elegido}`);
                    }}>
                    {MESES.map((nombre, i) => (
                      <option key={nombre} value={String(i + 1).padStart(2, "0")}>{nombre}</option>
                    ))}
                  </select>
                  <label htmlFor={`${id}-anio`}>Año</label>
                  <select id={`${id}-anio`} value={mes.slice(0, 4)} disabled={enviando}
                    onChange={(e) => {
                      const elegido = e.target.value;
                      setMes((actual) => `${elegido}-${actual.slice(5, 7)}`);
                    }}>
                    {aniosOfrecidos().map((anio) => (
                      <option key={anio} value={anio}>{anio}</option>
                    ))}
                  </select>
                </fieldset>
                <div className="acciones">
                  <button type="submit" className={conBorrador ? "secundario" : undefined} disabled={enviando}>
                    Abrir el borrador
                  </button>
                </div>
              </form>

              {enTrabajo.length === 0 ? (
                <p className="tarjeta no-imprimir">
                  {descartadas.length === 0 ? "Todavía no hay liquidaciones en este edificio." : "No hay borradores abiertos en este edificio."}
                </p>
              ) : (
                // Cada fila entera abre el borrador debajo (comentario 21); la abierta queda marcada.
                <ul className="filas-registro no-imprimir" aria-label="Liquidaciones">
                  {enTrabajo.map(filaDe)}
                </ul>
              )}
              {descartadas.length > 0 && (
                <details className="no-imprimir">
                  <summary>Ver descartados ({descartadas.length})</summary>
                  <ul className="filas-registro" aria-label="Borradores descartados">
                    {descartadas.map(filaDe)}
                  </ul>
                </details>
              )}

              {fallaDetalle !== null && (
                <section className="tarjeta" aria-label="Detalle de la liquidación">
                  <p className="aviso-error" role="alert">
                    No se pudo abrir esta liquidación. {fallaDetalle}
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
                    {tituloDeLiquidacion(detalle.liquidacion.periodo, detalle.liquidacion.estado, detalle.liquidacion.version)}
                  </h2>

                  <h3>Gastos</h3>
                  {detalle.gastos.length === 0 ? (
                    <p>Todavía no hay gastos agregados.</p>
                  ) : (
                    <ul className="versiones" aria-label="Gastos del borrador">
                      {detalle.gastos.map((g) => (
                        <li key={g.id}>
                          {g.razon_social}: {importe(g.importe_centavos, g.moneda)}, {g.concepto} ({nombreDeGrupo(g.grupo)},
                          pagada el {dia(g.pagada_el)}){" "}
                          {detalle.liquidacion.estado === "borrador" && (
                            <button type="button" className="secundario peligro no-imprimir" disabled={enviando}
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
                      <p className="no-imprimir">No hay órdenes pagadas hasta el fin de {periodo(detalle.liquidacion.periodo)} para agregar.</p>
                    ) : detalle.grupos.length === 0 ? (
                      <p className="no-imprimir">
                        Faltan los porcentajes de cada unidad para este período: sin ellos no se puede repartir. Los carga soporte.
                      </p>
                    ) : (
                      <form className="formulario no-imprimir" onSubmit={(e) => agregar(e, detalle.liquidacion.id, aAgregar, grupoDeGasto)}>
                        <fieldset>
                          <legend>Órdenes pagadas para agregar</legend>
                          {ofrecibles.length > 1 && (
                            <label className="opcion">
                              <input type="checkbox" checked={todas} disabled={enviando}
                                onChange={() => setElegidas(todas ? [] : ofrecibles.map((o) => o.orden_id))} />
                              Elegir las {ofrecibles.length}
                            </label>
                          )}
                          <ul className="tildes-pago" aria-label="Órdenes pagadas">
                            {ofrecibles.map((o) => (
                              <li key={o.orden_id}>
                                <label className="tilde-pago">
                                  <input type="checkbox" checked={elegidas.includes(o.orden_id)} disabled={enviando}
                                    onChange={() => alternar(o.orden_id)} />
                                  <span className="tilde-pago__datos">
                                    <span className="fila-pago__arriba">
                                      <strong>{o.razon_social}</strong>
                                      <span className="fila-pago__importe">{importe(o.importe_centavos, o.moneda)}</span>
                                    </span>
                                    <span>
                                      {o.concepto}, pagada el {dia(o.pagada_el)}
                                    </span>
                                  </span>
                                </label>
                              </li>
                            ))}
                          </ul>
                        </fieldset>
                        {unicoGrupo === null ? (
                          <>
                            <label htmlFor={`${id}-grupo`}>Grupo de gasto</label>
                            <select id={`${id}-grupo`} required value={grupo} disabled={enviando}
                              onChange={(e) => setGrupo(e.target.value)}>
                              <option value="">Elegir un grupo</option>
                              {detalle.grupos.map((g) => (
                                <option key={g.grupo} value={g.grupo}>
                                  {nombreDeGrupo(g.grupo)}
                                </option>
                              ))}
                            </select>
                          </>
                        ) : (
                          <p>Se reparten como {nombreDeGrupo(unicoGrupo)}: es el único grupo de gasto del período.</p>
                        )}
                        {progreso !== null && (
                          <p role="status">
                            Agregando {progreso.hechas + 1} de {progreso.total}…
                          </p>
                        )}
                        <div className="acciones">
                          <button type="submit" disabled={enviando || aAgregar.length === 0 || grupoDeGasto === ""}>
                            {aAgregar.length > 1 ? `Agregar ${aAgregar.length} gastos` : "Agregar el gasto"}
                          </button>
                        </div>
                      </form>
                    ))}

                  <h3>Simulación del reparto</h3>
                  {simulacion.bloqueo !== null ? (
                    <p className="aviso-error" role="alert">
                      No se puede repartir. {simulacion.bloqueo}
                    </p>
                  ) : simulacion.unidades.length === 0 || moneda === null ? (
                    <p>Sin gastos no hay nada que repartir.</p>
                  ) : (
                    <div className="tabla-desplazable tabla-tarjetas">
                      <table>
                        <caption>
                          Total {importe(simulacion.total_centavos, moneda)}, repartido por unidad
                        </caption>
                        <thead>
                          <tr>
                            <th scope="col">Unidad</th>
                            {grupos.map((g) => (
                              <th key={g} scope="col">
                                {nombreDeGrupo(g)}
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
                                <td key={g} data-etiqueta={nombreDeGrupo(g)}>{importe(u.por_grupo[g] ?? 0, moneda)}</td>
                              ))}
                              <td data-etiqueta="Total">{importe(u.total_centavos, moneda)}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}

                  {/* Donde termina el trabajo, qué hacer con él (AC-49). */}
                  <p>
                    Esto sirve para controlar los números antes de liquidar por el sistema de siempre. La aprobación y la publicación llegan más
                    adelante.
                  </p>
                  <div className="acciones no-imprimir">
                    <button type="button" className="secundario" onClick={() => window.print()}>
                      Imprimir o guardar como PDF
                    </button>
                    {detalle.liquidacion.estado === "borrador" && (
                      <button type="button" className="secundario peligro" disabled={enviando} onClick={() => setDescartando(true)}>
                        Descartar el borrador
                      </button>
                    )}
                  </div>
                  {descartando && detalle.liquidacion.estado === "borrador" && (
                    <Hoja titulo="Descartar el borrador" alEscapar={() => setDescartando(false)}>
                      <form className="formulario" onSubmit={(e) => descartar(e, detalle.liquidacion.id)}>
                        <p>El borrador queda guardado como descartado y sus gastos vuelven a estar disponibles.</p>
                        <label htmlFor={`${id}-motivo`}>Motivo para descartar el borrador</label>
                        <input id={`${id}-motivo`} data-foco required maxLength={500} value={motivo} disabled={enviando}
                          onChange={(e) => setMotivo(e.target.value)} />
                        <div className="acciones">
                          <button type="submit" className="peligro" disabled={enviando || motivo.trim() === ""}>
                            Descartar el borrador
                          </button>
                          <button type="button" className="secundario" disabled={enviando} onClick={() => setDescartando(false)}>
                            Cancelar
                          </button>
                        </div>
                      </form>
                    </Hoja>
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
