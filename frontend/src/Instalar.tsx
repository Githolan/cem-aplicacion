import { useState } from "react";
import { Link, Navigate } from "react-router";

import { RUTA_ENTRAR } from "./enlace";
import { instalar, useInstalacion } from "./instalacion";

export const AVISO_INSTALADA =
  "Ya está instalada. Seguí acá, o abrila desde el ícono de tu pantalla de inicio la próxima vez.";

type Pedido = "quieto" | "instalando" | "fallo";

// Página /instalar: el enlace que se comparte por WhatsApp o en un código QR en la cartelera. Es el
// primer paso del recorrido de Acceso (decisión del propietario del 05-11-2026, §2): terminada la
// instalación sigue sola a /entrar; si se cancela o falla, lo dice acá y se puede reintentar. El
// navegador no siempre puede abrir la aplicación instalada: por eso sigue esta pestaña.
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

  if (forma === "instalada") return <Navigate to="/" replace />;
  if (forma === "recien_instalada") {
    return <Navigate to={RUTA_ENTRAR} replace state={{ aviso: AVISO_INSTALADA }} />;
  }

  return (
    <>
      <h1>Instalá Consorcio en Mano en tu teléfono</h1>
      <p>
        No hace falta ir a ninguna tienda. La aplicación queda con su ícono en la pantalla de
        inicio y se abre como cualquier otra.
      </p>

      <section aria-labelledby="titulo-instalar" className="tarjeta">
        <h2 id="titulo-instalar">Cómo instalarla</h2>
        <div role="status" aria-live="polite">
          {pedido === "instalando" && <p>Instalando…</p>}
          {pedido === "fallo" && (
            <p className="aviso-error">No se instaló. Probá de nuevo, o seguí sin instalar.</p>
          )}
        </div>
        {forma === "boton" && (
          <>
            <p>Tocá el botón y confirmá.</p>
            <button type="button" onClick={pedirInstalacion}>
              Instalar la aplicación
            </button>
          </>
        )}
        {forma === "iphone" && (
          <ol className="pasos">
            <li>
              Abrí este enlace en <strong>Safari</strong>.
            </li>
            <li>
              Tocá el botón <strong>Compartir</strong> (el cuadrado con una flecha hacia arriba).
            </li>
            <li>
              Elegí <strong>Agregar a inicio</strong> y después <strong>Agregar</strong>.
            </li>
          </ol>
        )}
        {forma === "menu" && (
          <ol className="pasos">
            <li>
              En Android, abrí este enlace en <strong>Chrome</strong>.
            </li>
            <li>
              Tocá el menú (los tres puntos) y elegí <strong>Instalar aplicación</strong> o{" "}
              <strong>Agregar a la pantalla principal</strong>.
            </li>
            <li>
              En iPhone, abrilo en <strong>Safari</strong>, tocá <strong>Compartir</strong> y elegí{" "}
              <strong>Agregar a inicio</strong>.
            </li>
          </ol>
        )}
      </section>

      <p>
        <Link to={RUTA_ENTRAR}>Seguir sin instalar</Link>
      </p>
    </>
  );
}

// Enlace corto para la página de inicio; desaparece cuando la aplicación ya está instalada.
export function EnlaceInstalar() {
  const forma = useInstalacion();
  if (forma === "instalada" || forma === "recien_instalada") return null;
  return (
    <p>
      <Link to="/instalar">Instalá la aplicación en tu teléfono</Link>
    </p>
  );
}
