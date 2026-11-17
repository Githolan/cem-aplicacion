import { useEffect, useState } from "react";
import { Link } from "react-router";

import { CabeceraInterna } from "../CabeceraInterna";
import { FilaDeRegistro, useFichas } from "../FichaDeRegistro";
import { api } from "../api/cliente";
import type { components } from "../api/tipos";
import { codigoDe, SIN_CONEXION } from "../fallas";
import { RUTA_INICIO } from "../enlace";
import { REQUISITOS, usePuede } from "../permisos";
import {
  type Aviso,
  ESTADO,
  estadoSiNoEsActivo,
  mensajeDeError,
  RUTA_AUTORIZACIONES_PROVEEDORES,
  RUTA_CONSULTA_PROVEEDORES,
  sinLaFalla,
  sinPisarLoHecho,
  TITULO_AUTORIZACIONES_PROVEEDORES,
} from "./comun";
import { ElegirConsorcio } from "./ElegirConsorcio";
import { AvisoPantalla, ConsorcioSinServicio, FallaDeLectura } from "./EstadoPantalla";
import { datosDelProveedor, FichaDelProveedor } from "./FichaDelProveedor";
import { MARCA_FALTANTE, MARCA_POR_VENCER, MARCA_VENCIDA } from "./Proveedores";
import { exigirSesion, SinSesion, useConsorcio } from "./useConsorcio";

// Página /proveedores: la consulta de los proveedores y sus seguros para quien no los gestiona
// (RF50 configurable; app 0104; sí del propietario del 15-11-2026, P1 y P2). Por defecto la ve
// todo el Consejo y la administración; la ven también los habitantes que autorice el Consejo
// (app 0105, «Quién ve los proveedores») y, si el equipo los suma en la configuración del edificio,
// todos los de un tipo de vínculo. Solo lee: sin la cuenta del proveedor, sin cargar ni
// cambiar nada. Lo decide la API con `proveedores.ver`, y la base repite la regla. La excepción
// es el salteo por urgencia del bloqueo por documentación legal (app 0106), para quien la
// configuración del edificio deja saltear (por defecto, el Consejo).
//
// Informe de fricción, tanda 4: la ficha es la misma de la gestión, en una hoja sobre la lista
// (CPR-01), sin lo que no tiene valor ni el estado «Activo» (CPR-02). «Quién ve los proveedores» se
// abre desde acá (MEN-05).

type ProveedorConsulta = components["schemas"]["ProveedorConsulta"];
type Ficha = components["schemas"]["FichaProveedorConsulta"];

export { RUTA_CONSULTA_PROVEEDORES };
export const TITULO_CONSULTA_PROVEEDORES = "Proveedores y sus seguros";

type Lectura =
  | { tipo: "cargando" }
  | { tipo: "sin_servicio" }
  | {
      tipo: "ok";
      proveedores: ProveedorConsulta[];
      vencida: ReadonlySet<string>;
      porVencer: ReadonlySet<string>;
      faltante: ReadonlySet<string>;
    };

export function ConsultaProveedores() {
  const elegido = useConsorcio(REQUISITOS.consultaProveedores);
  return <DelEdificio key={elegido.consorcio} {...elegido} />;
}

function DelEdificio({ pantalla, consorcio, setConsorcio, aEntrar, aInicio, reintentar }: ReturnType<typeof useConsorcio>) {
  const [lectura, setLectura] = useState<Lectura>({ tipo: "cargando" });
  const [vuelta, setVuelta] = useState(0);
  const [ficha, setFicha] = useState<Ficha | null>(null);
  const [aviso, setAviso] = useState<Aviso | null>(null);
  /** Los salteos que registró esta persona desde que abrió la pantalla (comentario 40). */
  const [salteosPropios, setSalteosPropios] = useState<ReadonlySet<number>>(new Set());
  const fichas = useFichas<string>();
  const puede = usePuede(consorcio);

  useEffect(() => {
    if (consorcio === "") return;
    let vigente = true;
    void api
      .GET("/api/v1/consorcios/{consorcio_id}/consulta-proveedores", { params: { path: { consorcio_id: consorcio } } })
      .then(({ data, error, response }) => {
        exigirSesion(response);
        if (!vigente) return;
        const codigo = codigoDe(error);
        if (data !== undefined)
          setLectura({
            tipo: "ok",
            proveedores: data.elementos,
            vencida: new Set(data.documentacion_vencida),
            porVencer: new Set(data.documentacion_por_vencer),
            faltante: new Set(data.documentacion_faltante),
          });
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

  /** Abre la ficha, o la vuelve a leer después de un salteo. */
  function verFicha(p: { id: string }) {
    setAviso(null);
    void api
      .GET("/api/v1/consorcios/{consorcio_id}/consulta-proveedores/{proveedor_id}", {
        params: { path: { consorcio_id: consorcio, proveedor_id: p.id } },
      })
      .then(({ data, error, response }) => {
        exigirSesion(response);
        if (data !== undefined) {
          setFicha(data);
          fichas.abrir(p.id);
        } else setAviso({ texto: mensajeDeError(response.status, codigoDe(error)), ok: false });
      })
      .catch((e: unknown) => (e instanceof SinSesion ? aEntrar() : setAviso({ texto: SIN_CONEXION, ok: false })));
  }

  function reintentarLectura() {
    setAviso(sinLaFalla);
    setLectura({ tipo: "cargando" });
    setVuelta((v) => v + 1);
  }

  const enFicha = ficha !== null && fichas.abierta === ficha.proveedor.id ? ficha : null;

  return (
    <>
      <CabeceraInterna titulo={TITULO_CONSULTA_PROVEEDORES} volver={RUTA_INICIO} destino="Inicio" />
      <ElegirConsorcio pantalla={pantalla} consorcio={consorcio} deshabilitado={false} elegir={setConsorcio} />
      {pantalla.tipo === "cargando" && <p role="status">Cargando…</p>}
      {pantalla.tipo === "sin_consorcios" && <p className="tarjeta">Esta cuenta no tiene edificios en Consorcio en Mano.</p>}
      <ConsorcioSinServicio pantalla={pantalla} reintentar={reintentar} />
      <AvisoPantalla aviso={aviso} />

      {pantalla.tipo === "lista" && (
        <>
          {/* MEN-05: quién ve los proveedores se decide desde acá, no desde otra entrada del menú. */}
          {puede("proveedores.autorizar") && (
            <p>
              <Link to={RUTA_AUTORIZACIONES_PROVEEDORES} state={{ consorcio }} className="enlace-suelto">
                {TITULO_AUTORIZACIONES_PROVEEDORES}
              </Link>
            </p>
          )}
          {lectura.tipo === "cargando" && <p role="status">Cargando los proveedores…</p>}
          {lectura.tipo === "sin_servicio" && <FallaDeLectura alReintentar={reintentarLectura} avisar={setAviso} />}
          {lectura.tipo === "ok" &&
            (lectura.proveedores.length === 0 ? (
              <p className="tarjeta">El edificio no tiene proveedores cargados.</p>
            ) : (
              <ul className="filas-registro" aria-label="Proveedores">
                {lectura.proveedores.map((p) => (
                  <li key={p.id}>
                    <FilaDeRegistro
                      titulo={p.razon_social}
                      refFila={fichas.refFila(p.id)}
                      abierta={fichas.abierta === p.id}
                      alAbrir={() => verFicha(p)}
                      resumen={[
                        p.rubro,
                        estadoSiNoEsActivo(p.estado),
                        lectura.vencida.has(p.id) ? MARCA_VENCIDA : lectura.porVencer.has(p.id) ? MARCA_POR_VENCER : null,
                        lectura.faltante.has(p.id) ? MARCA_FALTANTE : null,
                      ]
                        .filter(Boolean)
                        .join(" · ")}
                    />
                  </li>
                ))}
              </ul>
            ))}

          {enFicha !== null && (
            <FichaDelProveedor
              consorcio={consorcio}
              ficha={enFicha}
              datos={datosDelProveedor(enFicha.proveedor)}
              puedeCambiar={false}
              puedeCargar={false}
              puedeSaltear={puede("egresos.saltear_documentacion")}
              salteosPropios={salteosPropios}
              alRegistrarSalteo={(numero) => setSalteosPropios((propios) => new Set([...propios, numero]))}
              aEntrar={aEntrar}
              alCambiar={() => {
                verFicha(enFicha.proveedor);
                setVuelta((v) => v + 1);
              }}
              alCerrar={() => {
                setFicha(null);
                fichas.cerrar();
              }}
            />
          )}
        </>
      )}
    </>
  );
}
