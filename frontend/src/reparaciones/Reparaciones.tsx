import { useEffect, useState } from "react";

import { CabeceraInterna } from "../CabeceraInterna";
import { api } from "../api/cliente";
import type { components } from "../api/tipos";
import { type Aviso, ESTADO, mensajeDeError, sinLaFalla, sinPisarLoHecho } from "../egresos/comun";
import { ElegirConsorcio } from "../egresos/ElegirConsorcio";
import { AvisoPantalla, ConsorcioSinServicio, FallaDeLectura } from "../egresos/EstadoPantalla";
import { exigirSesion, SinSesion, useConsorcio } from "../egresos/useConsorcio";
import { RUTA_INICIO } from "../enlace";
import { FilaDeRegistro, useFichas } from "../FichaDeRegistro";
import { codigoDe, SIN_CONEXION } from "../fallas";
import { REQUISITOS } from "../permisos";
import { type Resultado, ResultadoDelActo } from "../ResultadoDelActo";
import { FichaReparacion } from "./FichaReparacion";
import { NuevaGestion } from "./NuevaGestion";
import { ABIERTAS, CERRADAS, NUEVA_GESTION, resumenDe, SIN_REPARACIONES, TEXTO_ESTADO, TITULO_REPARACIONES, tituloDe } from "./textos";

export { RUTA_REPARACIONES, TITULO_REPARACIONES } from "./textos";

// Página /reparaciones: «Seguimiento» (documento 08, §13.1; RF19, app 0124). Quien pidió ve sus
// pedidos y su estado; quien gestiona (`reparaciones.gestionar`, el Consejo y la administración),
// todos. Arriba, «Pedir una reparación» («Nueva gestión») si la API dice que la persona puede; las
// abiertas primero y las terminadas plegadas. Cada ficha es el expediente, con la próxima acción.

type Lista = components["schemas"]["ListaReparaciones"];

type Lectura = { tipo: "cargando" } | { tipo: "sin_servicio" } | { tipo: "ok"; lista: Lista };

export function Reparaciones() {
  const elegido = useConsorcio(REQUISITOS.reparaciones);
  return <DelEdificio key={elegido.consorcio} {...elegido} />;
}

function DelEdificio({ pantalla, consorcio, setConsorcio, aEntrar, aInicio, reintentar }: ReturnType<typeof useConsorcio>) {
  const [lectura, setLectura] = useState<Lectura>({ tipo: "cargando" });
  const [vuelta, setVuelta] = useState(0);
  const [aviso, setAviso] = useState<Aviso | null>(null);
  const [resultado, setResultado] = useState<Resultado | null>(null);
  const [pidiendo, setPidiendo] = useState(false);
  const fichas = useFichas<string>();

  useEffect(() => {
    if (consorcio === "") return;
    let vigente = true;
    void api
      .GET("/api/v1/consorcios/{consorcio_id}/reparaciones", { params: { path: { consorcio_id: consorcio } } })
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
  const abiertas = lista?.elementos.filter((r) => ABIERTAS.includes(r.estado)) ?? [];
  const cerradas = lista?.elementos.filter((r) => !ABIERTAS.includes(r.estado)) ?? [];
  const fila = (r: Lista["elementos"][number]) => (
    <li key={r.id}>
      <FilaDeRegistro
        titulo={tituloDe(r)}
        resumen={resumenDe(r, lista?.gestiona === true)}
        estado={TEXTO_ESTADO[r.estado]}
        alAbrir={() => fichas.abrir(r.id)}
        refFila={fichas.refFila(r.id)}
        abierta={fichas.abierta === r.id}
      />
    </li>
  );
  const terminar = (texto: string) => {
    setResultado({ texto, ok: true });
    setVuelta((v) => v + 1);
  };

  return (
    <>
      <CabeceraInterna titulo={TITULO_REPARACIONES} volver={RUTA_INICIO} destino="Inicio" />
      <ElegirConsorcio pantalla={pantalla} consorcio={consorcio} deshabilitado={false} elegir={setConsorcio} />
      {pantalla.tipo === "cargando" && <p role="status">Cargando…</p>}
      {pantalla.tipo === "sin_consorcios" && <p className="tarjeta">Esta cuenta no tiene edificios en Consorcio en Mano.</p>}
      <ConsorcioSinServicio pantalla={pantalla} reintentar={reintentar} />
      <AvisoPantalla aviso={aviso} />
      {pantalla.tipo === "lista" && (
        <>
          {lectura.tipo === "cargando" && <p role="status">Cargando las reparaciones…</p>}
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
              {lista.pide && (
                <p>
                  <button type="button" onClick={() => setPidiendo(true)}>
                    {NUEVA_GESTION}
                  </button>
                </p>
              )}
              {lista.elementos.length === 0 ? (
                <p className="tarjeta">{SIN_REPARACIONES}</p>
              ) : (
                <>
                  {abiertas.length > 0 && (
                    <ul className="filas-registro" aria-label={TITULO_REPARACIONES}>
                      {abiertas.map(fila)}
                    </ul>
                  )}
                  {cerradas.length > 0 && (
                    <details>
                      <summary>
                        {CERRADAS} ({cerradas.length})
                      </summary>
                      <ul className="filas-registro" aria-label={CERRADAS}>
                        {cerradas.map(fila)}
                      </ul>
                    </details>
                  )}
                </>
              )}
              {pidiendo && (
                <NuevaGestion
                  consorcio={consorcio}
                  lista={lista}
                  aEntrar={aEntrar}
                  alCerrar={() => setPidiendo(false)}
                  alTerminar={(texto) => {
                    setPidiendo(false);
                    terminar(texto);
                  }}
                />
              )}
              {fichas.abierta !== null && (
                <FichaReparacion
                  key={fichas.abierta}
                  consorcio={consorcio}
                  id={fichas.abierta}
                  lista={lista}
                  aEntrar={aEntrar}
                  alCerrar={() => fichas.cerrar()}
                  alTerminar={(texto) => {
                    fichas.cerrar(false);
                    terminar(texto);
                  }}
                />
              )}
            </>
          )}
        </>
      )}
    </>
  );
}
