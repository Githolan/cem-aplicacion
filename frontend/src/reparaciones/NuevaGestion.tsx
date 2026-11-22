import { useId, useRef, useState } from "react";

import { api } from "../api/cliente";
import type { components } from "../api/tipos";
import { JPEG, PNG, TOPE_BYTES } from "../archivos";
import { encabezadoCsrf } from "../csrf";
import { ENCABEZADO_IDEMPOTENCIA, mensajeDeActo, renovarClave } from "../egresos/comun";
import { exigirSesion, SinSesion } from "../egresos/useConsorcio";
import { ErrorDeCampo, type ErroresDeCampo, faltaCompletar, hayErrores, marcaDe, SIN_ERRORES, sinElDe } from "../ErrorDeCampo";
import { codigoDe, SIN_CONEXION } from "../fallas";
import { SubirArchivo } from "../SubirArchivo";
import { type FinDeEnvio, VentanaDeFormulario } from "../VentanaDeFormulario";
import { subirArchivo } from "./archivoDelActo";
import { AGREGAR_FOTO, ESPACIO_COMUN, motivoDe, NUEVA_GESTION, TEXTO_CON_CUENTA } from "./textos";

// «Nueva gestión» (documento 08, §13.1; RF19, app 0124): quien vive o tiene una unidad pide una
// reparación a cargo del consorcio. Lugar (una unidad suya o un espacio común), texto y hasta
// `fotos_maximo` fotos. El pedido sale primero; las fotos se suben después, cada una por el
// antivirus. Si alguna no se carga (también si la subida no tiene conexión), el pedido queda igual,
// la ventana se cierra con el pedido hecho y dice cuáles faltan, para agregarlas desde el expediente.

type Lista = components["schemas"]["ListaReparaciones"];
type Reparacion = components["schemas"]["Reparacion"];

export const TIPOS_DE_FOTO = [JPEG, PNG] as const;
const COMUN = "";

export const pedidoListo = (numero: number) => `Listo: el pedido N.º ${numero} quedó registrado y se avisó a quien gestiona las reparaciones.`;
/** Las fotos que no subieron, por nombre: quien pidió las agrega desde el expediente («Agregar una
 * foto» se ofrece mientras la reparación está abierta y le faltan fotos, como recién pedida). */
export const fotosSinCargar = (nombres: readonly string[]) =>
  nombres.length === 1
    ? ` La foto ${nombres[0] ?? ""} no se cargó: se puede agregar desde el expediente con «${AGREGAR_FOTO}».`
    : ` Las fotos ${nombres.join(", ")} no se cargaron: se pueden agregar desde el expediente con «${AGREGAR_FOTO}».`;

type Props = {
  consorcio: string;
  lista: Lista;
  aEntrar: () => void;
  alCerrar: () => void;
  alTerminar: (texto: string, reparacion: Reparacion) => void;
};

/** Sube las fotos de un pedido ya creado y devuelve los nombres de las que no subieron. Sin conexión
 * o con la sesión vencida, esa y las que siguen quedan sin subir: no se intentan más. */
async function subirFotos(consorcio: string, reparacion: string, fotos: readonly File[]): Promise<string[]> {
  const sinCargar: string[] = [];
  for (const [i, foto] of fotos.entries()) {
    try {
      const subida = await subirArchivo(consorcio, reparacion, "foto", foto, crypto.randomUUID());
      if ("falla" in subida) sinCargar.push(foto.name);
    } catch {
      sinCargar.push(...fotos.slice(i).map((f) => f.name));
      break;
    }
  }
  return sinCargar;
}

export function NuevaGestion({ consorcio, lista, aEntrar, alCerrar, alTerminar }: Props) {
  const id = useId();
  const [descripcion, setDescripcion] = useState("");
  const [unidad, setUnidad] = useState(lista.unidades.length === 1 ? (lista.unidades[0]?.id ?? COMUN) : COMUN);
  const [lugar, setLugar] = useState("");
  const [fotos, setFotos] = useState<readonly File[]>([]);
  const [errores, setErrores] = useState<ErroresDeCampo>(SIN_ERRORES);
  const clave = useRef<string | null>(null);

  async function enviar(): Promise<FinDeEnvio> {
    const faltan = faltaCompletar({
      descripcion: [descripcion, "Falta contar qué hay que reparar."],
      ...(unidad === COMUN ? { lugar: [lugar, "Falta decir en qué espacio común."] as const } : {}),
    });
    if (hayErrores(faltan)) {
      setErrores(faltan);
      return "seguir";
    }
    clave.current ??= crypto.randomUUID();
    try {
      const { data, error, response } = await api.POST("/api/v1/consorcios/{consorcio_id}/reparaciones", {
        headers: encabezadoCsrf(),
        params: { path: { consorcio_id: consorcio }, header: { [ENCABEZADO_IDEMPOTENCIA]: clave.current } },
        body: { descripcion: descripcion.trim(), unidad_id: unidad === COMUN ? null : unidad, lugar: lugar.trim() === "" ? null : lugar.trim() },
      });
      exigirSesion(response);
      if (data === undefined) {
        const codigo = codigoDe(error);
        if (renovarClave(response.status, codigo)) clave.current = null;
        if (codigo === "texto_con_cuenta") setErrores({ descripcion: TEXTO_CON_CUENTA });
        return { rechazo: motivoDe(codigo, mensajeDeActo(response.status, error)) };
      }
      clave.current = null;
      // El pedido ya existe: desde acá nada vuelve a enviarlo. Una foto que falla, aun sin conexión
      // o con la sesión vencida, es una falla parcial que se dice al terminar.
      const sinCargar = await subirFotos(consorcio, data.id, fotos);
      alTerminar(pedidoListo(data.numero) + (sinCargar.length === 0 ? "" : fotosSinCargar(sinCargar)), data);
      return "cerrar";
    } catch (e: unknown) {
      if (!(e instanceof SinSesion)) return { rechazo: SIN_CONEXION };
      aEntrar();
      return "seguir";
    }
  }

  const idDescripcion = `${id}-descripcion`;
  const idUnidad = `${id}-unidad`;
  const idLugar = `${id}-lugar`;
  return (
    <VentanaDeFormulario titulo={NUEVA_GESTION} textoEnviar="Enviar el pedido" alEnviar={enviar} alCerrar={alCerrar} ancha>
      <label htmlFor={idUnidad}>Dónde</label>
      <select id={idUnidad} value={unidad} onChange={(ev) => setUnidad(ev.target.value)}>
        {lista.unidades.map((u) => (
          <option key={u.id} value={u.id}>
            Unidad {u.designacion}
          </option>
        ))}
        <option value={COMUN}>{ESPACIO_COMUN}</option>
      </select>
      <label htmlFor={idLugar}>{unidad === COMUN ? "Qué espacio común" : "Lugar dentro de la unidad (si hace falta)"}</label>
      <input
        id={idLugar}
        value={lugar}
        maxLength={lista.largo_lugar}
        {...marcaDe(idLugar, errores.lugar)}
        onChange={(ev) => {
          setLugar(ev.target.value);
          setErrores((e) => sinElDe(e, "lugar"));
        }}
      />
      <ErrorDeCampo idCampo={idLugar} mensaje={errores.lugar} />
      <label htmlFor={idDescripcion}>Qué hay que reparar</label>
      <textarea
        id={idDescripcion}
        data-foco
        rows={5}
        value={descripcion}
        maxLength={lista.largo_descripcion}
        {...marcaDe(idDescripcion, errores.descripcion)}
        onChange={(ev) => {
          setDescripcion(ev.target.value);
          setErrores((e) => sinElDe(e, "descripcion"));
        }}
      />
      <ErrorDeCampo idCampo={idDescripcion} mensaje={errores.descripcion} />
      <p className="ayuda">Quien gestiona las reparaciones recibe un aviso por correo; el texto y las fotos se ven solo en la aplicación.</p>
      {fotos.length > 0 && (
        <ul className="lista-documentacion" aria-label="Fotos elegidas">
          {fotos.map((f, i) => (
            <li key={`${f.name}-${i}`}>
              {f.name}{" "}
              <button type="button" className="secundario peligro" onClick={() => setFotos((todas) => todas.filter((_, j) => j !== i))}>
                Quitar <span className="solo-lector">{f.name}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
      {fotos.length < lista.fotos_maximo ? (
        <SubirArchivo
          titulo={fotos.length === 0 ? "Fotos (si hay)" : "Otra foto"}
          de="de la foto"
          tipos={TIPOS_DE_FOTO}
          tope={TOPE_BYTES}
          nota={`Hasta ${lista.fotos_maximo} fotos.`}
          elegido={null}
          alElegir={(f) => setFotos((todas) => [...todas, f])}
        />
      ) : (
        <p className="ayuda">Ya están las {lista.fotos_maximo} fotos que admite el pedido.</p>
      )}
    </VentanaDeFormulario>
  );
}
