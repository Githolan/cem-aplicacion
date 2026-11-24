import { useEffect, useState } from "react";
import { Link } from "react-router";

import { CabeceraInterna } from "../CabeceraInterna";
import { api } from "../api/cliente";
import { type Aviso, ESTADO, mensajeDeError, sinLaFalla, sinPisarLoHecho } from "../egresos/comun";
import { ElegirConsorcio } from "../egresos/ElegirConsorcio";
import { AvisoPantalla, ConsorcioSinServicio, FallaDeLectura } from "../egresos/EstadoPantalla";
import { exigirSesion, SinSesion, useConsorcio } from "../egresos/useConsorcio";
import { RUTA_INICIO } from "../enlace";
import { FilaDeRegistro, useFichas } from "../FichaDeRegistro";
import { codigoDe, SIN_CONEXION } from "../fallas";
import { REQUISITOS } from "../permisos";
import { CONSULTAS_MAXIMAS, ESPERA_ENTRE_CONSULTAS_MS } from "../reparaciones/archivoDelActo";
import { type Resultado, ResultadoDelActo } from "../ResultadoDelActo";
import { FichaCorreo } from "./FichaCorreo";
import { FichaPago } from "./FichaPago";
import { InformarPago } from "./InformarPago";
import {
  DECIDIDOS,
  INFORMAR,
  type ListaPagos,
  POR_CONFIRMAR,
  POR_CORREO,
  type PagoInformado,
  type PagoPorCorreo,
  resumenCorreo,
  resumenDe,
  RUTA_COTEJO_PAGOS_INFORMADOS,
  SIN_PAGOS,
  TEXTO_ESTADO,
  TITULO_PAGOS_INFORMADOS,
  tituloCorreo,
  tituloDe,
  VER_COTEJO,
} from "./textos";

export { RUTA_PAGOS_INFORMADOS, TITULO_PAGOS_INFORMADOS } from "./textos";

// Página /pagos-informados (PI-S6 del diseño «Pagos informados» del repositorio CEM). Quien tiene un
// vínculo confirmado de propietario, inquilino u ocupante informa los pagos de sus unidades, con el
// comprobante, y completa los que mandó por correo; el Consejo ve todos, confirma o rechaza los que
// esperan y compara lo confirmado con la columna «Pagos» de la liquidación. Arriba, lo que le toca
// hacer a quien lee; los ya decididos, plegados.

type Lectura =
  | { tipo: "cargando" }
  | { tipo: "sin_servicio" }
  | { tipo: "ok"; lista: ListaPagos; correo: Correo };

type Abierta = { tipo: "pago"; id: string } | { tipo: "correo"; id: string };
const claveDe = (a: Abierta) => `${a.tipo}:${a.id}`;

type Correo = { elementos: readonly PagoPorCorreo[]; direccion: string | null };

/** Los correos recibidos: ayudando no se ven (PI-D23) y la lista llega vacía. */
async function leerCorreo(consorcio: string): Promise<Correo> {
  const { data, error, response } = await api.GET("/api/v1/consorcios/{consorcio_id}/pagos-informados/por-correo", {
    params: { path: { consorcio_id: consorcio } },
  });
  exigirSesion(response);
  if (data !== undefined) return { elementos: data.elementos, direccion: data.direccion ?? null };
  if (response.status === ESTADO.sinPermiso) return { elementos: [], direccion: null };
  throw new FallaDeCorreo(mensajeDeError(response.status, codigoDe(error)));
}

class FallaDeCorreo extends Error {}

export function PagosInformados() {
  const elegido = useConsorcio(REQUISITOS.pagosInformados);
  return <DelEdificio key={elegido.consorcio} {...elegido} />;
}

function DelEdificio({ pantalla, consorcio, setConsorcio, aEntrar, aInicio, reintentar }: ReturnType<typeof useConsorcio>) {
  const [lectura, setLectura] = useState<Lectura>({ tipo: "cargando" });
  const [vuelta, setVuelta] = useState(0);
  const [aviso, setAviso] = useState<Aviso | null>(null);
  const [resultado, setResultado] = useState<Resultado | null>(null);
  const [informando, setInformando] = useState(false);
  const fichas = useFichas<string>();
  const [abierta, setAbierta] = useState<Abierta | null>(null);
  /** Cuántas veces se volvió a leer porque un comprobante seguía en el antivirus. */
  const [consultas, setConsultas] = useState(0);

  useEffect(() => {
    if (consorcio === "") return;
    let vigente = true;
    void Promise.all([
      api.GET("/api/v1/consorcios/{consorcio_id}/pagos-informados", { params: { path: { consorcio_id: consorcio } } }),
      leerCorreo(consorcio),
    ])
      .then(([{ data, error, response }, correo]) => {
        exigirSesion(response);
        if (!vigente) return;
        const codigo = codigoDe(error);
        if (data !== undefined) setLectura({ tipo: "ok", lista: data, correo });
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
          setAviso(sinPisarLoHecho(e instanceof FallaDeCorreo ? e.message : SIN_CONEXION));
        }
      });
    return () => {
      vigente = false;
    };
  }, [consorcio, vuelta, aEntrar, aInicio]);

  // Mientras un comprobante sigue en el antivirus, el pago no se confirma ni se ve; y un correo ya
  // analizado espera la lectura automática, que es otro trabajo. Se vuelve a leer cada tanto, con el
  // mismo ritmo y tope que los archivos de una reparación (con la lectura apagada, `leido` nunca
  // llega: el tope corta).
  const enAntivirus =
    lectura.tipo === "ok" &&
    ([...lectura.lista.elementos, ...lectura.correo.elementos].some((e) => e.comprobante_estado === "en_cuarentena") ||
      lectura.correo.elementos.some((r) => r.comprobante_estado === "disponible" && !r.leido));
  useEffect(() => {
    if (!enAntivirus || consultas >= CONSULTAS_MAXIMAS) return;
    const reloj = setTimeout(() => {
      setConsultas((c) => c + 1);
      setVuelta((v) => v + 1);
    }, ESPERA_ENTRE_CONSULTAS_MS);
    return () => clearTimeout(reloj);
  }, [enAntivirus, consultas, lectura]);

  const ok = lectura.tipo === "ok" ? lectura : null;
  const lista = ok?.lista ?? null;
  const correo = ok?.correo.elementos ?? [];
  const direccion = ok?.correo.direccion ?? null;
  const decidibles = new Set(["confirmar", "rechazar"]);
  const porConfirmar = lista?.elementos.filter((p) => p.acciones.some((a) => decidibles.has(a))) ?? [];
  const propiosAbiertos = lista?.elementos.filter((p) => !porConfirmar.includes(p) && (p.estado === "informado" || p.acciones.includes("corregir"))) ?? [];
  const resto = lista?.elementos.filter((p) => !porConfirmar.includes(p) && !propiosAbiertos.includes(p)) ?? [];

  const abrir = (a: Abierta) => {
    setAbierta(a);
    fichas.abrir(claveDe(a));
  };
  const cerrar = (devolverFoco = true) => {
    setAbierta(null);
    fichas.cerrar(devolverFoco);
  };
  const terminar = (texto: string) => {
    cerrar(false);
    setInformando(false);
    setResultado({ texto, ok: true });
    setConsultas(0);
    setVuelta((v) => v + 1);
  };
  const filaPago = (p: PagoInformado) => {
    const a: Abierta = { tipo: "pago", id: p.id };
    return (
      <li key={p.id}>
        <FilaDeRegistro
          titulo={tituloDe(p)}
          resumen={resumenDe(p)}
          estado={TEXTO_ESTADO[p.estado]}
          alAbrir={() => abrir(a)}
          refFila={fichas.refFila(claveDe(a))}
          abierta={fichas.abierta === claveDe(a)}
        />
      </li>
    );
  };
  const filaCorreo = (r: PagoPorCorreo) => {
    const a: Abierta = { tipo: "correo", id: r.id };
    return (
      <li key={r.id}>
        <FilaDeRegistro
          titulo={tituloCorreo(r)}
          resumen={resumenCorreo(r)}
          alAbrir={() => abrir(a)}
          refFila={fichas.refFila(claveDe(a))}
          abierta={fichas.abierta === claveDe(a)}
        />
      </li>
    );
  };
  const pagoAbierto = abierta?.tipo === "pago" ? lista?.elementos.find((p) => p.id === abierta.id) : undefined;
  const correoAbierto = abierta?.tipo === "correo" ? correo.find((r) => r.id === abierta.id) : undefined;
  const puedeInformar = lista !== null && lista.unidades.length > 0;

  return (
    <>
      <CabeceraInterna titulo={TITULO_PAGOS_INFORMADOS} volver={RUTA_INICIO} destino="Inicio" />
      <ElegirConsorcio pantalla={pantalla} consorcio={consorcio} deshabilitado={false} elegir={setConsorcio} />
      {pantalla.tipo === "cargando" && <p role="status">Cargando…</p>}
      {pantalla.tipo === "sin_consorcios" && <p className="tarjeta">Esta cuenta no tiene edificios en Consorcio en Mano.</p>}
      <ConsorcioSinServicio pantalla={pantalla} reintentar={reintentar} />
      <AvisoPantalla aviso={aviso} />
      {pantalla.tipo === "lista" && (
        <>
          {lectura.tipo === "cargando" && <p role="status">Cargando los pagos informados…</p>}
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
              <ResultadoDelActo resultado={resultado} alOtroActo={() => setResultado(null)} />
              {puedeInformar && (
                <>
                  <p>
                    <button type="button" onClick={() => setInformando(true)}>
                      {INFORMAR}
                    </button>
                  </p>
                  {direccion !== null && (
                    <p className="ayuda">
                      También se puede mandar el comprobante por correo a {direccion}, desde la dirección de esta cuenta: después se completa acá.
                    </p>
                  )}
                </>
              )}
              {correo.length > 0 && (
                <section aria-labelledby="pi-correo">
                  <h2 id="pi-correo">{POR_CORREO}</h2>
                  <ul className="filas-registro">{correo.map(filaCorreo)}</ul>
                </section>
              )}
              {porConfirmar.length > 0 && (
                <section aria-labelledby="pi-por-confirmar">
                  <h2 id="pi-por-confirmar">{POR_CONFIRMAR}</h2>
                  <ul className="filas-registro">{porConfirmar.map(filaPago)}</ul>
                </section>
              )}
              {propiosAbiertos.length > 0 && (
                <ul className="filas-registro" aria-label={TITULO_PAGOS_INFORMADOS}>
                  {propiosAbiertos.map(filaPago)}
                </ul>
              )}
              {lista.elementos.length === 0 && correo.length === 0 && <p className="tarjeta">{SIN_PAGOS}</p>}
              {resto.length > 0 && (
                <details>
                  <summary>
                    {DECIDIDOS} ({resto.length})
                  </summary>
                  <ul className="filas-registro" aria-label={DECIDIDOS}>
                    {resto.map(filaPago)}
                  </ul>
                </details>
              )}
              <p>
                <Link to={RUTA_COTEJO_PAGOS_INFORMADOS} state={{ consorcio }}>
                  {VER_COTEJO}
                </Link>
              </p>
              {informando && (
                <InformarPago
                  consorcio={consorcio}
                  modo={{ tipo: "nuevo" }}
                  unidades={lista.unidades}
                  largoNumero={lista.largo_numero_operacion}
                  aEntrar={aEntrar}
                  alCerrar={() => setInformando(false)}
                  alTerminar={terminar}
                />
              )}
              {pagoAbierto !== undefined && (
                <FichaPago
                  key={pagoAbierto.id}
                  consorcio={consorcio}
                  pago={pagoAbierto}
                  lista={lista}
                  aEntrar={aEntrar}
                  alCerrar={() => cerrar()}
                  alTerminar={terminar}
                />
              )}
              {correoAbierto !== undefined && (
                <FichaCorreo
                  key={correoAbierto.id}
                  consorcio={consorcio}
                  recibido={correoAbierto}
                  lista={lista}
                  aEntrar={aEntrar}
                  alCerrar={() => cerrar()}
                  alTerminar={terminar}
                />
              )}
            </>
          )}
        </>
      )}
    </>
  );
}
