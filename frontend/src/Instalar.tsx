import { useEffect, useState } from "react";
import { Link, Navigate } from "react-router";

import { RUTA_ENTRAR, RUTA_INSTALAR } from "./enlace";
import { enComputadora, instalar, useInstalacion } from "./instalacion";

export const AVISO_INSTALADA =
  "Ya está instalada. Se puede seguir acá, o abrirla desde el ícono de la pantalla de inicio la próxima vez.";

type Pedido = "quieto" | "instalando" | "fallo";

/** Si el navegador aceptó instalar pero nunca avisa que terminó, cuánto esperar antes de seguir
 * a /entrar: «Instalando…» no puede quedar como única salida (ACC-07). */
export const ESPERA_INSTALACION_MS = 15_000;

/** Quien tocó «Ahora no» en este dispositivo no vuelve a ver el enlace en el inicio ni en Entrar. */
const CLAVE_DESCARTADO = "cem.instalar.descartado";

// Página /instalar: el enlace que se comparte por WhatsApp o en un código QR en la cartelera. Es el
// primer paso del recorrido de Acceso (decisión del propietario del 05-11-2026, §2): terminada la
// instalación sigue sola a /entrar; si se cancela o falla, lo dice acá y se puede reintentar. El
// navegador no siempre puede abrir la aplicación instalada: por eso sigue esta pestaña. El evento
// del navegador sirve una sola vez: Reintentar recarga para que lo vuelva a ofrecer, y si no lo
// ofrece quedan los pasos del menú. En una computadora solo se instala con el botón: sin el evento,
// los pasos son para el teléfono.
export function Instalar() {
  const forma = useInstalacion();
  const [pedido, setPedido] = useState<Pedido>("quieto");

  function pedirInstalacion() {
    setPedido("instalando");
    instalar().then(
      (eleccion) => setPedido(eleccion === "accepted" ? "instalando" : "fallo"),
      () => setPedido("fallo"),
    );
  }

  // Aceptada, si el navegador no avisa que terminó, se sigue igual a /entrar (ACC-07).
  const [demorada, setDemorada] = useState(false);
  useEffect(() => {
    if (pedido !== "instalando") return undefined;
    const reloj = window.setTimeout(() => setDemorada(true), ESPERA_INSTALACION_MS);
    return () => window.clearTimeout(reloj);
  }, [pedido]);

  if (forma === "instalada") return <Navigate to="/" replace />;
  if (forma === "recien_instalada" || demorada) {
    return <Navigate to={RUTA_ENTRAR} replace state={{ aviso: AVISO_INSTALADA }} />;
  }

  // Después de tocar el botón el evento ya se gastó, pero sigue siendo la misma instalación.
  const enEstaComputadora = enComputadora(forma) && (forma === "boton" || pedido !== "quieto");

  return (
    <>
      <h1>Instalar Consorcio en Mano {enEstaComputadora ? "en esta computadora" : "en este dispositivo"}</h1>
      {/* Con el botón del navegador basta el título y el botón (ACC-07); sin él, los pasos. */}
      {forma !== "boton" && pedido === "quieto" && (
        <p>No hace falta ir a ninguna tienda: la aplicación queda con su ícono en la pantalla de inicio.</p>
      )}

      <section aria-label="Instalar" className="tarjeta">
        <div role="status" aria-live="polite">
          {pedido === "instalando" && <p>Instalando…</p>}
          {pedido === "fallo" && (
            <p className="aviso-error">No se instaló. Reintentar, o seguir sin instalar.</p>
          )}
          {pedido === "fallo" && forma !== "boton" && (
            <button type="button" onClick={() => window.location.reload()}>
              Reintentar
            </button>
          )}
        </div>
        {forma === "boton" && (
          <button type="button" onClick={pedirInstalacion}>
            Instalar la aplicación
          </button>
        )}
        {forma === "iphone" && (
          <ol className="pasos">
            <li>
              Abrir este enlace en <strong>Safari</strong>.
            </li>
            <li>
              Tocar el botón <strong>Compartir</strong> (el cuadrado con una flecha hacia arriba).
            </li>
            <li>
              Elegir <strong>Agregar a inicio</strong> y después <strong>Agregar</strong>.
            </li>
          </ol>
        )}
        {forma === "menu" && pedido === "quieto" && (
          <ol className="pasos">
            <li>
              En Android, abrir este enlace en <strong>Chrome</strong>.
            </li>
            <li>
              Tocar el menú (los tres puntos) y elegir <strong>Instalar aplicación</strong> o{" "}
              <strong>Agregar a la pantalla principal</strong>.
            </li>
          </ol>
        )}
      </section>

      <p>
        <Link to={RUTA_ENTRAR} className="enlace-suelto">Seguir sin instalar</Link>
      </p>
    </>
  );
}

const textoEnlace = (computadora: boolean) => `Instalar la aplicación ${computadora ? "en esta computadora" : "en este dispositivo"}`;

// Enlace corto para el inicio y Entrar (INI-10): solo cuando se puede instalar de verdad (el
// navegador ofrece el botón, o es un iPhone con «Agregar a inicio») y hasta que la persona dice
// «Ahora no» en este dispositivo. El camino permanente queda en Cuenta (`EnlaceInstalarEnCuenta`).
// Con el almacenamiento del sitio bloqueado, `localStorage` lanza `SecurityError` (Codex en #257): la
// preferencia no se puede leer ni guardar, como en `leerGuardada()` de lecturaAmpliada.ts. Sin ella, el
// enlace se ofrece y «Ahora no» lo esconde solo en esta pantalla.
function descartadoGuardado(): boolean {
  try {
    return window.localStorage.getItem(CLAVE_DESCARTADO) !== null;
  } catch {
    return false;
  }
}

function guardarDescartado(): void {
  try {
    window.localStorage.setItem(CLAVE_DESCARTADO, "1");
  } catch {
    // Sin almacenamiento la preferencia no dura; el enlace se esconde igual en esta pantalla.
  }
}

export function EnlaceInstalar() {
  const forma = useInstalacion();
  const [descartado, setDescartado] = useState(descartadoGuardado);
  if (descartado || (forma !== "boton" && forma !== "iphone")) return null;
  return (
    <p className="enlace-instalar">
      <Link to={RUTA_INSTALAR}>{textoEnlace(enComputadora(forma))}</Link>{" "}
      <button
        type="button"
        className="sutil"
        onClick={() => {
          guardarDescartado();
          setDescartado(true);
        }}
      >
        Ahora no
      </button>
    </p>
  );
}

// En Cuenta, el camino de siempre: desaparece cuando la aplicación ya está instalada, y en una
// computadora cuyo navegador no la ofrece, donde no hay nada que hacer.
export function EnlaceInstalarEnCuenta() {
  const forma = useInstalacion();
  if (forma === "instalada" || forma === "recien_instalada") return null;
  const computadora = enComputadora(forma);
  if (computadora && forma !== "boton") return null;
  return (
    <p>
      <Link to={RUTA_INSTALAR} className="enlace-suelto">
        {textoEnlace(computadora)}
      </Link>
    </p>
  );
}
