import { useCallback, useEffect, useState, useSyncExternalStore } from "react";
import { Link, useNavigate, useParams } from "react-router";

import { api } from "../api/cliente";
import type { components } from "../api/tipos";
import { ESTADO, type Aviso, mensajeDeError, sinLaFalla, sinPisarLoHecho } from "../egresos/comun";
import { ElegirConsorcio } from "../egresos/ElegirConsorcio";
import { AvisoPantalla, ConsorcioSinServicio, FallaDeLectura } from "../egresos/EstadoPantalla";
import { exigirSesion, SinSesion, useConsorcio } from "../egresos/useConsorcio";
import { codigoDe, SIN_CONEXION } from "../fallas";
import { REQUISITOS } from "../permisos";
import { Bandeja } from "./Bandeja";
import { Ficha } from "./Ficha";
import { Pestanas } from "./Pestanas";
import { RUTA_PAGOS, rutaFicha } from "./rutas";
import type { Paso } from "./textos";

// `/pagos` y `/pagos/:orden`: una bandeja y una ficha (§1). En PC la ficha se abre al lado de la
// bandeja, con la primera orden si no se eligió otra; en tablet y teléfono, aparte (§15).

type Elemento = components["schemas"]["ElementoBandejaPagos"];
type Lectura = { tipo: "cargando" } | { tipo: "sin_servicio" } | { tipo: "ok"; elementos: Elemento[] };

/** Desde 1025 px (documento de reglas, «Desktop»). */
const CONSULTA_AL_LADO = "(min-width: 64.0625em)";

function useAlLado(): boolean {
  return useSyncExternalStore(
    (avisar) => {
      const consulta = window.matchMedia(CONSULTA_AL_LADO);
      consulta.addEventListener("change", avisar);
      return () => consulta.removeEventListener("change", avisar);
    },
    () => window.matchMedia(CONSULTA_AL_LADO).matches,
  );
}

export function CircuitoPagos() {
  const elegido = useConsorcio(REQUISITOS.pagos);
  return <CircuitoDelEdificio key={elegido.consorcio} {...elegido} />;
}

function CircuitoDelEdificio({ pantalla, consorcio, setConsorcio, aEntrar, aInicio, reintentar }: ReturnType<typeof useConsorcio>) {
  const { orden = null } = useParams();
  const navegar = useNavigate();
  const alLado = useAlLado();
  const veBandeja = alLado || orden === null;
  const [lectura, setLectura] = useState<Lectura>({ tipo: "cargando" });
  const [aviso, setAviso] = useState<Aviso | null>(null);
  const [vuelta, setVuelta] = useState(0);
  /** Cambia al terminar un acto: la ficha de al lado, aunque sea la misma orden, se lee de nuevo. */
  const [acto, setActo] = useState(0);

  useEffect(() => {
    if (consorcio === "" || !veBandeja) return;
    let vigente = true;
    void api
      .GET("/api/v1/consorcios/{consorcio_id}/pagos/bandeja", { params: { path: { consorcio_id: consorcio } } })
      .then(({ data, error, response }) => {
        exigirSesion(response);
        if (!vigente) return;
        const codigo = codigoDe(error);
        if (data !== undefined) {
          setLectura({ tipo: "ok", elementos: data.elementos });
          setAviso(sinLaFalla);
        } else if (response.status === ESTADO.sinPermiso && codigo === "sin_permiso") aInicio();
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
  }, [consorcio, veBandeja, vuelta, aEntrar, aInicio]);

  const alCambiar = useCallback(() => setVuelta((v) => v + 1), []);

  const terminar = useCallback(
    (texto: string, seguirCon?: Paso) => {
      const irA = (destino: string) => {
        navegar(destino, { state: { aviso: texto, consorcio } });
        setVuelta((v) => v + 1);
        setActo((a) => a + 1);
      };
      if (seguirCon === undefined) return irA(RUTA_PAGOS);
      void api
        .GET("/api/v1/consorcios/{consorcio_id}/pagos/bandeja", { params: { path: { consorcio_id: consorcio } } })
        .then(({ data, response }) => {
          exigirSesion(response);
          // Sin la lista va a la bandeja, que dice qué pasó al leerla.
          const siguiente = data?.elementos.find((e) => e.paso === seguirCon);
          irA(siguiente === undefined ? RUTA_PAGOS : rutaFicha(siguiente.orden_id));
        })
        .catch((e: unknown) => (e instanceof SinSesion ? aEntrar() : irA(RUTA_PAGOS)));
    },
    [aEntrar, consorcio, navegar],
  );

  function elegir(id: string) {
    setConsorcio(id);
    if (orden !== null) navegar(RUTA_PAGOS);
  }

  const elementos = lectura.tipo === "ok" ? lectura.elementos : [];
  const abierta = orden ?? (alLado ? (elementos[0]?.orden_id ?? null) : null);
  const conFicha = abierta !== null && consorcio !== "";
  const conPantalla = pantalla.tipo === "lista" && consorcio !== "";

  return (
    <div className={alLado && conFicha ? "circuito-pagos circuito-pagos--al-lado" : "circuito-pagos"}>
      <div>
        {veBandeja && <h1>Pagos</h1>}
        {!veBandeja && (
          <p>
            <Link to={RUTA_PAGOS}>Volver a Pagos</Link>
          </p>
        )}
        <ElegirConsorcio pantalla={pantalla} consorcio={consorcio} deshabilitado={false} elegir={elegir} />
        {pantalla.tipo === "cargando" && <p role="status">Cargando…</p>}
        {pantalla.tipo === "sin_consorcios" && <p className="tarjeta">No tenés edificios en Consorcio en Mano.</p>}
        <ConsorcioSinServicio pantalla={pantalla} reintentar={reintentar} />
        {veBandeja && <AvisoPantalla aviso={aviso} />}
        {veBandeja && conPantalla && (
          <>
            {lectura.tipo === "cargando" && <p role="status">Cargando los pagos…</p>}
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
            {lectura.tipo === "ok" && <Bandeja elementos={lectura.elementos} consorcio={consorcio} abierta={abierta} />}
          </>
        )}
      </div>
      {conFicha && conPantalla && (
        <div>
          <Ficha key={`${abierta}-${acto}`} consorcio={consorcio} orden={abierta} titulo={alLado ? "h2" : "h1"}
            aEntrar={aEntrar} aInicio={aInicio} terminar={terminar} alCambiar={alCambiar} />
        </div>
      )}
      <Pestanas />
    </div>
  );
}
