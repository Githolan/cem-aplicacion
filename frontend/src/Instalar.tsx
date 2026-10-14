import { Link } from "react-router";

import { instalar, useInstalacion } from "./instalacion";

// Página /instalar: el enlace que se comparte por WhatsApp o en un código QR en la cartelera.
export function Instalar() {
  const forma = useInstalacion();

  return (
    <>
      <h1>Instalá Consorcio en Mano en tu teléfono</h1>
      <p>
        No hace falta ir a ninguna tienda. La aplicación queda con su ícono en la pantalla de
        inicio y se abre como cualquier otra.
      </p>

      <section aria-labelledby="titulo-instalar" className="tarjeta">
        <h2 id="titulo-instalar">Cómo instalarla</h2>
        {forma === "instalada" && (
          <p className="aviso-ok">Ya está instalada. Abrila desde el ícono de tu pantalla de inicio.</p>
        )}
        {forma === "boton" && (
          <>
            <p>Tocá el botón y confirmá.</p>
            <button type="button" onClick={() => void instalar()}>
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
        <Link to="/">Volver al inicio</Link>
      </p>
    </>
  );
}

// Enlace corto para la página de inicio; desaparece cuando la aplicación ya está instalada.
export function EnlaceInstalar() {
  const forma = useInstalacion();
  if (forma === "instalada") return null;
  return (
    <p>
      <Link to="/instalar">Instalá la aplicación en tu teléfono</Link>
    </p>
  );
}
