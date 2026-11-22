import { type ReactNode, useEffect, useId, useRef, useState } from "react";

import { CabeceraInterna } from "../CabeceraInterna";
import { api } from "../api/cliente";
import type { components } from "../api/tipos";
import { ComandoNoDisponible } from "../ComandoNoDisponible";
import { encabezadoCsrf } from "../csrf";
import { type Aviso, ENCABEZADO_IDEMPOTENCIA, ESTADO, mensajeDeActo, mensajeDeError, momento, renovarClave, sinLaFalla, sinPisarLoHecho } from "../egresos/comun";
import { ElegirConsorcio } from "../egresos/ElegirConsorcio";
import { AvisoPantalla, ConsorcioSinServicio, FallaDeLectura } from "../egresos/EstadoPantalla";
import { exigirSesion, SinSesion, useConsorcio } from "../egresos/useConsorcio";
import { RUTA_INICIO } from "../enlace";
import { ErrorDeCampo, type ErroresDeCampo, faltaCompletar, hayErrores, marcaDe, SIN_ERRORES, sinElDe } from "../ErrorDeCampo";
import { FichaDeRegistro, FilaDeRegistro, useFichas } from "../FichaDeRegistro";
import { codigoDe, SIN_CONEXION } from "../fallas";
import { RUTA_PADRON } from "../Padron";
import { REQUISITOS, usePuede } from "../permisos";
import { type Resultado, ResultadoDelActo } from "../ResultadoDelActo";
import { type FinDeEnvio, VentanaDeFormulario } from "../VentanaDeFormulario";

// Página /comunicados: los comunicados oficiales del edificio (RF28, mínimo para el piloto en Modo
// Consejo; app 0122; diseño P0 S8 del repositorio CEM). Los leen las personas con un vínculo
// confirmado y quien los emite. Quien emite tiene «Nuevo comunicado»: escribe el título y el texto,
// confirma sabiendo cuántas personas lo van a recibir, y sale un correo a cada una con el título y
// el enlace. Un comunicado no se cambia ni se borra: se corrige con otro que lo cita. Sin estados de
// lectura en este corte: quien emite ve cuántas personas lo recibieron y cuántos correos salieron.

type Lista = components["schemas"]["ListaComunicados"];
type Resumen = components["schemas"]["ComunicadoResumen"];
type Comunicado = components["schemas"]["Comunicado"];
type Referencia = components["schemas"]["ReferenciaComunicado"];

export const RUTA_COMUNICADOS = "/comunicados";
export const TITULO_COMUNICADOS = "Comunicados";
export const NUEVO = "Nuevo comunicado";
export const CORREGIR = "Publicar una corrección";
export const SIN_COMUNICADOS = "Todavía no hay comunicados en este edificio.";
export const CORREGIDO = "Corregido";
/** Un acto imposible: nadie lo recibiría. Solo el aviso y «Cancelar» (criterio 8). */
export const SIN_DESTINATARIOS = "Nadie tiene hoy un vínculo confirmado en el edificio: un comunicado no tendría a quién llegar. Los vínculos se confirman en el padrón.";
export const IR_AL_PADRON = "Ir al padrón";
export const NO_SE_CAMBIA = "Un comunicado no se cambia ni se borra: si hace falta, se publica una corrección que lo cita.";

const personas = (n: number) => (n === 1 ? "1 persona" : `${n} personas`);

/** El paso de confirmar: a cuántas personas les llega, antes de emitir. */
export const llegaA = (n: number) => `Lo van a recibir ${personas(n)} con un vínculo confirmado en el edificio, por correo y en la aplicación.`;
export const salioPara = (n: number) => `Listo: el comunicado salió para ${personas(n)}.`;
export const textoEnvios = (e: NonNullable<Comunicado["envios"]>) =>
  `Se dirigió a ${personas(e.destinatarios)}; ${e.enviados === 1 ? "salió 1 correo" : `salieron ${e.enviados} correos`}.`;

const tituloDe = (r: Referencia) => `«${r.titulo}», del ${momento(r.emitido_en)}`;

type Lectura = { tipo: "cargando" } | { tipo: "sin_servicio" } | { tipo: "ok"; lista: Lista };

export function Comunicados() {
  const elegido = useConsorcio(REQUISITOS.comunicados);
  return <DelEdificio key={elegido.consorcio} {...elegido} />;
}

function DelEdificio({ pantalla, consorcio, setConsorcio, aEntrar, aInicio, reintentar }: ReturnType<typeof useConsorcio>) {
  const [lectura, setLectura] = useState<Lectura>({ tipo: "cargando" });
  const [vuelta, setVuelta] = useState(0);
  const [aviso, setAviso] = useState<Aviso | null>(null);
  const [resultado, setResultado] = useState<Resultado | null>(null);
  /** La ventana de emitir: un comunicado nuevo o la corrección de uno. */
  const [escribiendo, setEscribiendo] = useState<{ corrige: Referencia | null } | null>(null);
  const fichas = useFichas<string>();

  useEffect(() => {
    if (consorcio === "") return;
    let vigente = true;
    void api
      .GET("/api/v1/consorcios/{consorcio_id}/comunicados", { params: { path: { consorcio_id: consorcio } } })
      .then(({ data, error, response }) => {
        exigirSesion(response);
        if (!vigente) return;
        const codigo = codigoDe(error);
        if (data !== undefined) setLectura({ tipo: "ok", lista: data });
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

  const lista = lectura.tipo === "ok" ? lectura.lista : null;

  /** Salió: la lista se vuelve a leer (el nuevo arriba, y el corregido marcado) y el resultado queda
   * donde estaba «Nuevo comunicado». */
  function alEmitir(c: Comunicado) {
    fichas.cerrar(false);
    setResultado({ texto: salioPara(c.envios?.destinatarios ?? 0), ok: true });
    setVuelta((v) => v + 1);
  }

  return (
    <>
      <CabeceraInterna titulo={TITULO_COMUNICADOS} volver={RUTA_INICIO} destino="Inicio" />
      <ElegirConsorcio pantalla={pantalla} consorcio={consorcio} deshabilitado={false} elegir={setConsorcio} />
      {pantalla.tipo === "cargando" && <p role="status">Cargando…</p>}
      {pantalla.tipo === "sin_consorcios" && <p className="tarjeta">Esta cuenta no tiene edificios en Consorcio en Mano.</p>}
      <ConsorcioSinServicio pantalla={pantalla} reintentar={reintentar} />
      <AvisoPantalla aviso={aviso} />
      {pantalla.tipo === "lista" && (
        <>
          {lectura.tipo === "cargando" && <p role="status">Cargando los comunicados…</p>}
          {lectura.tipo === "sin_servicio" && (
            <FallaDeLectura
              alReintentar={() => {
                setAviso(sinLaFalla);
                setLectura({ tipo: "cargando" });
                setVuelta((v) => v + 1);
              }}
              avisar={setAviso}
            />
          )}
          {lista !== null && (
            <>
              {lista.puede_emitir && (
                <div className="acciones">
                  <button type="button" onClick={() => setEscribiendo({ corrige: null })}>
                    {NUEVO}
                  </button>
                </div>
              )}
              <ResultadoDelActo resultado={resultado} alOtroActo={() => setResultado(null)} />
              {lista.elementos.length === 0 ? (
                <p className="tarjeta">{SIN_COMUNICADOS}</p>
              ) : (
                <ul className="filas-registro" aria-label={TITULO_COMUNICADOS}>
                  {lista.elementos.map((c) => (
                    <li key={c.id}>
                      <FilaDeRegistro
                        titulo={c.titulo}
                        resumen={resumenDe(c)}
                        estado={c.corregido ? CORREGIDO : undefined}
                        alAbrir={() => fichas.abrir(c.id)}
                        refFila={fichas.refFila(c.id)}
                        abierta={fichas.abierta === c.id}
                      />
                    </li>
                  ))}
                </ul>
              )}
              {fichas.abierta !== null && (
                <FichaComunicado
                  key={fichas.abierta}
                  consorcio={consorcio}
                  id={fichas.abierta}
                  puedeCorregir={lista.puede_emitir}
                  aEntrar={aEntrar}
                  alCerrar={() => fichas.cerrar()}
                  alCorregir={(r) => setEscribiendo({ corrige: r })}
                />
              )}
              {escribiendo !== null && lista.puede_emitir && (
                <VentanaEmitir
                  consorcio={consorcio}
                  lista={lista}
                  corrige={escribiendo.corrige}
                  aEntrar={aEntrar}
                  alCerrar={() => setEscribiendo(null)}
                  alEmitir={alEmitir}
                />
              )}
            </>
          )}
        </>
      )}
    </>
  );
}

/** El resumen de la fila: cuándo y quién; si corrige a otro, cuál. */
function resumenDe(c: Resumen): string {
  const base = `${momento(c.emitido_en)} · ${c.emitido_por_nombre}`;
  return c.corrige_a === null ? base : `${base} · Corrige «${c.corrige_a.titulo}»`;
}

type LecturaFicha = { tipo: "cargando" } | { tipo: "falla"; texto: string } | { tipo: "ok"; c: Comunicado };

type PropsFicha = {
  consorcio: string;
  id: string;
  puedeCorregir: boolean;
  aEntrar: () => void;
  alCerrar: () => void;
  alCorregir: (r: Referencia) => void;
};

/** La ficha de un comunicado: el texto completo y, para quien emite, cuántos lo recibieron. */
function FichaComunicado({ consorcio, id, puedeCorregir, aEntrar, alCerrar, alCorregir }: PropsFicha) {
  const [lectura, setLectura] = useState<LecturaFicha>({ tipo: "cargando" });

  useEffect(() => {
    let vigente = true;
    void api
      .GET("/api/v1/consorcios/{consorcio_id}/comunicados/{comunicado_id}", { params: { path: { consorcio_id: consorcio, comunicado_id: id } } })
      .then(({ data, error, response }) => {
        exigirSesion(response);
        if (!vigente) return;
        if (data !== undefined) setLectura({ tipo: "ok", c: data });
        else setLectura({ tipo: "falla", texto: mensajeDeError(response.status, codigoDe(error)) });
      })
      .catch((e: unknown) => {
        if (!vigente) return;
        if (e instanceof SinSesion) aEntrar();
        else setLectura({ tipo: "falla", texto: SIN_CONEXION });
      });
    return () => {
      vigente = false;
    };
  }, [consorcio, id, aEntrar]);

  if (lectura.tipo !== "ok")
    return (
      <FichaDeRegistro titulo={TITULO_COMUNICADOS} datos={[]} alCerrar={alCerrar}>
        {lectura.tipo === "cargando" ? (
          <p role="status">Cargando el comunicado…</p>
        ) : (
          <p role="alert" className="aviso-error">
            {lectura.texto}
          </p>
        )}
      </FichaDeRegistro>
    );
  const c = lectura.c;
  const datos: { etiqueta: string; valor: ReactNode }[] = [
    { etiqueta: "Emitido", valor: `${momento(c.emitido_en)}, por ${c.emitido_por_nombre}` },
  ];
  if (c.corrige_a !== null) datos.push({ etiqueta: "Corrige a", valor: tituloDe(c.corrige_a) });
  if (c.corregido_por.length > 0) datos.push({ etiqueta: "Lo corrige", valor: c.corregido_por.map(tituloDe).join("; ") });
  datos.push({ etiqueta: "Texto", valor: <pre className="texto-con-renglones">{c.texto}</pre> });
  if (c.envios !== null) datos.push({ etiqueta: "Envíos", valor: textoEnvios(c.envios) });
  return (
    <FichaDeRegistro titulo={c.titulo} datos={datos} alCerrar={alCerrar}>
      {puedeCorregir && (
        <div className="ficha-registro__acciones">
          <button type="button" className="secundario" onClick={() => alCorregir({ id: c.id, titulo: c.titulo, emitido_en: c.emitido_en })}>
            {CORREGIR}
          </button>
        </div>
      )}
    </FichaDeRegistro>
  );
}

type PropsEmitir = {
  consorcio: string;
  lista: Lista;
  corrige: Referencia | null;
  aEntrar: () => void;
  alCerrar: () => void;
  alEmitir: (c: Comunicado) => void;
};

/** Escribir y, en un segundo paso, confirmar sabiendo a cuántas personas les llega. Sin nadie que lo
 * reciba, solo el aviso y «Cancelar». */
function VentanaEmitir({ consorcio, lista, corrige, aEntrar, alCerrar, alEmitir }: PropsEmitir) {
  const id = useId();
  const [titulo, setTitulo] = useState("");
  const [texto, setTexto] = useState("");
  const [paso, setPaso] = useState<"escribir" | "confirmar">("escribir");
  const [errores, setErrores] = useState<ErroresDeCampo>(SIN_ERRORES);
  /** La misma clave mientras se reintenta el mismo comunicado: nunca sale dos veces. */
  const clave = useRef<string | null>(null);
  const posibles = lista.destinatarios_posibles ?? 0;
  const tituloVentana = corrige === null ? NUEVO : CORREGIR;
  const puede = usePuede(consorcio);

  async function enviar(): Promise<FinDeEnvio> {
    if (paso === "escribir") {
      const faltan = faltaCompletar({ titulo: [titulo, "Falta el título."], texto: [texto, "Falta el texto."] });
      if (hayErrores(faltan)) {
        setErrores(faltan);
        return "seguir";
      }
      setPaso("confirmar");
      return "seguir";
    }
    clave.current ??= crypto.randomUUID();
    try {
      const { data, error, response } = await api.POST("/api/v1/consorcios/{consorcio_id}/comunicados", {
        headers: encabezadoCsrf(),
        params: { path: { consorcio_id: consorcio }, header: { [ENCABEZADO_IDEMPOTENCIA]: clave.current } },
        body: { titulo: titulo.trim(), texto: texto.trim(), corrige_a: corrige?.id ?? null },
      });
      exigirSesion(response);
      if (data !== undefined) {
        clave.current = null;
        alEmitir(data);
        return "cerrar";
      }
      const codigo = codigoDe(error);
      if (renovarClave(response.status, codigo)) clave.current = null;
      // Lo que hay que cambiar está en lo escrito: se vuelve a ese paso, con el motivo a la vista.
      if (codigo === "texto_con_cuenta") setPaso("escribir");
      return { rechazo: mensajeDeActo(response.status, error) };
    } catch (e: unknown) {
      if (!(e instanceof SinSesion)) return { rechazo: SIN_CONEXION };
      aEntrar();
      return "seguir";
    }
  }

  const campo = (nombre: string) => ({ id: `${id}-${nombre}`, ...marcaDe(`${id}-${nombre}`, errores[nombre]) });
  const escribir = (nombre: string, poner: (v: string) => void) => (v: string) => {
    poner(v);
    setErrores((e) => sinElDe(e, nombre));
  };

  if (posibles === 0)
    return (
      <VentanaDeFormulario titulo={tituloVentana} textoEnviar={tituloVentana} alEnviar={() => "seguir"} alCerrar={alCerrar} sinEnviar>
        <ComandoNoDisponible
          motivo={SIN_DESTINATARIOS}
          resolver={puede(...REQUISITOS.padron.flat()) ? { texto: IR_AL_PADRON, a: RUTA_PADRON } : undefined}
        />
      </VentanaDeFormulario>
    );

  return (
    <VentanaDeFormulario titulo={tituloVentana} textoEnviar={paso === "escribir" ? "Seguir" : "Emitir"} alEnviar={enviar} alCerrar={alCerrar} ancha>
      {corrige !== null && <p>Corrige a {tituloDe(corrige)}.</p>}
      {paso === "escribir" ? (
        <>
          <label htmlFor={`${id}-titulo`}>Título</label>
          <input {...campo("titulo")} data-foco value={titulo} maxLength={lista.largo_titulo} onChange={(ev) => escribir("titulo", setTitulo)(ev.target.value)} />
          <ErrorDeCampo idCampo={`${id}-titulo`} mensaje={errores.titulo} />
          <label htmlFor={`${id}-texto`}>Texto</label>
          <textarea {...campo("texto")} value={texto} maxLength={lista.largo_texto} rows={8} onChange={(ev) => escribir("texto", setTexto)(ev.target.value)} />
          <ErrorDeCampo idCampo={`${id}-texto`} mensaje={errores.texto} />
          <p className="ayuda">El correo lleva el título y el enlace; el texto se lee en la aplicación.</p>
        </>
      ) : (
        <>
          <p>
            <strong>{titulo.trim()}</strong>
          </p>
          <p>{llegaA(posibles)}</p>
          <p className="ayuda">{NO_SE_CAMBIA}</p>
          <button type="button" className="secundario" onClick={() => setPaso("escribir")}>
            Volver a escribir
          </button>
        </>
      )}
    </VentanaDeFormulario>
  );
}
