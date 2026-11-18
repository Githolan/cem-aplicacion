import { useEffect, useId, useState } from "react";
import { Link } from "react-router";

import { CabeceraInterna } from "../CabeceraInterna";
import { api } from "../api/cliente";
import type { components } from "../api/tipos";
import { encabezadoCsrf } from "../csrf";
import { ENCABEZADO_IDEMPOTENCIA, ESTADO, fechaLegible, importe, mensajeDeActo, mensajeDeError, momento, momentoAlFinal, sinLaFalla, sinPisarLoHecho } from "../egresos/comun";
import { ElegirConsorcio } from "../egresos/ElegirConsorcio";
import { AvisoPantalla, ConsorcioSinServicio, FallaDeLectura } from "../egresos/EstadoPantalla";
import { PedirCodigo } from "../egresos/PedirCodigo";
import { exigirSesion, SinSesion, useConsorcio } from "../egresos/useConsorcio";
import { ErrorDeCampo, erroresDe, type ErroresDeCampo, faltaCompletar, hayErrores, marcaDe, SIN_ERRORES, sinElDe, soloDe } from "../ErrorDeCampo";
import { codigoDe, SIN_CONEXION } from "../fallas";
import { type DatoDeFicha, FichaDeRegistro, FilaDeRegistro, useFichas } from "../FichaDeRegistro";
import { REQUISITOS, usePuede } from "../permisos";
import { type FinDeEnvio, VentanaDeFormulario } from "../VentanaDeFormulario";
import { CargandoPagos } from "./CargandoPagos";
import { FilaDeLectura } from "./FilaEnlace";
import { RUTA_COTEJO, RUTA_PAGOS } from "./rutas";
import { FALTA_MOTIVO, LO_RESUELVE_QUIEN_CONCILIA, NO_ES_PAGO, RETIRAR_MARCA, TITULO_MARCADOS, TITULO_SIN_EXPLICAR, textoVerMarcados } from "./textos";
import { useActo } from "./useActo";

// `/pagos/extracto-sin-explicar`: los débitos de los extractos ya subidos que ningún pago explica
// (pregunta 8 del propietario, 13-11-2026, opción c). Lo ven quien concilia y la administración, que
// solo lee. Quien concilia puede decir que un débito no es el pago de una orden (una comisión, un
// impuesto), con motivo y el gesto: no se borra, sale de la lista (respuesta del 13-11, opción a).
// Las marcas vigentes se piden aparte: quien concilia puede retirarlas, con motivo, y el débito
// vuelve a la lista (respuesta del 13-11). Qué entra en cada lista y quién marca o retira lo decide
// el servidor (`backend/tests/test_avisos_y_sin_explicar_0098.py`).
//
// Informe de fricción del 17-11-2026 (tanda 6): sin el párrafo de presentación (COT-07); cada débito
// es una fila compacta que abre su ficha, y el motivo se escribe en su ventana (COT-08); lo ya
// marcado no queda debajo de la lista de trabajo, sino detrás de «Ver los marcados» (COT-09); el
// botón del acto está siempre activo y dice qué falta (C8P-17).
//
// Flujos 18-11-2026 (FL-CON-05, decisión 17): la administración, que solo lee, ve la lista sin ficha
// (la ficha no le agregaba nada ni tenía comandos) y una línea que dice quién lo resuelve.

type Movimiento = components["schemas"]["MovimientoSinExplicar"];
type Marca = components["schemas"]["MarcaNoEsPago"];
type Lectura =
  | { tipo: "cargando" }
  | { tipo: "sin_servicio" }
  | {
      tipo: "ok";
      elementos: Movimiento[];
      total: number;
      siguiente: string | null;
      marcas: Marca[];
      totalMarcas: number;
      siguienteMarcas: string | null;
    };
type Que = "marcar" | "retirar";

const CAMPOS_MOTIVO = { motivo: "el motivo" } as const;
const LARGO_MOTIVO = 300;
const ACTOS = {
  marcar: {
    nombre: "marcar que no es el pago de una orden",
    etiqueta: "Qué es este débito",
    boton: NO_ES_PAGO,
    abre: "secundario",
    peligro: false,
    listo: "Listo: el débito salió de la lista.",
  },
  // Retirar es un acto negativo: en rojo (`pesoDeBotones.ts`).
  retirar: {
    nombre: "retirar la marca",
    etiqueta: "Por qué se retira la marca",
    boton: RETIRAR_MARCA,
    abre: "secundario peligro",
    peligro: true,
    listo: "Listo: la marca se retiró y el débito volvió a la lista.",
  },
} as const;

const dato = (m: Movimiento | Marca) => (m.numero_operacion === null ? fechaLegible(m.fecha) : `Referencia ${m.numero_operacion} · ${fechaLegible(m.fecha)}`);

export function SinExplicar() {
  const elegido = useConsorcio(REQUISITOS.sinExplicar);
  return <SinExplicarDelEdificio key={elegido.consorcio} {...elegido} />;
}

function SinExplicarDelEdificio({ pantalla, consorcio, setConsorcio, aEntrar, aInicio, reintentar }: ReturnType<typeof useConsorcio>) {
  const id = useId();
  const [lectura, setLectura] = useState<Lectura>({ tipo: "cargando" });
  const [vuelta, setVuelta] = useState(0);
  const [verMarcados, setVerMarcados] = useState(false);
  /** La ficha abierta: un débito sin explicar o uno marcado (cada movimiento está en una sola lista). */
  const fichas = useFichas<string>();
  const [ventana, setVentana] = useState<Que | null>(null);
  const [motivo, setMotivo] = useState("");
  const [errores, setErrores] = useState<ErroresDeCampo>(SIN_ERRORES);
  /** Un rechazo que llega después de pedir el código, con la ventana todavía abierta. */
  const [tardio, setTardio] = useState<string | null>(null);
  const puede = usePuede(consorcio);
  const coteja = puede("egresos.conciliar_pago");
  const acto = useActo(aEntrar);
  const { aviso, setAviso, pendiente, setPendiente, enviando } = acto;

  useEffect(() => {
    if (consorcio === "") return;
    let vigente = true;
    const ruta = { params: { path: { consorcio_id: consorcio } } };
    void Promise.all([
      api.GET("/api/v1/consorcios/{consorcio_id}/pagos/extracto/sin-explicar", ruta),
      api.GET("/api/v1/consorcios/{consorcio_id}/pagos/extracto/no-es-pago", ruta),
    ])
      .then(([sueltos, marcados]) => {
        exigirSesion(sueltos.response);
        exigirSesion(marcados.response);
        if (!vigente) return;
        if (sueltos.data !== undefined && marcados.data !== undefined) {
          setAviso(sinLaFalla);
          setLectura({
            tipo: "ok", elementos: sueltos.data.elementos, total: sueltos.data.total, siguiente: sueltos.data.siguiente,
            marcas: marcados.data.elementos, totalMarcas: marcados.data.total, siguienteMarcas: marcados.data.siguiente,
          });
          return;
        }
        const falla = sueltos.data === undefined ? sueltos : marcados;
        const codigo = codigoDe(falla.error);
        if (falla.response.status === ESTADO.sinPermiso && codigo === "sin_permiso") aInicio();
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
  }, [consorcio, vuelta, aEntrar, aInicio, setAviso]);

  function leerMas<T>(pedido: Promise<{ data?: T; error?: unknown; response: Response }>, sumar: (data: T) => void) {
    void pedido
      .then(({ data, error, response }) => {
        exigirSesion(response);
        if (data === undefined) setAviso({ texto: mensajeDeError(response.status, codigoDe(error)), ok: false });
        else sumar(data);
      })
      .catch((e: unknown) => (e instanceof SinSesion ? aEntrar() : setAviso({ texto: SIN_CONEXION, ok: false })));
  }

  /** «Ver más»: la página siguiente de una de las dos listas, sumada a lo ya visto (Codex en #240). Si
   * falla, lo visto queda y el mismo botón la vuelve a pedir. */
  function verMas(lista: "sueltos" | "marcas", despues: string) {
    const opciones = { params: { path: { consorcio_id: consorcio }, query: { despues } } };
    if (lista === "sueltos")
      leerMas(api.GET("/api/v1/consorcios/{consorcio_id}/pagos/extracto/sin-explicar", opciones), (d) =>
        setLectura((l) => (l.tipo === "ok" ? { ...l, elementos: [...l.elementos, ...d.elementos], total: d.total, siguiente: d.siguiente } : l)),
      );
    else
      leerMas(api.GET("/api/v1/consorcios/{consorcio_id}/pagos/extracto/no-es-pago", opciones), (d) =>
        setLectura((l) => (l.tipo === "ok" ? { ...l, marcas: [...l.marcas, ...d.elementos], totalMarcas: d.total, siguienteMarcas: d.siguiente } : l)),
      );
  }

  function cerrarVentana() {
    setVentana(null);
    setMotivo("");
    setErrores(SIN_ERRORES);
    setTardio(null);
  }

  function abrirVentana(que: Que) {
    cerrarVentana();
    setAviso(null);
    setVentana(que);
  }

  /** Hecho, o el débito ya cambió: se cierran la ventana y la ficha y la lista se vuelve a leer. */
  function terminar() {
    cerrarVentana();
    fichas.cerrar(false);
    setVuelta((v) => v + 1);
  }

  function pedir(que: Que, movimiento: string, texto: string, clave: string) {
    const opciones = {
      headers: encabezadoCsrf(),
      params: { path: { consorcio_id: consorcio, movimiento_id: movimiento }, header: { [ENCABEZADO_IDEMPOTENCIA]: clave } },
      body: { motivo: texto },
    };
    return que === "marcar"
      ? api.POST("/api/v1/consorcios/{consorcio_id}/pagos/extracto/sin-explicar/{movimiento_id}/no-es-pago", opciones)
      : api.POST("/api/v1/consorcios/{consorcio_id}/pagos/extracto/no-es-pago/{movimiento_id}/retiro", opciones);
  }

  /** El acto, desde su ventana: el rechazo queda en la ventana; si pide el código, el reintento
   * termina por su cuenta y lo que salga mal se dice en la ventana (`tardio`). */
  async function enviar(que: Que, movimiento: string): Promise<FinDeEnvio> {
    const faltan = faltaCompletar({ motivo: [motivo, FALTA_MOTIVO] });
    if (hayErrores(faltan)) {
      setErrores(faltan);
      return "seguir";
    }
    const texto = motivo.trim();
    let fin: FinDeEnvio = "seguir";
    let primera = true;
    const fallo = (rechazo: string) => {
      setAviso(null);
      if (primera) fin = { rechazo };
      else setTardio(rechazo);
    };
    setTardio(null);
    await acto.hacer({
      quePide: [que, movimiento, texto],
      nombre: ACTOS[que].nombre,
      pedir: (clave) => pedir(que, movimiento, texto, clave),
      alTerminar: () => {
        fin = "cerrar";
        terminar();
        setAviso({ texto: ACTOS[que].listo, ok: true });
      },
      alFallar: (estado, codigo, error) => {
        fallo(mensajeDeActo(estado, error));
        setErrores(soloDe(erroresDe(error, mensajeDeActo(estado, error)), CAMPOS_MOTIVO));
        // El débito ya cambió: la ficha se cierra y el motivo se dice arriba.
        if (estado === ESTADO.conflicto || estado === ESTADO.noEncontrado || codigo === "movimiento_ya_explicado") {
          terminar();
          setAviso({ texto: mensajeDeActo(estado, error), ok: false });
        }
      },
      alNoLlegar: () => fallo(SIN_CONEXION),
    });
    primera = false;
    return fin;
  }

  const ok = lectura.tipo === "ok" ? lectura : null;
  const suelto = ok?.elementos.find((m) => m.movimiento_id === fichas.abierta);
  const marcado = ok?.marcas.find((m) => m.movimiento_id === fichas.abierta);

  /** La ficha de un débito: sus datos y, para quien concilia, el acto que le corresponde. */
  function ficha(m: Movimiento | Marca, que: Que, datos: DatoDeFicha[]) {
    const { etiqueta, boton, abre, peligro } = ACTOS[que];
    return (
      <FichaDeRegistro
        titulo={importe(m.importe_centavos, m.moneda)}
        datos={[...(m.numero_operacion === null ? [] : [{ etiqueta: "Referencia", valor: m.numero_operacion }]), { etiqueta: "Fecha", valor: fechaLegible(m.fecha) }, ...datos]}
        alCerrar={() => {
          cerrarVentana();
          fichas.cerrar();
        }}
        ocupada={enviando}
        ventana={
          ventana === que && (
            <VentanaDeFormulario titulo={boton} textoEnviar={boton} peligro={peligro} alCerrar={cerrarVentana} alEnviar={() => enviar(que, m.movimiento_id)}>
              <label htmlFor={`${id}-motivo`}>{etiqueta}</label>
              <textarea
                id={`${id}-motivo`}
                data-foco
                maxLength={LARGO_MOTIVO}
                rows={3}
                value={motivo}
                {...marcaDe(`${id}-motivo`, errores.motivo)}
                onChange={(evento) => {
                  setMotivo(evento.target.value);
                  setErrores((e) => sinElDe(e, "motivo"));
                }}
              />
              <ErrorDeCampo idCampo={`${id}-motivo`} mensaje={errores.motivo} />
              {tardio !== null && (
                <p className="aviso-error" role="alert">
                  {tardio}
                </p>
              )}
            </VentanaDeFormulario>
          )
        }
      >
        {coteja && (
          <p className="ficha-registro__acciones">
            <button type="button" className={abre} onClick={() => abrirVentana(que)}>
              {boton}
            </button>
          </p>
        )}
      </FichaDeRegistro>
    );
  }

  return (
    <>
      <CabeceraInterna titulo={TITULO_SIN_EXPLICAR} volver={RUTA_PAGOS} destino="Pagos" />
      <ElegirConsorcio pantalla={pantalla} consorcio={consorcio} deshabilitado={enviando} elegir={setConsorcio} />
      {pantalla.tipo === "cargando" && <p role="status">Cargando…</p>}
      {pantalla.tipo === "sin_consorcios" && <p className="tarjeta">Esta cuenta no tiene edificios en Consorcio en Mano.</p>}
      <ConsorcioSinServicio pantalla={pantalla} reintentar={reintentar} />
      <AvisoPantalla aviso={aviso} />
      {pendiente !== null && (
        <PedirCodigo
          acto={pendiente.acto}
          alVerificar={() => {
            const { reintentar: repetir } = pendiente;
            setPendiente(null);
            repetir();
          }}
          alCancelar={() => setPendiente(null)}
        />
      )}
      {pantalla.tipo === "lista" && consorcio !== "" && (
        <>
          <section aria-label={TITULO_SIN_EXPLICAR}>
            {lectura.tipo === "cargando" && <CargandoPagos />}
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
            {ok !== null && ok.total === 0 && <p className="tarjeta">El extracto no tiene nada sin explicar.</p>}
            {ok !== null && !coteja && (ok.total > 0 || ok.totalMarcas > 0) && <p className="comando-no-disponible">{LO_RESUELVE_QUIEN_CONCILIA}</p>}
            {ok !== null && ok.total > 0 && (
              <>
                <ul className="filas-registro" aria-label="Débitos sin explicar">
                  {ok.elementos.map((m) => (
                    <li key={m.movimiento_id}>
                      {coteja ? (
                        <FilaDeRegistro
                          titulo={importe(m.importe_centavos, m.moneda)}
                          resumen={dato(m)}
                          alAbrir={() => fichas.abrir(m.movimiento_id)}
                          refFila={fichas.refFila(m.movimiento_id)}
                          abierta={fichas.abierta === m.movimiento_id}
                        />
                      ) : (
                        <FilaDeLectura titulo={importe(m.importe_centavos, m.moneda)} resumen={dato(m)} />
                      )}
                    </li>
                  ))}
                </ul>
                {ok.siguiente !== null && (
                  <div className="formulario">
                    <button type="button" className="secundario" onClick={() => ok.siguiente !== null && verMas("sueltos", ok.siguiente)}>
                      Ver más débitos sin explicar
                    </button>
                  </div>
                )}
              </>
            )}
          </section>
          {ok !== null && ok.totalMarcas > 0 && !verMarcados && (
            <p className="salidas-pago">
              <button type="button" className="secundario" onClick={() => setVerMarcados(true)}>
                {textoVerMarcados(ok.totalMarcas)}
              </button>
            </p>
          )}
          {ok !== null && ok.totalMarcas > 0 && verMarcados && (
            <section aria-labelledby={`${id}-marcados`}>
              <h2 id={`${id}-marcados`} className="titulo-grupo">
                {TITULO_MARCADOS} <span className="cantidad">{ok.totalMarcas}</span>
              </h2>
              <ul className="filas-registro" aria-label={TITULO_MARCADOS}>
                {ok.marcas.map((m) => (
                  <li key={m.movimiento_id}>
                    {coteja ? (
                      <FilaDeRegistro
                        titulo={importe(m.importe_centavos, m.moneda)}
                        resumen={dato(m)}
                        alAbrir={() => fichas.abrir(m.movimiento_id)}
                        refFila={fichas.refFila(m.movimiento_id)}
                        abierta={fichas.abierta === m.movimiento_id}
                      />
                    ) : (
                      // Sin ficha, el motivo de la marca va en la fila.
                      <FilaDeLectura titulo={importe(m.importe_centavos, m.moneda)} resumen={`${dato(m)} · ${m.motivo}`} />
                    )}
                  </li>
                ))}
              </ul>
              {ok.siguienteMarcas !== null && (
                <div className="formulario">
                  <button type="button" className="secundario" onClick={() => ok.siguienteMarcas !== null && verMas("marcas", ok.siguienteMarcas)}>
                    Ver más marcados
                  </button>
                </div>
              )}
            </section>
          )}
          {/* Quien concilia sube el extracto que lo explique; la administración solo lee. */}
          {coteja && (
            <p className="salidas-pago">
              <Link to={RUTA_COTEJO} state={{ consorcio }} className="enlace-boton enlace-boton--borde">
                Cotejar con el banco
              </Link>
            </p>
          )}
          {coteja && suelto !== undefined && ficha(suelto, "marcar", [{ etiqueta: "Extracto subido", valor: momentoAlFinal(suelto.visto_en) }])}
          {coteja && marcado !== undefined &&
            ficha(marcado, "retirar", [
              { etiqueta: "Marcado", valor: momento(marcado.marcado_en) },
              { etiqueta: "Motivo", valor: marcado.motivo },
            ])}
        </>
      )}
    </>
  );
}
