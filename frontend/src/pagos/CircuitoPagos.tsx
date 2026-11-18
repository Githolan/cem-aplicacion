import { useCallback, useEffect, useMemo, useState } from "react";
import { useLocation, useNavigate, useParams } from "react-router";

import { CabeceraInterna } from "../CabeceraInterna";
import { api } from "../api/cliente";
import type { components } from "../api/tipos";
import { ESTADO, type Aviso, mensajeDeError, sinLaFalla, sinPisarLoHecho } from "../egresos/comun";
import { ElegirConsorcio } from "../egresos/ElegirConsorcio";
import { AvisoPantalla, ConsorcioSinServicio, FallaDeLectura } from "../egresos/EstadoPantalla";
import { exigirSesion, monedasDe, SinSesion, useConsorcio } from "../egresos/useConsorcio";
import { codigoDe, SIN_CONEXION } from "../fallas";
import { REQUISITOS } from "../permisos";
import { CargandoPagos } from "./CargandoPagos";
import { Bandeja } from "./Bandeja";
import { Ficha, type Seguir } from "./Ficha";
import { RUTA_PANEL } from "../panel/Panel";
import { RUTA_CONSULTA, RUTA_COTEJO, RUTA_PAGOS, rutaFicha } from "./rutas";
import { CONSULTA_AL_LADO, useAncho } from "./useAncho";

// `/pagos` y `/pagos/:orden`: una bandeja y una ficha (§1). Al abrir una orden se ve solo su ficha,
// en todos los anchos, con «Volver a Pagos» (decisión 1 del informe de fricción del 17-11-2026: ya
// no va al lado de la bandeja en PC ni se abre sola la primera). Abierta desde la consulta, vuelve a
// la consulta con lo buscado (CNS-01). Terminado un acto, en PC queda solo el «Listo» arriba de la
// bandeja (decisión 2 del 17-11); en tablet y teléfono sigue sola la orden siguiente del mismo paso.
// Abierta desde el cotejo o desde el resumen de pagos, vuelve ahí, con lo que esa pantalla mostraba
// (FL-CON-03, FL-PAN-01, flujos 18-11-2026).

type Elemento = components["schemas"]["ElementoBandejaPagos"];
type BandejaPagos = components["schemas"]["BandejaPagos"];
type Lectura = { tipo: "cargando" } | { tipo: "sin_servicio" } | { tipo: "ok"; elementos: Elemento[]; bandeja: BandejaPagos };

/** A dónde vuelve la ficha, con qué nombre y lo que viaja de vuelta. */
type Vuelta = { a: string; destino: string; estado?: unknown };

const A_LA_BANDEJA: Vuelta = { a: RUTA_PAGOS, destino: "Pagos" };

/** La pantalla de donde se abrió la ficha (`state.volver`): la consulta con su búsqueda, el cotejo o
 * el resumen de pagos, con lo que traía en `state.volverCon`; si no, la bandeja. */
function volverDe(estado: unknown): Vuelta {
  if (typeof estado !== "object" || estado === null || !("volver" in estado)) return A_LA_BANDEJA;
  const { volver } = estado;
  const volverCon = "volverCon" in estado ? estado.volverCon : undefined;
  if (typeof volver !== "string") return A_LA_BANDEJA;
  if (volver === RUTA_CONSULTA || volver.startsWith(`${RUTA_CONSULTA}?`)) return { a: volver, destino: "Consultar pagos" };
  if (volver === RUTA_COTEJO) return { a: volver, destino: "Cotejar con el banco", estado: volverCon };
  if (volver === RUTA_PANEL) return { a: volver, destino: "Resumen de pagos", estado: volverCon };
  return A_LA_BANDEJA;
}

export function CircuitoPagos() {
  const elegido = useConsorcio(REQUISITOS.pagos);
  return <CircuitoDelEdificio key={elegido.consorcio} {...elegido} />;
}

function CircuitoDelEdificio({ pantalla, consorcio, setConsorcio, aEntrar, aInicio, reintentar }: ReturnType<typeof useConsorcio>) {
  const { orden = null } = useParams();
  const volver = volverDe(useLocation().state);
  const navegar = useNavigate();
  const enPC = useAncho(CONSULTA_AL_LADO);
  const veBandeja = orden === null;
  const [lectura, setLectura] = useState<Lectura>({ tipo: "cargando" });
  const [aviso, setAviso] = useState<Aviso | null>(null);
  const [vuelta, setVuelta] = useState(0);
  /** Cambia al terminar un acto: si la siguiente es la misma orden, su ficha se lee de nuevo. */
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
          setLectura({ tipo: "ok", elementos: data.elementos, bandeja: data });
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
    (texto: string, seguir?: Seguir) => {
      const irA = (destino: string, aviso = texto) => {
        navegar(destino, { state: { aviso, consorcio } });
        setVuelta((v) => v + 1);
        setActo((a) => a + 1);
      };
      // En PC, el «Listo» arriba de la bandeja y nada más (decisión 2 del 17-11-2026).
      if (enPC || seguir === undefined) return irA(RUTA_PAGOS);
      // Si mientras tanto se abrió otra ficha, esa queda; la bandeja igual cambió.
      const irSiSigue = (destino: string, aviso = texto) => (seguir.abierta.current ? irA(destino, aviso) : setVuelta((v) => v + 1));
      void api
        .GET("/api/v1/consorcios/{consorcio_id}/pagos/bandeja", { params: { path: { consorcio_id: consorcio } } })
        .then(({ data, response }) => {
          exigirSesion(response);
          // Sin la lista va a la bandeja, que dice qué pasó al leerla.
          const quedan = data?.elementos.filter((e) => e.paso === seguir.paso) ?? [];
          const [siguiente] = quedan;
          if (siguiente === undefined) irSiSigue(RUTA_PAGOS);
          else irSiSigue(rutaFicha(siguiente.orden_id), `${texto} Sigue este, 1 de ${quedan.length}.`);
        })
        .catch((e: unknown) => (e instanceof SinSesion ? aEntrar() : irSiSigue(RUTA_PAGOS)));
    },
    [aEntrar, enPC, consorcio, navegar],
  );

  function elegir(id: string) {
    setConsorcio(id);
    if (orden !== null) navegar(RUTA_PAGOS);
  }

  const conPantalla = pantalla.tipo === "lista" && consorcio !== "";
  /** Los demás edificios, donde se busca una orden que no está en este (FL-BAN-02). La lista se
   * arma de nuevo en cada dibujo: se compara por los ids. */
  const idsOtros = pantalla.tipo === "lista" ? pantalla.consorcios.map((c) => c.id).filter((c) => c !== consorcio).join(" ") : "";
  const otrosEdificios = useMemo(() => (idsOtros === "" ? [] : idsOtros.split(" ")), [idsOtros]);

  return (
    <div className="circuito-pagos">
      {veBandeja && <h1>Pagos</h1>}
      {!veBandeja && (
        <CabeceraInterna titulo="Orden de pago" volver={volver.a} destino={volver.destino} estado={volver.estado} />
      )}
      <ElegirConsorcio pantalla={pantalla} consorcio={consorcio} deshabilitado={false} elegir={elegir} />
      {pantalla.tipo === "cargando" && <p role="status">Cargando…</p>}
      {pantalla.tipo === "sin_consorcios" && <p className="tarjeta">Esta cuenta no tiene edificios en Consorcio en Mano.</p>}
      <ConsorcioSinServicio pantalla={pantalla} reintentar={reintentar} />
      {veBandeja && <AvisoPantalla aviso={aviso} />}
      {veBandeja && conPantalla && (
        <>
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
          {lectura.tipo === "ok" && <Bandeja bandeja={lectura.bandeja} consorcio={consorcio} aEntrar={aEntrar} alCambiar={alCambiar} />}
        </>
      )}
      {orden !== null && conPantalla && (
        <Ficha key={`${orden}-${acto}`} consorcio={consorcio} monedas={monedasDe(pantalla, consorcio)} orden={orden}
          aEntrar={aEntrar} aInicio={aInicio} terminar={terminar} alCambiar={alCambiar}
          otrosEdificios={otrosEdificios} aOtroEdificio={setConsorcio} />
      )}
    </div>
  );
}
