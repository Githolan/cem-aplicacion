import { useEffect, useId, useRef, useState } from "react";

import { api } from "../api/cliente";
import { CabeceraInterna } from "../CabeceraInterna";
import { RUTA_INICIO } from "../enlace";
import type { components } from "../api/tipos";
import { encabezadoCsrf } from "../csrf";
import { ErrorDeCampo, type ErroresDeCampo, erroresDe, faltaCompletar, hayErrores, marcaDe, SIN_ERRORES, sinElDe } from "../ErrorDeCampo";
import { FilaDeRegistro, useFichas } from "../FichaDeRegistro";
import { codigoDe, esPasajera, SIN_CONEXION } from "../fallas";
import { type Resultado, ResultadoDelActo } from "../ResultadoDelActo";
import { type FinDeEnvio, VentanaDeFormulario } from "../VentanaDeFormulario";
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
import { useCodigoEnMedio } from "./useCodigoEnMedio";
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
//
// Informe de fricción del 17-11-2026, tanda 8: el período se elige en la ventana de «Abrir un
// borrador» (LIQ-01); abierto un borrador, se ve solo él, con «Volver a la lista» (LIQ-02); las
// órdenes se agregan en su ventana (LIQ-03, C8P-19) y el descarte también (C8P-20), con los botones
// siempre activos; el resultado sale en el borrador, donde se actuó (LIQ-06). Sin gastos no hay
// simulación que mostrar (LIQ-05) y «es un borrador» se dice una sola vez, en la lista (LIQ-04).

type Resumen = components["schemas"]["Liquidacion"];
type Detalle = components["schemas"]["LiquidacionDetalle"];
type Liquidable = components["schemas"]["OrdenLiquidable"];
type EstadoLiquidacion = Resumen["estado"];

export const RUTA_LIQUIDACION = "/liquidacion";
export const ABRIR_UN_BORRADOR = "Abrir un borrador";
export const AGREGAR_GASTOS = "Agregar gastos";
export const DESCARTAR = "Descartar el borrador";

type Lectura =
  | { tipo: "cargando" }
  | { tipo: "sin_servicio" }
  | { tipo: "ok"; liquidaciones: Resumen[]; liquidables: Liquidable[] };
/** Una lectura que pidió el segundo factor. `alCancelar`: qué queda a la vista si no se confirma
 * (AC-53: nunca un «Cargando…» sin salida). */
type Pendiente = { reintentar: () => void; alCancelar: () => void };
/** La clave de un «agregar» que todavía no se sabe si llegó, con lo que identifica su gasto. */
type GastoRetenido = { clave: string; liquidacion: string; orden: string; grupo: string };
type Acto =
  | { tipo: "preparar"; periodo: string }
  | { tipo: "quitar"; liquidacion: string; gasto: string }
  | { tipo: "descartar"; liquidacion: string; motivo: string };
/** La ventana abierta: elegir el período, agregar gastos o descartar el borrador. */
type Ventana = "abrir" | "agregar" | "descartar";

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
  /** La liquidación abierta: con ella a la vista, la lista no se muestra (LIQ-02). */
  const fichas = useFichas<string>();
  const abierta = fichas.abierta;
  const [detalle, setDetalle] = useState<Detalle | null>(null);
  /** Por qué no llegó el detalle de la abierta. */
  const [fallaDetalle, setFallaDetalle] = useState<string | null>(null);
  const [vuelta, setVuelta] = useState(0);
  /** El período elegido, «AAAA-MM»; empieza en el mes anterior (AC-51). */
  const [mes, setMes] = useState(mesAnterior);
  /** Las órdenes tildadas para agregar (AC-48). */
  const [elegidas, setElegidas] = useState<readonly string[]>([]);
  const [grupo, setGrupo] = useState("");
  const [motivo, setMotivo] = useState("");
  const [ventana, setVentana] = useState<Ventana | null>(null);
  const [errores, setErrores] = useState<ErroresDeCampo>(SIN_ERRORES);
  const [enviando, setEnviando] = useState(false);
  /** Mientras se agregan varias: cuántas van. */
  const [progreso, setProgreso] = useState<{ hechas: number; total: number } | null>(null);
  /** Por qué no llegó la lista. */
  const [aviso, setAviso] = useState<Aviso | null>(null);
  /** El resultado del último acto, donde se actuó: en el borrador o en la lista (LIQ-06). */
  const [resultado, setResultado] = useState<Resultado | null>(null);
  const [pendiente, setPendiente] = useState<Pendiente | null>(null);
  const codigo = useCodigoEnMedio();

  // Otra liquidación tiene otros grupos: la orden, el grupo y el motivo de descarte elegidos no
  // pasan de una a otra (un motivo escrito para un borrador no descarta otro).
  function abrir(liquidacion: string) {
    fichas.abrir(liquidacion);
    setFallaDetalle(null);
    setElegidas([]);
    setGrupo("");
    setMotivo("");
    setVentana(null);
    setErrores(SIN_ERRORES);
  }

  /** De vuelta a la lista; el foco vuelve a la fila (o, tras un acto, al resultado). */
  function volverALaLista(devolverFoco = true) {
    fichas.cerrar(devolverFoco);
    setVentana(null);
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
              reintentar: () => setVuelta((v) => v + 1),
              alCancelar: () => setFallaDetalle(mensajeDeError(estado, SEGUNDO_FACTOR_REQUERIDO)),
            });
            return;
          }
          setDetalle(uno?.data ?? null);
          setFallaDetalle(uno !== null && uno.data === undefined ? mensajeDeError(uno.response.status, codigoDe(uno.error)) : null);
          return;
        }
        const codigoError = codigoDe(falla.error);
        if (codigoError === SEGUNDO_FACTOR_REQUERIDO)
          setPendiente({
            reintentar: () => setVuelta((v) => v + 1),
            alCancelar: () => setLectura({ tipo: "sin_servicio" }),
          });
        else if (falla.response.status === ESTADO.sinPermiso && codigoError === "sin_permiso") aInicio();
        else {
          setLectura({ tipo: "sin_servicio" });
          setAviso(sinPisarLoHecho(mensajeDeError(falla.response.status, codigoError)));
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

  /** Un acto sobre la liquidación, con su clave; ante el segundo factor, la hoja del código encima y,
   * confirmado, el mismo pedido con la misma clave. Dice cómo terminó para la ventana que lo pidió. */
  async function hacer(acto: Acto): Promise<FinDeEnvio> {
    if (!montado.current) return "cerrar";
    soltarClaveSiCambio(clave, ultimoActo, JSON.stringify(acto));
    setEnviando(true);
    try {
      for (;;) {
        clave.current ??= crypto.randomUUID();
        actoRetenido.current = acto;
        const header = { [ENCABEZADO_IDEMPOTENCIA]: clave.current };
        const headers = encabezadoCsrf();
        const { data, error, response } = await (acto.tipo === "preparar"
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
              }));
        exigirSesion(response);
        if (data !== undefined) {
          clave.current = null;
          if (acto.tipo === "preparar") {
            // Vuelve a un período completo: los desplegables nunca quedan vacíos ni a medias (Codex en #236).
            setMes(mesAnterior());
            abrir(data.id);
            setResultado({ texto: "Listo: el borrador del período quedó abierto.", ok: true });
          } else if (acto.tipo === "quitar") setResultado({ texto: "Listo: el gasto salió del borrador.", ok: true });
          else {
            volverALaLista(false);
            setResultado({
              texto: "Listo: el borrador se descartó. Sus gastos vuelven a estar disponibles y el período admite uno nuevo.",
              ok: true,
            });
          }
          setVuelta((v) => v + 1);
          return "cerrar";
        }
        const codigoError = codigoDe(error);
        if (codigoError === SEGUNDO_FACTOR_REQUERIDO) {
          const que = { preparar: "abrir el borrador", quitar: "quitar el gasto", descartar: "descartar el borrador" }[acto.tipo];
          if (await codigo.pedir(que)) continue;
          return "seguir";
        }
        if (renovarClave(response.status, codigoError)) clave.current = null;
        const texto = mensajeDeError(response.status, codigoError);
        const cambio = cambioLoVisto(response.status, codigoError);
        if (cambio) setVuelta((v) => v + 1);
        if (acto.tipo === "quitar") {
          setResultado({ texto, ok: false });
          return "seguir";
        }
        if (acto.tipo === "descartar") {
          // El único dato del descarte es el motivo: un 422 es de él (el del campo, o el del servidor).
          if (response.status === ESTADO.invalido) {
            setErrores({ motivo: erroresDe(error).motivo ?? (error as { mensaje: string }).mensaje });
            return "seguir";
          }
          // Lo descartó otra persona: no queda nada que descartar; el porqué va en el borrador.
          if (cambio) {
            setResultado({ texto, ok: false });
            return "cerrar";
          }
        }
        return { rechazo: texto };
      }
    } catch (e: unknown) {
      if (!(e instanceof SinSesion)) {
        if (acto.tipo !== "quitar") return { rechazo: SIN_CONEXION };
        setResultado({ texto: SIN_CONEXION, ok: false });
        return "seguir";
      }
      aEntrar();
      return "seguir";
    } finally {
      setEnviando(false);
    }
  }

  async function preparar(): Promise<FinDeEnvio> {
    const periodoElegido = `${mes}-01`;
    // Un período admite un solo borrador: si ya lo tiene, se abre ese y no se pide otro.
    const existente = liquidaciones.find((l) => l.periodo === periodoElegido && l.estado === "borrador");
    if (existente !== undefined) {
      setMes(mesAnterior());
      abrir(existente.id);
      return "cerrar";
    }
    return hacer({ tipo: "preparar", periodo: periodoElegido });
  }

  /** Agrega las órdenes de a una, cada una con su pedido: el servidor valida cada gasto como
   * siempre. Que una orden no entre no frena a las demás; una falla pasajera (red, límite, servicio)
   * sí, y las que faltan quedan elegidas para repetir con las mismas claves. Si entraron todas, la
   * ventana se cierra y el «Listo» sale en el borrador; si no, la ventana dice cuáles no. */
  async function agregar(liquidacion: string, ordenes: readonly Liquidable[], grupoDeGasto: string): Promise<FinDeEnvio> {
    const faltan: Record<string, string> = {};
    if (ordenes.length === 0) faltan.ordenes = "Falta tildar las órdenes para agregar.";
    if (grupoDeGasto === "") faltan.grupo = "Falta elegir el grupo de gasto.";
    if (hayErrores(faltan)) {
      setErrores(faltan);
      return "seguir";
    }
    setEnviando(true);
    const agregadas: string[] = [];
    const fallas: string[] = [];
    let corte: string | null = null;
    try {
      for (let i = 0; i < ordenes.length; i += 1) {
        const o = ordenes[i]!;
        if (!montado.current) return "cerrar";
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
        const codigoError = codigoDe(error);
        if (codigoError === SEGUNDO_FACTOR_REQUERIDO) {
          if (await codigo.pedir("agregar los gastos")) {
            i -= 1; // la misma orden otra vez, con su misma clave
            continue;
          }
          corte = "Falta la confirmación.";
          break;
        }
        if (renovarClave(response.status, codigoError)) clavesDeGasto.current.delete(llave);
        if (esPasajera(response.status)) {
          corte = mensajeDeError(response.status, codigoError);
          break;
        }
        fallas.push(`${nombreDeOrden(o)}: ${motivoDelGasto(response.status, error)}`);
      }
    } catch (e: unknown) {
      if (e instanceof SinSesion) {
        aEntrar();
        return "seguir";
      }
      corte = SIN_CONEXION;
    } finally {
      setEnviando(false);
      setProgreso(null);
    }
    setElegidas((actuales) => actuales.filter((o) => !agregadas.includes(o)));
    setVuelta((v) => v + 1);
    const dicho = avisoDeAgregar(ordenes.length, agregadas.length, fallas, corte);
    if (dicho.ok) {
      setResultado(dicho);
      return "cerrar";
    }
    return { rechazo: dicho.texto };
  }

  function alternar(orden: string) {
    setErrores((e) => sinElDe(e, "ordenes"));
    setElegidas((actuales) => (actuales.includes(orden) ? actuales.filter((o) => o !== orden) : [...actuales, orden]));
  }

  async function descartar(liquidacion: string): Promise<FinDeEnvio> {
    const faltan = faltaCompletar({ motivo: [motivo, "Falta escribir el motivo."] });
    if (hayErrores(faltan)) {
      setErrores(faltan);
      return "seguir";
    }
    return hacer({ tipo: "descartar", liquidacion, motivo: motivo.trim() });
  }

  function abrirVentana(v: Ventana) {
    setErrores(SIN_ERRORES);
    setVentana(v);
  }

  const liquidaciones = lectura.tipo === "ok" ? lectura.liquidaciones : [];
  const liquidables = lectura.tipo === "ok" ? lectura.liquidables : [];
  // El detalle a la vista es el de la abierta: mientras llega el de otra, no se muestra el anterior.
  const visto = detalle !== null && detalle.liquidacion.id === abierta ? detalle : null;
  const simulacion = visto?.simulacion;
  // Un grupo puede tener dos versiones del esquema en el mismo borrador: una columna por grupo.
  const grupos = [...new Set(simulacion?.grupos.map((g) => g.grupo) ?? [])];
  const moneda = simulacion?.moneda ?? null;
  // Un borrador tiene una sola moneda: con gastos, solo se ofrecen órdenes de esa moneda.
  const monedaBorrador = visto?.gastos[0]?.moneda ?? null;
  // Como la regla del backend (0058): entra solo lo pagado hasta el último día del período (P3 136).
  const hasta = visto === null ? null : mesSiguiente(visto.liquidacion.periodo);
  const ofrecibles = liquidables.filter(
    (o) => (monedaBorrador === null || o.moneda === monedaBorrador) && (hasta === null || o.pagada_el < hasta),
  );
  // Solo cuentan las tildadas que siguen ofrecidas: lo que se agrega es lo que se ve tildado.
  const aAgregar = ofrecibles.filter((o) => elegidas.includes(o.orden_id));
  const todas = ofrecibles.length > 0 && aAgregar.length === ofrecibles.length;
  // Sin órdenes para ofrecer (entraron todas, o la lectura ya las muestra en el borrador), la
  // ventana de agregar no tiene qué mostrar: se cierra y no vuelve sola cuando haya otras.
  const sinQueAgregar = ventana === "agregar" && visto !== null && ofrecibles.length === 0;
  useEffect(() => {
    if (sinQueAgregar) setVentana(null);
  }, [sinQueAgregar]);
  // Con un solo grupo vigente no hay nada que elegir: va ese (AC-48, «un grupo por defecto»).
  const unicoGrupo = visto !== null && visto.grupos.length === 1 ? visto.grupos[0]!.grupo : null;
  const grupoDeGasto = unicoGrupo ?? grupo;
  // Las descartadas salen de la lista de trabajo y quedan plegadas: nada se borra (AC-47).
  const enTrabajo = liquidaciones.filter((l) => l.estado !== "descartada");
  const descartadas = liquidaciones.filter((l) => l.estado === "descartada");
  const filaDe = (l: Resumen) => (
    <li key={l.id}>
      <FilaDeRegistro titulo={tituloDeLiquidacion(l.periodo, l.estado, l.version)} alAbrir={() => abrir(l.id)}
        refFila={fichas.refFila(l.id)} deshabilitada={enviando} />
    </li>
  );
  const resultadoDelActo = <ResultadoDelActo resultado={resultado} alOtroActo={() => setResultado(null)} />;

  /** La lista: abrir un borrador nuevo y las liquidaciones del edificio. */
  function vistaLista() {
    return (
      <>
        {/* «Es un borrador» se dice una vez, acá (LIQ-04). */}
        <p>Es un borrador para revisar los números: todavía no se aprueba ni se publica a los propietarios.</p>
        <div className="acciones no-imprimir">
          <button type="button" disabled={enviando} onClick={() => abrirVentana("abrir")}>
            {ABRIR_UN_BORRADOR}
          </button>
        </div>
        {resultadoDelActo}
        {enTrabajo.length === 0 ? (
          <p className="tarjeta no-imprimir">
            {descartadas.length === 0 ? "Todavía no hay liquidaciones en este edificio." : "No hay borradores abiertos en este edificio."}
          </p>
        ) : (
          // Cada fila entera abre su liquidación, sola (comentario 21; LIQ-02).
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
        {ventana === "abrir" && (
          <VentanaDeFormulario titulo={ABRIR_UN_BORRADOR} textoEnviar="Abrir el borrador" alEnviar={preparar} alCerrar={() => setVentana(null)}>
            <label htmlFor={`${id}-mes`}>Mes</label>
            <select id={`${id}-mes`} data-foco value={mes.slice(5, 7)}
              onChange={(e) => {
                const elegido = e.target.value;
                setMes((actual) => `${actual.slice(0, 4)}-${elegido}`);
              }}>
              {MESES.map((nombre, i) => (
                <option key={nombre} value={String(i + 1).padStart(2, "0")}>{nombre}</option>
              ))}
            </select>
            <label htmlFor={`${id}-anio`}>Año</label>
            <select id={`${id}-anio`} value={mes.slice(0, 4)}
              onChange={(e) => {
                const elegido = e.target.value;
                setMes((actual) => `${elegido}-${actual.slice(5, 7)}`);
              }}>
              {aniosOfrecidos().map((anio) => (
                <option key={anio} value={anio}>{anio}</option>
              ))}
            </select>
          </VentanaDeFormulario>
        )}
      </>
    );
  }

  /** La ventana de agregar: las órdenes pagadas para tildar y el grupo de gasto. */
  function ventanaAgregar(d: Detalle) {
    const idOrdenes = `${id}-ordenes`;
    const marcaOrdenes = marcaDe(idOrdenes, errores.ordenes);
    return (
      <VentanaDeFormulario titulo={AGREGAR_GASTOS} textoEnviar={aAgregar.length > 1 ? `Agregar ${aAgregar.length} gastos` : "Agregar el gasto"}
        alEnviar={() => agregar(d.liquidacion.id, aAgregar, grupoDeGasto)} alCerrar={() => setVentana(null)} ancha>
        <fieldset>
          <legend>Órdenes pagadas para agregar</legend>
          {ofrecibles.length > 1 && (
            <label className="opcion">
              <input type="checkbox" data-foco checked={todas}
                onChange={() => {
                  setErrores((e) => sinElDe(e, "ordenes"));
                  setElegidas(todas ? [] : ofrecibles.map((o) => o.orden_id));
                }} />
              Elegir las {ofrecibles.length}
            </label>
          )}
          <ul className="tildes-pago" aria-label="Órdenes pagadas">
            {ofrecibles.map((o, i) => (
              <li key={o.orden_id}>
                <label className="tilde-pago">
                  <input type="checkbox" data-foco={ofrecibles.length === 1 && i === 0 ? true : undefined} checked={elegidas.includes(o.orden_id)}
                    {...marcaOrdenes} onChange={() => alternar(o.orden_id)} />
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
          <ErrorDeCampo idCampo={idOrdenes} mensaje={errores.ordenes} />
        </fieldset>
        {unicoGrupo === null ? (
          <>
            <label htmlFor={`${id}-grupo`}>Grupo de gasto</label>
            <select id={`${id}-grupo`} value={grupo} {...marcaDe(`${id}-grupo`, errores.grupo)}
              onChange={(e) => {
                setGrupo(e.target.value);
                setErrores((actuales) => sinElDe(actuales, "grupo"));
              }}>
              <option value="">Elegir un grupo</option>
              {d.grupos.map((g) => (
                <option key={g.grupo} value={g.grupo}>
                  {nombreDeGrupo(g.grupo)}
                </option>
              ))}
            </select>
            <ErrorDeCampo idCampo={`${id}-grupo`} mensaje={errores.grupo} />
          </>
        ) : (
          <p>Se reparten como {nombreDeGrupo(unicoGrupo)}: es el único grupo de gasto del período.</p>
        )}
        {progreso !== null && (
          <p role="status">
            Agregando {progreso.hechas + 1} de {progreso.total}…
          </p>
        )}
      </VentanaDeFormulario>
    );
  }

  /** La ventana del descarte: el motivo y la confirmación. */
  function ventanaDescartar(d: Detalle) {
    const idMotivo = `${id}-motivo`;
    return (
      <VentanaDeFormulario titulo={DESCARTAR} textoEnviar={DESCARTAR} peligro alEnviar={() => descartar(d.liquidacion.id)} alCerrar={() => setVentana(null)}>
        <p>El borrador queda guardado como descartado y sus gastos vuelven a estar disponibles.</p>
        <label htmlFor={idMotivo}>Motivo para descartar el borrador</label>
        <input id={idMotivo} data-foco maxLength={500} value={motivo} {...marcaDe(idMotivo, errores.motivo)}
          onChange={(e) => {
            setMotivo(e.target.value);
            setErrores((actuales) => sinElDe(actuales, "motivo"));
          }} />
        <ErrorDeCampo idCampo={idMotivo} mensaje={errores.motivo} />
      </VentanaDeFormulario>
    );
  }

  /** La liquidación abierta, sola (LIQ-02): por tener tabla e impresión, como vista de la página. */
  function vistaBorrador() {
    const volver = (
      <p className="no-imprimir">
        <button type="button" className="secundario" disabled={enviando} onClick={() => volverALaLista()}>
          Volver a la lista
        </button>
      </p>
    );
    if (fallaDetalle !== null)
      return (
        <>
          {volver}
          <section className="tarjeta" aria-label="Detalle de la liquidación">
            <p className="aviso-error" role="alert">
              No se pudo abrir esta liquidación. {fallaDetalle}
            </p>
            <Reintentar alReintentar={() => setVuelta((v) => v + 1)} />
          </section>
        </>
      );
    if (visto === null || simulacion === undefined)
      return (
        <>
          {volver}
          <p role="status">Abriendo la liquidación…</p>
        </>
      );
    const borrador = visto.liquidacion.estado === "borrador";
    const sinOrdenes = <p className="no-imprimir">No hay órdenes pagadas hasta el fin de {periodo(visto.liquidacion.periodo)} para agregar.</p>;
    return (
      <>
        {volver}
        <section className="tarjeta" aria-label={`Borrador de ${periodo(visto.liquidacion.periodo)}`}>
          <h2>{tituloDeLiquidacion(visto.liquidacion.periodo, visto.liquidacion.estado, visto.liquidacion.version)}</h2>
          {resultadoDelActo}

          <div className="liquidacion__titulo-gastos">
            <h3>Gastos</h3>
            {borrador && ofrecibles.length > 0 && visto.grupos.length > 0 && (
              <button type="button" className="secundario no-imprimir" disabled={enviando}
                onClick={() => abrirVentana("agregar")}>
                {AGREGAR_GASTOS}
              </button>
            )}
          </div>
          {visto.gastos.length === 0 ? (
            // Un solo mensaje de vacío (LIQ-05): sin órdenes para ofrecer, ese lo dice todo.
            borrador && ofrecibles.length === 0 ? sinOrdenes : <p>Todavía no hay gastos agregados.</p>
          ) : (
            <>
              <ul className="versiones" aria-label="Gastos del borrador">
                {visto.gastos.map((g) => (
                  <li key={g.id}>
                    {g.razon_social}: {importe(g.importe_centavos, g.moneda)}, {g.concepto} ({nombreDeGrupo(g.grupo)},
                    pagada el {dia(g.pagada_el)}){" "}
                    {borrador && (
                      <button type="button" className="secundario peligro no-imprimir" disabled={enviando}
                        aria-label={`Quitar ${g.razon_social}, ${importe(g.importe_centavos, g.moneda)}`}
                        onClick={() => void hacer({ tipo: "quitar", liquidacion: visto.liquidacion.id, gasto: g.id })}>
                        Quitar
                      </button>
                    )}
                  </li>
                ))}
              </ul>
              {borrador && ofrecibles.length === 0 && sinOrdenes}
            </>
          )}
          {borrador && ofrecibles.length > 0 && visto.grupos.length === 0 && (
            <p className="no-imprimir">
              Faltan los porcentajes de cada unidad para este período: sin ellos no se puede repartir. Los carga soporte.
            </p>
          )}

          {/* Sin gastos no hay nada que repartir: la simulación no se muestra (LIQ-05). */}
          {visto.gastos.length > 0 && (
            <>
              <h3>Simulación del reparto</h3>
              {simulacion.bloqueo !== null ? (
                <p className="aviso-error" role="alert">
                  No se puede repartir. {simulacion.bloqueo}
                </p>
              ) : simulacion.unidades.length === 0 || moneda === null ? (
                <p>Todavía no hay nada que repartir.</p>
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
            </>
          )}

          <div className="acciones no-imprimir">
            <button type="button" className="secundario" onClick={() => window.print()}>
              Imprimir o guardar como PDF
            </button>
            {borrador && (
              <button type="button" className="secundario peligro" disabled={enviando}
                onClick={() => abrirVentana("descartar")}>
                {DESCARTAR}
              </button>
            )}
          </div>
        </section>
        {borrador && ventana === "agregar" && !sinQueAgregar && ventanaAgregar(visto)}
        {borrador && ventana === "descartar" && ventanaDescartar(visto)}
      </>
    );
  }

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
          acto="ver la liquidación"
          alVerificar={() => {
            const { reintentar: repetir } = pendiente;
            setPendiente(null);
            repetir();
          }}
          alCancelar={() => {
            setPendiente(null);
            pendiente.alCancelar();
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
          {lectura.tipo === "ok" && (abierta === null ? vistaLista() : vistaBorrador())}
        </>
      )}
      {codigo.hoja}
    </>
  );
}
