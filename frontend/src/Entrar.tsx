import { useEffect, useRef, useState } from "react";
import { Navigate, useLocation } from "react-router";

import { api } from "./api/cliente";
import { EntrarConCodigo } from "./EntrarConCodigo";
import { EntrarConContrasena } from "./EntrarConContrasena";
import { EnlaceInstalar } from "./Instalar";
import { PedirEnlace } from "./PedirEnlace";
import { RUTA_ENTRAR, tokenDelFragmento, VIGENCIA_ENLACE_MS } from "./enlace";
import { useIrAlInicio, useSesion } from "./sesion";

// Página /entrar: la que abre el enlace del correo (documento 13, §10.3). El token llega en el
// fragmento (#token=…), que el navegador nunca manda al servidor: no queda en el registro de
// acceso de nginx ni en Referer. La página lo lee, lo borra de la URL y lo canjea con un POST,
// en el cuerpo. El backend comprueba además la cookie nonce del navegador que pidió el enlace.
//
// Antes de borrarlo de la URL lo guarda en sessionStorage (solo de esta pestaña; nunca viaja al
// servidor), así una recarga o una actualización de la aplicación a mitad de camino no lo pierde.
// Lo borra apenas termina el canje, salga bien o mal, y no usa uno guardado hace más que la
// vigencia del enlace.
//
// Es el paso de Acceso donde se elige cómo entrar (decisión del propietario del 05-11-2026, §2):
// primero el enlace por correo y, debajo, las otras formas, de a una por vez. Terminada la entrada
// sigue sola al inicio. Con sesión abierta no hay nada que hacer acá y sigue también, salvo que la
// persona venga a entrar de nuevo por seguridad (`state.renovar`).

const CLAVE_GUARDADO = "cem.entrar.enlace";

interface Guardado {
  token: string;
  /** Milisegundos desde la época: después de esto el token guardado ya no se usa. */
  venceEn: number;
}

type Estado = "sin_enlace" | "entrando" | "rechazado";

type Metodo = "enlace" | "codigo" | "contrasena";

const OTRAS_FORMAS: Record<Metodo, string> = {
  enlace: "Pedir el enlace por correo",
  codigo: "Entrar con un código",
  contrasena: "Entrar con contraseña",
};

interface Lectura {
  token: string | null;
  /** El token vino del fragmento y todavía no está guardado. */
  delFragmento: boolean;
}

export function guardarToken(token: string, ahora: number): void {
  const guardado: Guardado = { token, venceEn: ahora + VIGENCIA_ENLACE_MS };
  window.sessionStorage.setItem(CLAVE_GUARDADO, JSON.stringify(guardado));
}

export function olvidarToken(): void {
  window.sessionStorage.removeItem(CLAVE_GUARDADO);
}

/** El token guardado en esta pestaña si no venció; uno vencido se borra. */
export function tokenGuardado(ahora: number): string | null {
  const texto = window.sessionStorage.getItem(CLAVE_GUARDADO);
  if (texto === null) return null;
  const guardado = JSON.parse(texto) as Partial<Guardado>;
  if (typeof guardado.token !== "string" || typeof guardado.venceEn !== "number") {
    // Solo esta página escribe esa clave: otra forma es un error de programación, no un dato.
    throw new Error(`${CLAVE_GUARDADO} en sessionStorage no tiene la forma esperada`);
  }
  if (guardado.venceEn <= ahora) {
    olvidarToken();
    return null;
  }
  return guardado.token;
}

function leerToken(): Lectura {
  const delFragmento = tokenDelFragmento(window.location.hash);
  if (delFragmento !== null) return { token: delFragmento, delFragmento: true };
  return { token: tokenGuardado(Date.now()), delFragmento: false };
}

export function Entrar() {
  // En el inicializador: StrictMode monta dos veces y la segunda vez el fragmento ya no está.
  const [{ token, delFragmento }] = useState(leerToken);
  const [estado, setEstado] = useState<Estado>(token ? "entrando" : "sin_enlace");
  const [metodo, setMetodo] = useState<Metodo>("enlace");
  const [pedido, setPedido] = useState<{ correo: string; mensaje: string } | null>(null);
  const canjeado = useRef(false);
  const saliendo = useRef(false);
  const formas = useRef<HTMLDivElement>(null);
  const eligio = useRef(false);
  const irAlInicio = useIrAlInicio();
  const { state: llegada } = useLocation();
  const renovar = (llegada as { renovar?: boolean } | null)?.renovar === true;
  const sesion = useSesion(token === null && !renovar);

  // Al cambiar de forma, el foco va al primer campo: lo de arriba ya no es lo que se eligió.
  useEffect(() => {
    if (eligio.current) formas.current?.querySelector("input")?.focus();
  }, [metodo]);

  // Al recargar o irse, el navegador corta el canje en curso y fetch falla: ese canje no
  // terminó, así que el token se queda guardado para la página que viene.
  useEffect(() => {
    const alSalir = () => {
      saliendo.current = true;
    };
    const alVolver = () => {
      saliendo.current = false;
    };
    window.addEventListener("pagehide", alSalir);
    window.addEventListener("pageshow", alVolver);
    return () => {
      window.removeEventListener("pagehide", alSalir);
      window.removeEventListener("pageshow", alVolver);
    };
  }, []);

  useEffect(() => {
    if (!token || canjeado.current) return;
    canjeado.current = true;
    // Primero a sessionStorage, después fuera de la barra de direcciones, del historial y de
    // una captura de pantalla. Uno ya guardado conserva su vencimiento.
    if (delFragmento) guardarToken(token, Date.now());
    window.history.replaceState(window.history.state, "", RUTA_ENTRAR);
    void api.POST("/api/v1/auth/canje", { body: { token } }).then(
      ({ error }) => {
        olvidarToken();
        if (error === undefined) irAlInicio("Entraste.");
        else setEstado("rechazado");
      },
      () => {
        if (saliendo.current) return;
        olvidarToken();
        setEstado("rechazado");
      },
    );
    // `irAlInicio` cambia en cada render; el canje sale una sola vez por `canjeado`.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, delFragmento]);

  function elegir(forma: Metodo) {
    eligio.current = true;
    setMetodo(forma);
  }

  if (sesion === "con_sesion") return <Navigate to="/" replace />;

  return (
    <>
      <h1>Entrar a Consorcio en Mano</h1>
      <div role="status" aria-live="polite" className={estado === "sin_enlace" ? undefined : "tarjeta"}>
        {estado === "entrando" && <p>Entrando…</p>}
        {estado === "rechazado" && (
          <p className="aviso-error">
            No pudimos hacerte entrar con este enlace. Puede haber vencido, haberse usado o
            haberse abierto en otro navegador. Pedí uno nuevo desde el mismo navegador donde lo
            vas a abrir.
          </p>
        )}
      </div>
      {estado !== "entrando" && (
        <>
          <div ref={formas}>
            {metodo === "enlace" && (
              <PedirEnlace
                alPedir={(correo, mensaje) => {
                  setPedido({ correo, mensaje });
                  elegir("codigo");
                }}
              />
            )}
            {metodo === "codigo" && (
              <EntrarConCodigo correoInicial={pedido?.correo} aviso={pedido?.mensaje ?? null} />
            )}
            {metodo === "contrasena" && <EntrarConContrasena />}
          </div>
          <section aria-labelledby="titulo-otras-formas" className="tarjeta">
            <h2 id="titulo-otras-formas">Otras formas de entrar</h2>
            {(Object.keys(OTRAS_FORMAS) as Metodo[])
              .filter((forma) => forma !== metodo)
              .map((forma) => (
                <p key={forma}>
                  <button type="button" onClick={() => elegir(forma)}>
                    {OTRAS_FORMAS[forma]}
                  </button>
                </p>
              ))}
          </section>
          <EnlaceInstalar />
        </>
      )}
    </>
  );
}
