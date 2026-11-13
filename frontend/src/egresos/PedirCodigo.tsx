import { type FormEvent, useEffect, useId, useRef, useState } from "react";
import { Link, useLocation } from "react-router";

import { ActivarLlave } from "../ActivarLlave";
import { api } from "../api/cliente";
import { CodigosRecuperacion } from "../CodigosRecuperacion";
import { encabezadoCsrf } from "../csrf";
import { codigoDe, SIN_CONEXION } from "../fallas";
import { Hoja } from "../Hoja";
import { confirmarConLlave, esDeEsteDispositivo, llaveDisponible } from "../llaveAcceso";
import { type MetodoConfirmar, recordarMetodo, ultimoMetodo } from "../metodoConfirmar";
import { FallaDeLectura, mensajeDeLectura } from "../permisos";
import { codigoAplicacion, codigoVerificacion, RUTA_SEGUNDO_FACTOR } from "../SegundoFactor";
import { mensajeDeError } from "./comun";

// Confirmar en el momento (rediseño de pagos, §12): ante `segundo_factor_requerido` aparece directo
// el último método usado en este dispositivo, sin un paso «Confirmá que sos vos». Con la llave es el
// aviso del sistema; con un código, una hoja encima de la pantalla. «Directo» es elegir el método, no
// saltear el gesto: lo sigue pidiendo y controlando el servidor. Al confirmar, la pantalla repite el
// mismo acto con la misma `Idempotency-Key`. «Usar otro método» aparece recién si falla o se vuelve
// atrás, y ofrece solo lo que la persona tiene activo.
//
// Un navegador puede negar la llave si el gesto del botón ya quedó lejos (Safari): cae en «No se pudo
// confirmar», y «Probar de nuevo» la pide con un toque propio.
//
// Informe de fricción, AC-34: quien tiene la llave en otro equipo y acá no la puede usar no cae
// directo en un código de recuperación (se gasta uno): la hoja lo explica y lo ofrece. Quien no
// tiene nada activado activa la llave en la misma hoja, sin salir de la pantalla y sin perder lo
// escrito; el alta deja la sesión verificada y el acto sigue.

interface Props {
  /** Qué se va a reintentar, en palabras («aprobar la orden»). */
  acto: string;
  alVerificar: () => void;
  alCancelar: () => void;
}

type Activos = Record<MetodoConfirmar, boolean>;
type Clase = Exclude<MetodoConfirmar, "llave">;

type Paso =
  | { tipo: "leyendo" }
  | { tipo: "llave" }
  | { tipo: "codigo"; clase: Clase }
  | { tipo: "fallo"; mensaje: string }
  | { tipo: "otro" }
  | { tipo: "llave_lejos" }
  | { tipo: "sin_alta" }
  | { tipo: "codigos"; codigos: string[] };

const TEXTO_METODO: Record<MetodoConfirmar, string> = {
  llave: "PIN, huella o cara",
  aplicacion: "Código de la aplicación",
  recuperacion: "Código de recuperación",
};
const METODOS = Object.keys(TEXTO_METODO) as MetodoConfirmar[];

const TITULO_CODIGO: Record<Clase, string> = {
  aplicacion: "Escribí el código de tu aplicación",
  recuperacion: "Escribí un código de recuperación",
};

const LLAVE_EN_OTRO_LADO = "Este navegador no puede usar tu llave de acceso. Probá desde el dispositivo donde la tenés.";

async function leerActivos(): Promise<{ activos: Activos; llaves: number }> {
  const { data, error, response } = await api.GET("/api/v1/auth/totp");
  if (data === undefined) throw new FallaDeLectura(mensajeDeError(response.status, codigoDe(error)));
  return {
    activos: {
      llave: llaveDisponible() && data.llaves > 0,
      aplicacion: data.activo,
      recuperacion: data.codigos_recuperacion_restantes > 0,
    },
    llaves: data.llaves,
  };
}

/** El último si sigue activo; si no, lo que menos pide: la llave de acá, después la aplicación. */
async function primerMetodo(activos: Activos): Promise<MetodoConfirmar | null> {
  const ultimo = ultimoMetodo();
  if (ultimo !== null && activos[ultimo]) return ultimo;
  if (activos.llave) {
    const { data } = await api.GET("/api/v1/auth/llave");
    if (data?.some((k) => esDeEsteDispositivo(k.credencial_id)) === true) return "llave";
  }
  return (["aplicacion", "llave", "recuperacion"] as const).find((m) => activos[m]) ?? null;
}

/** Sin ningún método no hay nada que pedir: el camino es darlo de alta, acá mismo si se puede. */
function SinAlta({ conLlave }: { conLlave: boolean }) {
  const { pathname } = useLocation();
  return conLlave ? (
    <p>
      Otras opciones:{" "}
      <Link to={RUTA_SEGUNDO_FACTOR} state={{ volver: pathname }}>
        la página del segundo factor
      </Link>
      . Lo escrito en esta pantalla puede perderse.
    </p>
  ) : (
    <p role="alert">
      Todavía no está activada.{" "}
      <Link to={RUTA_SEGUNDO_FACTOR} state={{ volver: pathname }}>
        Activarla
      </Link>{" "}
      y volver a este paso.
    </p>
  );
}

export function PedirCodigo({ acto, alVerificar, alCancelar }: Props) {
  const id = useId();
  const [paso, setPaso] = useState<Paso>({ tipo: "leyendo" });
  const [activos, setActivos] = useState<Activos | null>(null);
  const [vuelta, setVuelta] = useState(0);
  const [codigo, setCodigo] = useState("");
  const [enviando, setEnviando] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const campo = useRef<HTMLInputElement>(null);
  const llave = useRef<AbortController | null>(null);

  // Si la pantalla se desmonta (otro edificio, otra página) mientras se verifica, lo pendiente no
  // sigue: se descarta con ella, y el aviso del sistema se cierra.
  const montado = useRef(true);
  useEffect(() => {
    montado.current = true;
    return () => {
      montado.current = false;
      llave.current?.abort();
    };
  }, []);

  function confirmar() {
    const controlador = new AbortController();
    llave.current = controlador;
    setPaso({ tipo: "llave" });
    void confirmarConLlave(controlador.signal).then((resultado) => {
      if (!montado.current || controlador.signal.aborted) return;
      if (resultado.ok) {
        recordarMetodo("llave");
        alVerificar();
      } else setPaso({ tipo: "fallo", mensaje: resultado.mensaje });
    });
  }

  function usar(metodo: MetodoConfirmar) {
    setError(null);
    setCodigo("");
    if (metodo === "llave") confirmar();
    else setPaso({ tipo: "codigo", clase: metodo });
  }

  useEffect(() => {
    let vigente = true;
    leerActivos()
      .then(async ({ activos: leidos, llaves }) => {
        const primero = await primerMetodo(leidos);
        if (!vigente) return;
        setActivos(leidos);
        // La llave está en otro equipo y acá solo queda un código de recuperación: no se gasta sin avisar.
        if (primero === "recuperacion" && llaves > 0 && !leidos.llave && ultimoMetodo() !== "recuperacion")
          setPaso({ tipo: "llave_lejos" });
        else if (primero !== null) usar(primero);
        else if (llaves > 0) setPaso({ tipo: "fallo", mensaje: LLAVE_EN_OTRO_LADO });
        else setPaso({ tipo: "sin_alta" });
      })
      .catch((falla: unknown) => {
        if (vigente) setPaso({ tipo: "fallo", mensaje: mensajeDeLectura(falla) });
      });
    return () => {
      vigente = false;
    };
    // `usar` cambia en cada render; lo que vuelve a leer es `vuelta`.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [vuelta]);

  // El campo se apaga mientras se verifica: si el código no sirvió, se sigue escribiendo ahí.
  useEffect(() => {
    if (!enviando) campo.current?.focus();
  }, [enviando]);

  function verificar(limpio: string, clase: Clase) {
    setEnviando(true);
    setError(null);
    void api
      .POST("/api/v1/auth/totp/verificar", { body: { codigo: limpio }, headers: encabezadoCsrf() })
      .then(({ data, error: falla, response }) => {
        if (!montado.current) return;
        if (data !== undefined) {
          setCodigo("");
          recordarMetodo(clase);
          alVerificar();
          return;
        }
        const motivo = codigoDe(falla);
        if (motivo === "segundo_factor_invalido" || motivo === "codigo_invalido") {
          setCodigo("");
          setError("El código no es válido. Probá con el que muestra ahora la aplicación.");
        } else if (motivo === "segundo_factor_sin_alta") setPaso({ tipo: "sin_alta" });
        else setError(mensajeDeError(response.status, motivo));
      })
      .catch(() => {
        if (montado.current) setError(SIN_CONEXION);
      })
      .finally(() => {
        if (montado.current) setEnviando(false);
      });
  }

  function escribir(valor: string, clase: Clase) {
    setCodigo(valor);
    const completo = clase === "aplicacion" ? codigoAplicacion(valor) : null;
    if (completo !== null) verificar(completo, clase);
  }

  function enviar(evento: FormEvent<HTMLFormElement>, clase: Clase) {
    evento.preventDefault();
    const limpio = codigoVerificacion(codigo);
    if (limpio === null) setError("Escribí los seis números de la aplicación o un código de recuperación.");
    else verificar(limpio, clase);
  }

  const hayOtro = activos !== null && METODOS.filter((m) => activos[m]).length > 1;
  const cancelar = (
    <button type="button" className="secundario peligro" disabled={enviando} onClick={alCancelar}>
      Cancelar
    </button>
  );
  const usarOtro = hayOtro && (
    <button type="button" className="secundario" disabled={enviando} onClick={() => setPaso({ tipo: "otro" })}>
      Usar otro método
    </button>
  );

  if (paso.tipo === "leyendo") return <p role="status">Preparando la confirmación…</p>;

  if (paso.tipo === "llave")
    return (
      <Hoja key="llave" titulo="Usá tu PIN, huella o cara" alEscapar={alCancelar}>
        <p>Para {acto}. Lo pide tu dispositivo y después seguimos con el mismo pedido.</p>
        <div className="formulario">
          <div className="acciones">{cancelar}</div>
        </div>
      </Hoja>
    );

  if (paso.tipo === "fallo")
    return (
      <Hoja key="fallo" titulo="No se pudo confirmar" alEscapar={alCancelar}>
        <p role="alert">{paso.mensaje}</p>
        <div className="formulario">
          <div className="acciones">
            <button
              type="button"
              data-foco
              onClick={() => (activos?.llave === true ? confirmar() : setVuelta((v) => v + 1))}
            >
              Probar de nuevo
            </button>
            {usarOtro}
            {cancelar}
          </div>
        </div>
      </Hoja>
    );

  if (paso.tipo === "otro")
    return (
      <Hoja key="otro" titulo="Usar otro método" alEscapar={alCancelar}>
        <div className="formulario">
          <div className="acciones">
            {METODOS.filter((m) => activos?.[m] === true).map((m, i) => (
              <button key={m} type="button" data-foco={i === 0 ? true : undefined} onClick={() => usar(m)}>
                {TEXTO_METODO[m]}
              </button>
            ))}
            {cancelar}
          </div>
        </div>
      </Hoja>
    );

  if (paso.tipo === "llave_lejos")
    return (
      <Hoja key="llave_lejos" titulo="La llave está en otro dispositivo" alEscapar={alCancelar}>
        <p>
          Este navegador no puede usar la llave de acceso. Para {acto} se puede usar un código de
          recuperación; cada uno sirve una sola vez.
        </p>
        <div className="formulario">
          <div className="acciones">
            <button type="button" data-foco onClick={() => usar("recuperacion")}>
              Usar un código de recuperación (se gasta uno)
            </button>
            {cancelar}
          </div>
        </div>
      </Hoja>
    );

  if (paso.tipo === "codigos")
    return (
      <Hoja key="codigos" titulo="Guardar los códigos de recuperación">
        <p className="aviso-ok">Listo: la llave quedó activada.</p>
        <CodigosRecuperacion codigos={paso.codigos} medio="llave" alGuardar={alVerificar} />
      </Hoja>
    );

  if (paso.tipo === "sin_alta") {
    const conLlave = llaveDisponible();
    return (
      <Hoja key="sin_alta" titulo="Falta la verificación en dos pasos" alEscapar={alCancelar}>
        <p>
          Para {acto} hace falta la verificación en dos pasos.
          {conLlave && " Se activa acá mismo, con el PIN, la huella o la cara de este dispositivo."}
        </p>
        <ActivarLlave
          alActivar={(codigos) => (codigos === null ? alVerificar() : setPaso({ tipo: "codigos", codigos }))}
        />
        <SinAlta conLlave={conLlave} />
        <div className="formulario">
          <div className="acciones">{cancelar}</div>
        </div>
      </Hoja>
    );
  }

  const { clase } = paso;
  return (
    <Hoja key={clase} titulo={TITULO_CODIGO[clase]} alEscapar={alCancelar}>
      <p>Para {acto}. Después seguimos con el mismo pedido.</p>
      <form className="formulario" onSubmit={(e) => enviar(e, clase)} noValidate>
        <label htmlFor={`${id}-codigo`}>Código</label>
        <input
          id={`${id}-codigo`}
          ref={campo}
          data-foco
          inputMode={clase === "aplicacion" ? "numeric" : "text"}
          autoComplete="one-time-code"
          value={codigo}
          disabled={enviando}
          aria-invalid={error !== null}
          aria-describedby={error === null ? undefined : `${id}-error`}
          onChange={(e) => escribir(e.target.value, clase)}
        />
        {error !== null && (
          <p id={`${id}-error`} role="alert">
            {error}
          </p>
        )}
        <div className="acciones">
          {clase === "recuperacion" && (
            <button type="submit" disabled={enviando}>
              {enviando ? "Verificando…" : "Verificar y seguir"}
            </button>
          )}
          {usarOtro}
          {cancelar}
        </div>
      </form>
    </Hoja>
  );
}
